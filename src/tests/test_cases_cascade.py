"""Cascade-/SET_NULL-Tests fuer Case-Loeschung (Refs Matrix DEV-CASE-13, DEV-CASE-14).

Refs Matrix DEV-CASE-13: Wird ein Case hart geloescht, kaskadieren alle
abhaengigen OutcomeGoals und Milestones (``on_delete=CASCADE``) — keine
Wirkungsziele oder Meilensteine bleiben als Waisen zurueck.

Refs Matrix DEV-CASE-14: Events bleiben beim harten Loeschen eines Case
erhalten und werden auf ``case=NULL`` gesetzt (``on_delete=SET_NULL``) —
die Dokumentationskette bricht nicht ab.

Diese Tests verifizieren den DB-Level-Cascade-Vertrag der Models
``Case``, ``OutcomeGoal``, ``Milestone`` und ``Event`` (siehe
``src/core/models/case.py``, ``src/core/models/outcome.py``,
``src/core/models/event.py``). Sie pruefen den IST-Zustand und sollen
unveraendert weiterlaufen — eine Aenderung der ``on_delete``-Constraints
ist eine API-Aenderung und braucht einen separaten Plan.

Refs #922 (Master), #926.
"""

import pytest
from django.utils import timezone

from core.models import Event, Milestone, OutcomeGoal


@pytest.mark.django_db
class TestCaseCascadeDelete:
    """DB-Cascade-Vertrag bei hartem ``Case.delete()``."""

    def test_case_delete_cascades_outcome_goal(self, case_open, outcome_goal):
        """Refs Matrix DEV-CASE-13: ``OutcomeGoal.case`` ist
        ``on_delete=CASCADE`` — beim Loeschen des Case verschwindet das
        Ziel mit.
        """
        goal_pk = outcome_goal.pk
        case_open.delete()
        assert not OutcomeGoal.objects.filter(pk=goal_pk).exists(), (
            "OutcomeGoal blieb nach Case.delete() bestehen — CASCADE-Constraint auf OutcomeGoal.case fehlt."
        )

    def test_case_delete_cascades_milestone(self, case_open, outcome_goal, milestone):
        """Refs Matrix DEV-CASE-13: ``Milestone.goal`` ist
        ``on_delete=CASCADE`` — beim Loeschen des Case kaskadiert die
        Loeschung transitiv ueber das ``OutcomeGoal`` auf die
        ``Milestones``.
        """
        milestone_pk = milestone.pk
        case_open.delete()
        assert not Milestone.objects.filter(pk=milestone_pk).exists(), (
            "Milestone blieb nach Case.delete() bestehen — "
            "transitive CASCADE-Kette Case -> OutcomeGoal -> Milestone "
            "ist unterbrochen."
        )

    def test_case_delete_cascades_goals_and_milestones_together(self, case_open, outcome_goal, milestone):
        """Refs Matrix DEV-CASE-13: ein einziger ``case_open.delete()``-
        Aufruf raeumt Goals UND Milestones in einem Schritt ab.
        """
        goal_pk = outcome_goal.pk
        milestone_pk = milestone.pk

        case_open.delete()

        assert not OutcomeGoal.objects.filter(pk=goal_pk).exists()
        assert not Milestone.objects.filter(pk=milestone_pk).exists()

    def test_case_delete_sets_event_case_null(
        self, facility, client_identified, doc_type_contact, staff_user, case_open
    ):
        """Refs Matrix DEV-CASE-14: ``Event.case`` ist
        ``on_delete=SET_NULL`` — beim Loeschen des Case bleibt das Event
        erhalten und verliert lediglich die Fall-Referenz.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            case=case_open,
            created_by=staff_user,
        )
        event_pk = event.pk

        case_open.delete()

        # Event muss weiter existieren — die Dokumentationskette bleibt
        # erhalten, lediglich die Fall-Referenz wird auf NULL gesetzt.
        assert Event.objects.filter(pk=event_pk).exists(), (
            "Event wurde mit dem Case mitgeloescht — SET_NULL-Constraint auf Event.case fehlt."
        )
        event.refresh_from_db()
        assert event.case_id is None, (
            f"Event.case_id ist nach Case.delete() nicht NULL (Wert: {event.case_id!r}) — SET_NULL hat nicht gegriffen."
        )
