"""Optimistic-Locking-Tests (Refs #591 WP2).

Bündelt Service-übergreifende Locking-Tests, die nicht klar zu einem
einzelnen Model gehören:

- Settings-Service (Conflict + Success)
- Timezone-Edge-Case gegen ``check_version_conflict``
- Sequenzielles "Race" (deterministisch, kein Thread)

Das eigentliche Locking liegt in :mod:`core.services.locking` und
vergleicht den geparsten ``datetime``-Instant gegen den vom Client
mitgelieferten Wert (Refs #595).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, WorkItem
from core.services.locking import check_version_conflict
from core.services.settings import update_settings
from core.services.workitems import update_workitem


@pytest.mark.django_db
class TestSettingsOptimisticLocking:
    """Locking auf ``update_settings`` (Refs #531).

    Der Settings-Service akzeptiert ``expected_updated_at`` — siehe
    :file:`src/core/services/settings.py`. Bestätigt am Code (Parameter
    ist Keyword-only, wird an ``check_version_conflict`` durchgereicht).
    """

    def test_conflict_with_stale_timestamp_raises_validation_error(self, settings_obj, staff_user):
        stale = "2000-01-01T00:00:00+00:00"
        before = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        with pytest.raises(ValidationError):
            update_settings(
                settings_obj,
                staff_user,
                expected_updated_at=stale,
                session_timeout_minutes=999,
            )
        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes != 999
        # Kein Audit-Log bei Konflikt (Transaktion + Raise vor dem Save).
        after = AuditLog.objects.filter(action=AuditLog.Action.SETTINGS_CHANGE).count()
        assert after == before

    def test_success_with_current_timestamp(self, settings_obj, staff_user):
        settings_obj.refresh_from_db()
        current = settings_obj.updated_at.isoformat()
        update_settings(
            settings_obj,
            staff_user,
            expected_updated_at=current,
            session_timeout_minutes=42,
        )
        settings_obj.refresh_from_db()
        assert settings_obj.session_timeout_minutes == 42


@pytest.mark.django_db
class TestLockingTimezoneEdgeCases:
    """Edge-Cases im Umgang mit Timezone-Varianten von ``updated_at``.

    ``check_version_conflict`` normalisiert beide Seiten zu
    ``datetime``-Instants (Refs #595). Damit ist der Vergleich
    offset-unabhängig — ein semantisch identischer Timestamp mit anderem
    Offset wird korrekt als "kein Konflikt" erkannt.
    """

    def test_exact_utc_iso_string_passes(self, sample_workitem):
        """Ein Byte-identischer UTC-ISO-String darf keinen Konflikt werfen."""
        sample_workitem.refresh_from_db()
        iso_utc = sample_workitem.updated_at.isoformat()
        # Darf nicht raisen.
        check_version_conflict(sample_workitem, iso_utc)

    def test_same_instant_different_offset_should_not_conflict(self, sample_workitem):
        """Gleicher Instant mit ``+01:00``-Offset sollte kein Konflikt sein."""
        import zoneinfo

        sample_workitem.refresh_from_db()
        berlin = zoneinfo.ZoneInfo("Europe/Berlin")
        shifted = sample_workitem.updated_at.astimezone(berlin)
        # zoneinfo liefert ggf. +01:00 (Winterzeit) oder +02:00 (Sommerzeit) —
        # beides ist derselbe Instant, nur anders formatiert.
        iso_shifted = shifted.isoformat()
        assert iso_shifted != sample_workitem.updated_at.isoformat()
        # Semantischer Vergleich → kein Raise (Refs #595).
        check_version_conflict(sample_workitem, iso_shifted)

    def test_none_disables_check(self, sample_workitem):
        """``None``/empty dürfen nie einen Konflikt auslösen."""
        check_version_conflict(sample_workitem, None)
        check_version_conflict(sample_workitem, "")


@pytest.mark.django_db
class TestSequentialRace:
    """Deterministische "Race"-Simulation ohne Threads.

    Beide Aufrufe nutzen denselben ``expected_updated_at``. Der erste
    erfolgt, der zweite muss als Konflikt scheitern, weil der Server-Wert
    nach dem ersten Save bereits fortgeschritten ist.
    """

    def test_second_update_with_stale_timestamp_conflicts(self, facility, client_identified, staff_user):
        # Zwei separate WorkItems, aber derselbe Flow pro Item:
        item_a = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="A",
        )
        item_b = WorkItem.objects.create(
            facility=facility,
            client=client_identified,
            created_by=staff_user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            title="B",
        )

        # Beide "Clients" lesen gleichzeitig:
        ts_a = item_a.updated_at.isoformat()
        ts_b = item_b.updated_at.isoformat()

        # Erster Aufruf pro Item: success.
        update_workitem(item_a, staff_user, title="A-first", expected_updated_at=ts_a)
        update_workitem(item_b, staff_user, title="B-first", expected_updated_at=ts_b)

        # Zweiter Aufruf pro Item mit dem ALTEN Timestamp: Conflict.
        with pytest.raises(ValidationError):
            update_workitem(item_a, staff_user, title="A-second", expected_updated_at=ts_a)
        with pytest.raises(ValidationError):
            update_workitem(item_b, staff_user, title="B-second", expected_updated_at=ts_b)

        # Erster Save ist persistiert, zweiter nicht.
        item_a.refresh_from_db()
        item_b.refresh_from_db()
        assert item_a.title == "A-first"
        assert item_b.title == "B-first"

    def test_sequential_race_on_same_workitem(self, sample_workitem, staff_user):
        """Klassisches Race-Szenario: 2 Clients lesen dasselbe Item zur gleichen Zeit."""
        sample_workitem.refresh_from_db()
        initial_ts = sample_workitem.updated_at.isoformat()

        # Client 1 committed zuerst.
        update_workitem(sample_workitem, staff_user, title="Erster", expected_updated_at=initial_ts)

        # Client 2 hat noch den alten Timestamp in seinem Formular.
        with pytest.raises(ValidationError):
            update_workitem(
                sample_workitem,
                staff_user,
                title="Zweiter (stale)",
                expected_updated_at=initial_ts,
            )

        sample_workitem.refresh_from_db()
        assert sample_workitem.title == "Erster"
