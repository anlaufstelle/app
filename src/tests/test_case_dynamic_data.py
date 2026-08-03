"""Case-Dynamikdaten: ``document_type`` + ``data_json`` (M2 P5, Refs #1058).

P5 gibt dem ``Case``-Aggregat dieselbe dynamische Feldmechanik, die
``Client``/``Event`` schon haben: einen ``DocumentType``-FK
(``entity_type=case``) und ein ``data_json``-JSONField, dessen als
``is_encrypted`` markierte Slugs beim ``save()`` Fernet-verschluesselt
werden (:class:`DynamicDataEncryptionMixin`, P4a).

Abgedeckt (Struktur spiegelt ``test_client_dynamic_data.py``, P4b):

* **Verschluesselung** — der encrypted-Slug liegt als Token in der DB-Zeile
  (Raw-Query, nicht ueber das ORM-Objekt), der normale Slug im Klartext;
  Roundtrip via ``safe_decrypt``.
* **Service-Guard** — Cross-Facility-Typ und ``entity_type=client``/``event``-
  Typ werden mit deutscher ``ValidationError`` abgewiesen (create + update).
* **Whitelist** — ``validate_data_json`` verwirft Slugs, die nicht am Typ
  haengen; der Service muss den *Rueckgabewert* verwenden.
* **Anonymisierung** — ``_redact_cases_and_episodes`` (aufgerufen aus
  ``anonymize_client``) leert ``data_json`` und loest den
  ``document_type``-PROTECT-Verweis.
* **Audit** — ``changed_fields`` nennt ``data_json``/``document_type``, das
  Detail traegt aber keine Werte.
* **Optimistic Locking** — ``update_case`` mit ``expected_updated_at`` bleibt
  mit den neuen Feldern funktionsfaehig (Erfolg + Konfliktfall).
* **PROTECT** — ein von einem Fall referenzierter Typ ist nicht loeschbar.
"""

import json

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import override_settings

from core.models import AuditLog, Case, DocumentType
from core.services.case import create_case, update_case
from core.services.client import anonymize_client
from core.services.file_vault import is_encrypted_value, safe_decrypt


def raw_data_json(case) -> dict:
    """``core_case.data_json`` direkt aus der DB-Zeile (ohne ORM-Cache).

    Der Treiber liefert JSONB als rohen Text zurueck (kein automatisches
    Deserialisieren wie im ORM) — genau das ist hier gewuenscht: die Probe soll
    den *gespeicherten* Inhalt sehen, nicht die vom Modell aufbereitete Sicht.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT data_json FROM core_case WHERE id = %s", [str(case.pk)])
        raw = cur.fetchone()[0]
    return json.loads(raw) if isinstance(raw, str) else raw


@pytest.fixture
def fernet_key():
    """Frischer Fernet-Key fuer die Verschluesselungs-Proben.

    Die Test-Settings erben ``ENCRYPTION_KEY`` aus der Umgebung — die
    Krypto-Tests setzen ihn deshalb explizit (Muster P4a/P4b).
    """
    return Fernet.generate_key().decode()


@pytest.mark.django_db
class TestCaseDataJsonEncryption:
    """``Case.save()`` verschluesselt die als encrypted markierten Slugs."""

    def test_encrypted_slug_is_ciphertext_in_db(
        self, facility, client_identified, doc_type_case_form, staff_user, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P5-Krypto-01",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "Sehr sensibel"},
            )

            row = raw_data_json(case)
            assert is_encrypted_value(row["verlauf"]), "encrypted-Slug liegt im Klartext in der DB"
            assert "Sehr sensibel" not in str(row["verlauf"])
            assert safe_decrypt(row["verlauf"]) == "Sehr sensibel"

    def test_plain_slug_stays_plaintext(self, facility, client_identified, doc_type_case_form, staff_user, fernet_key):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P5-Krypto-02",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden", "verlauf": "Sehr sensibel"},
            )

            assert raw_data_json(case)["anlass"] == "Mietschulden"

    def test_resave_does_not_double_encrypt(
        self, facility, client_identified, doc_type_case_form, staff_user, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = Case.objects.create(
                facility=facility,
                client=client_identified,
                title="P5-Krypto-03",
                created_by=staff_user,
                document_type=doc_type_case_form,
                data_json={"verlauf": "Einmal"},
            )
            case.description = "geaendert"
            case.save()

            assert safe_decrypt(raw_data_json(case)["verlauf"]) == "Einmal"

    def test_create_case_service_encrypts_too(
        self, facility, client_identified, doc_type_case_form, staff_user, fernet_key
    ):
        """Der Service-Pfad haengt am selben ``save()`` — kein zweiter Krypto-Ort."""
        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Krypto-04",
                document_type=doc_type_case_form,
                data_json={"verlauf": "Service-Pfad"},
            )

            assert is_encrypted_value(raw_data_json(case)["verlauf"])


@pytest.mark.django_db
class TestCaseDocumentTypeGuard:
    """Der Service akzeptiert nur Typen der eigenen Einrichtung mit ``entity_type=case``."""

    def test_create_rejects_foreign_facility_type(self, facility, client_identified, foreign_doc_type_case, staff_user):
        with pytest.raises(ValidationError, match="Einrichtung"):
            create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Guard-01",
                document_type=foreign_doc_type_case,
            )

    def test_create_rejects_client_entity_type(self, facility, client_identified, doc_type_client_profile, staff_user):
        assert doc_type_client_profile.entity_type == DocumentType.EntityType.CLIENT
        with pytest.raises(ValidationError, match="Fälle"):
            create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Guard-02",
                document_type=doc_type_client_profile,
            )

    def test_create_rejects_event_entity_type(self, facility, client_identified, doc_type_contact, staff_user):
        assert doc_type_contact.entity_type == DocumentType.EntityType.EVENT
        with pytest.raises(ValidationError, match="Fälle"):
            create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Guard-03",
                document_type=doc_type_contact,
            )

    def test_update_rejects_foreign_facility_type(self, case_open, foreign_doc_type_case, staff_user):
        with pytest.raises(ValidationError, match="Einrichtung"):
            update_case(case_open, staff_user, document_type=foreign_doc_type_case)

    def test_update_rejects_event_entity_type(self, case_open, doc_type_contact, staff_user):
        with pytest.raises(ValidationError, match="Fälle"):
            update_case(case_open, staff_user, document_type=doc_type_contact)

    def test_data_json_without_document_type_is_rejected(self, facility, client_identified, staff_user):
        """Ohne Typ gibt es keine Whitelist — dann darf auch nichts gespeichert werden."""
        with pytest.raises(ValidationError, match="Dokumentationstyp"):
            create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Guard-04",
                data_json={"irgendwas": "Wert"},
            )

    def test_valid_type_is_accepted(self, facility, client_identified, doc_type_case_form, staff_user):
        case = create_case(
            facility,
            staff_user,
            client_identified,
            title="P5-Guard-05",
            document_type=doc_type_case_form,
        )
        assert case.document_type_id == doc_type_case_form.pk

    def test_update_rejects_raw_pk_spelling(self, case_open, doc_type_case_form, staff_user):
        """Haertung (Refs #1058, Review-Punkt 10, analog P4b
        ``test_client_dynamic_data.py::test_update_rejects_raw_pk_spelling``):
        ``update_case`` bleibt bei der strengen Allowlist (nur ``document_type``,
        keine ``*_id``-Schreibweise) — die Allowlist weist die Roh-PK-Schreibweise
        als unbekanntes Feld ab, bevor sie ueberhaupt den Typ-Guard erreicht."""
        with pytest.raises(ValueError, match="document_type_id"):
            update_case(case_open, staff_user, document_type_id=doc_type_case_form.pk)


@pytest.mark.django_db
class TestCaseDataJsonWhitelist:
    """``validate_data_json`` filtert fremde Keys — der Service nutzt den Rueckgabewert."""

    def test_create_drops_unknown_keys(self, facility, client_identified, doc_type_case_form, staff_user):
        case = create_case(
            facility,
            staff_user,
            client_identified,
            title="P5-White-01",
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden", "nicht-am-typ": "Schmuggelware"},
        )

        assert "nicht-am-typ" not in case.data_json
        assert case.data_json["anlass"] == "Mietschulden"
        assert "nicht-am-typ" not in raw_data_json(case)

    def test_update_drops_unknown_keys(self, case_open, doc_type_case_form, staff_user):
        update_case(
            case_open,
            staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden", "nicht-am-typ": "Schmuggelware"},
        )

        case_open.refresh_from_db()
        assert case_open.data_json == {"anlass": "Mietschulden"}

    def test_type_switch_drops_slugs_of_the_old_type(
        self, facility, client_identified, doc_type_case_form, staff_user, fernet_key
    ):
        """E11-Merge-Policy: nach dem Typwechsel bleiben nur Slugs des NEUEN Typs."""
        from core.models import DocumentTypeField, FieldTemplate

        other_type = DocumentType.objects.create(
            facility=facility,
            category=DocumentType.Category.ADMIN,
            entity_type=DocumentType.EntityType.CASE,
            name="Fallformular (anderes)",
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Zielvereinbarung",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=other_type, field_template=ft, sort_order=0)

        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-White-03",
                document_type=doc_type_case_form,
                data_json={"anlass": "Mietschulden"},
            )
            update_case(case, staff_user, document_type=other_type)

        case.refresh_from_db()
        assert case.data_json == {}, "Slugs des alten Typs muessen beim Typwechsel wegfallen"


@pytest.mark.django_db
class TestCaseDynamicDataAnonymization:
    """Anonymisierung ueber ``_redact_cases_and_episodes`` leert Dynamikdaten (E7)."""

    def test_anonymize_wipes_data_json_and_document_type(
        self, facility, client_identified, doc_type_case_form, staff_user, fernet_key
    ):
        with override_settings(ENCRYPTION_KEY=fernet_key):
            case = create_case(
                facility,
                staff_user,
                client_identified,
                title="P5-Anon-01",
                document_type=doc_type_case_form,
                data_json={"anlass": "Bei Familie Musterfrau", "verlauf": "Sensibel"},
            )

            anonymize_client(client_identified, user=staff_user)

        case.refresh_from_db()
        assert case.data_json == {}
        assert case.document_type_id is None
        assert raw_data_json(case) == {}

    def test_anonymized_case_no_longer_blocks_type_deletion(
        self, facility, client_identified, doc_type_case_form, staff_user
    ):
        create_case(
            facility,
            staff_user,
            client_identified,
            title="P5-Anon-02",
            document_type=doc_type_case_form,
        )
        anonymize_client(client_identified, user=staff_user)

        doc_type_case_form.delete()
        assert not DocumentType.objects.filter(pk=doc_type_case_form.pk).exists()


@pytest.mark.django_db
class TestCaseDynamicDataAudit:
    """``update_case`` meldet die Feldnamen, niemals die Werte."""

    def test_changed_fields_contain_dynamic_fields_without_values(self, case_open, doc_type_case_form, staff_user):
        update_case(
            case_open,
            staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Streng geheime Adresse"},
        )

        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CASE_UPDATE,
            target_id=str(case_open.pk),
        ).latest("timestamp")
        assert set(entry.detail["changed_fields"]) == {"document_type", "data_json"}
        assert "Streng geheime Adresse" not in str(entry.detail)


@pytest.mark.django_db
class TestCaseDynamicDataOptimisticLocking:
    """``update_case`` mit ``expected_updated_at`` bleibt mit den neuen Feldern funktionsfaehig."""

    def test_success_with_current_timestamp_and_dynamic_fields(self, case_open, doc_type_case_form, staff_user):
        case_open.refresh_from_db()
        current = case_open.updated_at.isoformat()
        update_case(
            case_open,
            staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "OK"},
            expected_updated_at=current,
        )
        case_open.refresh_from_db()
        assert case_open.data_json == {"anlass": "OK"}
        assert case_open.document_type_id == doc_type_case_form.pk

    def test_conflict_leaves_dynamic_fields_unchanged(self, case_open, doc_type_case_form, staff_user):
        stale = "2000-01-01T00:00:00+00:00"
        with pytest.raises(ValidationError):
            update_case(
                case_open,
                staff_user,
                document_type=doc_type_case_form,
                data_json={"anlass": "Sollte nicht ankommen"},
                expected_updated_at=stale,
            )
        case_open.refresh_from_db()
        assert case_open.data_json == {}
        assert case_open.document_type_id is None


@pytest.mark.django_db
class TestCaseOrphanFieldsDisplay:
    """Refs #1725(a): verwaiste ``data_json``-Keys erscheinen auch im Fall-Detail
    in der eigenen „Nicht mehr zugeordnet"-Sektion (gemeinsames Partial mit dem
    Personen-Detail)."""

    def test_orphan_appears_separately_with_facility_sensitivity(
        self, facility, client_identified, doc_type_case_form, staff_user, lead_user
    ):
        from core.models import FieldTemplate
        from core.services.dynamic_data import build_dynamic_data_context

        orphan_ft = FieldTemplate.objects.create(
            facility=facility,
            name="Ehemals-Fallfeld",
            field_type=FieldTemplate.FieldType.TEXTAREA,
            sensitivity=DocumentType.Sensitivity.HIGH,
            is_encrypted=True,
        )
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Waisen-Fall",
            created_by=staff_user,
            document_type=doc_type_case_form,
            data_json={"anlass": "Mietschulden", orphan_ft.slug: "geheim"},
        )
        # STAFF: verwaistes HIGH-Feld restricted, zugeordnetes Feld sichtbar.
        ctx_staff = build_dynamic_data_context(case.document_type, case.data_json, staff_user)
        assert [f["label"] for f in ctx_staff["dynamic_fields"]] == ["Anlass"]
        assert len(ctx_staff["dynamic_orphan_fields"]) == 1
        orphan = ctx_staff["dynamic_orphan_fields"][0]
        assert orphan["orphaned"] is True
        assert orphan["restricted"] is True
        assert "geheim" not in str(orphan["value"])
        # LEAD sieht den Waisenwert.
        ctx_lead = build_dynamic_data_context(case.document_type, case.data_json, lead_user)
        assert ctx_lead["dynamic_orphan_fields"][0]["restricted"] is False


@pytest.mark.django_db
class TestCaseDocumentTypeProtect:
    """``on_delete=PROTECT``: ein benutzter Typ ist nicht loeschbar (UI-Block: P8)."""

    def test_type_with_case_reference_cannot_be_deleted(
        self, facility, client_identified, doc_type_case_form, staff_user
    ):
        create_case(
            facility,
            staff_user,
            client_identified,
            title="P5-Protect-01",
            document_type=doc_type_case_form,
        )

        with pytest.raises(ProtectedError):
            doc_type_case_form.delete()

        assert DocumentType.objects.filter(pk=doc_type_case_form.pk).exists()
