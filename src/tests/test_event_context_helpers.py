"""Refs #804 (C-37): Service-Extraktion EventCreate/UpdateView."""

from __future__ import annotations

import pytest

from core.models import Settings
from core.services.events.context import (
    build_attachment_context,
    resolve_default_document_type,
)


@pytest.mark.django_db
class TestResolveDefaultDocumentType:
    def test_no_settings_returns_none(self, facility):
        # Facility-Fixture liefert noch keine Settings-Row.
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type is None
        assert initial == {}

    def test_settings_without_default_returns_none(self, facility):
        Settings.objects.create(facility=facility)
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type is None
        assert initial == {}

    def test_active_default_returns_pair(self, facility, doc_type_contact):
        Settings.objects.create(facility=facility, default_document_type=doc_type_contact)
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type == doc_type_contact
        assert initial == {"document_type": doc_type_contact.pk}

    def test_inactive_default_drops_back_to_none(self, facility, doc_type_contact):
        doc_type_contact.is_active = False
        doc_type_contact.save(update_fields=["is_active"])
        Settings.objects.create(facility=facility, default_document_type=doc_type_contact)
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type is None
        assert initial == {}

    def test_default_from_other_facility_drops_back_to_none(
        self,
        facility,
        second_facility,
        doc_type_contact,
    ):
        # Default zeigt auf einen DocumentType einer anderen Facility — darf
        # nicht durchgereicht werden (defense-in-depth gegen kaputte Daten
        # in den Settings).
        from core.models import DocumentType

        foreign = DocumentType.objects.create(
            facility=second_facility,
            name="Foreign",
            is_active=True,
        )
        Settings.objects.create(facility=facility, default_document_type=foreign)
        doc_type, initial = resolve_default_document_type(facility)
        assert doc_type is None
        assert initial == {}


@pytest.mark.django_db
class TestBuildAttachmentContext:
    def test_event_with_empty_data_json_is_empty(self, sample_event):
        sample_event.data_json = {}
        sample_event.save(update_fields=["data_json"])
        assert build_attachment_context(sample_event) == {}


@pytest.mark.django_db
class TestComputeEventSearchText:
    """Refs #827 (C-60): Plain-text-Suchindex aus data_json."""

    def _make_doc_type(self, facility, fields):
        from core.models import DocumentType, DocumentTypeField, FieldTemplate

        dt = DocumentType.objects.create(facility=facility, name="DT-Test")
        for slug, kwargs in fields:
            ft = FieldTemplate.objects.create(facility=facility, name=slug.title(), **kwargs)
            DocumentTypeField.objects.create(document_type=dt, field_template=ft)
        return dt

    def test_includes_default_sensitivity_unencrypted_text(self, facility):
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("notiz", {})])
        text = compute_event_search_text({"notiz": "Hallo Welt"}, dt)
        assert text == "Hallo Welt"

    def test_excludes_encrypted_field(self, facility):
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("geheim", {"is_encrypted": True})])
        text = compute_event_search_text({"geheim": "secret"}, dt)
        assert text == ""

    def test_excludes_elevated_sensitivity_field(self, facility):
        from core.models import DocumentType
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(
            facility,
            [("hohe", {"sensitivity": DocumentType.Sensitivity.HIGH, "is_encrypted": True})],
        )
        text = compute_event_search_text({"hohe": "geheim"}, dt)
        assert text == ""

    def test_excludes_unknown_sensitivity_field(self, facility):
        """Refs #1991 (Fix-Runde 1): unbekannte Werte fail-closed statt indexiert.

        Der Filter fragte nach Mengen-Zugehoerigkeit
        (``sensitivity in {elevated, high}``) und nicht nach dem Rang — ein
        Wert ausserhalb von ``DocumentType.Sensitivity`` fiel durch beide
        Zweige in den Klartext-Index, obwohl ``user_can_see_field`` ihn seit
        #1991 fuer JEDE Rolle sperrt. ``choices`` sind Formular-Validierung,
        keine DB-Beschraenkung — der Fall ist ueber eine Migration, ein Paket
        oder eine korrupte Zeile erreichbar.
        """
        from core.models import FieldTemplate
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("sonderfeld", {})])
        FieldTemplate.objects.filter(slug="sonderfeld").update(sensitivity="kritisch")
        text = compute_event_search_text({"sonderfeld": "geheim"}, dt)
        assert text == ""

    def test_includes_explicit_normal_and_inherited_sensitivity(self, facility):
        """Verhaltensneutralitaet: die beiden indexierten Faelle bleiben drin.

        ``""`` heisst „erbt vom Dokumentationstyp"; der Typ-Schutz greift auf
        Ereignis-Ebene (``event_search_queryset`` filtert
        ``document_type__sensitivity__in``), deshalb ist der geerbte Wert
        weiterhin indexierbar.
        """
        from core.models import DocumentType
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(
            facility,
            [
                ("geerbt", {"sensitivity": ""}),
                ("ausdruecklich", {"sensitivity": DocumentType.Sensitivity.NORMAL}),
            ],
        )
        text = compute_event_search_text({"geerbt": "AAA", "ausdruecklich": "BBB"}, dt)
        assert "AAA" in text and "BBB" in text

    def test_excludes_elevated_but_unencrypted_field(self, facility):
        """Gegenprobe zu ``test_excludes_elevated_sensitivity_field``: ELEVATED
        allein reicht zum Ausschluss, auch ohne ``is_encrypted``."""
        from core.models import DocumentType
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("erhoeht", {"sensitivity": DocumentType.Sensitivity.ELEVATED})])
        text = compute_event_search_text({"erhoeht": "vertraulich"}, dt)
        assert text == ""

    def test_skips_file_marker_dicts(self, facility):
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("anhang", {})])
        text = compute_event_search_text({"anhang": {"__file__": True, "name": "doc.pdf"}}, dt)
        assert text == ""

    def test_concatenates_list_values(self, facility):
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("themen", {})])
        text = compute_event_search_text({"themen": ["beratung", "wohnen"]}, dt)
        assert "beratung" in text and "wohnen" in text

    def test_unknown_slug_is_dropped(self, facility):
        from core.services.events.fields import compute_event_search_text

        dt = self._make_doc_type(facility, [("notiz", {})])
        text = compute_event_search_text({"unbekannt": "X", "notiz": "OK"}, dt)
        assert text == "OK"

    def test_event_save_signal_keeps_search_text_in_sync(
        self,
        facility,
        client_identified,
        doc_type_contact,
        staff_user,
    ):
        """pre_save-Signal in core/signals/event_search.py."""
        from django.utils import timezone

        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "abcdef"},
            created_by=staff_user,
        )
        assert event.search_text == "abcdef"

        event.data_json = {"notiz": "umstellung"}
        event.save()
        event.refresh_from_db()
        assert event.search_text == "umstellung"

    def test_event_without_file_markers_is_empty(self, sample_event):
        sample_event.data_json = {"note": "Hallo"}
        sample_event.save(update_fields=["data_json"])
        assert build_attachment_context(sample_event) == {}
