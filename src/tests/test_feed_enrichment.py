"""Tests for feed preview enrichment, badge color filters, and activity template tags."""

from datetime import datetime, time

import pytest
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from core.models import Activity, DocumentType, DocumentTypeField, FieldTemplate
from core.services.dashboard import log_activity, render_activity_summary
from core.services.events import build_feed_items, enrich_events_with_preview
from core.templatetags.core_tags import activity_target_url, doctype_badge_classes
from tests._factories import make_document_type, make_event


@pytest.mark.django_db
class TestEnrichEventsWithPreview:
    """Tests for enrich_events_with_preview()."""

    def test_basic_preview_fields(self, facility, staff_user, client_identified, doc_type_contact):
        # doc_type_contact has Dauer (number, sort=0) and Notiz (textarea, sort=1)
        # Event with dauer=15 → preview should show Dauer:15, but NOT Notiz (textarea)
        event = make_event(
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
        dt = make_document_type(facility=facility, name="Multi")
        for i in range(5):
            ft = FieldTemplate.objects.create(facility=facility, name=f"Feld{i}", field_type="text")
            DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=i)
        data = {f"feld{i}": f"val{i}" for i in range(5)}
        event = make_event(
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
        event = make_event(
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

    def test_expanded_fields_include_textarea(self, facility, staff_user, client_identified, doc_type_contact):
        """Refs #707: expanded_fields zeigt alle Felder inkl. textarea, damit
        Notizen ohne Detail-Klick im Feed lesbar sind."""
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10, "notiz": "Krise eskaliert, RD verständigt"},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert hasattr(event, "expanded_fields")
        labels = {f["label"] for f in event.expanded_fields}
        assert {"Dauer", "Notiz"} <= labels
        notiz_entry = next(f for f in event.expanded_fields if f["label"] == "Notiz")
        assert notiz_entry["is_textarea"] is True
        assert notiz_entry["value"] == "Krise eskaliert, RD verständigt"

    def test_expanded_fields_no_three_field_limit(self, facility, staff_user, client_identified):
        """Refs #707: expanded_fields hat kein Limit von 3 (anders als preview_fields)."""
        dt = make_document_type(facility=facility, name="MultiExpand")
        for i in range(5):
            ft = FieldTemplate.objects.create(facility=facility, name=f"Feld{i}", field_type="text")
            DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=i)
        data = {f"feld{i}": f"val{i}" for i in range(5)}
        event = make_event(
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
        assert len(event.expanded_fields) == 5

    def test_encrypted_field_hidden_for_assistant(self, facility, assistant_user, client_identified):
        # ELEVATED doc type with encrypted field
        dt = make_document_type(
            facility=facility,
            name="Sensitive",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type="text",
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
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
        dt = make_document_type(
            facility=facility,
            name="SensitiveAdmin",
            sensitivity=DocumentType.Sensitivity.ELEVATED,
        )
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Geheim",
            field_type="text",
            is_encrypted=True,
        )
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
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
        dt = make_document_type(facility=facility, name="BoolTest")
        ft = FieldTemplate.objects.create(facility=facility, name="Aktiv", field_type="boolean")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
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
        dt = make_document_type(facility=facility, name="SelectTest")
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
        event = make_event(
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
        dt = make_document_type(facility=facility, name="MultiTest")
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
        event = make_event(
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

    def test_file_marker_stage_a_renders_label(self, facility, staff_user, client_identified):
        """Stufe-A-File-Marker (`{"__file__": True, ...}`) ist im Preview ein
        sprechender Hinweis statt rohes Dict-Repr (Refs #670).
        """
        dt = make_document_type(facility=facility, name="StageA")
        ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type="file")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={"scan": {"__file__": True, "attachment_id": "abc"}},
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "[Datei]"

    def test_file_marker_stage_b_singular(self, facility, staff_user, client_identified):
        """Stufe-B-Marker (`__files__` mit 1 Entry) → `[1 Datei]`."""
        dt = make_document_type(facility=facility, name="StageB1")
        ft = FieldTemplate.objects.create(facility=facility, name="Scans", field_type="file")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={
                "scans": {
                    "__files__": True,
                    "entries": [{"id": "abc", "sort": 0}],
                }
            },
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "[1 Datei]"

    def test_file_marker_stage_b_plural(self, facility, staff_user, client_identified):
        """Stufe-B-Marker (`__files__` mit 3 Entries) → `[3 Dateien]`."""
        dt = make_document_type(facility=facility, name="StageB3")
        ft = FieldTemplate.objects.create(facility=facility, name="Scans", field_type="file")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={
                "scans": {
                    "__files__": True,
                    "entries": [
                        {"id": "a", "sort": 0},
                        {"id": "b", "sort": 1},
                        {"id": "c", "sort": 2},
                    ],
                }
            },
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)
        assert event.preview_fields[0]["value"] == "[3 Dateien]"

    def test_empty_data_json(self, facility, staff_user, client_identified, doc_type_contact):
        event = make_event(
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
        e1 = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        e2 = make_event(
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
        event = make_event(
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
            action=Activity.Action.EVENT_CREATED,
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )
        items = build_feed_items(facility, today, feed_type="")
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.CREATED not in verbs
        assert Activity.Verb.UPDATED in verbs

    def test_activities_filter_includes_created(self, facility, staff_user, client_identified, doc_type_contact):
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))
        event = make_event(
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
            action=Activity.Action.EVENT_CREATED,
        )
        items = build_feed_items(facility, today, feed_type="activities")
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.CREATED in verbs


@pytest.mark.django_db
class TestActivitySensitivityFilter:
    """Tests for sensitivity-based activity filtering in feed (#562)."""

    def test_activity_for_hidden_event_excluded_from_feed(self, facility, staff_user, client_identified):
        """Activity targeting a HIGH-sensitivity event must not appear for staff users."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))

        # HIGH sensitivity → invisible to staff (max ELEVATED)
        dt_high = make_document_type(
            facility=facility,
            name="Vertraulich",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=dt_high,
            occurred_at=now,
            data_json={},
            created_by=staff_user,
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        activity_items = [i for i in items if i["type"] == "activity"]
        target_ids = [i["object"].target_id for i in activity_items]
        assert event.pk not in target_ids

    def test_activity_for_visible_event_included(self, facility, staff_user, client_identified, doc_type_contact):
        """Activity targeting a NORMAL-sensitivity event must remain visible for staff."""
        today = timezone.localdate()
        now = timezone.make_aware(datetime.combine(today, time(12, 0)))

        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=now,
            data_json={"dauer": 10},
            created_by=staff_user,
        )
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=event,
            action=Activity.Action.EVENT_CREATED,
        )

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        activity_items = [i for i in items if i["type"] == "activity"]
        target_ids = [i["object"].target_id for i in activity_items]
        assert event.pk in target_ids

    def test_non_event_activity_unaffected(self, facility, staff_user, client_identified):
        """Activity targeting a Client (not an Event) must not be filtered out."""
        today = timezone.localdate()

        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            action=Activity.Action.CLIENT_UPDATED,
        )

        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        activity_items = [i for i in items if i["type"] == "activity"]
        assert len(activity_items) == 1
        assert activity_items[0]["object"].target_id == client_identified.pk


@pytest.mark.django_db
class TestActivityTargetUrl:
    """Tests for activity_target_url template tag."""

    def test_client_target(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            action=Activity.Action.CLIENT_UPDATED,
        )
        url = activity_target_url(activity)
        assert f"/clients/{client_identified.pk}/" in url

    def test_event_target(self, facility, staff_user, sample_event):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=sample_event,
            action=Activity.Action.EVENT_CREATED,
        )
        url = activity_target_url(activity)
        assert f"/events/{sample_event.pk}/" in url

    def test_workitem_target(self, facility, staff_user, sample_workitem):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.COMPLETED,
            target=sample_workitem,
            action=Activity.Action.WORKITEM_COMPLETED,
        )
        url = activity_target_url(activity)
        assert f"/workitems/{sample_workitem.pk}/" in url

    def test_case_target(self, facility, staff_user, case_open):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=case_open,
            action=Activity.Action.CASE_CLOSED,
        )
        url = activity_target_url(activity)
        assert f"/cases/{case_open.pk}/" in url

    def test_deleted_verb_returns_empty(self, facility, staff_user, client_identified):
        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.DELETED,
            target=client_identified,
            action=Activity.Action.CLIENT_TRASHED,
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
            context={"schema": 1, "action": Activity.Action.LEGACY_REDACTED},
        )
        url = activity_target_url(activity)
        assert url == ""


@pytest.mark.django_db
class TestTrashedCaseLeavesTheFeed:
    """Papierkorb-Grenze im Zeitstrom (Refs #1818, ADR-044 §7).

    ``close_case``/``reopen_case`` rendern den Falltitel aus der Zielreferenz.
    Der Papierkorb nimmt den Fall aus Liste, Detail, Episoden, Zielen und
    Ereignis-Select; der Zeitstrom folgt derselben Sichtbarkeitsgrenze.
    """

    def _feed_summaries(self, facility, user):
        return [
            render_activity_summary(item["object"])
            for item in build_feed_items(facility, timezone.localdate(), "activities", user=user)
            if item["type"] == "activity"
        ]

    def test_case_activity_disappears_with_the_case(self, facility, staff_user, case_open):
        from core.services.case import close_case, soft_delete_case

        close_case(case_open, staff_user)
        assert any(case_open.title in s for s in self._feed_summaries(facility, staff_user))

        soft_delete_case(case_open, staff_user)
        assert self._feed_summaries(facility, staff_user) == []

    def test_activity_returns_after_a_restore(self, facility, staff_user, admin_user, case_open):
        """Die Zeile ist nicht umsonst geschrieben — sie kommt mit dem Fall zurueck."""
        from core.services.case import close_case, restore_case, soft_delete_case

        close_case(case_open, staff_user)
        soft_delete_case(case_open, staff_user)
        restore_case(case_open, admin_user)
        assert any(case_open.title in s for s in self._feed_summaries(facility, staff_user))

    def test_other_targets_are_untouched(self, facility, staff_user, case_open, client_identified):
        from core.services.case import soft_delete_case

        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            action=Activity.Action.CLIENT_UPDATED,
        )
        soft_delete_case(case_open, staff_user)
        assert f"Person {client_identified.pseudonym} aktualisiert" in self._feed_summaries(facility, staff_user)

    def test_target_url_is_dropped_for_a_trashed_case(self, facility, staff_user, case_open):
        """Zweite Verteidigungslinie: der ``deleted``-Verb-Zweig deckt nur die
        Loesch-Zeile selbst, nicht aeltere ``created``/``updated``-Zeilen."""
        from core.services.case import soft_delete_case

        activity = log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=case_open,
            action=Activity.Action.CASE_CLOSED,
        )
        assert activity_target_url(activity) != ""
        soft_delete_case(case_open, staff_user)
        assert activity_target_url(activity) == ""


@pytest.mark.django_db
class TestPreviewOrderMatchesBlockSequence:
    """#1713: die Vorschau-Feldreihenfolge folgt der globalen Block-Sequenz.

    Damit Feed-Vorschau und Event-Formular NICHT unterschiedlich sortieren:
    ``expanded_fields`` muss der ``iter_fields_in_block_order``-Reihenfolge folgen
    (gruppierte Felder innerhalb ihres Gruppenblocks, freie Felder an ihrer
    Blockposition) — ein flaches ``order_by("sort_order")`` würde scrambeln.
    """

    def test_expanded_fields_follow_block_order(self, facility, staff_user, client_identified):
        from core.models import FieldGroup
        from core.services.config import iter_fields_in_block_order

        dt = make_document_type(facility=facility, name="FeedInterleaved")

        def _ft(name):
            return FieldTemplate.objects.create(facility=facility, name=name, field_type="text")

        # freies Feld (0), Gruppe (1) mit zwei Feldern, freies Feld (2).
        DocumentTypeField.objects.create(document_type=dt, field_template=_ft("Frei A"), field_group=None, sort_order=0)
        grp = FieldGroup.objects.create(document_type=dt, name="Mitte", sort_order=1)
        DocumentTypeField.objects.create(document_type=dt, field_template=_ft("Innen 1"), field_group=grp, sort_order=0)
        DocumentTypeField.objects.create(document_type=dt, field_template=_ft("Innen 2"), field_group=grp, sort_order=1)
        DocumentTypeField.objects.create(document_type=dt, field_template=_ft("Frei B"), field_group=None, sort_order=2)

        data = {"frei-a": "a", "innen-1": "1", "innen-2": "2", "frei-b": "b"}
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json=data,
            created_by=staff_user,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]
        enrich_events_with_preview(feed_items, staff_user)

        labels_in_order = [f["label"] for f in event.expanded_fields]
        expected = [dtf.field_template.name for dtf in iter_fields_in_block_order(dt)]
        assert labels_in_order == expected
        assert labels_in_order == ["Frei A", "Innen 1", "Innen 2", "Frei B"]
