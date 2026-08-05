"""Tests fuer ``core.services.case.workitems`` und seine internen Helper.

Enthaelt die Cluster (Split aus ``test_workitems.py``):

* ``TestUpdateWorkItemService`` — Public-API ``update_workitem`` Service.
* ``TestApplyStatusTransitionHelper`` — Refs #906: ``_apply_status_transition``.
* ``TestMaybeDuplicateRecurringHelper`` — Refs #906: ``_maybe_duplicate_recurring``.

Fixture ``workitem_open`` ist inline gehalten (kopiert aus dem Original-File),
weil pytest File-spezifische Fixtures nicht ueber Imports entdeckt.
"""

import pytest

from core.models import WorkItem
from core.models.activity import Activity
from core.services.case import update_workitem


@pytest.fixture
def workitem_open(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        title="Offene Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.fixture
def workitem_unassigned(facility, staff_user):
    """Personenlose Aufgabe (Refs #1786): Attach ist hier weiterhin erlaubt."""
    return WorkItem.objects.create(
        facility=facility,
        client=None,
        created_by=staff_user,
        title="Personenlose Aufgabe",
        status=WorkItem.Status.OPEN,
        priority=WorkItem.Priority.NORMAL,
    )


@pytest.mark.django_db
class TestUpdateWorkItemService:
    """Tests for the update_workitem service function."""

    def test_updates_fields(self, facility, staff_user, workitem_open):
        updated = update_workitem(
            workitem_open,
            staff_user,
            title="Neuer Titel",
            priority=WorkItem.Priority.URGENT,
        )
        updated.refresh_from_db()
        assert updated.title == "Neuer Titel"
        assert updated.priority == WorkItem.Priority.URGENT

    def test_logs_updated_activity(self, facility, staff_user, workitem_open):
        update_workitem(workitem_open, staff_user, title="Geaendert")
        assert Activity.objects.filter(
            verb=Activity.Verb.UPDATED,
            target_id=workitem_open.pk,
        ).exists()

    def test_update_workitem_rejects_unknown_field(self, workitem_open, staff_user):
        with pytest.raises(ValueError, match="Feld"):
            update_workitem(workitem_open, staff_user, status="done")

    def test_updates_client_association(self, facility, staff_user, workitem_unassigned, client_identified):
        """Nachtraegliches Zuordnen an eine personenlose Aufgabe bleibt erlaubt.

        Refs #1786 (Maintainer-Entscheid 2026-08-05): frueher pruefte dieser
        Test den freien Wechsel der Personenzuordnung — der ist jetzt verboten.
        """
        update_workitem(workitem_unassigned, staff_user, client=client_identified)
        workitem_unassigned.refresh_from_db()
        assert workitem_unassigned.client == client_identified

    def test_update_workitem_allows_attach_to_unassigned(self, workitem_unassigned, client_identified, staff_user):
        """Nachtraegliches Zuordnen an personenlose Aufgabe bleibt erlaubt (Maintainer-Entscheid 2026-08-05)."""
        result = update_workitem(workitem_unassigned, staff_user, client=client_identified)
        assert result.client == client_identified

    def test_update_workitem_rejects_client_change(self, workitem_open, client_qualified, staff_user):
        with pytest.raises(ValueError, match="Personenzuordnung"):
            update_workitem(workitem_open, staff_user, client=client_qualified)

    def test_clears_client_association(self, facility, staff_user, workitem_open):
        """Refs #1786: Entfernen der Personenzuordnung ist jetzt verboten."""
        with pytest.raises(ValueError, match="Personenzuordnung"):
            update_workitem(workitem_open, staff_user, client=None)

    def test_update_workitem_same_client_is_noop(self, workitem_open, staff_user):
        """Identischer Wert = No-op: kein Fehler, keine Aenderung — und **kein**
        Audit-Eintrag.

        Die Audit-Assertion gehoert dazu (Refs #1786): der Service wirft
        ``client`` aus ``fields``, damit kein leerer Diff entsteht. Ohne sie
        pruefte der Test nur den Rueckgabewert, und ein zurueckgebauter ``pop``
        fiele erst im Audit-Log auf — dort, wo niemand danach sucht.
        """
        from core.models import AuditLog

        result = update_workitem(workitem_open, staff_user, client=workitem_open.client)

        assert result.client == workitem_open.client
        workitem_open.refresh_from_db()
        assert workitem_open.client is not None
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.WORKITEM_UPDATE, target_id=str(workitem_open.pk)
        ).exists()

    def test_update_workitem_pin_prueft_den_db_stand_unter_lock(
        self, workitem_unassigned, client_identified, client_qualified, staff_user
    ):
        """TOCTOU (Refs #1786): Zwischen Laden und Aufruf setzt ein Racer die Person.

        Die geladene Instanz ist personenlos — nach ihr waere der Aufruf ein
        erlaubter Attach. Der DB-Stand traegt inzwischen eine andere Person; ohne
        Row-Lock und frischen Lesezugriff ueberschriebe der zweite Attach die
        Zuordnung des ersten, und genau das verbietet der Maintainer-Entscheid
        vom 2026-08-05 (Zuordnen ja, Wechseln nein).
        """
        WorkItem.objects.filter(pk=workitem_unassigned.pk).update(client=client_identified)
        assert workitem_unassigned.client_id is None, "Instanz ist bewusst stale"

        with pytest.raises(ValueError, match="Personenzuordnung"):
            update_workitem(workitem_unassigned, staff_user, client=client_qualified)

        workitem_unassigned.refresh_from_db()
        assert workitem_unassigned.client == client_identified

    def test_update_workitem_stale_instanz_erhaelt_die_zuordnung(
        self, workitem_unassigned, client_identified, staff_user
    ):
        """Gegenrichtung derselben Race: Der Aufruf nennt **dieselbe** Person,
        die der Racer gesetzt hat — ein No-op, kein Fehler.

        Der Fall ist die Falle am Fix: das volle ``save()`` schreibt alle Felder
        der (stale, personenlosen) Instanz zurueck. Wird ``client`` nur aus
        ``fields`` geworfen, ohne den DB-Stand auf die Instanz zu uebernehmen,
        nullt der Service die Zuordnung — das Gegenteil eines Pins.
        """
        WorkItem.objects.filter(pk=workitem_unassigned.pk).update(client=client_identified)

        update_workitem(workitem_unassigned, staff_user, client=client_identified, title="Neuer Titel")

        workitem_unassigned.refresh_from_db()
        assert workitem_unassigned.client == client_identified
        assert workitem_unassigned.title == "Neuer Titel"

    def test_returns_updated_workitem(self, facility, staff_user, workitem_open):
        result = update_workitem(workitem_open, staff_user, description="Neue Beschreibung")
        assert result.pk == workitem_open.pk
        assert result.description == "Neue Beschreibung"

    def test_update_workitem_erhaelt_parallelen_statuswechsel(self, workitem_open, staff_user):
        """Lost Update (Refs #1789): der Service schreibt nur die genannten Spalten.

        Die uebergebene Instanz stammt aus einem frueheren Read; zwischen Read
        und Aufruf hat ein anderer Request den Status auf DONE gesetzt. Ein
        volles ``save()`` schriebe den stalen ``status``/``completed_at``
        zurueck und macht die abgeschlossene Aufgabe still wieder offen —
        obwohl der Aufruf nur die Prioritaet aendern wollte.
        """
        from django.utils import timezone

        completed = timezone.now()
        WorkItem.objects.filter(pk=workitem_open.pk).update(
            status=WorkItem.Status.DONE,
            completed_at=completed,
        )
        assert workitem_open.status == WorkItem.Status.OPEN, "Instanz ist bewusst stale"

        update_workitem(workitem_open, staff_user, priority=WorkItem.Priority.URGENT)

        workitem_open.refresh_from_db()
        assert workitem_open.status == WorkItem.Status.DONE
        assert workitem_open.completed_at == completed
        assert workitem_open.priority == WorkItem.Priority.URGENT

    def test_update_workitem_erhaelt_recurrence_marker(self, workitem_open, staff_user):
        """Derselbe Lost-Update-Pfad fuer den Idempotenz-Marker der Wiedervorlage.

        ``recurrence_duplicated_at`` verhindert, dass eine wiederkehrende
        Aufgabe zweimal dupliziert wird (Refs #596). Wird er von einem stalen
        Save auf ``None`` zurueckgesetzt, entsteht beim naechsten
        DONE-Uebergang ein Doppel-Duplikat.
        """
        from django.utils import timezone

        marker = timezone.now()
        WorkItem.objects.filter(pk=workitem_open.pk).update(recurrence_duplicated_at=marker)
        assert workitem_open.recurrence_duplicated_at is None, "Instanz ist bewusst stale"

        update_workitem(workitem_open, staff_user, priority=WorkItem.Priority.URGENT)

        workitem_open.refresh_from_db()
        assert workitem_open.recurrence_duplicated_at == marker

    def test_update_workitem_ueberschreibt_redaktion_nicht(self, workitem_open, client_identified, staff_user):
        """Der schwerste Fall des Lost Updates: ein staler Save re-publiziert
        redigierten Klartext.

        ``_redact_workitems`` laeuft aus den Anonymisierungs-Kaskaden und
        ueberschreibt ``title``/``description`` per Queryset-``update()`` — die
        Instanz eines parallel offenen Bearbeiten-Requests kennt danach noch
        den Klartext. Nennt der Aufruf ``title``/``description`` gar nicht,
        darf der Service sie auch nicht schreiben; es gibt keinen
        nachgelagerten Prune, der das wieder einfangen wuerde
        (docs/security-notes.md § Offline-Widerruf).
        """
        from core.services.client.main import _redact_workitems

        workitem_open.title = "Klartext-Titel"
        workitem_open.description = "Klartext-Beschreibung"
        workitem_open.save(update_fields=["title", "description"])
        _redact_workitems(client_identified)

        update_workitem(workitem_open, staff_user, priority=WorkItem.Priority.URGENT)

        workitem_open.refresh_from_db()
        assert workitem_open.title == "Aufgabe (anonymisiert)"
        assert workitem_open.description == ""
        assert workitem_open.priority == WorkItem.Priority.URGENT


@pytest.mark.django_db
class TestApplyStatusTransitionHelper:
    """Refs #906: isolierte Tests fuer ``_apply_status_transition``.

    Die Funktion mutiert das ``workitem``-Objekt in-place, **ohne**
    ``save()`` — der Caller (single oder bulk) entscheidet ueber
    Persistenz. Tests verifizieren jede der gemeinsamen Regeln.
    """

    def _make(self, facility, user, *, status, assigned_to=None, completed_at=None, recurrence=None):
        from core.models import WorkItem

        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            item_type=WorkItem.ItemType.TASK,
            status=status,
            assigned_to=assigned_to,
            completed_at=completed_at,
            recurrence=recurrence or WorkItem.Recurrence.NONE,
            title="Test",
        )

    def test_returns_false_on_identical_status(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        changed = _apply_status_transition(wi, WorkItem.Status.OPEN, staff_user, auto_assign=True)
        assert changed is False
        # In-place keine Mutation.
        assert wi.status == WorkItem.Status.OPEN
        assert wi.assigned_to is None

    def test_auto_assigns_to_user_on_in_progress(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        changed = _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=True)
        assert changed is True
        assert wi.status == WorkItem.Status.IN_PROGRESS
        assert wi.assigned_to == staff_user

    def test_no_auto_assign_when_disabled(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=False)
        assert wi.assigned_to is None

    def test_preserves_existing_assignee_on_in_progress(self, facility, staff_user, lead_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN, assigned_to=lead_user)
        _apply_status_transition(wi, WorkItem.Status.IN_PROGRESS, staff_user, auto_assign=True)
        # Vorhandener Assignee bleibt — Auto-Assign nur, wenn noch keiner gesetzt.
        assert wi.assigned_to == lead_user

    def test_sets_completed_at_on_done(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.IN_PROGRESS)
        assert wi.completed_at is None
        _apply_status_transition(wi, WorkItem.Status.DONE, staff_user, auto_assign=True)
        assert wi.completed_at is not None

    def test_sets_completed_at_on_dismissed(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.OPEN)
        _apply_status_transition(wi, WorkItem.Status.DISMISSED, staff_user, auto_assign=True)
        assert wi.completed_at is not None

    def test_clears_completed_at_on_reopen(self, facility, staff_user):
        from django.utils import timezone as dj_timezone

        from core.models import WorkItem
        from core.services.case import _apply_status_transition

        wi = self._make(facility, staff_user, status=WorkItem.Status.DONE, completed_at=dj_timezone.now())
        _apply_status_transition(wi, WorkItem.Status.OPEN, staff_user, auto_assign=True)
        assert wi.completed_at is None


@pytest.mark.django_db
class TestMaybeDuplicateRecurringHelper:
    """Refs #906: isolierte Tests fuer ``_maybe_duplicate_recurring``."""

    def _make(self, facility, user, *, recurrence, due_date=None):
        from core.models import WorkItem

        return WorkItem.objects.create(
            facility=facility,
            created_by=user,
            item_type=WorkItem.ItemType.TASK,
            status=WorkItem.Status.OPEN,
            recurrence=recurrence,
            due_date=due_date,
            title="Test",
        )

    def test_no_duplicate_when_status_is_not_done(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.WEEKLY)
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.IN_PROGRESS)
        assert WorkItem.objects.count() == before

    def test_no_duplicate_when_recurrence_is_none(self, facility, staff_user):
        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.NONE)
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.DONE)
        assert WorkItem.objects.count() == before

    def test_duplicates_when_done_and_recurring(self, facility, staff_user):
        from datetime import date

        from core.models import WorkItem
        from core.services.case import _maybe_duplicate_recurring

        wi = self._make(facility, staff_user, recurrence=WorkItem.Recurrence.WEEKLY, due_date=date(2026, 1, 1))
        before = WorkItem.objects.count()
        _maybe_duplicate_recurring(wi, staff_user, WorkItem.Status.DONE)
        assert WorkItem.objects.count() == before + 1
