"""Owner-generische Download-Routen + fuenfstufiges Gate (M24/AP4, Refs #1738).

ADR-039 §12 legt fuer den Anhang-Download fuenf Stufen fest:

1. Owner-Lookup facility-gescopt + ``is_deleted=False`` -> 404,
2. Doc-Type-Sensitivity (``user_can_see_document_type``) -> 404
   (``document_type is None`` ⇒ fail-closed),
3. Anhang haengt an GENAU diesem Owner und ist nicht soft-geloescht -> 404,
4. Feld-Sensitivity (``user_can_see_field``) -> 403,
5. Datei auf Disk -> 404 + ``logger.error``.

Dazu das **Rollen-Gefaelle**: Personen-Downloads sind ASSISTANT_PLUS,
Fall-Downloads STAFF_PLUS (Faelle sind fuer die Assistenz unsichtbar; ein
Assistenz-Download waere eine Gate-Umgehung).
"""

from __future__ import annotations

import logging

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from django_ratelimit.core import get_usage

from core.constants import RATELIMIT_FREQUENT
from core.models import AuditLog, Case, Client, DocumentType, DocumentTypeField, FieldTemplate
from core.services.file_vault import get_attachment_path, store_encrypted_file
from tests._sensitivity_helpers import make_inherited_fields_explicit

pytestmark = pytest.mark.django_db

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _file_template(facility, name, *, sensitivity=""):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=sensitivity,
    )


def _attach(facility, owner, ft, user, filename):
    """Anhang speichern und den Stufe-B-Marker im ``data_json`` setzen."""
    upload = SimpleUploadedFile(filename, PDF_HEADER, content_type="application/pdf")
    attachment = store_encrypted_file(facility, upload, ft, owner, user)
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[ft.slug] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    owner.save(update_fields=["data_json"])
    return attachment


@pytest.fixture
def client_owner(facility, staff_user, doc_type_client_profile):
    """Person mit Personen-Typ + FILE-Feld + einem Anhang."""
    ft = _file_template(facility, "Personen-Scan")
    DocumentTypeField.objects.create(document_type=doc_type_client_profile, field_template=ft, sort_order=9)
    person = Client.objects.create(
        facility=facility,
        pseudonym="Datei-Person",
        contact_stage=Client.ContactStage.IDENTIFIED,
        document_type=doc_type_client_profile,
        created_by=staff_user,
    )
    return person, _attach(facility, person, ft, staff_user, "person.pdf"), ft


@pytest.fixture
def case_owner(facility, staff_user, client_identified, doc_type_case_form):
    """Fall mit Fall-Typ + FILE-Feld + einem Anhang."""
    ft = _file_template(facility, "Fall-Scan")
    DocumentTypeField.objects.create(document_type=doc_type_case_form, field_template=ft, sort_order=9)
    case = Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Fall mit Datei",
        document_type=doc_type_case_form,
        created_by=staff_user,
    )
    return case, _attach(facility, case, ft, staff_user, "fall.pdf"), ft


def _client_url(person, attachment):
    return reverse("core:client_attachment_download", kwargs={"pk": person.pk, "attachment_pk": attachment.pk})


def _case_url(case, attachment):
    return reverse("core:case_attachment_download", kwargs={"pk": case.pk, "attachment_pk": attachment.pk})


class TestClientAttachmentDownload:
    def test_staff_downloads_client_attachment(self, client, staff_user, client_owner):
        person, attachment, _ft = client_owner
        client.force_login(staff_user)
        response = client.get(_client_url(person, attachment))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PDF_HEADER

    def test_assistant_may_download_client_attachment(self, client, assistant_user, client_owner):
        """Rollen-Gefaelle: Personen-Downloads sind ASSISTANT_PLUS."""
        person, attachment, _ft = client_owner
        client.force_login(assistant_user)
        assert client.get(_client_url(person, attachment)).status_code == 200

    def test_download_requires_login(self, client, client_owner):
        person, attachment, _ft = client_owner
        response = client.get(_client_url(person, attachment))
        assert response.status_code == 302
        assert "/login" in response["Location"]

    def test_foreign_facility_client_is_404(self, client, staff_user, foreign_client_attachment, foreign_client):
        """Stufe 1: fremde Einrichtung ist von „existiert nicht" ununterscheidbar."""
        client.force_login(staff_user)
        assert client.get(_client_url(foreign_client, foreign_client_attachment)).status_code == 404

    def test_trashed_client_is_404(self, client, staff_user, client_owner):
        """Stufe 1: Papierkorb-Person liefert keine Dateien mehr aus."""
        person, attachment, _ft = client_owner
        person.is_deleted = True
        person.deleted_at = timezone.now()
        person.save(update_fields=["is_deleted", "deleted_at"])
        client.force_login(staff_user)
        assert client.get(_client_url(person, attachment)).status_code == 404

    def test_client_without_document_type_is_404(self, client, staff_user, client_owner):
        """Stufe 2 fail-closed: ohne Typ gibt es keine Bezugs-Sensitivitaet."""
        person, attachment, _ft = client_owner
        person.document_type = None
        person.save(update_fields=["document_type", "data_json"])
        client.force_login(staff_user)
        assert client.get(_client_url(person, attachment)).status_code == 404

    def test_high_document_type_is_404_for_staff(self, client, staff_user, client_owner, doc_type_client_profile):
        """Stufe 2: 404 statt 403 — die Zusatzfelder-Sektion ist fuer
        Unterberechtigte komplett abwesend, ein 403 waere ein Existenz-Leak."""
        person, attachment, _ft = client_owner
        make_inherited_fields_explicit(doc_type_client_profile)
        doc_type_client_profile.sensitivity = DocumentType.Sensitivity.HIGH
        doc_type_client_profile.save(update_fields=["sensitivity"])
        client.force_login(staff_user)
        assert client.get(_client_url(person, attachment)).status_code == 404

    def test_attachment_of_other_owner_is_404(self, client, staff_user, client_owner, client_identified):
        """Stufe 3: der Anhang muss an GENAU diesem Owner haengen."""
        _person, attachment, _ft = client_owner
        client.force_login(staff_user)
        url = reverse(
            "core:client_attachment_download",
            kwargs={"pk": client_identified.pk, "attachment_pk": attachment.pk},
        )
        assert client.get(url).status_code == 404

    def test_event_attachment_not_reachable_via_client_route(self, client, staff_user, client_owner, authz_attachment):
        """Stufe 3: ein ereignis-eigener Anhang bleibt fuer die Personen-Route unsichtbar."""
        person, _attachment, _ft = client_owner
        client.force_login(staff_user)
        url = reverse(
            "core:client_attachment_download",
            kwargs={"pk": person.pk, "attachment_pk": authz_attachment.pk},
        )
        assert client.get(url).status_code == 404

    def test_soft_deleted_attachment_is_404(self, client, staff_user, client_owner):
        """Stufe 3: ``deleted_at`` schliesst den Direkt-Link (Refs #1375)."""
        person, attachment, _ft = client_owner
        attachment.deleted_at = timezone.now()
        attachment.save(update_fields=["deleted_at"])
        client.force_login(staff_user)
        assert client.get(_client_url(person, attachment)).status_code == 404

    def test_high_field_sensitivity_is_403(self, client, staff_user, client_owner):
        """Stufe 4: Feld-Sensitivity antwortet 403 — dass es Zusatzfelder gibt,
        weiss der Aufrufer an dieser Stelle bereits."""
        person, attachment, ft = client_owner
        ft.sensitivity = DocumentType.Sensitivity.HIGH
        ft.save(update_fields=["sensitivity"])
        client.force_login(staff_user)
        assert client.get(_client_url(person, attachment)).status_code == 403

    def test_missing_file_on_disk_is_404_and_logged(self, client, staff_user, client_owner, caplog):
        """Stufe 5: fehlende ``.enc`` -> 404 statt Verbindungsabbruch im Stream."""
        person, attachment, _ft = client_owner
        get_attachment_path(attachment).unlink()
        client.force_login(staff_user)
        with caplog.at_level(logging.ERROR, logger="core.views.attachments"):
            assert client.get(_client_url(person, attachment)).status_code == 404
        assert any("missing on disk" in record.getMessage() for record in caplog.records)

    def test_audit_detail_carries_owner_keys(self, client, staff_user, client_owner):
        person, attachment, ft = client_owner
        client.force_login(staff_user)
        client.get(_client_url(person, attachment))
        log = AuditLog.objects.filter(action=AuditLog.Action.DOWNLOAD).latest("timestamp")
        assert log.target_type == "Attachment"
        assert log.detail == {"owner_type": "client", "owner_id": str(person.pk), "field": ft.slug}


class TestCaseAttachmentDownload:
    def test_staff_downloads_case_attachment(self, client, staff_user, case_owner):
        case, attachment, _ft = case_owner
        client.force_login(staff_user)
        response = client.get(_case_url(case, attachment))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == PDF_HEADER

    def test_assistant_is_denied(self, client, assistant_user, case_owner):
        """Rollen-Gefaelle (ADR-039 §12): Faelle sind fuer die Assistenz unsichtbar."""
        case, attachment, _ft = case_owner
        client.force_login(assistant_user)
        assert client.get(_case_url(case, attachment)).status_code in (403, 404)

    def test_foreign_facility_case_is_404(self, client, staff_user, foreign_case_attachment, foreign_case):
        client.force_login(staff_user)
        assert client.get(_case_url(foreign_case, foreign_case_attachment)).status_code == 404

    def test_case_without_document_type_is_404(self, client, staff_user, case_owner):
        case, attachment, _ft = case_owner
        case.document_type = None
        case.save(update_fields=["document_type", "data_json"])
        client.force_login(staff_user)
        assert client.get(_case_url(case, attachment)).status_code == 404

    def test_trashed_case_is_404(self, client, staff_user, case_owner):
        """Stufe 1: Papierkorb-Fall liefert keine Dateien mehr aus.

        ``CaseAttachmentDownloadView.get_owner`` scopt auf ``is_deleted=False``
        — genau wie Detail- und Bearbeiten-View. Ohne diesen Pin haette ein
        Wegfall des Filters (der Anhang selbst bleibt ja gueltig) niemanden
        gestoert: der Fall ist aus jeder Liste verschwunden, der Deep-Link
        haette weiter entschluesselt.
        """
        case, attachment, _ft = case_owner
        case.is_deleted = True
        case.deleted_at = timezone.now()
        case.save(update_fields=["is_deleted", "deleted_at"])
        client.force_login(staff_user)
        assert client.get(_case_url(case, attachment)).status_code == 404

    def test_trashed_client_case_attachment_is_404(self, client, staff_user, case_owner, client_identified):
        """Stufe 1 ueber die Person (Refs #1777): der Fall selbst kennt keinen
        Loeschworkflow — die Loeschgrenze verlaeuft ueber ``case.client``. Eine
        Person im Papierkorb macht Fall-Detail/-Bearbeiten 404, die Fall-Datei
        muss demselben Schnitt folgen."""
        case, attachment, _ft = case_owner
        client_identified.is_deleted = True
        client_identified.deleted_at = timezone.now()
        client_identified.save(update_fields=["is_deleted", "deleted_at"])
        client.force_login(staff_user)
        assert client.get(_case_url(case, attachment)).status_code == 404

    def test_anonymized_client_case_attachment_is_404(self, client, staff_user, case_owner, client_identified):
        """Stufe 1 ueber die Person (Refs #1777): anonymisiert (``is_active=False``)
        ohne Papierkorb — der K-Anon-Bridge-Zustand, den ein ``is_deleted``-Gate
        nicht faengt."""
        case, attachment, _ft = case_owner
        client_identified.is_active = False
        client_identified.save(update_fields=["is_active"])
        client.force_login(staff_user)
        assert client.get(_case_url(case, attachment)).status_code == 404

    def test_attachment_of_other_case_is_404(self, client, staff_user, facility, client_identified, case_owner):
        """Stufe 3: Owner-Bindung — der Anhang muss an GENAU diesem Fall haengen."""
        _case, attachment, _ft = case_owner
        other_case = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall ohne Datei",
            document_type=_case.document_type,
            created_by=staff_user,
        )
        client.force_login(staff_user)
        url = reverse(
            "core:case_attachment_download",
            kwargs={"pk": other_case.pk, "attachment_pk": attachment.pk},
        )
        assert client.get(url).status_code == 404

    def test_audit_detail_carries_owner_keys(self, client, staff_user, case_owner):
        case, attachment, ft = case_owner
        client.force_login(staff_user)
        client.get(_case_url(case, attachment))
        log = AuditLog.objects.filter(action=AuditLog.Action.DOWNLOAD).latest("timestamp")
        assert log.detail == {"owner_type": "case", "owner_id": str(case.pk), "field": ft.slug}


class TestEventDownloadAuditDetail:
    def test_event_detail_keeps_event_id_and_gains_owner_keys(self, client, staff_user, sample_event, authz_attachment):
        """Doppel-Verschluesselung des Detail-Schemas (Refs #1738).

        ``owner_type``/``owner_id`` sind das neue, einheitliche Schema
        (ADR-039 §5); ``event_id`` bleibt fuer ereignis-eigene Zeilen zusaetzlich
        stehen, damit die Art.-15-Zuordnung Alt- und Neuzeilen ohne Sonderfall
        matcht.
        """
        client.force_login(staff_user)
        url = reverse(
            "core:attachment_download",
            kwargs={"pk": sample_event.pk, "attachment_pk": authz_attachment.pk},
        )
        client.get(url)
        log = AuditLog.objects.filter(action=AuditLog.Action.DOWNLOAD).latest("timestamp")
        assert log.detail == {
            "owner_type": "event",
            "owner_id": str(sample_event.pk),
            "event_id": str(sample_event.pk),
            "field": authz_attachment.field_template.slug,
        }


class TestSharedRatelimitBucket:
    def test_all_three_routes_share_one_bucket(
        self, client, staff_user, ratelimit_enabled, sample_event, authz_attachment, client_owner, case_owner
    ):
        """S3 (Refs #1084) owner-generisch: EIN Eimer ``attachment_download``.

        Drei Eimer waeren dreifaches Budget fuer denselben teuren Vorgang
        (serverseitige Entschluesselung je Request).

        ``ratelimit_enabled`` (conftest) nagelt das Zaehlfenster fest — sonst
        koennte die abschliessende ``get_usage``-Probe schon in einem anderen
        Fenster stehen als die drei Requests davor und ``count == 0`` lesen
        (Refs #1900).
        """
        person, client_attachment, _cft = client_owner
        case, case_attachment, _kft = case_owner
        client.force_login(staff_user)
        event_url = reverse(
            "core:attachment_download",
            kwargs={"pk": sample_event.pk, "attachment_pk": authz_attachment.pk},
        )
        assert client.get(event_url).status_code == 200
        assert client.get(_client_url(person, client_attachment)).status_code == 200
        assert client.get(_case_url(case, case_attachment)).status_code == 200

        probe = RequestFactory().get("/")
        probe.user = staff_user
        usage = get_usage(
            probe,
            group="attachment_download",
            key="user",
            rate=RATELIMIT_FREQUENT,
            method="GET",
            increment=False,
        )
        assert usage["count"] == 3
