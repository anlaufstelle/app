"""Tests für Concurrency-Schutz von ``update_workitem_status`` (Refs #129 Teil A, Refs #733).

Verifiziert:
- Idempotenz: zweimaliges Setzen desselben Status erzeugt KEINEN zweiten
  Activity-Eintrag und veraendert ``completed_at`` nicht erneut.
- ``select_for_update()`` + ``@transaction.atomic`` vermeiden, dass zwei
  zeitgleiche Requests denselben ``old_status`` lesen und beide einen
  Recurrence-Folge-WorkItem oder doppelte Activity-Eintraege erzeugen.
"""

import threading

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections, connection

from core.models import Activity, WorkItem
from core.services.workitems import update_workitem_status


def _count_completed(workitem):
    ct = ContentType.objects.get_for_model(WorkItem)
    return Activity.objects.filter(
        facility=workitem.facility,
        target_type=ct,
        target_id=workitem.pk,
        verb=Activity.Verb.COMPLETED,
    ).count()


@pytest.fixture
def workitem_done(facility, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        created_by=staff_user,
        title="Bereits erledigt",
        status=WorkItem.Status.DONE,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.mark.django_db
class TestUpdateWorkitemStatusIdempotency:
    """Wiederholtes Setzen desselben Status ist No-op."""

    def test_repeated_done_does_not_duplicate_activity(self, facility, staff_user, workitem_done):
        before = _count_completed(workitem_done)
        # Erster Aufruf — Status ist schon DONE, also Idempotenz-Guard greift.
        update_workitem_status(workitem_done, WorkItem.Status.DONE, staff_user)
        update_workitem_status(workitem_done, WorkItem.Status.DONE, staff_user)
        after = _count_completed(workitem_done)
        assert after == before, (
            "Idempotenz-Guard muss verhindern, dass wiederholte DONE-Aufrufe "
            "einen weiteren COMPLETED-Activity-Eintrag erzeugen."
        )

    def test_repeated_done_does_not_modify_completed_at(self, facility, staff_user, workitem_done):
        original_completed_at = workitem_done.completed_at
        update_workitem_status(workitem_done, WorkItem.Status.DONE, staff_user)
        workitem_done.refresh_from_db()
        assert workitem_done.completed_at == original_completed_at, (
            "completed_at darf bei Idempotenz-Guard nicht neu gesetzt werden."
        )


@pytest.mark.django_db(transaction=True)
class TestUpdateWorkitemStatusRace:
    """Concurrency: zwei zeitgleiche Status-Updates auf dasselbe WorkItem.

    Wir nutzen zwei Threads mit eigenen DB-Connections. ``select_for_update()``
    serialisiert die beiden Aufrufe — Thread B wartet, bis Thread A sein
    COMMIT erledigt hat. Anschliessend sieht Thread B den neuen Status und
    der Idempotenz-Guard verhindert Doppelarbeit.
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("select_for_update erfordert PostgreSQL")

    def test_concurrent_done_results_in_single_activity(self, facility, staff_user):
        wi = WorkItem.objects.create(
            facility=facility,
            created_by=staff_user,
            title="Race-Aufgabe",
            status=WorkItem.Status.OPEN,
            priority=WorkItem.Priority.NORMAL,
        )

        errors = []

        def worker():
            try:
                update_workitem_status(wi, WorkItem.Status.DONE, staff_user)
            except Exception as exc:  # pragma: no cover — Sammeltest
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Worker-Fehler: {errors!r}"

        wi.refresh_from_db()
        assert wi.status == WorkItem.Status.DONE

        completed_count = _count_completed(wi)
        assert completed_count == 1, (
            f"Bei zwei zeitgleichen DONE-Updates darf nur EIN COMPLETED-Activity-"
            f"Eintrag entstehen — gefunden: {completed_count}"
        )
