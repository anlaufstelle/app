"""Tests for feed preview enrichment, badge color filters, and activity template tags."""

from datetime import datetime, time

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.activity import log_activity
from core.services.feed import build_feed_items, enrich_events_with_preview
from core.templatetags.core_tags import activity_target_url, doctype_badge_classes, verb_badge_classes


@pytest.mark.django_db
class TestEnrichEventsWithPreview:
    """Tests for enrich_events_with_preview()."""

    def test_basic_preview_fields(self, facility, staff_user, client_identified, doc_type_contact):
        # doc_type_contact has Dauer (number, sort=0) and Notiz (textarea, sort=1)
        # Event with dauer=15 → preview should show Dauer:15, but NOT Notiz (textarea)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 15, "notiz": "text"},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert hasattr(event, "preview_fields")
        assert len(event.preview_fields) == 1  # Only Dauer (Notiz is textarea)
        assert event.preview_fields[0]["label"] == "Dauer"
        assert event.preview_fields[0]["value"] == "15"

    def test_max_three_fields(self, facility, staff_user, client_identified):
        # Create doc type with 5 non-textarea fields
        dt = DocumentType.objects.create(facility=facility, name="Multi", category="contact")
        for i in range(5):
            ft = FieldTemplate.objects.create(facility=facility, name=f"Feld{i}", field_type="text")
            DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=i)
        data = {f"feld{i}": f"val{i}" for i in range(5)}
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json=data,
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert len(event.preview_fields) == 3

    def test_textarea_skipped(self, facility, staff_user, client_identified, doc_type_contact):
        # doc_type_contact: Dauer=number, Notiz=textarea
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10, "notiz": "long text"},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        labels = [pf["label"] for pf in event.preview_fields]
        assert "Notiz" not in labels
        assert "Dauer" in labels

    def test_encrypted_field_hidden_for_assistant(self, facility, assistant_user, client_identified):
        # ELEVATED doc type with encrypted field
        dt = DocumentType.objects.create(
            facility=facility,
            name="Sensitive",
            category="service",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type="text",
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"geheim": "secret"},
            created_by=assistant_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, assistant_user)
        assert event.preview_fields == []

    def test_encrypted_field_visible_for_admin(self, facility, admin_user, client_identified):
        dt = DocumentType.objects.create(
            facility=facility,
            name="SensitiveAdmin",
            category="service",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type="text",
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"geheim": "visible"},
            created_by=admin_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, admin_user)
        assert len(event.preview_fields) == 1
        assert event.preview_fields[0]["label"] == "Geheim"

    def test_boolean_formatting(self, facility, staff_user, client_identified):
        dt = DocumentType.objects.create(facility=facility, name="BoolTest", category="contact")
        ft = FieldTemplate.objects.create(facility=facility, name="Aktiv", field_type="boolean")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"aktiv": True},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "Ja"

    def test_select_label_resolved(self, facility, staff_user, client_identified):
        dt = DocumentType.objects.create(facility=facility, name="SelectTest", category="contact")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Art",
            field_type="select",
            options_json=[
                {"slug": "a", "label": "Alpha", "is_active": True},
                {"slug": "b", "label": "Beta", "is_active": True},
            ],
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"art": "b"},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "Beta"

    def test_multi_select_labels(self, facility, staff_user, client_identified):
        dt = DocumentType.objects.create(facility=facility, name="MultiTest", category="contact")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Leistungen",
            field_type="multi_select",
            options_json=[
                {"slug": "essen", "label": "Essen", "is_active": True},
                {"slug": "kleidung", "label": "Kleidung", "is_active": True},
            ],
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"leistungen": ["essen", "kleidung"]},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "Essen, Kleidung"

    def test_empty_data_json(self, facility, staff_user, client_identified, doc_type_contact):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields == []

    def test_non_event_items_unchanged(self, facility, staff_user, sample_workitem):
        # Activity/workitem items should pass through without preview_fields
        feed_items = [{"type": "workitem", "occurred_at": timezone.now(), "object": sample_workitem}]
        enrich_events_with_preview(feed_items, staff_user)
        assert not hasattr(sample_workitem, "preview_fields")

    def test_batch_efficiency(self, facility, staff_user, client_identified, doc_type_contact):
        # Two events with same doc type — should batch into 1 DTF query
        e1 = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        e2 = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 20},
            created_by=staff_user,
        )
        feed_items = [
            {"type": "event", "occurred_at": e1.occurred_at, "object": e1},
            {"type": "event", "occurred_at": e2.occurred_at, "object": e2},
        ]
        # Just verify it works — both get enriched
        enrich_events_with_preview(feed_items, staff_user)
        assert len(e1.preview_fields) == 1
        assert len(e2.preview_fields) == 1

    def test_empty_feed_items(self, staff_user):
        # Should not error on empty list
        enrich_events_with_preview([], staff_user)


class TestDoctypeBadgeClasses:
    """Tests for doctype_badge_classes template filter."""

    def test_known_color(self):
        assert doctype_badge_classes("indigo") == "bg-indigo-100 text-indigo-800"
        assert doctype_badge_classes("red") == "bg-red-100 text-red-800"
        assert doctype_badge_classes("amber") == "bg-amber-100 text-amber-800"

    def test_unknown_color_fallback(self):
        assert doctype_badge_classes("magenta") == "bg-indigo-100 text-indigo-800"

    def test_empty_string_fallback(self):
        assert doctype_badge_classes("") == "bg-indigo-100 text-indigo-800"

    def test_none_fallback(self):
        assert doctype_badge_classes(None) == "bg-indigo-100 text-indigo-800"


@pytest.mark.django_db
class TestMixedFeedCreatedFilter:
    """Tests for created-activity filtering in mixed feed."""

    def test_mixed_feed_excludes_created_activities(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={"dauer": 15},
            created_by=staff_user,
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=event,
            summary="Kontakt erstellt",
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=event,
            summary="Kontakt aktualisiert",
        )
        items = build_feed_items(facility, today, feed_type="")
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.CREATED not in verbs
        assert Activity.Verb.UPDATED in verbs

    def test_activities_filter_includes_created(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={"dauer": 15},
            created_by=staff_user,
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=event,
            summary="Kontakt erstellt",
        )
        items = build_feed_items(facility, today, feed_type="activities")
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.CREATED in verbs


class TestVerbBadgeClasses:
    """Tests for verb_badge_classes template filter."""

    def test_known_verbs(self):
        assert verb_badge_classes("updated") == "bg-gray-100 text-gray-800"
        assert verb_badge_classes("deleted") == "bg-red-100 text-red-800"
        assert verb_badge_classes("qualified") == "bg-indigo-100 text-indigo-800"
        assert verb_badge_classes("completed") == "bg-green-100 text-green-800"
        assert verb_badge_classes("reopened") == "bg-amber-100 text-amber-800"

    def test_fallback(self):
        assert verb_badge_classes("unknown") == "bg-gray-100 text-gray-800"
        assert verb_badge_classes("") == "bg-gray-100 text-gray-800"


@pytest.mark.django_db
class TestActivityTargetUrl:
    """Tests for activity_target_url template tag."""

    def test_client_target(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            summary="Klientel aktualisiert",
        )
        url = activity_target_url(activity)
        assert f"/clients/{client_identified.pk}/" in url

    def test_event_target(self, facility, staff_user, sample_event):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=sample_event,
            summary="Kontakt aktualisiert",
        )
        url = activity_target_url(activity)
        assert f"/events/{sample_event.pk}/" in url

    def test_workitem_target(self, facility, staff_user, sample_workitem):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.COMPLETED,
            target=sample_workitem,
            summary="Aufgabe erledigt",
        )
        url = activity_target_url(activity)
        assert f"/workitems/{sample_workitem.pk}/" in url

    def test_case_target(self, facility, staff_user, case_open):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=case_open,
            summary="Fall aktualisiert",
        )
        url = activity_target_url(activity)
        assert f"/cases/{case_open.pk}/" in url

    def test_deleted_verb_returns_empty(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.DELETED,
            target=client_identified,
            summary="Klientel gelöscht",
        )
        url = activity_target_url(activity)
        assert url == ""

    def test_unknown_model_returns_empty(self, facility, staff_user):
        activity = Activity.objects.create(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target_type=ContentType.objects.get_for_model(Activity),
            target_id=staff_user.pk,
            summary="Unbekannt",
        )
        url = activity_target_url(activity)
        assert url == ""
