"""Follow-Up-Tests für Mutation-Survivors in ``core.services.events.feed``.

Refs #930. Ziel: Mutationen an den Branch- und Boundary-Grenzen
in ``get_time_range``, ``build_feed_items``, ``_format_preview_value`` und
``enrich_events_with_preview`` killen (Top-Survivor ``build_feed_items``
mit 57 überlebenden Mutationen).

Adressiert speziell folgende Mutationsklassen:

1. ``feed_type == "" or feed_type == "all"`` → beide Disjunkte sowie ein
   dritter Wert (``"events"``) werden einzeln geprüft.
2. ``[:FEED_MAX_PER_TYPE]`` → wir patchen das Limit klein und legen
   ``limit + 1`` Datensätze an, um Off-by-One-Mutationen zu fangen.
3. ``occurred_at__gte=start_dt`` / ``occurred_at__lte=end_dt`` → Items
   exakt am Anfang und Ende der Range müssen drin sein.
4. ``time_filter.start_time <= time_filter.end_time`` → Midnight-Overlap
   (22:00–08:00) wird in ``get_time_range`` getestet.
5. ``exclude(verb=Activity.Verb.CREATED)`` nur bei ``include_all`` →
   eine CREATED-Activity ist in ``feed_type="activities"`` sichtbar, in
   ``feed_type="all"`` aber ausgefiltert.
6. ``reverse=True`` im finalen Sort → zwei Items mit unterschiedlichen
   Timestamps müssen absteigend (neuestes zuerst) erscheinen.
7. ``len(preview_fields) < 3`` in ``enrich_events_with_preview`` → mit 4
   sichtbaren Feldern dürfen nur 3 ins ``preview_fields`` landen.

Hinweis: ``FEED_MAX_PER_TYPE`` (default ``200``) wird in den Cap-Tests
über ``unittest.mock.patch`` auf einen kleinen Wert reduziert, weil
``feed.py`` die Konstante per ``from … import FEED_MAX_PER_TYPE`` einbindet
und ein Patch im Source-Modul ``core.services.events.feed`` daher greift, ohne
dass wir hunderte Events anlegen müssen.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import (
    Activity,
    DocumentType,
    DocumentTypeField,
    Event,
    FieldTemplate,
    TimeFilter,
    WorkItem,
)
from core.services.dashboard import log_activity
from core.services.events import (
    _format_preview_value,
    build_feed_items,
    enrich_events_with_preview,
    get_time_range,
)

# ---------------------------------------------------------------------------
# get_time_range — full-day, normal range, midnight-overlap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetTimeRange:
    """Refs ``get_time_range`` (Line 17)."""

    def test_no_time_filter_returns_full_day(self):
        """Ohne ``time_filter`` muss die Range den kompletten Tag abdecken:
        ``time.min`` bis ``time.max``."""
        target = datetime(2026, 5, 17).date()
        start_dt, end_dt = get_time_range(target, time_filter=None)
        assert start_dt.time() == time.min
        assert end_dt.time() == time.max
        assert start_dt.date() == target
        assert end_dt.date() == target

    def test_normal_range_same_day(self, facility):
        """``start_time <= end_time`` → Start und Ende am selben Tag."""
        tf = TimeFilter.objects.create(
            facility=facility,
            label="Tag",
            start_time=time(8, 0),
            end_time=time(16, 0),
        )
        target = datetime(2026, 5, 17).date()
        start_dt, end_dt = get_time_range(target, time_filter=tf)
        assert start_dt.date() == target
        assert end_dt.date() == target
        assert start_dt.time() == time(8, 0)
        assert end_dt.time() == time(16, 0)

    def test_midnight_overlap_ends_next_day(self, facility):
        """``start_time > end_time`` (Nachtschicht) → Ende liegt am
        ``target + 1 Tag``. Mutation ``<=`` → ``<`` oder ``>`` zu ``>=``
        würde hier wahlweise das End-Datum falsch berechnen oder den
        Branch nie betreten."""
        tf = TimeFilter.objects.create(
            facility=facility,
            label="Nacht",
            start_time=time(22, 0),
            end_time=time(8, 0),
        )
        target = datetime(2026, 5, 17).date()
        start_dt, end_dt = get_time_range(target, time_filter=tf)
        assert start_dt.date() == target
        assert end_dt.date() == target + timedelta(days=1)
        assert start_dt.time() == time(22, 0)
        assert end_dt.time() == time(8, 0)

    def test_boundary_start_equals_end_is_normal_branch(self, facility):
        """Boundary: ``start_time == end_time`` ist laut Code (``<=``) der
        Normal-Range-Branch — Ende muss am selben Tag liegen, nicht am
        Folgetag. Mutation ``<=`` → ``<`` würde hier auf den Midnight-
        Overlap-Branch springen und ``end_dt.date() == target + 1``
        liefern."""
        tf = TimeFilter.objects.create(
            facility=facility,
            label="Punkt",
            start_time=time(12, 0),
            end_time=time(12, 0),
        )
        target = datetime(2026, 5, 17).date()
        start_dt, end_dt = get_time_range(target, time_filter=tf)
        assert start_dt.date() == target
        assert end_dt.date() == target, "start == end darf NICHT als Midnight-Overlap behandelt werden"


# ---------------------------------------------------------------------------
# build_feed_items — feed_type-Branches, Boundaries, Sort, Cap
# ---------------------------------------------------------------------------


def _aware(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(year, month, day, hour, minute))


@pytest.fixture
def normal_doc_type(facility):
    """NORMAL-Sensitivity DocumentType ohne system_type — für Standard-Events."""
    return DocumentType.objects.create(
        facility=facility,
        name="Normal",
        category=DocumentType.Category.CONTACT,
    )


@pytest.fixture
def ban_doc_type(facility):
    """system_type=BAN DocumentType, ELEVATED — damit Staff es lesen darf."""
    return DocumentType.objects.create(
        facility=facility,
        name="Hausverbot",
        category=DocumentType.Category.ADMIN,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )


@pytest.mark.django_db
class TestBuildFeedItemsTypeBranches:
    """``include_all = feed_type == "" or feed_type == "all"`` — beide
    Disjunkte einzeln + dritter Wert."""

    def test_empty_string_triggers_include_all(
        self, facility, staff_user, client_identified, normal_doc_type, ban_doc_type
    ):
        """``feed_type=""`` → ``include_all=True`` → Events werden geladen,
        aber Ban-Events sind exkludiert (system_type='ban'). Ohne den
        Excludeswürde der Ban-Event hier auftauchen."""
        today = timezone.localdate()
        # Normaler Event
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=normal_doc_type,
            occurred_at=_aware(today.year, today.month, today.day, 10, 0),
            data_json={},
            created_by=staff_user,
        )
        # Ban-Event — muss bei include_all aus dem Events-Block raus,
        # erscheint dafür im Bans-Block.
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_doc_type,
            occurred_at=_aware(today.year, today.month, today.day, 11, 0),
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="", user=staff_user)
        types = [i["type"] for i in items]
        # include_all → mindestens Events- und Bans-Bucket aktiv
        assert "ban" in types, "Empty feed_type muss include_all triggern (Bans im Result)"
        # Ban-Event darf NICHT als 'event' auftauchen (würde Duplikat sein)
        event_ids = [i["object"].pk for i in items if i["type"] == "event"]
        ban_ids = [i["object"].pk for i in items if i["type"] == "ban"]
        for bid in ban_ids:
            assert bid not in event_ids, "Ban darf nicht zusätzlich als 'event' im Feed sein"

    def test_all_string_triggers_include_all(self, facility, staff_user, client_identified, ban_doc_type):
        """``feed_type="all"`` → ``include_all=True`` → Bans-Bucket aktiv."""
        today = timezone.localdate()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_doc_type,
            occurred_at=_aware(today.year, today.month, today.day, 11, 0),
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="all", user=staff_user)
        types = {i["type"] for i in items}
        assert "ban" in types

    def test_events_string_does_not_trigger_include_all(
        self, facility, staff_user, client_identified, normal_doc_type, ban_doc_type
    ):
        """Dritter Wert ``feed_type="events"`` → ``include_all=False``.
        Hier muss der Bans-Bucket leer sein UND der Ban-Event darf den
        Events-Bucket füllen (kein Exclude bei single-type).

        Mutation ``or`` → ``and`` würde ``include_all`` immer ``False``
        machen — der Test verlangt aber ZUSÄTZLICH, dass der Ban-Event
        bei ``"events"`` sichtbar ist (kein Exclude), während er bei
        ``""``/``"all"`` aus dem Events-Bucket ausgeschlossen wird.
        """
        today = timezone.localdate()
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=normal_doc_type,
            occurred_at=_aware(today.year, today.month, today.day, 10, 0),
            data_json={},
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_doc_type,
            occurred_at=_aware(today.year, today.month, today.day, 11, 0),
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        types = {i["type"] for i in items}
        # Nur Events-Bucket, kein Bans-Bucket
        assert "ban" not in types, "feed_type=events darf den Bans-Bucket nicht laden"
        # Bei single-type 'events' KEIN Exclude des Ban-Typs
        all_event_dt_ids = {i["object"].document_type_id for i in items if i["type"] == "event"}
        assert ban_doc_type.id in all_event_dt_ids, (
            "feed_type=events muss ALLE Events liefern, auch Ban-Typ (Exclude greift nur bei include_all)"
        )


@pytest.mark.django_db
class TestBuildFeedItemsBoundary:
    """Zeitfenster-Boundaries (``__gte`` / ``__lte``)."""

    def test_event_exactly_at_start_dt_included(self, facility, staff_user, client_identified, normal_doc_type):
        """Event genau am Anfang der Range (``occurred_at == start_dt``)
        muss enthalten sein (``__gte``). Mutation ``__gte`` → ``__gt``
        würde diesen Event verlieren."""
        today = timezone.localdate()
        start_dt = timezone.make_aware(datetime.combine(today, time.min))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=normal_doc_type,
            occurred_at=start_dt,
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        assert len([i for i in items if i["type"] == "event"]) == 1

    def test_event_exactly_at_end_dt_included(self, facility, staff_user, client_identified, normal_doc_type):
        """Event genau am Ende der Range (``occurred_at == end_dt``,
        also ``time.max``) muss enthalten sein (``__lte``). Mutation
        ``__lte`` → ``__lt`` würde diesen Event verlieren."""
        today = timezone.localdate()
        end_dt = timezone.make_aware(datetime.combine(today, time.max))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=normal_doc_type,
            occurred_at=end_dt,
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        assert len([i for i in items if i["type"] == "event"]) == 1

    def test_event_one_microsecond_before_start_excluded(
        self, facility, staff_user, client_identified, normal_doc_type
    ):
        """Negativ-Boundary: Event ein µs VOR ``start_dt`` darf NICHT drin
        sein. Sichert, dass der Test oben nicht versehentlich alles
        durchwinkt."""
        today = timezone.localdate()
        before_start = timezone.make_aware(datetime.combine(today, time.min)) - timedelta(microseconds=1)
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=normal_doc_type,
            occurred_at=before_start,
            data_json={},
            created_by=staff_user,
        )
        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        assert [i for i in items if i["type"] == "event"] == []


@pytest.mark.django_db
class TestBuildFeedItemsCap:
    """``[:FEED_MAX_PER_TYPE]`` — Off-by-One-Sentinel.

    Wir patchen ``FEED_MAX_PER_TYPE`` im Feed-Modul klein (2), legen 3
    Events an und verifizieren, dass exakt 2 zurückkommen. Mutation
    ``[:N]`` → ``[:N+1]`` oder ``[:N-1]`` würde den Count verschieben.
    """

    def test_cap_enforced(self, facility, staff_user, client_identified, normal_doc_type):
        today = timezone.localdate()
        for hour in (9, 10, 11):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=normal_doc_type,
                occurred_at=_aware(today.year, today.month, today.day, hour, 0),
                data_json={},
                created_by=staff_user,
            )
        with patch("core.services.events.feed.FEED_MAX_PER_TYPE", 2):
            items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        assert len([i for i in items if i["type"] == "event"]) == 2

    def test_cap_returns_newest_when_truncating(self, facility, staff_user, client_identified, normal_doc_type):
        """Sentinel-Test: Bei Cap müssen die NEUESTEN Events drin sein
        (``order_by("-occurred_at")[:N]``). Mutation ``-occurred_at`` →
        ``occurred_at`` würde die ältesten zurückgeben."""
        today = timezone.localdate()
        for hour in (9, 10, 11):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=normal_doc_type,
                occurred_at=_aware(today.year, today.month, today.day, hour, 0),
                data_json={},
                created_by=staff_user,
            )
        with patch("core.services.events.feed.FEED_MAX_PER_TYPE", 2):
            items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        event_items = [i for i in items if i["type"] == "event"]
        # ``occurred_at`` ist UTC-aware aus dem DB — fuer Hour-Assertion
        # zurueck in lokale TZ konvertieren (sonst CEST/UTC-Offset-Bug).
        hours = sorted(timezone.localtime(i["occurred_at"]).hour for i in event_items)
        assert hours == [10, 11], f"Cap muss die NEUESTEN Events behalten, bekam {hours}"


@pytest.mark.django_db
class TestBuildFeedItemsCreatedExclude:
    """``exclude(verb=Activity.Verb.CREATED)`` nur bei ``include_all``."""

    def test_created_visible_in_single_activities_feed(self, facility, staff_user, client_identified):
        """``feed_type="activities"`` (include_all=False) → CREATED-
        Aktivitäten bleiben sichtbar."""
        today = timezone.localdate()
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Klient erstellt",
        )
        items = build_feed_items(facility, today, feed_type="activities", user=staff_user)
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.CREATED in verbs, (
            "Single-Type 'activities' MUSS CREATED enthalten — Exclude greift nur bei include_all"
        )

    def test_created_excluded_in_all_feed(self, facility, staff_user, client_identified):
        """``feed_type="all"`` (include_all=True) → CREATED-Aktivitäten
        werden exkludiert (redundant zur first-class Card)."""
        today = timezone.localdate()
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.CREATED,
            target=client_identified,
            summary="Klient erstellt",
        )
        # Eine UPDATED-Activity als Sanity-Check, dass der Bucket gefüllt
        # wird — sonst könnte der Test fälschlich grün sein, weil
        # Activities komplett fehlen.
        log_activity(
            facility=facility,
            actor=staff_user,
            verb=Activity.Verb.UPDATED,
            target=client_identified,
            summary="Klient geändert",
        )
        items = build_feed_items(facility, today, feed_type="all", user=staff_user)
        activity_items = [i for i in items if i["type"] == "activity"]
        verbs = [i["object"].verb for i in activity_items]
        assert Activity.Verb.UPDATED in verbs, "Sanity: UPDATED muss im all-Feed sein"
        assert Activity.Verb.CREATED not in verbs, "include_all MUSS CREATED-Activities exkludieren"


@pytest.mark.django_db
class TestBuildFeedItemsSort:
    """Finaler Sort ``reverse=True`` — neueste zuerst.

    Mutation ``reverse=True`` → ``reverse=False`` würde die Reihenfolge
    invertieren.
    """

    def test_items_sorted_descending_by_occurred_at(self, facility, staff_user, client_identified, normal_doc_type):
        today = timezone.localdate()
        # Drei Events mit unterschiedlichen Timestamps in zufälliger
        # Erstellungsreihenfolge, damit das Test-Ergebnis nicht durch
        # Insert-Order, sondern durch sorted(...) entsteht.
        ts_early = _aware(today.year, today.month, today.day, 9, 0)
        ts_mid = _aware(today.year, today.month, today.day, 12, 0)
        ts_late = _aware(today.year, today.month, today.day, 18, 0)
        for ts in (ts_mid, ts_early, ts_late):
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=normal_doc_type,
                occurred_at=ts,
                data_json={},
                created_by=staff_user,
            )
        items = build_feed_items(facility, today, feed_type="events", user=staff_user)
        timestamps = [i["occurred_at"] for i in items if i["type"] == "event"]
        assert timestamps == sorted(timestamps, reverse=True), f"Final sort muss DESC sein, bekam {timestamps}"
        # Härtere Variante: NEUESTE muss an Position 0 stehen
        assert timestamps[0] == ts_late


# ---------------------------------------------------------------------------
# _format_preview_value — Format-Branches pro field_type
# ---------------------------------------------------------------------------


class _FT:
    """Mini-Stub einer FieldTemplate für ``_format_preview_value``-Tests.

    Wir umgehen die DB hier bewusst, weil ``_format_preview_value`` rein
    funktional ist und nur ``field_type``/``options_json`` braucht.
    """

    def __init__(self, field_type: str, options_json=None):
        self.field_type = field_type
        self.options_json = options_json or []


class TestFormatPreviewValue:
    """Refs ``_format_preview_value`` (Line 130)."""

    def test_boolean_true_yields_ja(self):
        assert _format_preview_value(True, _FT("boolean")) == "Ja"

    def test_boolean_false_yields_nein(self):
        """Mutation: ``"Ja" if value else "Nein"`` → invertiert würde False
        zu "Ja" mappen."""
        assert _format_preview_value(False, _FT("boolean")) == "Nein"

    def test_select_maps_slug_to_label(self):
        ft = _FT("select", options_json=[{"slug": "rot", "label": "Rot"}])
        assert _format_preview_value("rot", ft) == "Rot"

    def test_select_unknown_slug_fallback_to_str(self):
        """Mutation: ``label_map.get(value, str(value))`` → ``.get(value)``
        ohne Default würde ``None`` zurückgeben."""
        ft = _FT("select", options_json=[{"slug": "rot", "label": "Rot"}])
        assert _format_preview_value("blau", ft) == "blau"

    def test_multi_select_joins_labels_with_comma_space(self):
        ft = _FT(
            "multi_select",
            options_json=[
                {"slug": "a", "label": "Alpha"},
                {"slug": "b", "label": "Beta"},
            ],
        )
        assert _format_preview_value(["a", "b"], ft) == "Alpha, Beta"

    def test_multi_select_without_options_json_falls_through_to_str(self):
        """Wenn ``options_json`` leer ist, greift der Branch nicht und
        es fällt auf ``str(value)``."""
        ft = _FT("multi_select", options_json=[])
        assert _format_preview_value(["a", "b"], ft) == "['a', 'b']"

    def test_file_marker_single_yields_datei_placeholder(self):
        """Dict-Branch mit ``__file__`` → '[Datei]' (i18n)."""
        ft = _FT("text")
        result = _format_preview_value({"__file__": True, "name": "x.pdf"}, ft)
        assert result == "[Datei]"

    def test_files_marker_with_entries_uses_count(self):
        """Dict-Branch mit ``__files__`` → '[N Dateien]' (ngettext)."""
        ft = _FT("text")
        result = _format_preview_value(
            {"__files__": True, "entries": [{"id": 1}, {"id": 2}, {"id": 3}]},
            ft,
        )
        assert "3" in result

    def test_plain_value_falls_through_to_str(self):
        ft = _FT("text")
        assert _format_preview_value(42, ft) == "42"


# ---------------------------------------------------------------------------
# enrich_events_with_preview — Limit von 3 Preview-Feldern
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnrichEventsPreviewLimit:
    """Refs ``enrich_events_with_preview`` (Line 158).

    Branch ``len(preview_fields) < 3`` mit 4 nicht-textarea-Feldern:
    Preview bleibt bei 3 stehen, Expanded zeigt alle 4.

    Mutation ``< 3`` → ``<= 3`` würde 4 Felder durchwinken,
    Mutation ``< 3`` → ``< 4`` würde dasselbe tun,
    Mutation ``< 3`` → ``< 2`` würde nur 2 Felder zulassen.
    """

    def test_preview_capped_at_three_when_four_visible(self, facility, staff_user, client_identified):
        dt = DocumentType.objects.create(
            facility=facility,
            name="VierFelder",
            category=DocumentType.Category.CONTACT,
        )
        for i in range(4):
            ft = FieldTemplate.objects.create(
                facility=facility,
                name=f"FeldA{i}",
                field_type=FieldTemplate.FieldType.TEXT,
            )
            DocumentTypeField.objects.create(
                document_type=dt,
                field_template=ft,
                sort_order=i,
            )
        data = {f"felda{i}": f"v{i}" for i in range(4)}
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
        assert len(event.preview_fields) == 3, f"preview_fields muss auf 3 cappen, bekam {len(event.preview_fields)}"
        assert len(event.expanded_fields) == 4, "expanded_fields darf nicht cappen — Refs #707"

    def test_preview_includes_exactly_three_with_three_visible(self, facility, staff_user, client_identified):
        """Boundary: bei genau 3 Feldern werden alle 3 in preview übernommen
        (``len(preview_fields) < 3`` ist erfüllt, solange noch < 3)."""
        dt = DocumentType.objects.create(
            facility=facility,
            name="DreiFelder",
            category=DocumentType.Category.CONTACT,
        )
        for i in range(3):
            ft = FieldTemplate.objects.create(
                facility=facility,
                name=f"FeldB{i}",
                field_type=FieldTemplate.FieldType.TEXT,
            )
            DocumentTypeField.objects.create(
                document_type=dt,
                field_template=ft,
                sort_order=i,
            )
        data = {f"feldb{i}": f"v{i}" for i in range(3)}
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


# ---------------------------------------------------------------------------
# Sanity: build_feed_items + WorkItem-Bucket-Boundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildFeedItemsWorkItemBoundary:
    """Sicherheitsnetz für ``created_at__gte/lte``-Boundary im WorkItem-Bucket
    (analog zu Events). Mutmut mutiert beide Stellen einzeln."""

    def test_workitem_exactly_at_start_included(self, facility, staff_user, client_identified):
        today = timezone.localdate()
        start_dt = timezone.make_aware(datetime.combine(today, time.min))
        # WorkItem.created_at ist auto_now_add — wir setzen es nachträglich
        # per .update() um auto_now_add zu umgehen.
        wi = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="Boundary-Task",
        )
        WorkItem.objects.filter(pk=wi.pk).update(created_at=start_dt)
        items = build_feed_items(facility, today, feed_type="workitems", user=staff_user)
        wi_items = [i for i in items if i["type"] == "workitem"]
        assert len(wi_items) == 1


# ---------------------------------------------------------------------------
# Refs #1160 R1a: _build_event_preview_fields (aus enrich_events_with_preview)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBuildEventPreviewFieldsHelper:
    """``_build_event_preview_fields`` — Skip-Bedingungen + Preview/Expanded-Split.

    Direkt-Tests fuer den aus ``enrich_events_with_preview`` extrahierten Helfer.
    """

    def _dt_with_fields(self, facility, specs):
        dt = DocumentType.objects.create(facility=facility, name="PreviewDT", category="contact")
        fts = []
        for i, spec in enumerate(specs):
            ft = FieldTemplate.objects.create(facility=facility, **spec)
            DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=i)
            fts.append(ft)
        return dt, fts

    def test_preview_capped_at_three_expanded_unbounded(self, facility, staff_user, client_identified):
        from core.services.events.feed import _build_event_preview_fields

        dt, fts = self._dt_with_fields(facility, [{"name": f"Feld{i}", "field_type": "text"} for i in range(5)])
        data = {ft.slug: f"val{i}" for i, ft in enumerate(fts)}
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json=data,
            created_by=staff_user,
        )
        preview, expanded = _build_event_preview_fields(event, fts, staff_user)
        assert len(preview) == 3
        assert len(expanded) == 5

    def test_textarea_excluded_from_preview_included_in_expanded(self, facility, staff_user, client_identified):
        from core.services.events.feed import _build_event_preview_fields

        dt, fts = self._dt_with_fields(
            facility,
            [
                {"name": "Zahl", "field_type": "number"},
                {"name": "Notiz", "field_type": "textarea"},
            ],
        )
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={fts[0].slug: 5, fts[1].slug: "lang"},
            created_by=staff_user,
        )
        preview, expanded = _build_event_preview_fields(event, fts, staff_user)
        assert [p["label"] for p in preview] == ["Zahl"]
        assert {e["label"] for e in expanded} == {"Zahl", "Notiz"}

    def test_empty_none_and_falsy_boolean_skipped(self, facility, staff_user, client_identified):
        from core.services.events.feed import _build_event_preview_fields

        dt, fts = self._dt_with_fields(
            facility,
            [
                {"name": "Leer", "field_type": "text"},
                {"name": "Fehlt", "field_type": "text"},
                {"name": "Flag", "field_type": "boolean"},
                {"name": "Liste", "field_type": "multi_select"},
                {"name": "Gut", "field_type": "text"},
            ],
        )
        ft_empty, ft_missing, ft_bool, ft_list, ft_good = fts
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={
                ft_empty.slug: "",  # leerer String → skip
                ft_bool.slug: False,  # falsy boolean → skip
                ft_list.slug: [],  # leere Liste → skip
                ft_good.slug: "ok",
                # ft_missing.slug fehlt komplett → None → skip
            },
            created_by=staff_user,
        )
        preview, expanded = _build_event_preview_fields(event, fts, staff_user)
        labels = {e["label"] for e in expanded}
        assert labels == {"Gut"}
