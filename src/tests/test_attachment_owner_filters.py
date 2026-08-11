"""Owner-Filter ``?case=``/``?event=`` auf ``AttachmentListView`` (Konzept #1801
§6.5, Refs #1813 T4).

``?client=`` existierte bereits (Filter über alle drei Owner-Wege). Analog
dazu: ``?case=`` (``Q(case_id=v) | Q(event__case_id=v)`` — ein Fall-Anhang
hängt entweder direkt am Fall ODER an einem seiner Ereignisse) und
``?event=`` (``Q(event_id=v)``), exakt nach dem bestehenden
``_filter_by_uuid``-Muster: fail-closed, eine unbrauchbare UUID liefert eine
leere Ergebnismenge statt eines 500ers oder der ungefilterten Gesamtliste.

Der Assistenz-Ausschluss (``case__isnull=True``, ADR-039 §12) läuft VOR
diesen neuen Filtern — ein ``?case=`` darf für die Assistenz keinen
Nebeneingang in die Fall-Dokumentation öffnen, auch nicht für einen Fall der
eigenen Einrichtung.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.file_vault import store_encrypted_file

pytestmark = pytest.mark.django_db

LIST_URL_NAME = "core:attachment_list"

PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _file_template(facility, name):
    return FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE)


def _doc_type(facility, name, entity_type, file_field):
    dt = DocumentType.objects.create(facility=facility, name=name, entity_type=entity_type)
    DocumentTypeField.objects.create(document_type=dt, field_template=file_field, sort_order=0)
    return dt


def _attach(facility, owner, ft, user, filename):
    upload = SimpleUploadedFile(filename, PDF_HEADER + filename.encode(), content_type="application/pdf")
    attachment = store_encrypted_file(facility, upload, ft, owner, user)
    owner.data_json = dict(owner.data_json or {})
    owner.data_json[ft.slug] = {"__files__": True, "entries": [{"id": str(attachment.pk), "sort": 0}]}
    owner.save(update_fields=["data_json"])
    return attachment


@pytest.fixture
def case_with_direct_and_event_attachment(facility, staff_user, client_identified, case_open):
    """Ein Fall mit einer fall-eigenen UND einer über sein Ereignis hängenden Datei."""
    ft_case = _file_template(facility, "Fall-Datei")
    ft_event = _file_template(facility, "Ereignis-in-Fall-Datei")
    dt_case = _doc_type(facility, "Falltyp mit Datei", DocumentType.EntityType.CASE, ft_case)
    dt_event = _doc_type(facility, "Ereignistyp mit Datei (im Fall)", DocumentType.EntityType.EVENT, ft_event)

    case_open.document_type = dt_case
    case_open.save(update_fields=["document_type"])
    direct = _attach(facility, case_open, ft_case, staff_user, "fall_direkt.pdf")

    event_in_case = Event.objects.create(
        facility=facility,
        client=client_identified,
        case=case_open,
        document_type=dt_event,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    via_event = _attach(facility, event_in_case, ft_event, staff_user, "fall_via_ereignis.pdf")
    return case_open, direct, event_in_case, via_event


@pytest.fixture
def unrelated_event_attachment(facility, staff_user, client_identified):
    """Ein Ereignis (mit Datei) ohne Fallbezug — Kontrastfixture."""
    ft = _file_template(facility, "Unabhängige Ereignis-Datei")
    dt = _doc_type(facility, "Unabhängiger Ereignistyp", DocumentType.EntityType.EVENT, ft)
    event = Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=staff_user,
    )
    attachment = _attach(facility, event, ft, staff_user, "unabhaengig.pdf")
    return event, attachment


class TestCaseOwnerFilter:
    def test_case_filter_matches_direct_and_event_owned_files(
        self, client, staff_user, case_with_direct_and_event_attachment
    ):
        case, _direct, _event, _via_event = case_with_direct_and_event_attachment
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME), {"case": str(case.pk)}).content.decode()
        assert "fall_direkt.pdf" in content
        assert "fall_via_ereignis.pdf" in content

    def test_case_filter_excludes_unrelated_files(
        self, client, staff_user, case_with_direct_and_event_attachment, unrelated_event_attachment
    ):
        case, *_ = case_with_direct_and_event_attachment
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME), {"case": str(case.pk)}).content.decode()
        assert "unabhaengig.pdf" not in content

    def test_non_uuid_case_filter_returns_empty(self, client, staff_user, case_with_direct_and_event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"case": "keine-uuid"})
        assert response.status_code == 200
        assert response.context["attachments"] == []

    def test_foreign_facility_case_filter_returns_empty(
        self, client, staff_user, facility, foreign_case, foreign_case_attachment
    ):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"case": str(foreign_case.pk)})
        assert response.status_code == 200
        assert response.context["attachments"] == []

    def test_assistant_case_filter_returns_empty(self, client, assistant_user, case_with_direct_and_event_attachment):
        """ADR-039 §12: Fälle sind für die Assistenz unsichtbar — ein
        ``?case=`` liefert für sie die LEERE Menge, nicht nur die fall-eigene
        Zeile gefiltert.

        Auch die event-eigene Zeile (``fall_via_ereignis.pdf``) darf nicht
        erscheinen: die Datei selbst ist der Assistenz zwar zugänglich, aber
        eine Treffer-Antwort auf den Fall-Schlüssel beantwortet ihr die sonst
        gezielt verborgene Zuordnungsfrage Datei↔Fall (Orakel über
        ``event__case_id``; Security-Review-Befund zur #1813-Integration).
        """
        case, *_ = case_with_direct_and_event_attachment
        client.force_login(assistant_user)
        content = client.get(reverse(LIST_URL_NAME), {"case": str(case.pk)}).content.decode()
        assert "fall_direkt.pdf" not in content
        assert "fall_via_ereignis.pdf" not in content

    def test_selected_case_is_in_context_for_htmx_reload(
        self, client, staff_user, case_with_direct_and_event_attachment
    ):
        case, *_ = case_with_direct_and_event_attachment
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"case": str(case.pk)})
        assert response.context["selected_case"] == str(case.pk)


class TestEventOwnerFilter:
    def test_event_filter_matches_only_that_event(
        self, client, staff_user, case_with_direct_and_event_attachment, unrelated_event_attachment
    ):
        _case, _direct, event_in_case, _via_event = case_with_direct_and_event_attachment
        client.force_login(staff_user)
        content = client.get(reverse(LIST_URL_NAME), {"event": str(event_in_case.pk)}).content.decode()
        assert "fall_via_ereignis.pdf" in content
        assert "unabhaengig.pdf" not in content
        assert "fall_direkt.pdf" not in content

    def test_non_uuid_event_filter_returns_empty(self, client, staff_user, unrelated_event_attachment):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"event": "keine-uuid"})
        assert response.status_code == 200
        assert response.context["attachments"] == []

    def test_foreign_facility_event_filter_returns_empty(
        self, client, staff_user, facility, foreign_event, foreign_attachment
    ):
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"event": str(foreign_event.pk)})
        assert response.status_code == 200
        assert response.context["attachments"] == []

    def test_selected_event_is_in_context_for_htmx_reload(self, client, staff_user, unrelated_event_attachment):
        event, _attachment = unrelated_event_attachment
        client.force_login(staff_user)
        response = client.get(reverse(LIST_URL_NAME), {"event": str(event.pk)})
        assert response.context["selected_event"] == str(event.pk)
