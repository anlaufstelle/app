"""Service-Tests fuer den Art.-16-Berichtigungsworkflow (ADR-041, Refs #1060).

Jeder Guard von :func:`approve_rectification` bekommt einen eigenen
Negativ-Test — die Reihenfolge ist Teil des Vertrags (Status → Selbst-Review →
Rolle → Pfad-Re-Validierung gegen den *Reviewer* → Klartext-Konflikt), damit ein
Antrag nie an der falschen Stelle scheitert und eine Fehlermeldung nie mehr
verraet, als der Aufrufer ohnehin wissen darf.
"""

import json
from datetime import date, time

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from core.models import AuditLog, Client, DocumentType, RectificationRequest
from core.services.client.rectification import (
    approve_rectification,
    decode_rectification_text,
    decode_rectification_value,
    rectifiable_fields,
    reject_rectification,
    request_rectification,
)
from core.services.file_vault import is_encrypted_value
from tests._sensitivity_helpers import make_inherited_fields_explicit


def _paths(client, user):
    return [path for path, _label in rectifiable_fields(client, user)]


def _add_high_field(facility, document_type, name="Suchtdiagnose"):
    """Feld mit Sensibilitaet HOCH (und damit verschluesselt) am Typ ergaenzen.

    ``doc_type_client_profile.diagnose`` ist zwar verschluesselt, erbt aber die
    NORMAL-Sensitivity des Typs — fuer den Sichtbarkeits-Filter braucht es ein
    echtes HOCH-Feld.
    """
    from core.models import DocumentType, DocumentTypeField, FieldTemplate

    ft = FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=ft, sort_order=7)
    return ft


def _add_typed_field(facility, document_type, name, field_type, sort_order=11):
    """Dynamisches Feld eines beliebigen Typs am Dokumentationstyp ergaenzen."""
    from core.models import DocumentTypeField, FieldTemplate

    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=field_type)
    DocumentTypeField.objects.create(document_type=document_type, field_template=ft, sort_order=sort_order)
    return ft


# ---------------------------------------------------------------------------
# rectifiable_fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRectifiableFields:
    def test_core_fields_without_document_type(self, client_identified, staff_user):
        assert _paths(client_identified, staff_user) == ["pseudonym", "age_cluster", "notes"]

    def test_contact_stage_is_not_rectifiable(self, client_identified, staff_user):
        """E10: der Stufenwechsel hat einen eigenen Workflow (Gates + Audit)."""
        assert "contact_stage" not in _paths(client_identified, staff_user)

    def test_dynamic_slugs_are_offered(self, client_identified, doc_type_client_profile, lead_user):
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        paths = _paths(client_identified, lead_user)
        assert "data_json.wohnsituation" in paths
        assert "data_json.diagnose" in paths

    def test_file_fields_are_excluded(self, facility, client_identified, doc_type_client_profile, lead_user):
        from core.models import DocumentTypeField, FieldTemplate

        ft_file = FieldTemplate.objects.create(
            facility=facility,
            name="Bescheid",
            field_type=FieldTemplate.FieldType.FILE,
        )
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=ft_file, sort_order=9)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        assert "data_json.bescheid" not in _paths(client_identified, lead_user)

    def test_invisible_fields_are_filtered_per_user(
        self, facility, client_identified, doc_type_client_profile, assistant_user
    ):
        """Ein HOCH-Feld darf die Assistenz nicht einmal als Ziel angeboten bekommen."""
        _add_high_field(facility, doc_type_client_profile)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        paths = _paths(client_identified, assistant_user)
        assert "data_json.wohnsituation" in paths
        assert "data_json.suchtdiagnose" not in paths

    def test_labels_come_from_field_names(self, client_identified, doc_type_client_profile, lead_user):
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        labels = dict(rectifiable_fields(client_identified, lead_user))
        assert labels["data_json.wohnsituation"] == "Wohnsituation"
        assert str(labels["pseudonym"]) == "Pseudonym"


# ---------------------------------------------------------------------------
# request_rectification
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRequestRectification:
    def test_unknown_field_path_is_rejected(self, client_identified, staff_user):
        with pytest.raises(ValidationError):
            request_rectification(client_identified, staff_user, "facility_id", "x", "Grund")

    def test_contact_stage_is_rejected(self, client_identified, staff_user):
        with pytest.raises(ValidationError):
            request_rectification(client_identified, staff_user, "contact_stage", "qualified", "Grund")

    def test_invisible_dynamic_field_is_rejected(
        self, facility, client_identified, doc_type_client_profile, assistant_user
    ):
        _add_high_field(facility, doc_type_client_profile)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        with pytest.raises(ValidationError):
            request_rectification(client_identified, assistant_user, "data_json.suchtdiagnose", "X", "Grund")

    def test_old_value_is_captured_from_the_record(self, client_identified, staff_user):
        rr = request_rectification(client_identified, staff_user, "pseudonym", "Neu-01", "Tippfehler")
        assert decode_rectification_value(rr.old_value) == client_identified.pseudonym
        assert decode_rectification_value(rr.new_value) == "Neu-01"

    def test_values_are_encrypted_at_rest(self, client_identified, staff_user):
        rr = request_rectification(client_identified, staff_user, "notes", "Neue Notiz", "Falsch protokolliert")
        for column in ("old_value", "new_value", "reason"):
            assert is_encrypted_value(getattr(rr, column)), f"{column} liegt im Klartext"
        with connection.cursor() as cur:
            cur.execute(
                "SELECT old_value::text, new_value::text, reason::text FROM core_rectificationrequest WHERE id = %s",
                [str(rr.pk)],
            )
            row = cur.fetchone()
        blob = " ".join(row)
        assert "Neue Notiz" not in blob
        assert "Falsch protokolliert" not in blob
        assert blob.count("gAAAA") == 3, f"Fernet-Marker fehlen: {blob}"

    def test_second_open_request_for_same_field_is_a_validation_error(self, client_identified, staff_user):
        request_rectification(client_identified, staff_user, "pseudonym", "Neu-01", "Grund")
        with pytest.raises(ValidationError):
            request_rectification(client_identified, staff_user, "pseudonym", "Neu-02", "Grund")

    @pytest.mark.parametrize(
        ("field_path", "value"),
        [("age_cluster", "quatsch"), ("pseudonym", "   "), ("pseudonym", "P" * 101)],
    )
    def test_out_of_range_value_is_refused_at_request_time(self, client_identified, staff_user, field_path, value):
        """Der Wertebereich haengt am SERVICE — ``update_client`` ruft kein ``full_clean()``.

        Das Antragsformular erfasst ``new_value`` als schlichtes ``CharField``
        ohne Choices/Validatoren; ``_validate_new_value`` ist damit die einzige
        Instanz, die einen unmoeglichen ``age_cluster``, ein leeres Pseudonym
        oder einen zu langen Wert abfaengt — sonst faellt der Fehler erst beim
        Save der GENEHMIGUNG als DataError/500 bei der pruefenden Person auf.
        """
        with pytest.raises(ValidationError):
            request_rectification(client_identified, staff_user, field_path, value, "Grund")
        assert not RectificationRequest.objects.filter(target_client=client_identified).exists()

    def test_audit_entry_without_values(self, client_identified, staff_user):
        rr = request_rectification(client_identified, staff_user, "notes", "Neue Notiz", "Begruendungstext")
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.RECTIFICATION_REQUESTED,
            target_id=str(rr.pk),
        ).latest("timestamp")
        assert entry.detail["field_path"] == "notes"
        assert "Neue Notiz" not in str(entry.detail)
        assert "Begruendungstext" not in str(entry.detail)
        assert client_identified.pseudonym not in str(entry.detail)


# ---------------------------------------------------------------------------
# approve_rectification — Guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApproveGuards:
    def test_closed_request_cannot_be_approved_twice(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        approve_rectification(rr, lead_user)
        with pytest.raises(ValidationError):
            approve_rectification(rr, lead_user)

    def test_self_review_is_refused(self, client_identified, lead_user):
        rr = request_rectification(client_identified, lead_user, "notes", "A", "Grund")
        with pytest.raises(ValidationError):
            approve_rectification(rr, lead_user)

    def test_staff_may_not_approve(self, client_identified, staff_user, facility):
        from core.models import User

        other_staff = User.objects.create_user(username="zweitkraft", role=User.Role.STAFF, facility=facility)
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        with pytest.raises(ValidationError):
            approve_rectification(rr, other_staff)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING

    @pytest.mark.parametrize("field,value", [("is_deleted", True), ("is_active", False)])
    def test_revoked_client_cannot_be_rectified(self, client_identified, staff_user, lead_user, field, value):
        """Papierkorb/Anonymisierung zwischen Antrag und Pruefung (Refs #1771).

        Ein genehmigter Antrag schriebe sonst frischen Klartext in einen bereits
        redigierten Datensatz — die Anonymisierung waere rueckwirkend entwertet.
        """
        rr = request_rectification(client_identified, staff_user, "notes", "Neuer Text", "Grund")
        setattr(client_identified, field, value)
        client_identified.save(update_fields=[field])

        with pytest.raises(ValidationError):
            approve_rectification(rr, lead_user)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        client_identified.refresh_from_db()
        assert client_identified.notes != "Neuer Text"

    def test_path_revalidation_uses_the_reviewers_own_view(
        self, facility, client_identified, doc_type_client_profile, staff_user
    ):
        """Sichtbarkeits-/Konfigurationsdifferenz zwischen Antrag und Pruefung.

        Der Feldpfad wird beim Antrag validiert — zwischen Antrag und
        Genehmigung kann sich die Konfiguration aber aendern. Hier faellt die
        Feldzuordnung am Dokumentationstyp weg; danach ist ``data_json.anmerkung``
        fuer den pruefenden Blick kein berichtigungsfaehiges Ziel mehr und die
        Genehmigung muss kontrolliert scheitern, statt einen verwaisten Slug ins
        ``data_json`` zu schreiben. Genau dafuer ruft der Service
        ``rectifiable_fields(client, reviewer)`` erneut auf — mit dem
        **Reviewer**, nicht mit dem Antragsteller.
        """
        from core.models import DocumentTypeField, FieldTemplate, User

        ft = FieldTemplate.objects.create(facility=facility, name="Anmerkung", field_type=FieldTemplate.FieldType.TEXT)
        DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=ft, sort_order=8)
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])

        rr = request_rectification(client_identified, staff_user, "data_json.anmerkung", "Neu", "Grund")
        DocumentTypeField.objects.filter(document_type=doc_type_client_profile, field_template=ft).delete()

        reviewer = User.objects.create_user(username="pruefleitung", role=User.Role.LEAD, facility=facility)
        with pytest.raises(ValidationError):
            approve_rectification(rr, reviewer)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING

    def test_raised_sensitivity_does_not_block_a_lead_reviewer(
        self, facility, client_identified, doc_type_client_profile, lead_user
    ):
        """Gegenprobe: die Re-Validierung darf nicht falsch-positiv blocken.

        Wird der Dokumentationstyp zwischen Antrag und Pruefung auf HOCH
        gehoben, bleibt das Feld fuer Leitung/Admin sichtbar
        (``ROLE_MAX_SENSITIVITY``) — die Genehmigung muss durchlaufen. Ohne
        diesen Test koennte der Guard versehentlich gegen die
        Antragsteller-Sicht oder pauschal gegen NORMAL pruefen.
        """
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        rr = request_rectification(client_identified, lead_user, "data_json.wohnsituation", "Neu", "Grund")
        make_inherited_fields_explicit(doc_type_client_profile)
        doc_type_client_profile.sensitivity = DocumentType.Sensitivity.HIGH
        doc_type_client_profile.save(update_fields=["sensitivity"])

        from core.models import User

        reviewer = User.objects.create_user(username="zweitleitung", role=User.Role.LEAD, facility=facility)
        approve_rectification(rr, reviewer)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.APPROVED

    def test_conflict_guard_when_value_changed_meanwhile(self, client_identified, staff_user, lead_user):
        from core.services.client import update_client

        rr = request_rectification(client_identified, staff_user, "notes", "Antragswert", "Grund")
        update_client(client_identified, lead_user, notes="Jemand anderes war schneller")

        with pytest.raises(ValidationError):
            approve_rectification(rr, lead_user)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        client_identified.refresh_from_db()
        assert client_identified.notes == "Jemand anderes war schneller"

    def test_conflict_guard_compares_plaintext_not_tokens(
        self, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        """Regressionswaechter: verschluesselte Werte sind frisch gesalzen.

        ``diagnose`` ist ``is_encrypted=True``. Ein byte-weiser Vergleich der
        Fernet-Tokens wuerde IMMER Ungleichheit melden und jede Genehmigung
        eines verschluesselten Feldes unmoeglich machen.
        """
        client_identified.document_type = doc_type_client_profile
        client_identified.data_json = {"diagnose": "Bestand"}
        client_identified.save()

        rr = request_rectification(client_identified, staff_user, "data_json.diagnose", "Korrigiert", "Grund")
        approve_rectification(rr, lead_user)

        client_identified.refresh_from_db()
        from core.services.file_vault import safe_decrypt

        assert safe_decrypt(client_identified.data_json["diagnose"]) == "Korrigiert"

    def test_client_row_is_locked_before_the_guards(
        self, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        """TOCTOU: Widerruf- und Konflikt-Guard muessen auf einer GESPERRTEN Zeile liegen.

        Beide Guards lesen den Client (``is_deleted``/``is_active`` bzw. den
        aktuellen Klartextwert). Ohne ``select_for_update`` beruhten sie auf
        einem stalen Snapshot: zwischen diesem Lesen und dem Lock in
        ``update_client`` kann eine parallele Transaktion die Person in den
        Papierkorb legen, anonymisieren oder den Wert aendern — die Genehmigung
        liefe trotzdem durch. Geprueft wird die Query-Struktur, weil sich das
        Rennen im Ein-Verbindungs-Testlauf nicht nachstellen laesst.
        """
        from django.test.utils import CaptureQueriesContext

        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])
        rr = request_rectification(client_identified, staff_user, "notes", "Neu", "Grund")

        with CaptureQueriesContext(connection) as ctx:
            approve_rectification(rr, lead_user)

        client_reads = [q["sql"] for q in ctx.captured_queries if 'FROM "core_client"' in q["sql"]]
        assert client_reads, "Kein Lesezugriff auf die Client-Zeile aufgezeichnet"
        assert "FOR UPDATE" in client_reads[0], (
            f"Erster Client-Zugriff der Genehmigung ist ungesperrt (TOCTOU):\n{client_reads[0]}"
        )
        guard_reads = [sql for sql in client_reads if "core_documenttype" in sql]
        assert guard_reads, "Guard-Lesezugriff (select_related('document_type')) nicht gefunden"
        for sql in guard_reads:
            # ``of=("self",)``: ``document_type`` ist ``null=True``, ein nacktes
            # ``FOR UPDATE`` scheiterte an der nullable Seite des OUTER JOIN.
            assert 'FOR UPDATE OF "core_client"' in sql, sql

    def test_pseudonym_collision_becomes_validation_error(self, facility, client_identified, staff_user, lead_user):
        Client.objects.create(facility=facility, pseudonym="Belegt-01", created_by=staff_user)
        rr = request_rectification(client_identified, staff_user, "pseudonym", "Belegt-01", "Grund")
        with pytest.raises(ValidationError):
            approve_rectification(rr, lead_user)
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING


# ---------------------------------------------------------------------------
# approve/reject — Ende zu Ende
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApproveEndToEnd:
    def test_core_field_is_applied(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "pseudonym", "Richtig-01", "Tippfehler")
        approve_rectification(rr, lead_user, review_note="Nachweis lag vor")

        client_identified.refresh_from_db()
        assert client_identified.pseudonym == "Richtig-01"
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.APPROVED
        assert rr.reviewed_by == lead_user
        assert rr.reviewed_at is not None
        assert decode_rectification_text(rr.review_note) == "Nachweis lag vor"

    def test_data_json_field_is_applied_and_audited(
        self, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        client_identified.document_type = doc_type_client_profile
        client_identified.data_json = {"wohnsituation": "Wohnungslos"}
        client_identified.save()

        rr = request_rectification(
            client_identified,
            staff_user,
            "data_json.wohnsituation",
            "Betreutes Wohnen",
            "Umzug belegt",
        )
        approve_rectification(rr, lead_user)

        client_identified.refresh_from_db()
        assert client_identified.data_json["wohnsituation"] == "Betreutes Wohnen"

        actions = list(
            AuditLog.objects.filter(target_id__in=[str(rr.pk), str(client_identified.pk)]).values_list(
                "action", flat=True
            )
        )
        assert AuditLog.Action.RECTIFICATION_REQUESTED in actions
        assert AuditLog.Action.RECTIFICATION_APPROVED in actions
        assert AuditLog.Action.CLIENT_UPDATE in actions

        approved = AuditLog.objects.filter(
            action=AuditLog.Action.RECTIFICATION_APPROVED,
            target_id=str(rr.pk),
        ).latest("timestamp")
        assert "Betreutes Wohnen" not in str(approved.detail)
        assert "Wohnungslos" not in str(approved.detail)

    def test_other_dynamic_values_stay_encrypted_and_intact(
        self, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        client_identified.document_type = doc_type_client_profile
        client_identified.data_json = {"wohnsituation": "Wohnungslos", "diagnose": "Bestand"}
        client_identified.save()

        rr = request_rectification(client_identified, staff_user, "data_json.wohnsituation", "Neu", "Grund")
        approve_rectification(rr, lead_user)

        client_identified.refresh_from_db()
        assert is_encrypted_value(client_identified.data_json["diagnose"])
        from core.services.file_vault import safe_decrypt

        assert safe_decrypt(client_identified.data_json["diagnose"]) == "Bestand"

    def test_typed_values_survive_the_round_trip(self, facility, client_identified, staff_user, lead_user):
        """JSON-Stufe vor der Verschluesselung: eine Mehrfachauswahl bleibt Liste."""
        from core.models import DocumentTypeField, FieldTemplate

        dt = DocumentType.objects.create(
            facility=facility,
            entity_type=DocumentType.EntityType.CLIENT,
            name="Bedarfe",
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Bedarf",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[
                {"slug": "wohnen", "label": "Wohnen"},
                {"slug": "arbeit", "label": "Arbeit"},
            ],
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        client_identified.document_type = dt
        client_identified.data_json = {"bedarf": ["wohnen"]}
        client_identified.save()

        rr = request_rectification(client_identified, staff_user, "data_json.bedarf", ["wohnen", "arbeit"], "Ergaenzt")
        assert decode_rectification_value(rr.new_value) == ["wohnen", "arbeit"]
        approve_rectification(rr, lead_user)

        client_identified.refresh_from_db()
        assert client_identified.data_json["bedarf"] == ["wohnen", "arbeit"]

    @pytest.mark.parametrize(
        ("name", "field_type_attr", "value", "expected_iso"),
        [
            ("Erstkontakt", "DATE", date(2026, 1, 15), "2026-01-15"),
            ("Beginn", "TIME", time(9, 30), "09:30:00"),
        ],
    )
    def test_date_and_time_values_round_trip_as_iso(
        self,
        facility,
        client_identified,
        doc_type_client_profile,
        staff_user,
        lead_user,
        name,
        field_type_attr,
        value,
        expected_iso,
    ):
        """DATE/TIME kommen als ``date``/``time``-Objekt aus dem Formular.

        ``json.dumps`` kann beide nicht serialisieren — ohne die
        ISO-Normalisierung (SSOT ``_serialize_json_value``, Refs #1073) reisst
        schon der ANTRAG mit ``TypeError`` (500), und der spaetere
        Konflikt-Guard verglich einen ``date`` mit dem ISO-String aus
        ``data_json``.
        """
        from core.models import FieldTemplate

        ft = _add_typed_field(
            facility,
            doc_type_client_profile,
            name,
            getattr(FieldTemplate.FieldType, field_type_attr),
        )
        client_identified.document_type = doc_type_client_profile
        client_identified.save(update_fields=["document_type", "data_json"])

        rr = request_rectification(client_identified, staff_user, f"data_json.{ft.slug}", value, "Korrigiert")
        assert decode_rectification_value(rr.new_value) == expected_iso

        approve_rectification(rr, lead_user)
        client_identified.refresh_from_db()
        assert client_identified.data_json[ft.slug] == expected_iso

    def test_date_conflict_guard_matches_the_stored_iso_value(
        self, facility, client_identified, doc_type_client_profile, staff_user, lead_user
    ):
        """Gegenprobe: der alte Wert eines belegten DATE-Slugs liegt als ISO-String vor.

        Wuerde nur ``new_value`` normalisiert, waere ``old_value`` typgleich —
        der Klartext-Konflikt-Guard verglich dann ``date(...)`` mit ``"..."``
        und blockte jede Genehmigung eines Datumsfelds.
        """
        from core.models import FieldTemplate

        ft = _add_typed_field(facility, doc_type_client_profile, "Erstkontakt", FieldTemplate.FieldType.DATE)
        client_identified.document_type = doc_type_client_profile
        client_identified.data_json = {ft.slug: "2026-01-01"}
        client_identified.save()

        rr = request_rectification(client_identified, staff_user, f"data_json.{ft.slug}", date(2026, 2, 2), "Grund")
        assert decode_rectification_value(rr.old_value) == "2026-01-01"
        approve_rectification(rr, lead_user)

        client_identified.refresh_from_db()
        assert client_identified.data_json[ft.slug] == "2026-02-02"

    def test_approving_frees_the_partial_unique_slot(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "notes", "Erste Korrektur", "Grund")
        approve_rectification(rr, lead_user)
        again = request_rectification(client_identified, staff_user, "notes", "Zweite Korrektur", "Grund")
        assert again.status == RectificationRequest.Status.PENDING


@pytest.mark.django_db
class TestReject:
    def test_reject_sets_status_and_audits(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        reject_rectification(rr, lead_user, "Wahrnehmungsfrage, kein Faktenfehler")

        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.REJECTED
        assert rr.reviewed_by == lead_user
        assert decode_rectification_text(rr.review_note) == "Wahrnehmungsfrage, kein Faktenfehler"
        client_identified.refresh_from_db()
        assert client_identified.notes != "A"

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.RECTIFICATION_REJECTED,
            target_id=str(rr.pk),
        ).latest("timestamp")
        assert "Wahrnehmungsfrage" not in str(entry.detail)

    def test_review_note_is_encrypted_at_rest(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        reject_rectification(rr, lead_user, "Vertraulicher Pruefvermerk")
        rr.refresh_from_db()
        assert is_encrypted_value(rr.review_note)
        with connection.cursor() as cur:
            cur.execute("SELECT review_note::text FROM core_rectificationrequest WHERE id = %s", [str(rr.pk)])
            (stored,) = cur.fetchone()
        assert "Vertraulicher Pruefvermerk" not in stored
        assert "gAAAA" in stored

    def test_self_review_rejection_is_refused(self, client_identified, lead_user):
        rr = request_rectification(client_identified, lead_user, "notes", "A", "Grund")
        with pytest.raises(ValidationError):
            reject_rectification(rr, lead_user, "Vermerk")

    def test_staff_may_not_reject(self, facility, client_identified, staff_user):
        from core.models import User

        other_staff = User.objects.create_user(username="zweitkraft2", role=User.Role.STAFF, facility=facility)
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        with pytest.raises(ValidationError):
            reject_rectification(rr, other_staff, "Vermerk")

    def test_closed_request_cannot_be_rejected(self, client_identified, staff_user, lead_user):
        rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
        reject_rectification(rr, lead_user, "Vermerk")
        with pytest.raises(ValidationError):
            reject_rectification(rr, lead_user, "Nochmal")

    @pytest.mark.parametrize(("field", "value"), [("is_deleted", True), ("is_active", False)])
    def test_revoked_client_request_cannot_be_rejected(self, client_identified, staff_user, lead_user, field, value):
        """S5-Restluecke (Refs #1060): die Anonymisierung laesst offene Antraege
        im Status PENDING und leert ihre Wertspalten — eine Ablehnung schriebe
        danach einen FRISCHEN verschluesselten Pruefvermerk auf die redigierte
        Zeile und entwertete die ADR-041-Zusage. Widerruf-Guard analog approve."""
        rr = request_rectification(client_identified, staff_user, "notes", "Neu", "Grund")
        setattr(client_identified, field, value)
        client_identified.save(update_fields=[field])

        with pytest.raises(ValidationError):
            reject_rectification(rr, lead_user, "Frischer Vermerk")
        rr.refresh_from_db()
        assert rr.status == RectificationRequest.Status.PENDING
        assert rr.review_note is None


@pytest.mark.django_db
def test_decode_helpers_survive_corrupt_tokens(client_identified, staff_user):
    """``safe_decrypt``-Fallback darf nicht als ``json.loads``-Crash durchschlagen."""
    rr = request_rectification(client_identified, staff_user, "notes", "A", "Grund")
    rr.new_value = {"__encrypted__": True, "value": "gAAAAABkaputt"}
    assert isinstance(decode_rectification_value(rr.new_value), str)
    assert json.dumps(decode_rectification_value(rr.old_value)) is not None
