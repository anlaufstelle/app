"""Mutation-Followup-Tests für ``core.services.snapshot`` — Branches/Boundaries.

Refs #930. Sub-File aus ``test_mutation_followup_snapshot``;
enthält die Test-Klassen für die ``is_multi_month_range``-/Boundary-/
Datums-Helfer:
``TestIsMultiMonthRange``, ``TestCreateOrUpdateSnapshotBranches``,
``TestEnsureSnapshotsForMonthsBranches``, ``TestGetSnapshot`` und
``TestSplitIntoSegmentsBoundaries``.

Constraint: Tests gegen Verify-DB (``POSTGRES_DB=anlaufstelle_verify``).
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import (
    DocumentType,
    Event,
    StatisticsSnapshot,
)
from core.services.dashboard import (
    _split_into_segments,
    create_or_update_snapshot,
    ensure_snapshots_for_months,
    get_snapshot,
    is_multi_month_range,
)
from tests._mutation_followup_snapshot_helpers import _make_event

# ---------------------------------------------------------------------------
# is_multi_month_range — Boundary an 31 Tagen
# ---------------------------------------------------------------------------


class TestIsMultiMonthRange:
    """Refs ``is_multi_month_range`` (Line 18).

    Boundary ``(date_to - date_from).days > 31``. Mutationen:
    - ``> 31`` → ``>= 31`` würde 31-Tage-Range falsch als multi-month melden.
    - ``> 31`` → ``> 30`` würde 31-Tage-Range fälschlich auf True flippen.
    - ``> 31`` → ``< 31`` würde alle Vorzeichen invertieren.
    - ``or`` → ``and`` würde die None-Kurzschluss-Logik kippen.
    """

    def test_both_none_returns_false(self):
        """Mutation am ``or`` würde diesen Kurzschluss-Branch kippen."""
        assert is_multi_month_range(None, None) is False

    def test_from_none_returns_false(self):
        """Mutation am ``date_from is None`` würde diesen Branch killen."""
        assert is_multi_month_range(None, date(2025, 3, 1)) is False

    def test_to_none_returns_false(self):
        """Mutation am ``date_to is None`` würde diesen Branch killen."""
        assert is_multi_month_range(date(2025, 3, 1), None) is False

    def test_same_day_returns_false(self):
        """0 Tage Diff → False (eindeutig sub-month)."""
        d = date(2025, 3, 1)
        assert is_multi_month_range(d, d) is False

    def test_thirty_days_returns_false(self):
        """30-Tage-Range darf NICHT multi-month sein.

        Mutation ``> 31`` → ``> 29`` würde hier auf True flippen.
        """
        assert is_multi_month_range(date(2025, 3, 1), date(2025, 3, 31)) is False

    def test_exactly_31_days_diff_returns_false(self):
        """Boundary: genau 31 Tage Diff (also 32 Kalendertage) — laut Doc
        ist eine Range *fully within* einem Kalendermonat erlaubt; der Code
        nimmt aber die naive Tagedifferenz.

        Mutation ``> 31`` → ``>= 31`` würde diese 31-Tage-Range fälschlich
        auf True flippen.
        """
        # 31 Tage Diff (z.B. 1. Jan bis 1. Feb)
        assert is_multi_month_range(date(2025, 1, 1), date(2025, 2, 1)) is False

    def test_32_days_diff_returns_true(self):
        """Boundary: 32 Tage → True. Mutation ``> 31`` → ``> 32`` würde
        diesen Test killen."""
        assert is_multi_month_range(date(2025, 1, 1), date(2025, 2, 2)) is True

    def test_full_january_within_month_returns_false(self):
        """31-tägige Januar-Range (1.–31.) bleibt False — 30 Tage Diff."""
        assert is_multi_month_range(date(2025, 1, 1), date(2025, 1, 31)) is False

    def test_large_range_returns_true(self):
        """Quartal: >> 31 Tage. Mutation am Vergleichsoperator wird gefangen."""
        assert is_multi_month_range(date(2025, 1, 1), date(2025, 3, 31)) is True


# ---------------------------------------------------------------------------
# create_or_update_snapshot — Create vs Update + Monats-Boundaries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreateOrUpdateSnapshotBranches:
    """Refs ``create_or_update_snapshot`` (Line 33).

    Adressierte Mutationen:
    - ``calendar.monthrange(year, month)`` → falsche Tageszahl im Boundary.
    - ``date(year, month, 1)`` → andere Defaults.
    - ``date(year, month, last_day)`` → off-by-one am Monatsende.
    - ``update_or_create`` → ``create`` (würde IntegrityError bei Update).
    - ``defaults={"data": stats, "jugendamt_data": jg_stats}`` — beide Keys.
    """

    def test_creates_new_snapshot_when_none_exists(self, facility, staff_user, client_identified, doc_type_contact):
        """Create-Branch des ``update_or_create``."""
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        assert not StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=1).exists()
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.data["total_contacts"] == 1

    def test_updates_existing_snapshot_in_place(self, facility, staff_user, client_identified, doc_type_contact):
        """Update-Branch: gleiche PK bleibt erhalten, ``data`` ändert sich.

        Mutation ``update_or_create`` → ``create`` würde hier ``IntegrityError``
        (UniqueConstraint) werfen.
        """
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap_before = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        pk_before = snap_before.pk

        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap_after = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap_after.pk == pk_before, "Update muss dieselbe Row beibehalten"
        assert snap_after.data["total_contacts"] == 2
        # UniqueConstraint: nie zwei Snapshots
        assert StatisticsSnapshot.objects.filter(facility=facility, year=2025, month=1).count() == 1

    def test_february_non_leap_year_28_days(self, facility, staff_user, client_identified, doc_type_contact):
        """Boundary: Februar 2025 hat 28 Tage. Event am 28.2. muss drin sein.

        Mutation ``calendar.monthrange`` → konstantem 31 würde den 29.–31.2.
        gar nicht filtern (gibt's nicht), aber das Event am 28. bleibt.
        Wir prüfen explizit, dass Snapshot existiert und Event zählt.
        """
        feb28 = datetime(2025, 2, 28, 14, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, feb28)
        create_or_update_snapshot(facility, 2025, 2)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=2)
        assert snap.data["total_contacts"] == 1

    def test_february_leap_year_29_days_event_included(self, facility, staff_user, client_identified, doc_type_contact):
        """Schaltjahr-Boundary: Februar 2024 hat 29 Tage.

        Event am 29.2.2024 muss im Snapshot enthalten sein — Mutation
        ``calendar.monthrange`` → konstantem 28 würde den 29. ausschließen.
        """
        # 2024 ist Schaltjahr (durch 4 teilbar, nicht durch 100 außer 400)
        assert calendar.monthrange(2024, 2)[1] == 29
        feb29 = datetime(2024, 2, 29, 14, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, feb29)
        create_or_update_snapshot(facility, 2024, 2)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2024, month=2)
        assert snap.data["total_contacts"] == 1, "Schaltjahr-29.2. muss im Snapshot zählen"

    def test_30_day_month_event_on_last_day(self, facility, staff_user, client_identified, doc_type_contact):
        """30-Tage-Monat: April 2025. Event am 30.4. muss drin sein.

        Mutation ``date(year, month, last_day)`` → konstantem 28 würde
        den 30. abschneiden.
        """
        apr30 = datetime(2025, 4, 30, 14, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, apr30)
        create_or_update_snapshot(facility, 2025, 4)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=4)
        assert snap.data["total_contacts"] == 1

    def test_31_day_month_event_on_last_day(self, facility, staff_user, client_identified, doc_type_contact):
        """31-Tage-Monat: Januar 2025. Event am 31.1. muss drin sein."""
        jan31 = datetime(2025, 1, 31, 14, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan31)
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.data["total_contacts"] == 1

    def test_event_on_first_day_of_month_included(self, facility, staff_user, client_identified, doc_type_contact):
        """Boundary: Event am 1. Tag des Monats.

        Mutation ``date(year, month, 1)`` → ``date(year, month, 2)`` würde
        diesen Event abschneiden.
        """
        feb01 = datetime(2025, 2, 1, 0, 1)
        _make_event(facility, client_identified, doc_type_contact, staff_user, feb01)
        create_or_update_snapshot(facility, 2025, 2)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=2)
        assert snap.data["total_contacts"] == 1

    def test_top_clients_removed_from_snapshot(self, facility, staff_user, client_identified, doc_type_contact):
        """``stats.pop("top_clients", None)`` muss greifen.

        Mutation ``pop`` → ``get`` würde top_clients in den Snapshot leaken
        (Datenschutz-Bug, Refs Snapshot-Doc).
        """
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert "top_clients" not in snap.data, "top_clients darf nicht in Snapshot landen (PII-Risiko)"

    def test_jugendamt_data_persisted_separately(self, facility, staff_user, client_identified):
        """``defaults={..., "jugendamt_data": jg_stats}`` — separater Feldlauf.

        Mutation ``jg_stats`` → ``{}`` oder Vertauschung mit ``data`` würde
        ``jugendamt_data`` leeren oder duplizieren.
        """
        dt = DocumentType.objects.create(
            facility=facility,
            name="Kontakt-JG",
            category=DocumentType.Category.CONTACT,
            system_type=DocumentType.SystemType.CONTACT,
        )
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, dt, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.jugendamt_data["total"] == 1
        # data und jugendamt_data sind getrennte Felder — beide gefüllt
        assert snap.data["total_contacts"] == 1
        assert snap.jugendamt_data != snap.data

    def test_by_document_type_enriched_with_system_type(self, facility, staff_user, client_identified):
        """``entry["system_type"] = dt.system_type if dt and dt.system_type
        else ""`` — Enrichment greift.

        Mutation ``dt.system_type if dt and dt.system_type else ""`` →
        ``"normal"`` würde alle Snapshots mit falscher System-Type-
        Annotation leaken.
        """
        dt = DocumentType.objects.create(
            facility=facility,
            name="Begleitung",
            category=DocumentType.Category.SERVICE,
            system_type=DocumentType.SystemType.ACCOMPANIMENT,
        )
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, dt, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        entries = snap.data["by_document_type"]
        # Genau ein Entry, korrekt enriched
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "Begleitung"
        assert entry["system_type"] == "accompaniment"
        assert entry["document_type_id"] == str(dt.id)

    def test_by_document_type_enriched_empty_when_no_system_type(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Negativbranch: ohne ``system_type`` bleibt Annotation leer.

        Mutation ``dt and dt.system_type`` → ``dt or dt.system_type`` würde
        diesen Branch killen.
        """
        # doc_type_contact hat KEIN system_type
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        create_or_update_snapshot(facility, 2025, 1)
        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        entries = snap.data["by_document_type"]
        entry = next(e for e in entries if e["name"] == "Kontakt")
        assert entry["system_type"] == ""
        # document_type_id wird trotzdem gesetzt, weil dt gefunden wurde
        assert entry["document_type_id"] == str(doc_type_contact.id)


# ---------------------------------------------------------------------------
# ensure_snapshots_for_months — current/future month wird ausgelassen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnsureSnapshotsForMonthsBranches:
    """Refs ``ensure_snapshots_for_months`` (Line 82).

    Adressierte Mutationen:
    - ``(year, month) < current`` → ``<=`` würde current_month Snapshot
      anlegen (Doku verbietet das).
    - ``< current`` → ``> current`` würde nur future_months snapshotten.
    """

    def test_current_month_skipped(self, facility, staff_user, client_identified, doc_type_contact):
        """Current-month-Event darf KEINEN Snapshot triggern."""
        today = timezone.localdate()
        current_dt = timezone.make_aware(datetime(today.year, today.month, max(1, min(today.day, 28)), 10, 0))
        _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            current_dt,
        )
        events = Event.objects.filter(facility=facility)
        ensure_snapshots_for_months(facility, events)
        assert not StatisticsSnapshot.objects.filter(facility=facility, year=today.year, month=today.month).exists(), (
            "Current month darf NICHT gesnapshottet werden"
        )

    def test_past_month_creates_snapshot(self, facility, staff_user, client_identified, doc_type_contact):
        """Past-month-Event MUSS Snapshot triggern."""
        past_dt = datetime(2024, 6, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, past_dt)
        events = Event.objects.filter(facility=facility)
        ensure_snapshots_for_months(facility, events)
        assert StatisticsSnapshot.objects.filter(facility=facility, year=2024, month=6).exists()

    def test_mixed_past_and_current_only_past_snapshotted(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Zwei Events, einer past einer current → nur past wird snapshottet."""
        today = timezone.localdate()
        past_dt = datetime(2024, 6, 15, 10, 0)
        current_dt = timezone.make_aware(datetime(today.year, today.month, max(1, min(today.day, 28)), 10, 0))
        _make_event(facility, client_identified, doc_type_contact, staff_user, past_dt)
        _make_event(
            facility,
            client_identified,
            doc_type_contact,
            staff_user,
            current_dt,
        )
        events = Event.objects.filter(facility=facility)
        ensure_snapshots_for_months(facility, events)
        snapshots = StatisticsSnapshot.objects.filter(facility=facility)
        snap_months = set(snapshots.values_list("year", "month"))
        assert (2024, 6) in snap_months
        assert (today.year, today.month) not in snap_months


# ---------------------------------------------------------------------------
# get_snapshot — beide Branches
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetSnapshot:
    """Refs ``get_snapshot`` (Line 93).

    Adressierte Mutationen:
    - ``snap.data if snap else None`` → ``snap.jugendamt_data`` Vertauschung.
    - ``if snap else None`` → ``if snap else {}`` würde Aufrufer kippen.
    """

    def test_returns_none_when_no_snapshot(self, facility):
        """Negativbranch: keine Row → None."""
        assert get_snapshot(facility, 2025, 1) is None

    def test_returns_data_dict_when_exists(self, facility):
        """Positivbranch: Row vorhanden → ``snap.data`` (nicht jugendamt_data)."""
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={"marker_data": "yes", "total_contacts": 5},
            jugendamt_data={"marker_jg": "yes", "total": 99},
        )
        result = get_snapshot(facility, 2025, 1)
        assert result is not None
        assert result.get("marker_data") == "yes", "get_snapshot muss .data zurückgeben, nicht .jugendamt_data"
        assert "marker_jg" not in result

    def test_other_year_returns_none(self, facility):
        """Year-Filter greift — Mutation ``year=year`` → ``year=year+1`` etc."""
        StatisticsSnapshot.objects.create(facility=facility, year=2025, month=1, data={"x": 1}, jugendamt_data={})
        assert get_snapshot(facility, 2024, 1) is None
        assert get_snapshot(facility, 2026, 1) is None

    def test_other_month_returns_none(self, facility):
        """Month-Filter greift."""
        StatisticsSnapshot.objects.create(facility=facility, year=2025, month=1, data={"x": 1}, jugendamt_data={})
        assert get_snapshot(facility, 2025, 2) is None


# ---------------------------------------------------------------------------
# _split_into_segments — Jahres-Übergang + Boundary
# ---------------------------------------------------------------------------


class TestSplitIntoSegmentsBoundaries:
    """Refs ``_split_into_segments`` (Line 104).

    Adressierte Mutationen:
    - ``cursor = date(year + 1, 1, 1) if month == 12 else
      date(year, month + 1, 1)`` — Jahres-Übergang Dec→Jan.
    - ``month == 12`` → ``month == 11`` würde am 11. Monat falsch wrappen.
    - ``year + 1`` → ``year`` oder ``year + 2`` würde Dec→Jan crashen oder springen.
    - ``is_full_month and is_past_month`` — beide Konjunkte einzeln.
    - ``seg_from == first_of_month and seg_to == last_of_month`` — beide.
    - ``(year, month) < current_ym`` → ``<=`` oder ``>``.
    """

    def test_december_to_january_year_rollover(self):
        """Dec→Jan Jahres-Übergang. Mutation ``year + 1`` → ``year`` würde
        in einer Endlosschleife oder falschen Segment-Sequenz enden.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 3, 15)
            segments = _split_into_segments(date(2025, 12, 1), date(2026, 1, 31))
        assert len(segments) == 2
        # Dezember 2025
        assert segments[0] == (date(2025, 12, 1), date(2025, 12, 31), True)
        # Januar 2026
        assert segments[1] == (date(2026, 1, 1), date(2026, 1, 31), True)

    def test_november_to_december_within_same_year(self):
        """Boundary: Nov→Dec wechselt NICHT das Jahr (month==11, kein Rollover).

        Mutation ``month == 12`` → ``month == 11`` würde hier fälschlich
        zu ``date(year+1, 1, 1)`` springen.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 3, 15)
            segments = _split_into_segments(date(2025, 11, 1), date(2025, 12, 31))
        assert len(segments) == 2
        # November 2025 ist erstes Segment
        assert segments[0] == (date(2025, 11, 1), date(2025, 11, 30), True)
        # Dezember 2025 ist zweites Segment (selbes Jahr)
        assert segments[1] == (date(2025, 12, 1), date(2025, 12, 31), True)

    def test_full_past_month_is_snapshot_eligible(self):
        """Beide Konjunkte True → use_snapshot True."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))
        assert segments[0][2] is True

    def test_full_current_month_not_snapshot(self):
        """``is_full_month=True`` aber ``is_past_month=False`` → False.

        Mutation ``and`` → ``or`` würde hier die ``True`` zurückgeben.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))
        assert segments[0][2] is False, "Current month darf nie use_snapshot=True bekommen"

    def test_partial_past_month_not_snapshot(self):
        """``is_past_month=True`` aber ``is_full_month=False`` → False.

        Auch das ``and`` würde durch ``or`` invertiert auf True flippen.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 5), date(2025, 3, 25))
        assert segments[0][2] is False

    def test_leap_year_february_full_month(self):
        """Schaltjahr-Februar: 1.–29.2.2024 ist full month.

        Mutation ``calendar.monthrange`` → 28 würde 29.2. ausschließen
        und full_month=False liefern.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 1, 15)
            segments = _split_into_segments(date(2024, 2, 1), date(2024, 2, 29))
        assert len(segments) == 1
        assert segments[0] == (date(2024, 2, 1), date(2024, 2, 29), True)

    def test_seg_to_capped_at_date_to(self):
        """``seg_to = min(last_of_month, date_to)`` — Cap am Range-Ende.

        Mutation ``min`` → ``max`` würde seg_to über date_to hinausschießen.
        """
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 15))
        # Halber Januar
        assert segments[0][1] == date(2025, 1, 15)
        # is_full_month=False, weil seg_to != last_of_month
        assert segments[0][2] is False

    def test_future_month_not_snapshot(self):
        """``is_past_month`` für future month False → use_snapshot False."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 6, 1), date(2025, 6, 30))
        assert segments[0][2] is False, "Future month darf nie use_snapshot=True bekommen"

    def test_single_day_range_correct_segment(self):
        """Range eines einzelnen Tages."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 17), date(2025, 3, 17))
        assert len(segments) == 1
        assert segments[0] == (date(2025, 3, 17), date(2025, 3, 17), False)

    def test_year_rollover_with_current_in_new_year(self):
        """Dec/Jan-Rollover, current=Feb des neuen Jahres → beide Monate past."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 2, 10)
            segments = _split_into_segments(date(2025, 12, 1), date(2026, 1, 31))
        assert segments[0] == (date(2025, 12, 1), date(2025, 12, 31), True)
        assert segments[1] == (date(2026, 1, 1), date(2026, 1, 31), True)

    def test_year_rollover_with_current_in_january_of_new_year(self):
        """Dec→Jan, current=Jan → Dec past, Jan current."""
        with patch("core.services.dashboard.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 1, 15)
            segments = _split_into_segments(date(2025, 12, 1), date(2026, 1, 31))
        # December 2025: full past month
        assert segments[0] == (date(2025, 12, 1), date(2025, 12, 31), True)
        # January 2026: current month → no snapshot
        assert segments[1] == (date(2026, 1, 1), date(2026, 1, 31), False)
