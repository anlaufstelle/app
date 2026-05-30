"""Follow-Up-Tests für Mutation-Survivors in ``core.services.snapshot``.

Refs Welle 7 (#930). Ziel: Mutmut-Survivors in den Snapshot-Helper-Funktionen
killen — Datums-Boundaries (Monats-/Jahres-/Schaltjahr-Übergänge),
Create-vs-Update-Branches, Aggregat-Counts pro Feld und die Cutoff-Logik
``snapshot vs. live`` im hybriden Statistik-Pfad.

Adressierte Mutationsklassen (84 Survivors):

1. ``is_multi_month_range`` (Line 18): Boundary ``(date_to - date_from).days > 31``.
   Mutationen ``> 31`` → ``>= 31``, ``> 30``, ``< 31`` werden mit exakt 31, 32
   und 30 Tagen abgedeckt.
2. ``create_or_update_snapshot`` (Line 33): ``calendar.monthrange``,
   ``date(year, month, 1)``, ``date(year, month, last_day)`` —
   Schaltjahr-Februar (29), Standard-Februar (28), 30/31-Tage-Monate.
   Plus: Create vs. Update via ``StatisticsSnapshot.objects.update_or_create``.
3. ``ensure_snapshots_for_months`` (Line 82): ``(year, month) < current``
   filter — Boundary "current_month wird ausgelassen" und "future_month wird
   ausgelassen".
4. ``get_snapshot`` (Line 93): ``snap.data if snap else None`` — beide
   Branches.
5. ``_split_into_segments`` (Line 104): Jahres-Übergang Dec→Jan (Line 133
   ``date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)``),
   ``is_full_month and is_past_month`` — beide Konjunkte einzeln.
6. ``_empty_stats`` / ``_empty_jugendamt_stats``: jedes Feld einzeln
   (``total_contacts``, ``unique_clients``, ``by_contact_stage["anonym"]`` etc.)
   damit Mutationen einzelner Keys/Defaults gefangen werden.
7. ``_merge_stats`` (Line 149): jede Aggregation pro Feld einzeln.
   ``+`` ↔ ``-`` Mutationen werden mit asymmetrischen Inputs verifiziert
   (1+2 = 3, nicht -1).
8. ``_merge_jugendamt_stats`` (Line 208): ``entry[0], entry[1]`` Tuple/List-
   Normalisierung; category_map-Sum.
9. ``get_statistics_hybrid`` (Line 248): Cutoff-Logik ``use_snapshot`` +
   Snapshot-Fallback bei fehlendem Snapshot. ``top_clients`` immer aus
   Live-Query.

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
from core.services.snapshot import (
    _empty_jugendamt_stats,
    _empty_stats,
    _merge_jugendamt_stats,
    _merge_stats,
    _split_into_segments,
    create_or_update_snapshot,
    ensure_snapshots_for_months,
    get_snapshot,
    get_statistics_hybrid,
    is_multi_month_range,
)

# ---------------------------------------------------------------------------
# is_multi_month_range — Boundary an 31 Tagen
# ---------------------------------------------------------------------------


class TestIsMultiMonthRange:
    """Refs Welle 7 — ``is_multi_month_range`` (Line 18).

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


def _make_event(facility, client, doc_type, user, dt, *, anonymous=False):
    aware_dt = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=aware_dt,
        data_json={},
        is_anonymous=anonymous,
        created_by=user,
    )


@pytest.mark.django_db
class TestCreateOrUpdateSnapshotBranches:
    """Refs Welle 7 — ``create_or_update_snapshot`` (Line 33).

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
    """Refs Welle 7 — ``ensure_snapshots_for_months`` (Line 82).

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
    """Refs Welle 7 — ``get_snapshot`` (Line 93).

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
    """Refs Welle 7 — ``_split_into_segments`` (Line 104).

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
        with patch("core.services.snapshot.timezone") as mock_tz:
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
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 3, 15)
            segments = _split_into_segments(date(2025, 11, 1), date(2025, 12, 31))
        assert len(segments) == 2
        # November 2025 ist erstes Segment
        assert segments[0] == (date(2025, 11, 1), date(2025, 11, 30), True)
        # Dezember 2025 ist zweites Segment (selbes Jahr)
        assert segments[1] == (date(2025, 12, 1), date(2025, 12, 31), True)

    def test_full_past_month_is_snapshot_eligible(self):
        """Beide Konjunkte True → use_snapshot True."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))
        assert segments[0][2] is True

    def test_full_current_month_not_snapshot(self):
        """``is_full_month=True`` aber ``is_past_month=False`` → False.

        Mutation ``and`` → ``or`` würde hier die ``True`` zurückgeben.
        """
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 3, 1), date(2025, 3, 31))
        assert segments[0][2] is False, "Current month darf nie use_snapshot=True bekommen"

    def test_partial_past_month_not_snapshot(self):
        """``is_past_month=True`` aber ``is_full_month=False`` → False.

        Auch das ``and`` würde durch ``or`` invertiert auf True flippen.
        """
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 5), date(2025, 3, 25))
        assert segments[0][2] is False

    def test_leap_year_february_full_month(self):
        """Schaltjahr-Februar: 1.–29.2.2024 ist full month.

        Mutation ``calendar.monthrange`` → 28 würde 29.2. ausschließen
        und full_month=False liefern.
        """
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 1, 15)
            segments = _split_into_segments(date(2024, 2, 1), date(2024, 2, 29))
        assert len(segments) == 1
        assert segments[0] == (date(2024, 2, 1), date(2024, 2, 29), True)

    def test_seg_to_capped_at_date_to(self):
        """``seg_to = min(last_of_month, date_to)`` — Cap am Range-Ende.

        Mutation ``min`` → ``max`` würde seg_to über date_to hinausschießen.
        """
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 1, 1), date(2025, 1, 15))
        # Halber Januar
        assert segments[0][1] == date(2025, 1, 15)
        # is_full_month=False, weil seg_to != last_of_month
        assert segments[0][2] is False

    def test_future_month_not_snapshot(self):
        """``is_past_month`` für future month False → use_snapshot False."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            segments = _split_into_segments(date(2025, 6, 1), date(2025, 6, 30))
        assert segments[0][2] is False, "Future month darf nie use_snapshot=True bekommen"

    def test_single_day_range_correct_segment(self):
        """Range eines einzelnen Tages."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            segments = _split_into_segments(date(2025, 3, 17), date(2025, 3, 17))
        assert len(segments) == 1
        assert segments[0] == (date(2025, 3, 17), date(2025, 3, 17), False)

    def test_year_rollover_with_current_in_new_year(self):
        """Dec/Jan-Rollover, current=Feb des neuen Jahres → beide Monate past."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 2, 10)
            segments = _split_into_segments(date(2025, 12, 1), date(2026, 1, 31))
        assert segments[0] == (date(2025, 12, 1), date(2025, 12, 31), True)
        assert segments[1] == (date(2026, 1, 1), date(2026, 1, 31), True)

    def test_year_rollover_with_current_in_january_of_new_year(self):
        """Dec→Jan, current=Jan → Dec past, Jan current."""
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2026, 1, 15)
            segments = _split_into_segments(date(2025, 12, 1), date(2026, 1, 31))
        # December 2025: full past month
        assert segments[0] == (date(2025, 12, 1), date(2025, 12, 31), True)
        # January 2026: current month → no snapshot
        assert segments[1] == (date(2026, 1, 1), date(2026, 1, 31), False)


# ---------------------------------------------------------------------------
# _empty_stats / _empty_jugendamt_stats — Field-by-Field
# ---------------------------------------------------------------------------


class TestEmptyStats:
    """Refs Welle 7 — ``_empty_stats`` (Line 138).

    Adressierte Mutationen: jedes Feld einzeln (Mutmut mutiert single keys/
    initial-Werte). Wir prüfen ALLE Keys + Defaults explizit.
    """

    def test_total_contacts_zero(self):
        assert _empty_stats()["total_contacts"] == 0

    def test_unique_clients_zero(self):
        assert _empty_stats()["unique_clients"] == 0

    def test_by_contact_stage_three_keys_zero(self):
        stage = _empty_stats()["by_contact_stage"]
        assert stage["anonym"] == 0
        assert stage["identifiziert"] == 0
        assert stage["qualifiziert"] == 0
        # Genau diese drei Keys, kein extra-Key
        assert set(stage.keys()) == {"anonym", "identifiziert", "qualifiziert"}

    def test_by_document_type_empty_list(self):
        assert _empty_stats()["by_document_type"] == []
        # Liste, nicht None — Aufrufer iteriert per for-loop
        assert isinstance(_empty_stats()["by_document_type"], list)

    def test_by_age_cluster_empty_list(self):
        assert _empty_stats()["by_age_cluster"] == []
        assert isinstance(_empty_stats()["by_age_cluster"], list)

    def test_returns_dict_with_exactly_five_top_level_keys(self):
        """Mutation würde einen Key droppen oder hinzufügen."""
        keys = set(_empty_stats().keys())
        assert keys == {
            "total_contacts",
            "by_contact_stage",
            "by_document_type",
            "by_age_cluster",
            "unique_clients",
        }


class TestEmptyJugendamtStats:
    """Refs Welle 7 — ``_empty_jugendamt_stats`` (Line 198)."""

    def test_total_zero(self):
        assert _empty_jugendamt_stats()["total"] == 0

    def test_unique_clients_zero(self):
        assert _empty_jugendamt_stats()["unique_clients"] == 0

    def test_by_category_empty_list(self):
        assert _empty_jugendamt_stats()["by_category"] == []
        assert isinstance(_empty_jugendamt_stats()["by_category"], list)

    def test_by_age_cluster_empty_list(self):
        assert _empty_jugendamt_stats()["by_age_cluster"] == []
        assert isinstance(_empty_jugendamt_stats()["by_age_cluster"], list)

    def test_returns_dict_with_exactly_four_top_level_keys(self):
        keys = set(_empty_jugendamt_stats().keys())
        assert keys == {"total", "by_category", "by_age_cluster", "unique_clients"}


# ---------------------------------------------------------------------------
# _merge_stats — Feld-für-Feld + Asymmetrie für +/-
# ---------------------------------------------------------------------------


class TestMergeStatsPerField:
    """Refs Welle 7 — ``_merge_stats`` (Line 149).

    Adressierte Mutationen:
    - ``+=`` → ``-=`` per Feld (total_contacts, unique_clients, stage-keys,
      doc_type count, age_cluster count).
    - ``.get(key, 0)`` → ``.get(key)`` würde None liefern und crashen.
    - ``stats_list = []`` → ``_empty_stats()`` (early return) prüfen.
    - ``key in doc_type_map`` → ``not in`` würde Doppel-Insert produzieren.
    - ``sorted(..., reverse=True)`` → ``reverse=False`` würde Sortierung
      invertieren.
    """

    def test_empty_list_returns_empty_stats(self):
        """``if not stats_list: return _empty_stats()`` — Early-Return.

        Mutation ``not`` → identity würde immer den loop laufen lassen
        (würde aber durch leeren Loop noch _empty_stats liefern). Aber
        Mutation ``_empty_stats()`` → ``{}`` würde fehlende keys liefern.
        """
        result = _merge_stats([])
        # Strukturell identisch zu _empty_stats
        assert result == _empty_stats()

    def test_total_contacts_summed(self):
        """``merged["total_contacts"] += stats.get("total_contacts", 0)``."""
        s1 = _empty_stats()
        s1["total_contacts"] = 3
        s2 = _empty_stats()
        s2["total_contacts"] = 7
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 10

    def test_total_contacts_asymmetric_inputs(self):
        """Asymmetrische Inputs (1+2 != 2-1 != 1-2 != -1).

        Mutmut mutiert ``+=`` → ``-=``: würde 1-2 = -1 liefern statt 3.
        Mutmut mutiert ``=`` (initial 0) → 1: würde 4 liefern statt 3.
        """
        s1 = _empty_stats()
        s1["total_contacts"] = 1
        s2 = _empty_stats()
        s2["total_contacts"] = 2
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 3, "1 + 2 = 3 (nicht -1, nicht 1, nicht 2)"

    def test_unique_clients_summed_separately_from_total(self):
        """``unique_clients`` darf NICHT mit ``total_contacts`` getauscht sein."""
        s1 = _empty_stats()
        s1["total_contacts"] = 5
        s1["unique_clients"] = 3
        s2 = _empty_stats()
        s2["total_contacts"] = 10
        s2["unique_clients"] = 7
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 15
        assert result["unique_clients"] == 10
        # Sanity: nicht vertauscht
        assert result["total_contacts"] != result["unique_clients"]

    def test_anonym_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["anonym"] = 2
        s2 = _empty_stats()
        s2["by_contact_stage"]["anonym"] = 3
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 5

    def test_identifiziert_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["identifiziert"] = 4
        s2 = _empty_stats()
        s2["by_contact_stage"]["identifiziert"] = 1
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["identifiziert"] == 5

    def test_qualifiziert_summed_per_key(self):
        s1 = _empty_stats()
        s1["by_contact_stage"]["qualifiziert"] = 6
        s2 = _empty_stats()
        s2["by_contact_stage"]["qualifiziert"] = 0
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["qualifiziert"] == 6

    def test_three_stages_summed_independently(self):
        """Mutation in der Stage-Loop (``for key in (...)``) würde Keys droppen."""
        s1 = _empty_stats()
        s1["by_contact_stage"] = {"anonym": 1, "identifiziert": 2, "qualifiziert": 3}
        s2 = _empty_stats()
        s2["by_contact_stage"] = {"anonym": 10, "identifiziert": 20, "qualifiziert": 30}
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 11
        assert result["by_contact_stage"]["identifiziert"] == 22
        assert result["by_contact_stage"]["qualifiziert"] == 33

    def test_doc_type_composite_key_merge(self):
        """Composite-Key ``(name, category)``. Mutation ``entry["name"],
        entry["category"]`` → nur name würde Kategorien zusammenwerfen.
        """
        s1 = _empty_stats()
        s1["by_document_type"] = [{"name": "Kontakt", "category": "contact", "count": 2}]
        s2 = _empty_stats()
        s2["by_document_type"] = [
            {"name": "Kontakt", "category": "contact", "count": 3},
            {"name": "Kontakt", "category": "service", "count": 5},  # andere category
        ]
        result = _merge_stats([s1, s2])
        # Same composite key merged
        by_dt = {(e["name"], e["category"]): e["count"] for e in result["by_document_type"]}
        assert by_dt[("Kontakt", "contact")] == 5
        # Different category-key stays separate
        assert by_dt[("Kontakt", "service")] == 5

    def test_doc_type_first_occurrence_creates_entry(self):
        """``else: doc_type_map[composite] = {**entry}`` — neue Composite-Keys
        landen mit shallow-copy im Result."""
        s1 = _empty_stats()
        s1["by_document_type"] = [{"name": "Neu", "category": "cat", "count": 7}]
        result = _merge_stats([s1])
        assert len(result["by_document_type"]) == 1
        assert result["by_document_type"][0]["count"] == 7

    def test_doc_type_sorted_desc_by_count(self):
        """``sorted(..., reverse=True)``. Mutation ``reverse=False`` würde
        die kleinsten zuerst liefern."""
        s1 = _empty_stats()
        s1["by_document_type"] = [
            {"name": "Klein", "category": "x", "count": 1},
            {"name": "Gross", "category": "y", "count": 100},
            {"name": "Mittel", "category": "z", "count": 10},
        ]
        result = _merge_stats([s1])
        counts = [e["count"] for e in result["by_document_type"]]
        assert counts == [100, 10, 1]

    def test_age_cluster_merged_by_cluster_key(self):
        """``cluster = entry["cluster"]`` — Composite ist nur ``cluster``."""
        s1 = _empty_stats()
        s1["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 2}]
        s2 = _empty_stats()
        s2["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 3}]
        result = _merge_stats([s1, s2])
        assert len(result["by_age_cluster"]) == 1
        assert result["by_age_cluster"][0]["count"] == 5

    def test_age_cluster_sorted_desc_by_count(self):
        s1 = _empty_stats()
        s1["by_age_cluster"] = [
            {"cluster": "u18", "label": "Unter 18", "count": 2},
            {"cluster": "18_26", "label": "18–26", "count": 8},
            {"cluster": "27_plus", "label": "27+", "count": 5},
        ]
        result = _merge_stats([s1])
        counts = [e["count"] for e in result["by_age_cluster"]]
        assert counts == [8, 5, 2]

    def test_get_with_default_zero_handles_missing_keys(self):
        """``.get("total_contacts", 0)`` — Default 0. Mutation ``.get(key)``
        ohne Default würde bei fehlendem Key None liefern → TypeError beim ``+=``.
        """
        s1 = {}  # völlig leeres dict — nichts da
        s2 = _empty_stats()
        s2["total_contacts"] = 5
        # darf nicht crashen
        result = _merge_stats([s1, s2])
        assert result["total_contacts"] == 5

    def test_get_by_contact_stage_default_handles_missing_subkey(self):
        """``stats.get("by_contact_stage", {}).get(key, 0)`` —
        beide Defaults essenziell."""
        s1 = {"total_contacts": 0}  # kein by_contact_stage
        s2 = _empty_stats()
        s2["by_contact_stage"]["anonym"] = 7
        result = _merge_stats([s1, s2])
        assert result["by_contact_stage"]["anonym"] == 7


# ---------------------------------------------------------------------------
# _merge_jugendamt_stats — Tupel/List-Normalisierung + Aggregation
# ---------------------------------------------------------------------------


class TestMergeJugendamtStatsPerField:
    """Refs Welle 7 — ``_merge_jugendamt_stats`` (Line 208).

    Adressierte Mutationen:
    - ``total += stats.get("total", 0)``  → ``-=``.
    - ``entry[0], entry[1]`` → ``entry[1], entry[0]`` würde Name und Count
      vertauschen.
    - ``category_map.get(name, 0) + count`` → ``- count`` würde subtrahieren.
    - List-Comprehension ``[(name, count) ...]`` würde Tupel/List-Form mutieren.
    """

    def test_empty_list_returns_empty_jugendamt_stats(self):
        result = _merge_jugendamt_stats([])
        assert result == _empty_jugendamt_stats()

    def test_total_summed(self):
        s1 = _empty_jugendamt_stats()
        s1["total"] = 3
        s2 = _empty_jugendamt_stats()
        s2["total"] = 4
        result = _merge_jugendamt_stats([s1, s2])
        assert result["total"] == 7

    def test_total_asymmetric_inputs(self):
        s1 = _empty_jugendamt_stats()
        s1["total"] = 2
        s2 = _empty_jugendamt_stats()
        s2["total"] = 5
        result = _merge_jugendamt_stats([s1, s2])
        assert result["total"] == 7, "2+5=7, nicht -3, nicht 5, nicht 2"

    def test_unique_clients_summed(self):
        s1 = _empty_jugendamt_stats()
        s1["unique_clients"] = 4
        s2 = _empty_jugendamt_stats()
        s2["unique_clients"] = 6
        result = _merge_jugendamt_stats([s1, s2])
        assert result["unique_clients"] == 10

    def test_by_category_tuple_inputs(self):
        """Tupel-Inputs: ``("Kontakte", 5)``."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 5)]
        result = _merge_jugendamt_stats([s1])
        # Output ist immer list[tuple]
        assert result["by_category"] == [("Kontakte", 5)]

    def test_by_category_list_inputs(self):
        """List-Inputs (so kommen sie aus JSON-Snapshots zurück)."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [["Beratung", 3]]
        result = _merge_jugendamt_stats([s1])
        # Output ist tuple — Mutation ``[0], [1]`` → ``[1], [0]`` würde
        # ``(3, "Beratung")`` liefern.
        assert result["by_category"] == [("Beratung", 3)]

    def test_by_category_merge_same_name_sums_counts(self):
        """Same name in zwei Snapshots → Counts summieren.

        Mutation ``category_map.get(name, 0) + count`` → ``- count`` würde
        2 - 3 = -1 liefern statt 5.
        """
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 2)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [("Kontakte", 3)]
        result = _merge_jugendamt_stats([s1, s2])
        cats = dict(result["by_category"])
        assert cats["Kontakte"] == 5

    def test_by_category_distinct_names_preserved(self):
        """Verschiedene Kategorien bleiben separat."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("Kontakte", 2), ("Beratung", 1)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [("Vermittlung", 4)]
        result = _merge_jugendamt_stats([s1, s2])
        cats = dict(result["by_category"])
        assert cats == {"Kontakte": 2, "Beratung": 1, "Vermittlung": 4}

    def test_by_category_tuple_and_list_mixed_normalize_to_tuple(self):
        """Tuple + List mixed input → beide werden im Output zu tuple."""
        s1 = _empty_jugendamt_stats()
        s1["by_category"] = [("X", 1)]
        s2 = _empty_jugendamt_stats()
        s2["by_category"] = [["X", 2]]
        result = _merge_jugendamt_stats([s1, s2])
        # X-Summe 3, als tuple
        assert ("X", 3) in result["by_category"]

    def test_age_cluster_merged(self):
        s1 = _empty_jugendamt_stats()
        s1["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 2}]
        s2 = _empty_jugendamt_stats()
        s2["by_age_cluster"] = [{"cluster": "18_26", "label": "18–26", "count": 4}]
        result = _merge_jugendamt_stats([s1, s2])
        assert len(result["by_age_cluster"]) == 1
        assert result["by_age_cluster"][0]["count"] == 6

    def test_age_cluster_sorted_desc(self):
        s1 = _empty_jugendamt_stats()
        s1["by_age_cluster"] = [
            {"cluster": "a", "label": "A", "count": 1},
            {"cluster": "b", "label": "B", "count": 10},
            {"cluster": "c", "label": "C", "count": 5},
        ]
        result = _merge_jugendamt_stats([s1])
        counts = [e["count"] for e in result["by_age_cluster"]]
        assert counts == [10, 5, 1]


# ---------------------------------------------------------------------------
# get_statistics_hybrid — Cutoff snapshot vs live + top_clients-Branch
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetStatisticsHybridCutoff:
    """Refs Welle 7 — ``get_statistics_hybrid`` (Line 248).

    Adressierte Mutationen:
    - ``if use_snapshot: stats = get_snapshot(...)`` — Conditional kippt.
    - ``if stats is None: stats = get_statistics(...)`` — Fallback bei
      fehlendem Snapshot.
    - ``stats.pop("top_clients", None)`` im Segment-Loop.
    - ``merged["top_clients"] = live_full["top_clients"]`` —
      top_clients IMMER live, nie aus Snapshot.
    """

    def test_uses_snapshot_when_use_snapshot_true_and_snapshot_exists(self, facility):
        """Snapshot-Branch greift bei use_snapshot=True UND vorhandenem Snapshot.

        Mutation ``if use_snapshot`` → ``if not use_snapshot`` würde nie
        den Snapshot nutzen.
        """
        # Snapshot mit Marker, der nicht aus Live-Query stammen kann
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 999,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 999,
            },
            jugendamt_data={},
        )
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        # Live-Query hätte 0 ergeben — wir lesen 999 aus Snapshot
        assert result["total_contacts"] == 999

    def test_fallback_to_live_when_snapshot_missing(self, facility, staff_user, client_identified, doc_type_contact):
        """``if stats is None: stats = get_statistics(...)`` Fallback-Branch.

        Mutation ``if stats is None`` → ``if stats is not None`` würde
        diesen Branch kippen.
        """
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        # KEIN Snapshot vorhanden
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        assert result["total_contacts"] == 1

    def test_current_month_uses_live_ignoring_snapshot(self, facility, staff_user, client_identified, doc_type_contact):
        """Cutoff: aktueller Monat IMMER live, Snapshot ignoriert."""
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=3,
            data={
                "total_contacts": 999,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 999,
            },
            jugendamt_data={},
        )
        mar = datetime(2025, 3, 5, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 3, 1), date(2025, 3, 31))
        assert result["total_contacts"] == 1, "Current month muss live sein, nicht 999 aus Snapshot"

    def test_top_clients_always_from_live_full_range(self, facility, staff_user, client_identified, doc_type_contact):
        """``merged["top_clients"] = live_full["top_clients"]``.

        Mutation ``live_full["top_clients"]`` → ``[]`` würde leere Liste
        liefern.
        """
        # Snapshot ohne top_clients (so wird er bei create_or_update_snapshot
        # bewusst gespeichert)
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 0,
                "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 0,
            },
            jugendamt_data={},
        )
        # Event in Januar
        jan = datetime(2025, 1, 15, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, jan)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        # top_clients muss im Result-Dict landen (Key-Existenz)
        assert "top_clients" in result, "top_clients muss vom live_full immer in den merged-Dict gesetzt werden"

    def test_segment_pop_top_clients_does_not_crash(self, facility, staff_user, client_identified, doc_type_contact):
        """``stats.pop("top_clients", None)`` Segment-Branch.

        Mutation ``pop("top_clients", None)`` → ``pop("top_clients")``
        (ohne default) würde KeyError werfen, falls Snapshot keinen Key hat.
        """
        # Snapshot ohne top_clients (Normalfall)
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 5,
                "by_contact_stage": {"anonym": 0, "identifiziert": 5, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 3,
                # KEIN top_clients-Key (so wie create_or_update_snapshot speichert)
            },
            jugendamt_data={},
        )
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 6, 15)
            # darf nicht crashen
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        assert result["total_contacts"] == 5

    def test_merged_combines_snapshot_and_live_month_correctly(
        self, facility, staff_user, client_identified, doc_type_contact
    ):
        """Range über mehrere Monate: Snapshot-Monat + Live-Monat sauber addiert.

        Mutation an ``segment_stats.append(stats)`` (z.B. ``= [stats]``)
        würde nur das letzte Segment behalten.
        """
        # Snapshot für Januar mit 5 Kontakten
        StatisticsSnapshot.objects.create(
            facility=facility,
            year=2025,
            month=1,
            data={
                "total_contacts": 5,
                "by_contact_stage": {"anonym": 0, "identifiziert": 5, "qualifiziert": 0},
                "by_document_type": [],
                "by_age_cluster": [],
                "unique_clients": 5,
            },
            jugendamt_data={},
        )
        # Live-Event in März (current month bei localdate=2025-03-15)
        mar = datetime(2025, 3, 10, 10, 0)
        _make_event(facility, client_identified, doc_type_contact, staff_user, mar)
        with patch("core.services.snapshot.timezone") as mock_tz:
            mock_tz.localdate.return_value = date(2025, 3, 15)
            result = get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 3, 31))
        # 5 (Jan-Snapshot) + 0 (Feb live, leer) + 1 (Mar live) = 6
        assert result["total_contacts"] == 6
