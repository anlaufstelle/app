"""Optimistic-Locking-Tests (Refs #591 WP2, #1338).

Bündelt Service-übergreifende Locking-Tests, die nicht klar zu einem
einzelnen Model gehören:

- Settings-Service (Conflict + Success)
- Timezone-Edge-Case gegen ``check_version_conflict``
- Sequenzielles "Race" (deterministisch, kein Thread)
- Token-Pflicht (``require_token``) + Error-Codes (Refs #1338)
- Echtes Thread-Race gegen ``update_event`` (Refs #1338)

Das eigentliche Locking liegt in :mod:`core.services.security.locking` und
vergleicht den geparsten ``datetime``-Instant gegen den vom Client
mitgelieferten Wert (Refs #595). Seit #1338 laeuft der Read unter
``select_for_update()`` -- Direktaufrufe von ``check_version_conflict`` mit
einem nicht-leeren ``expected_updated_at`` muessen deshalb in
``transaction.atomic()`` erfolgen (sonst ``TransactionManagementError``).
Service-Aufrufe (``update_workitem``, ``update_settings``, ``update_event``, …)
sind bereits ``@transaction.atomic`` und brauchen keinen zusaetzlichen Wrap.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction

from core.models import AuditLog, Event, WorkItem
from core.services.case import update_workitem
from core.services.events import update_event
from core.services.security import check_version_conflict
from core.services.system import update_settings


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
        # Refs #1338: select_for_update() im Direktaufruf braucht eine
        # umgebende Transaktion (sonst TransactionManagementError). Das
        # Verhalten der Funktion selbst aendert sich durch den Wrap nicht.
        with transaction.atomic():
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
        # Refs #1338: siehe Kommentar oben (select_for_update braucht atomic).
        with transaction.atomic():
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


@pytest.mark.django_db
class TestRequireVersionToken:
    """Token-Pflicht fuer JSON-/Offline-Replay-Clients (Refs #1338).

    ``require_token=False`` (Default) aendert nichts am bisherigen No-Op-
    Verhalten fuer leere/fehlende Tokens -- siehe ``test_none_disables_
    check`` oben, die unveraendert gruen bleibt. ``require_token=True``
    macht denselben leeren/fehlenden Wert zu einem expliziten Fehler, damit
    ein JSON-/Offline-Replay-Edit nie mehr als stilles Last-Write-Wins
    durchgeht (K3).
    """

    def test_require_token_true_with_none_raises_missing_token(self, sample_workitem):
        with pytest.raises(ValidationError) as exc_info:
            check_version_conflict(sample_workitem, None, require_token=True)
        assert exc_info.value.code == "missing_token"

    def test_require_token_true_with_empty_string_raises_missing_token(self, sample_workitem):
        with pytest.raises(ValidationError) as exc_info:
            check_version_conflict(sample_workitem, "", require_token=True)
        assert exc_info.value.code == "missing_token"

    def test_require_token_true_with_valid_token_passes(self, sample_workitem):
        """Ein korrekter Token loest trotz ``require_token=True`` keinen
        Fehler aus -- die Pflicht betrifft nur den leeren/fehlenden Fall."""
        sample_workitem.refresh_from_db()
        current = sample_workitem.updated_at.isoformat()
        with transaction.atomic():
            # Darf nicht raisen.
            check_version_conflict(sample_workitem, current, require_token=True)

    def test_require_token_default_is_false(self, sample_workitem):
        """Regressionsschutz #1338: ohne explizites ``require_token`` bleibt
        das alte No-Op-Verhalten fuer leere/fehlende Tokens erhalten -- die
        vier unveraenderten Aufrufer (``update_case``, ``update_settings``,
        ``update_client``, ``update_workitem``) verlassen sich darauf."""
        check_version_conflict(sample_workitem, None)
        check_version_conflict(sample_workitem, "")


@pytest.mark.django_db
class TestCheckVersionConflictErrorCodes:
    """``ValidationError.code`` ist der Vertrag, über den ``EventUpdateView``
    zwischen "Token fehlt", "Token kaputt" und "echter Konflikt"
    unterscheidet (Refs #1338)."""

    def test_conflict_error_has_version_conflict_code(self, sample_workitem):
        sample_workitem.refresh_from_db()
        stale = "2000-01-01T00:00:00+00:00"
        with transaction.atomic():
            with pytest.raises(ValidationError) as exc_info:
                check_version_conflict(sample_workitem, stale)
        assert exc_info.value.code == "version_conflict"

    def test_invalid_token_raises_invalid_token_code_not_valueerror(self, sample_workitem):
        """Ein nicht-ISO-parsebarer Token darf keinen ungefangenen
        ``ValueError`` mehr auslösen (500 beim Aufrufer) — Refs #1338."""
        with transaction.atomic():
            with pytest.raises(ValidationError) as exc_info:
                check_version_conflict(sample_workitem, "nicht-iso")
        assert exc_info.value.code == "invalid_token"


@pytest.mark.django_db(transaction=True)
class TestUpdateEventConcurrentRace:
    """Race-Repro fuer den TOCTOU-Bug in ``check_version_conflict`` (Refs #1338).

    Dieser Test ist gegen den heutigen Code (ohne ``select_for_update``) ROT:
    ``check_version_conflict`` liest ``updated_at`` mit einem einfachen
    ``SELECT`` ohne Row-Lock. Zwei zeitgleiche ``update_event``-Aufrufe mit
    demselben Token T0 lesen deshalb BEIDE denselben (noch aktuellen) Wert,
    bestehen BEIDE den Konflikt-Check und committen BEIDE erfolgreich — die
    zuerst geschriebene Aenderung wird von der zweiten still ueberschrieben
    (Lost Update), ohne dass irgendjemand eine ``ValidationError`` sieht.

    Aufbau: Thread 1 wird ueber einen Patch auf ``Event.save`` NACH dem
    Konflikt-Check (aber VOR dem eigentlichen Schreiben) pausiert. Waehrend
    der Pause durchlaeuft Thread 2 sein komplettes ``update_event`` mit
    demselben T0. Danach wird Thread 1 freigegeben.

    - Heute (ungefixt): Thread 2 liest T0 == DB-Wert (Thread 1 hat noch
      nichts geschrieben) → kein Konflikt, Thread 2 committet. Thread 1
      schreibt anschliessend ungehindert drueber (Lost Update). Ergebnis:
      NULL Konflikte, obwohl zwei Schreiber denselben Stand sahen.
    - Nach dem ``select_for_update``-Fix: Thread 1 haelt den Row-Lock ab dem
      Konflikt-Check bis zu seinem Commit. Thread 2s eigener
      ``select_for_update()``-Aufruf blockiert deshalb, bis Thread 1
      committet hat, und sieht danach den fortgeschrittenen Wert → Konflikt.
      Ergebnis: GENAU EIN Konflikt.

    Refs #1338.
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("select_for_update erfordert PostgreSQL")

    def test_concurrent_update_with_same_token_exactly_one_conflicts(self, sample_event, staff_user):
        sample_event.refresh_from_db()
        event_pk = sample_event.pk
        t0 = sample_event.updated_at.isoformat()

        barrier = threading.Barrier(2)
        results = {}
        real_save = Event.save

        def patched_save(self_evt, *args, **kwargs):
            # Pausiert NUR Thread 1 (racer-1) -- NACH check_version_conflict,
            # VOR dem eigentlichen Schreiben. Thread 2 (racer-2) ist von
            # diesem Hook unberuehrt und laeuft ungebremst durch.
            if self_evt.pk == event_pk and threading.current_thread().name == "racer-1":
                barrier.wait(timeout=5)  # Runde 1: "Thread 1 ist pausiert"
                barrier.wait(timeout=5)  # Runde 2: "Thread 1 darf weiter"
            return real_save(self_evt, *args, **kwargs)

        def worker(name, notiz):
            try:
                event_local = Event.objects.get(pk=event_pk)
                update_event(event_local, staff_user, {"dauer": 1, "notiz": notiz}, expected_updated_at=t0)
                results[name] = "ok"
            except ValidationError as exc:
                results[name] = exc.code or "error-ohne-code"
            except Exception as exc:  # pragma: no cover - Sammeltest fuer Diagnose
                results[name] = f"unexpected: {exc!r}"
            finally:
                connections.close_all()

        with mock.patch.object(Event, "save", patched_save):
            t1 = threading.Thread(target=worker, args=("racer-1", "von-thread-1"), name="racer-1")
            t2 = threading.Thread(target=worker, args=("racer-2", "von-thread-2"), name="racer-2")

            t1.start()
            barrier.wait(timeout=5)  # warten, bis Thread 1 pausiert ist

            t2.start()
            # Vorsprung fuer Thread 2: ungefixt laeuft er ungehindert komplett
            # durch (inkl. Commit) und dieser Join kehrt sofort zurueck.
            # Gefixt blockiert er im Row-Lock von Thread 1 -- der Join laeuft
            # in den Timeout, Thread 2 bleibt aber am Leben und wird unten
            # nach der Freigabe von Thread 1 fertig eingesammelt.
            t2.join(timeout=1.0)

            barrier.wait(timeout=5)  # Thread 1 freigeben
            t1.join(timeout=10)
            t2.join(timeout=10)

        assert not t1.is_alive(), "Thread 1 haengt (Barrier-Timeout?)"
        assert not t2.is_alive(), "Thread 2 haengt (Row-Lock nie freigegeben?)"

        assert results.get("racer-1") == "ok", f"Thread 1 (zuerst) darf nie in Konflikt laufen: {results!r}"
        conflicts = [name for name, outcome in results.items() if outcome == "version_conflict"]
        assert len(conflicts) == 1, (
            f"Erwartet GENAU EINEN version_conflict bei gleichzeitigem Update "
            f"mit demselben Token -- bekommen: {results!r}"
        )
