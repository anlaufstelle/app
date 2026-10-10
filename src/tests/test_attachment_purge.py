"""Ersatzloses (physisches) Loeschen einer Anhang-Versionskette — Refs #1874.

Der Ticket-Titel („heute nur Ersetzen moeglich") ist am Code widerlegt: das
**Entfernen** gibt es seit #622 an allen drei Owner-Formularen
(``dynamic_fields.html`` -> ``attachment-remove.js`` -> ``_apply_remove`` ->
``soft_delete_attachment_chain``). Offen war zweierlei:

1. Das Entfernen ist **Soft-Delete** — ``qs.update(deleted_at=...)``; der
   Orphan-Sweep verschont die Datei ausdruecklich, die verschluesselte ``.enc``
   blieb bis zur Owner-Loeschung liegen.
2. Die zentrale Datei-Uebersicht hatte **gar keine** Loeschaktion (nur
   Download), und es gab keine Delete-Route.

Dieser Modul deckt den neuen physischen Pfad ab: Service (Dateien + Zeilen +
Marker), Legal-Hold-Blocker, Rollen-/Sichtbarkeits-Gate der Route,
datenminimiertes Audit und die Auswirkung auf die Art.-15-Auskunft.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditLog,
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    LegalHold,
)
from core.models.attachment import Attachment
from core.services.attachments import purge_attachment_chain
from core.services.file_vault import (
    commit_staged_upload,
    get_attachment_path,
    prepare_encrypted_upload,
    store_encrypted_file,
)

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)

DELETE_URL_NAME = "core:attachment_delete"
LIST_URL_NAME = "core:attachment_list"


def _upload(name):
    return SimpleUploadedFile(name, PDF_HEADER + name.encode(), content_type="application/pdf")


def _file_template(facility, name, *, sensitivity=""):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=sensitivity,
    )


def _doc_type(facility, name, entity_type, *, sensitivity=DocumentType.Sensitivity.NORMAL, file_field=None):
    dt = DocumentType.objects.create(
        facility=facility,
        name=name,
        entity_type=entity_type,
        sensitivity=sensitivity,
    )
    if file_field is not None:
        DocumentTypeField.objects.create(document_type=dt, field_template=file_field, sort_order=0)
    return dt


def _attach(facility, owner, ft, user, filename, *, sort=0):
    attachment = store_encrypted_file(facility, _upload(filename), ft, owner, user)
    data_json = dict(owner.data_json or {})
    marker = data_json.get(ft.slug) or {"__files__": True, "entries": []}
    entries = [*marker["entries"], {"id": str(attachment.pk), "sort": sort}]
    data_json[ft.slug] = {"__files__": True, "entries": entries}
    owner.data_json = data_json
    owner.save(update_fields=["data_json"])
    return attachment


def _replace(facility, owner, ft, user, attachment, filename):
    """Zweite Version derselben Kette (Replace-Pfad) + Marker nachziehen."""
    staged = prepare_encrypted_upload(facility, _upload(filename), user, owner=owner)
    new_attachment = commit_staged_upload(owner, ft, staged, user, supersedes=attachment)
    data_json = dict(owner.data_json or {})
    entries = [
        {"id": str(new_attachment.pk), "sort": entry["sort"]} if entry["id"] == str(attachment.pk) else entry
        for entry in data_json[ft.slug]["entries"]
    ]
    data_json[ft.slug] = {"__files__": True, "entries": entries}
    owner.data_json = data_json
    owner.save(update_fields=["data_json"])
    return new_attachment


@pytest.fixture
def event_chain(facility, staff_user, client_identified):
    """Ereignis-eigener Anhang mit ZWEI Versionen (Kette) + Marker."""
    ft = _file_template(facility, "Ereignis-Datei")
    dt = _doc_type(facility, "Ereignistyp mit Datei", DocumentType.EntityType.EVENT, file_field=ft)
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    old = _attach(facility, event, ft, staff_user, "alt.pdf")
    head = _replace(facility, event, ft, staff_user, old, "neu.pdf")
    return event, head, old, ft


# ---------------------------------------------------------------------------
# Service — Dateien, Zeilen, Marker
# ---------------------------------------------------------------------------


class TestPurgeRemovesFilesRowsAndMarker:
    def test_all_versions_of_the_chain_leave_disk_and_db(self, event_chain, django_capture_on_commit_callbacks):
        event, head, old, _ft = event_chain
        paths = [get_attachment_path(head), get_attachment_path(old)]
        assert all(path.exists() for path in paths)

        # Refs #2045 (Cross-Review-Runde 4): Der ``.enc``-Unlink laeuft seit
        # der Rollback-Haertung ueber ``transaction.on_commit`` — unter der
        # Test-Transaktion committet nichts, die Callbacks muessen also
        # ausdruecklich ausgefuehrt werden.
        with django_capture_on_commit_callbacks(execute=True):
            purged = purge_attachment_chain(event, head.entry_id)

        assert purged == 2
        assert not any(path.exists() for path in paths)
        assert not Attachment.objects.filter(pk__in=[head.pk, old.pk]).exists()

    def test_rollback_keeps_files_and_rows_together(self, event_chain):
        """Rollback-Konsistenz (Cross-Review-Runde 4).

        Rollt eine aeussere Transaktion nach dem Purge zurueck, muessen Zeilen
        UND Dateien zurueckkehren. Vorher lag der ``.enc``-Unlink in der
        Transaktion und war nicht rollback-bar: die Zeilen kamen wieder, die
        Dateien nicht — die Kette zeigte danach dauerhaft ins Leere und der
        Download antwortete 404.
        """
        from django.db import transaction

        event, head, old, _ft = event_chain
        paths = [get_attachment_path(head), get_attachment_path(old)]

        with pytest.raises(RuntimeError), transaction.atomic():
            purge_attachment_chain(event, head.entry_id)
            raise RuntimeError("Abbruch nach dem Purge")

        assert all(path.exists() for path in paths), "Dateien wurden trotz Rollback entfernt"
        assert Attachment.objects.filter(entry_id=head.entry_id).count() == 2

    def test_data_json_marker_loses_the_entry(self, event_chain):
        """Invariante I-M1 (ADR-039 §7): physischer Loeschpfad raeumt den Marker mit."""
        event, head, _old, ft = event_chain
        purge_attachment_chain(event, head.entry_id)
        event.refresh_from_db()
        assert ft.slug not in event.data_json

    def test_other_chain_of_the_same_field_survives(self, facility, staff_user, event_chain):
        event, head, _old, ft = event_chain
        other = _attach(facility, event, ft, staff_user, "zweite.pdf", sort=1)

        purge_attachment_chain(event, head.entry_id)

        event.refresh_from_db()
        assert get_attachment_path(other).exists()
        assert Attachment.objects.filter(pk=other.pk).exists()
        assert event.data_json[ft.slug]["entries"] == [{"id": str(other.pk), "sort": 1}]

    def test_soft_deleted_chain_is_purged_too(self, event_chain, django_capture_on_commit_callbacks):
        """Der Soft-Delete-Bestand ist genau das Problem — er muss erfassbar sein."""
        event, head, old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())

        with django_capture_on_commit_callbacks(execute=True):
            assert purge_attachment_chain(event, head.entry_id) == 2
        assert not get_attachment_path(old).exists()

    def test_unknown_entry_id_is_a_no_op(self, event_chain):
        event, _head, _old, _ft = event_chain
        import uuid

        assert purge_attachment_chain(event, uuid.uuid4()) == 0

    def test_marker_cleanup_reaches_the_chain_behind_text_and_foreign_markers(
        self, facility, staff_user, client_identified
    ):
        """Die Marker-Bereinigung darf nicht am ersten fremden Slug enden.

        Reale Owner tragen Text-Slugs und weitere FILE-Felder VOR dem Marker
        der vernichteten Kette (``data_json`` in Feld-Reihenfolge; jsonb
        sortiert Schluessel nach Laenge). Endet die Schleife von
        ``_drop_purged_marker_entries`` an einem Nicht-Marker-Slug oder an
        einem vom Purge unberuehrten Marker, bleibt der Marker der physisch
        geloeschten Zeilen stehen — Invariante I-M1 (ADR-039 §7) verletzt,
        der Stale-Eintrag leckt in Facility-Export und Konflikt-Diff.
        """
        ft_other = _file_template(facility, "Beiblatt")
        ft_target = _file_template(facility, "Zielbeleg neu")
        dt = _doc_type(facility, "Typ mit zwei Dateifeldern", DocumentType.EntityType.EVENT, file_field=ft_other)
        DocumentTypeField.objects.create(document_type=dt, field_template=ft_target, sort_order=1)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"notiz": "bleibt stehen"},
            created_by=staff_user,
        )
        other = _attach(facility, event, ft_other, staff_user, "unbeteiligt.pdf")
        target = _attach(facility, event, ft_target, staff_user, "ziel.pdf")

        # Reihenfolge-Vorbedingung des Szenarios: Text-Slug und unbeteiligter
        # Marker stehen VOR dem Ziel-Marker — in Insertion-Order UND nach dem
        # jsonb-Reload (Schluessel-Laenge 5 < 8 < 13).
        event = Event.objects.get(pk=event.pk)
        assert list(event.data_json) == ["notiz", ft_other.slug, ft_target.slug]

        purge_attachment_chain(event, target.entry_id)

        event.refresh_from_db()
        assert ft_target.slug not in event.data_json
        assert event.data_json["notiz"] == "bleibt stehen"
        assert event.data_json[ft_other.slug]["entries"] == [{"id": str(other.pk), "sort": 0}]
        assert get_attachment_path(other).exists()


# ---------------------------------------------------------------------------
# Legal Hold — Blocker fuer den physischen Pfad
# ---------------------------------------------------------------------------


class TestLegalHoldBlocksPurge:
    """Ein Purge unter aktivem Legal Hold zerstoert beweissicherungspflichtige
    Daten (Spoliation). ``get_active_hold_target_ids`` kannte bisher nur
    Event/Client/Activity — die Anhang-Domaene fragte gar nicht."""

    def test_hold_on_the_event_blocks(self, facility, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(PermissionDenied):
            purge_attachment_chain(event, head.entry_id)
        assert get_attachment_path(head).exists()

    def test_hold_on_the_person_of_the_event_blocks(self, facility, lead_user, event_chain, client_identified):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=client_identified.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(PermissionDenied):
            purge_attachment_chain(event, head.entry_id)

    def test_dismissed_hold_does_not_block(self, facility, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="aufgehoben",
            created_by=lead_user,
            dismissed_at=timezone.now(),
            dismissed_by=lead_user,
        )
        assert purge_attachment_chain(event, head.entry_id) == 2


# ---------------------------------------------------------------------------
# Route — Rollen-Gate, Bestaetigung, Audit
# ---------------------------------------------------------------------------


class TestPurgeRoute:
    def test_staff_may_not_open_the_confirmation(self, client, staff_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(staff_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 403

    def test_staff_may_not_post(self, client, staff_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(staff_user)
        response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 403
        assert get_attachment_path(head).exists()

    def test_lead_sees_the_confirmation_with_filename_and_version_count(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 200
        content = response.content.decode()
        assert "neu.pdf" in content
        assert response.context["version_count"] == 2

    def test_lead_purge_removes_the_file_and_redirects_to_the_list(
        self, client, lead_user, event_chain, django_capture_on_commit_callbacks
    ):
        _event, head, old, _ft = event_chain
        path = get_attachment_path(head)
        client.force_login(lead_user)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 302
        assert response.url == reverse(LIST_URL_NAME)
        assert not path.exists()
        assert not get_attachment_path(old).exists()
        assert not Attachment.objects.filter(entry_id=head.entry_id).exists()

    def test_audit_row_carries_no_plaintext_filename(self, client, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))

        entry = AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_DELETE).get()
        assert entry.target_type == "Attachment"
        # ADR-039 §5: target_id ist die OWNER-PK — die Anhangzeile existiert
        # nach dem Purge nicht mehr und waere eine tote Referenz.
        assert entry.target_id == str(event.pk)
        assert entry.detail["owner_type"] == "event"
        assert entry.detail["versions"] == 2
        assert entry.detail["filename_ext"] == "pdf"
        assert entry.detail["filename_hash"]
        assert "neu.pdf" not in str(entry.detail)

    def test_hold_blocks_the_route_and_keeps_the_file(self, client, facility, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(lead_user)
        response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}), follow=True)
        assert get_attachment_path(head).exists()
        assert Attachment.objects.filter(entry_id=head.entry_id).count() == 2
        assert any("Legal Hold" in str(m) for m in response.context["messages"])

    def test_restriction_blocks_the_service_and_keeps_the_file(self, event_chain, staff_user, client_identified):
        """Art.-18-Sperrvermerk blockiert den Purge wie ein Legal Hold — aber
        aus anderem Grund (Refs #2045).

        Der Guard sitzt seit Cross-Review-Runde 2 IM Service unter dessen
        eigener Sperre, nicht ungesperrt in der View davor; deshalb wird hier
        der Service direkt geprueft.
        """
        from core.models import Client as ClientModel
        from core.services.client.restriction import restrict_processing

        event, head, _old, _ft = event_chain
        restrict_processing(client_identified, staff_user, reason=ClientModel.RestrictionReason.ACCURACY_DISPUTED)
        with pytest.raises(PermissionDenied):
            purge_attachment_chain(event, head.entry_id)
        assert get_attachment_path(head).exists()
        assert Attachment.objects.filter(entry_id=head.entry_id).count() == 2

    def test_restriction_blocks_the_route_and_keeps_the_file(
        self, client, lead_user, staff_user, event_chain, client_identified
    ):
        """Der vollstaendige Weg: Anhang vorhanden, Purge-POST abgesetzt,
        Refusal und Fortbestand von Datei und Kette."""
        from core.models import Client as ClientModel
        from core.services.client.restriction import restrict_processing

        _event, head, _old, _ft = event_chain
        restrict_processing(client_identified, staff_user, reason=ClientModel.RestrictionReason.ACCURACY_DISPUTED)
        client.force_login(lead_user)
        response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}), follow=True)
        assert get_attachment_path(head).exists()
        assert Attachment.objects.filter(entry_id=head.entry_id).count() == 2
        assert any("Art. 18" in str(m) for m in response.context["messages"])

    def test_restriction_is_checked_under_a_client_row_lock(self, event_chain):
        """Der Guard laeuft unter der Client-ZEILENSPERRE und vor dem Loeschen
        (Re-Review 2).

        Die Vorfassung nutzte das ungesperrte Anzeigepraedikat: zwischen
        Pruefung und ``delete()`` konnte die Einschraenkung gesetzt werden und
        der Purge lief trotzdem durch.

        Geprueft wird das am ausgefuehrten SQL, nicht am Verhalten: die
        Sperr-Eigenschaft ist einzelthreadig nicht beobachtbar (eine Transaktion
        blockiert sich nicht selbst), aber das ``FOR UPDATE`` auf
        ``core_client`` VOR dem ``DELETE`` auf den Anhaengen ist es. Dieselbe
        Technik wie in ``test_restriction_lock_order``.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        event, head, _old, _ft = event_chain
        with CaptureQueriesContext(connection) as captured:
            purge_attachment_chain(event, head.entry_id)

        client_lock_at = None
        delete_at = None
        for index, query in enumerate(captured.captured_queries):
            sql = query["sql"].upper()
            if client_lock_at is None and "CORE_CLIENT" in sql and "FOR UPDATE" in sql:
                client_lock_at = index
            if delete_at is None and sql.startswith('DELETE FROM "CORE_ATTACHMENT"'):
                delete_at = index

        assert client_lock_at is not None, "Client-Zeile wurde beim Purge nicht gesperrt"
        assert delete_at is not None, "kein DELETE auf den Anhaengen gefunden"
        assert client_lock_at < delete_at, "Sperre faellt erst nach dem Loeschen"

    def test_confirmation_page_shows_the_restriction_blocker(
        self, client, lead_user, staff_user, event_chain, client_identified
    ):
        from core.models import Client as ClientModel
        from core.services.client.restriction import restrict_processing

        _event, head, _old, _ft = event_chain
        restrict_processing(client_identified, staff_user, reason=ClientModel.RestrictionReason.ACCURACY_DISPUTED)
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 200
        assert response.context["restriction_blocked"] is True

    def test_confirmation_page_shows_the_hold_blocker(self, client, facility, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 200
        assert response.context["hold_blocked"] is True

    def test_foreign_facility_attachment_is_404(self, client, lead_user, second_facility, foreign_staff):
        ft = _file_template(second_facility, "Fremde Datei")
        dt = _doc_type(second_facility, "Fremdtyp", DocumentType.EntityType.CLIENT, file_field=ft)
        foreign_client = Client.objects.create(
            facility=second_facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Fremd-01",
            document_type=dt,
            created_by=foreign_staff,
        )
        attachment = _attach(second_facility, foreign_client, ft, foreign_staff, "fremd.pdf")
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": attachment.pk}))
        assert response.status_code == 404

    def test_owner_without_document_type_is_404(self, client, lead_user, facility, staff_user, client_identified):
        """Fail-closed wie Stufe 2 des Download-Gates (ADR-039 §12)."""
        ft = _file_template(facility, "Personen-Datei")
        attachment = _attach(facility, client_identified, ft, staff_user, "ohne-typ.pdf")
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": attachment.pk}))
        assert response.status_code == 404

    def test_case_owned_attachment_is_purgeable_by_lead(
        self, client, lead_user, facility, staff_user, client_identified, django_capture_on_commit_callbacks
    ):
        ft = _file_template(facility, "Fall-Datei")
        dt = _doc_type(facility, "Falltyp mit Datei", DocumentType.EntityType.CASE, file_field=ft)
        case = Case.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            title="Fall mit Datei",
            data_json={},
            created_by=staff_user,
        )
        attachment = _attach(facility, case, ft, staff_user, "fall.pdf")
        path = get_attachment_path(attachment)
        client.force_login(lead_user)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": attachment.pk}))
        assert response.status_code == 302
        assert not path.exists()


class TestPurgeActionInOverview:
    def test_lead_sees_the_delete_action(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) in response.content.decode()

    def test_staff_does_not_see_the_delete_action(self, client, staff_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) not in response.content.decode()


# ---------------------------------------------------------------------------
# Art.-15-Auskunft
# ---------------------------------------------------------------------------


class TestArticle15AfterPurge:
    """Vor dem Purge weist die Auskunft die Datei aus (``removed_at`` bei
    Soft-Delete). Danach gibt es keine gespeicherte Datei mehr — die Auskunft
    darf keine behaupten, muss den Vorgang aber im Zugriffsprotokoll zeigen."""

    def test_file_disappears_but_deletion_shows_up_in_the_audit_trail(
        self, client, lead_user, facility, event_chain, client_identified
    ):
        _event, head, _old, _ft = event_chain
        from core.services.client.export import export_client_data

        before = export_client_data(client_identified, facility, lead_user)
        assert before["events"][0]["attachments"]

        client.force_login(lead_user)
        client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))

        after = export_client_data(client_identified, facility, lead_user)
        assert "attachments" not in after["events"][0]
        assert any(
            entry["category"] == "Anhang" and entry["action"] == str(AuditLog.Action.ATTACHMENT_DELETE.label)
            for entry in after["audit_trail"]
        )


# ---------------------------------------------------------------------------
# Bestaetigungsseite — Benennung des Datensatzes
# ---------------------------------------------------------------------------


class TestConfirmationNamesTheRecord:
    """Der Verzicht auf den Vier-Augen-Antragsweg haengt an dieser Seite.

    Traegt sie nur das generische Owner-Label („Ereignis"/„Person"/„Fall"),
    sind zwei Bestaetigungsseiten fuer zwei gleichnamige Dateien an zwei
    verschiedenen Personen bis auf die UUID in der Adresszeile Zeichen fuer
    Zeichen identisch — und die Aktion ist unwiderruflich.
    """

    def test_pseudonym_document_type_and_upload_time_are_rendered(self, client, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        content = response.content.decode()

        assert response.context["client_pseudonym"] == "Test-ID-01"
        assert response.context["doc_type_name"] == event.document_type.name
        assert "Test-ID-01" in content
        assert event.document_type.name in content
        assert str(head.created_at.year) in content

    def test_two_identically_named_files_get_distinguishable_pages(
        self, client, facility, lead_user, staff_user, event_chain
    ):
        """Der Fehlklick-Fall: zwei Personen, je eine ``Bescheid.pdf``."""
        _event, head, _old, ft = event_chain
        other_person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Test-ID-99",
            created_by=staff_user,
        )
        dt = _doc_type(facility, "Zweiter Ereignistyp", DocumentType.EntityType.EVENT, file_field=ft)
        other_event = Event.objects.create(
            facility=facility,
            client=other_person,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        other = _attach(facility, other_event, ft, staff_user, "neu.pdf")

        client.force_login(lead_user)
        first = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk})).content.decode()
        second = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": other.pk})).content.decode()

        assert "Test-ID-01" in first
        assert "Test-ID-99" not in first
        assert "Test-ID-99" in second
        assert "Test-ID-01" not in second

    def test_warning_qualifies_the_promise_against_media_backups(self, client, lead_user, event_chain):
        """Die Zusage „keine Wiederherstellung" gilt nur fuer die Anwendung.

        Wo ein Medien-Backup laeuft, lebt die ``.enc`` bis zum Ablauf der
        Backup-Aufbewahrung weiter (``docs/disaster-recovery.md`` §2/§3) — eine
        unbedingte Zusage waere gegenueber der betroffenen Person falsch.
        """
        _event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert 'data-testid="attachment-purge-backup-hint"' in response.content.decode()


# ---------------------------------------------------------------------------
# Stufe-1-Filter der Route
# ---------------------------------------------------------------------------


class TestPurgeResolutionPins:
    def test_prior_version_is_not_a_purge_target(self, client, lead_user, event_chain):
        """Nur die Ketten-SPITZE ist Loeschgegenstand — eine Vorversion faellt
        mit der Kette, ist aber kein eigener Einstieg (``is_current``)."""
        _event, _head, old, _ft = event_chain
        client.force_login(lead_user)
        assert client.get(reverse(DELETE_URL_NAME, kwargs={"pk": old.pk})).status_code == 404
        assert client.post(reverse(DELETE_URL_NAME, kwargs={"pk": old.pk})).status_code == 404
        assert get_attachment_path(old).exists()


# ---------------------------------------------------------------------------
# Entfernte (soft-geloeschte) Ketten — der Anlassfall des Tickets
# ---------------------------------------------------------------------------


class TestRemovedChainsStayPurgeable:
    """Wer die irrtuemlich hochgeladene Datei zuerst ueber „Entfernen"
    aus der Dokumentation nimmt, darf sich damit nicht den Loeschweg
    verbauen — sonst bleibt die ``.enc`` bis zur Anonymisierung liegen,
    also genau der Zustand, den das Ticket beseitigen wollte."""

    def test_confirmation_opens_for_a_removed_chain(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(lead_user)
        response = client.get(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 200
        assert response.context["removed_at"] is not None

    def test_removed_chain_is_purgeable(self, client, lead_user, event_chain, django_capture_on_commit_callbacks):
        _event, head, old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(lead_user)
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        assert response.status_code == 302
        assert not get_attachment_path(head).exists()
        assert not get_attachment_path(old).exists()

    def test_overview_lists_removed_chains_for_lead(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(lead_user)

        active = client.get(reverse(LIST_URL_NAME))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) not in active.content.decode()

        removed = client.get(reverse(LIST_URL_NAME), {"removed": "1"})
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) in removed.content.decode()
        assert removed.context["show_removed"] is True
        # Kein Download-Einstieg: der Download-View antwortet auf entfernte
        # Zeilen mit 404 (Stufe 3, Refs #1375) — ein Link waere tot.
        assert removed.context["attachments"][0]["download_url"] == ""

    def test_removed_view_stays_closed_for_staff(self, client, staff_user, event_chain):
        _event, head, _old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"removed": "1"})
        assert response.context["show_removed"] is False
        assert response.context["attachments"] == []


# ---------------------------------------------------------------------------
# Einstieg am Owner-Objekt (Akzeptanzkriterium #1874)
# ---------------------------------------------------------------------------


class TestPurgeEntryPointAtTheOwner:
    def test_event_detail_offers_the_delete_link_to_lead(self, client, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) in response.content.decode()

    def test_event_detail_hides_the_delete_link_from_staff(self, client, staff_user, event_chain):
        event, head, _old, _ft = event_chain
        client.force_login(staff_user)
        response = client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}) not in response.content.decode()

    def test_client_detail_offers_the_delete_link_to_lead(self, client, lead_user, facility, staff_user):
        ft = _file_template(facility, "Personen-Datei")
        dt = _doc_type(facility, "Personentyp mit Datei", DocumentType.EntityType.CLIENT, file_field=ft)
        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Test-ID-77",
            document_type=dt,
            created_by=staff_user,
        )
        attachment = _attach(facility, person, ft, staff_user, "personen.pdf")
        client.force_login(lead_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": person.pk}))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": attachment.pk}) in response.content.decode()


# ---------------------------------------------------------------------------
# Gesperrter Versuch hinterlaesst eine Spur
# ---------------------------------------------------------------------------


class TestBlockedAttemptIsAudited:
    """Ein Loeschversuch auf beweissicherungspflichtige Daten ist genau der
    Vorgang, den man im Protokoll sehen will. Der Audit-Write des Purge steht
    IN der Transaktion, die der Blocker zurueckrollt — die Spur muss deshalb
    danach und ausserhalb geschrieben werden."""

    def test_hold_blocked_post_writes_a_legal_hold_entry(self, client, facility, lead_user, event_chain):
        event, head, _old, _ft = event_chain
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(lead_user)
        client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))

        assert not AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_DELETE).exists()
        entry = AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).get()
        assert entry.target_type == "Attachment"
        assert entry.target_id == str(event.pk)
        assert entry.detail["category"] == "legal_hold_blocked_purge"
        assert "neu.pdf" not in str(entry.detail)


# ---------------------------------------------------------------------------
# Stufe-1-Facility-Scope
# ---------------------------------------------------------------------------


class TestPurgeStaysInsideTheFacility:
    """Der erste Filter der Route (``for_facility``) ist die Einrichtungsgrenze.

    Ohne Pin liesse er sich durch ein nacktes ``Attachment.objects`` ersetzen,
    ohne dass ein Test faellt — bei einem physischen Loeschpfad waere das die
    teuerste denkbare stille Regression.
    """

    def test_foreign_attachment_is_invisible_to_both_methods(self, client, lead_user, foreign_attachment):
        client.force_login(lead_user)
        url = reverse(DELETE_URL_NAME, kwargs={"pk": foreign_attachment.pk})
        assert client.get(url).status_code == 404
        assert client.post(url).status_code == 404
        assert Attachment.objects.filter(pk=foreign_attachment.pk).exists()


# ---------------------------------------------------------------------------
# Doppelklick-Rennen
# ---------------------------------------------------------------------------


class TestChainIsLockedBeforeTheAuditWrite:
    """``_resolve`` laeuft ausserhalb der Transaktion.

    Ohne Zeilensperre koennten zwei fast gleichzeitige POSTs beide eine volle
    Kette sehen und beide einen ATTACHMENT_DELETE-Eintrag schreiben — der
    zweite mit einer Versionszahl, die niemanden mehr betrifft. Das Protokoll
    ist unveraenderlich, eine falsche Zeile bleibt drin.
    """

    def test_post_locks_the_chain_rows(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        with CaptureQueriesContext(connection) as captured:
            client.post(reverse(DELETE_URL_NAME, kwargs={"pk": head.pk}))
        locking = [q["sql"] for q in captured.captured_queries if "FOR UPDATE" in q["sql"].upper()]
        assert any("core_attachment" in sql for sql in locking), "Kette wird nicht gesperrt"

    def test_second_post_neither_purges_nor_logs_again(self, client, lead_user, event_chain):
        """Der zweite Aufruf faellt schon am Stufe-1-Lookup aus — kein zweiter Eintrag."""
        _event, head, _old, _ft = event_chain
        client.force_login(lead_user)
        url = reverse(DELETE_URL_NAME, kwargs={"pk": head.pk})
        assert client.post(url).status_code == 302
        assert client.post(url).status_code == 404
        assert AuditLog.objects.filter(action=AuditLog.Action.ATTACHMENT_DELETE).count() == 1


# ---------------------------------------------------------------------------
# Entfernt-Ansicht der Uebersicht
# ---------------------------------------------------------------------------


class TestRemovedOverviewIsReachable:
    def test_lead_gets_a_toggle_into_the_removed_view(self, client, lead_user, event_chain):
        client.force_login(lead_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert 'data-testid="attachment-removed-toggle"' in response.content.decode()

    def test_staff_gets_no_toggle(self, client, staff_user, event_chain):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME))
        assert 'data-testid="attachment-removed-toggle"' not in response.content.decode()

    def test_removed_row_carries_no_download_anchor(self, client, lead_user, event_chain):
        _event, head, _old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(lead_user)
        content = client.get(reverse(LIST_URL_NAME), {"removed": "1"}).content.decode()
        assert 'href=""' not in content

    def test_the_view_survives_the_first_filter_touch(self, client, lead_user, event_chain):
        """Refs #1874: Der Filtergriff darf die Ansicht nicht auf die aktiven Dateien kippen.

        Ohne ein mitgesendetes ``removed`` beantwortet die View den
        HTMX-Nachladevorgang der Filterleiste als Anfrage nach den aktiven
        Ketten — Tabelle und Ueberschrift zeigten dann Verschiedenes.
        """
        _event, head, _old, _ft = event_chain
        Attachment.objects.filter(entry_id=head.entry_id).update(deleted_at=timezone.now())
        client.force_login(lead_user)

        seite = client.get(reverse(LIST_URL_NAME), {"removed": "1"})
        assert seite.context["show_removed"] is True
        # Das Feld, das die Filterleiste beim Nachladen mitschickt.
        assert 'name="removed"' in seite.content.decode()

        # Genau der Request, den das Dokumentationstyp-Dropdown absetzt.
        nachgeladen = client.get(
            reverse(LIST_URL_NAME),
            {"document_type": "", "client": "", "case": "", "event": "", "removed": "1"},
            headers={"hx-request": "true"},
        )
        assert nachgeladen.context["show_removed"] is True

    def test_every_filter_control_forwards_the_removed_flag(self, client, lead_user, event_chain):
        """Jede ``hx-include``-Liste der Filterleiste muss ``removed`` fuehren."""
        client.force_login(lead_user)
        content = client.get(reverse(LIST_URL_NAME), {"removed": "1"}).content.decode()
        # Nur die Filterleiste dieser Seite; das globale Suchfeld der Navigation
        # traegt ebenfalls ein hx-include, zielt aber auf /search/.
        leiste = content.split("data-filter-persist", 1)[1].split("</div>", 1)[0]
        includes = [z for z in leiste.splitlines() if "hx-include=" in z]
        assert includes, "Filterleiste ohne hx-include gefunden"
        assert all("[name='removed']" in z for z in includes), includes


# ---------------------------------------------------------------------------
# Einstieg am Fall-Objekt
# ---------------------------------------------------------------------------


class TestPurgeEntryPointAtTheCase:
    def test_case_detail_offers_the_delete_link_to_lead(self, client, lead_user, facility, staff_user, case_open):
        ft = _file_template(facility, "Fall-Datei")
        dt = _doc_type(facility, "Falltyp mit Datei", DocumentType.EntityType.CASE, file_field=ft)
        case_open.document_type = dt
        case_open.save(update_fields=["document_type", "data_json"])
        attachment = _attach(facility, case_open, ft, staff_user, "fall.pdf")
        client.force_login(lead_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case_open.pk}))
        assert reverse(DELETE_URL_NAME, kwargs={"pk": attachment.pk}) in response.content.decode()


@pytest.mark.django_db(transaction=True)
class TestPurgeInAutocommit:
    """Direktaufruf OHNE aeussere Transaktion (Cross-Review-Runde 4).

    Der Default ``django_db`` wickelt jeden Test in eine Transaktion — ein
    Test, der dort „laeuft ohne aeussere Transaktion" behauptet, beweist genau
    das NICHT. Hier laeuft echter Autocommit: ``select_for_update`` scheitert
    dort ohne eigene Klammer, und ``transaction.on_commit`` feuert wirklich
    (kein ``django_capture_on_commit_callbacks`` noetig).

    Bewusst langsamer — die Tabellen werden zwischen den Tests geleert statt
    zurueckgerollt.
    """

    def test_purge_brings_its_own_transaction_and_unlinks_after_commit(self, event_chain):
        event, head, old, _ft = event_chain
        paths = [get_attachment_path(head), get_attachment_path(old)]
        assert all(path.exists() for path in paths)

        assert purge_attachment_chain(event, head.entry_id) == 2

        assert not Attachment.objects.filter(entry_id=head.entry_id).exists()
        assert not any(path.exists() for path in paths), "on_commit hat den Unlink nicht ausgefuehrt"
