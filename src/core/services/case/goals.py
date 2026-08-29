"""Service layer for OutcomeGoal and Milestone CRUD."""

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Activity, AuditLog
from core.models.outcome import Milestone, OutcomeGoal
from core.services.audit import audit_event
from core.services.audit.diff import changed_fields_vs_db
from core.services.dashboard import log_activity

logger = logging.getLogger(__name__)


@transaction.atomic
def create_goal(case, user, title, description=""):
    """Create a new OutcomeGoal for a case.

    Art.-18-Gate (Refs #2045): Wirkungsziele und Meilensteine sind Inhalt der
    Fallakte — ihre Bearbeitung ist fachliche Nutzung, nicht Speicherung.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(case.client_id, lock=True)
    goal = OutcomeGoal(
        case=case,
        title=title,
        description=description,
        created_by=user,
    )
    goal.save()
    return goal


@transaction.atomic
def update_goal(goal, user, title=None, description=None):
    """Update mutable fields on a goal (title, description) + AuditLog.

    Refs #1959: ``user`` ist neu und Pflicht — ohne Akteur kann
    ``audit_event`` keine verwertbare Zeile schreiben, und eine Bearbeitung
    des Wirkungsziels aendert Falldaten wie jede andere. Positionell wie in
    ``update_case``/``update_event``, damit der Service-Layer ein Muster
    behaelt.

    ``fields`` entsteht aus den nicht-``None``-Argumenten; ein Aufruf ohne
    Feldargumente ist ein No-Op. Der Diff laeuft ueber
    :func:`changed_fields_vs_db` gegen den DB-Stand — die View reicht
    Formularwerte durch, die Instanz kann bereits mutiert sein (Threat-Model
    TB6, dieselbe Falle wie bei der Episode). Audit nur bei echtem Diff.

    ``OutcomeGoal`` fuehrt kein ``updated_at``; es gibt hier folgerichtig
    keinen Versionskonflikt-Check.
    """
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(goal.case.client_id, lock=True)
    fields = {}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if not fields:
        return goal

    changed_fields = changed_fields_vs_db(goal, fields)
    for key, value in fields.items():
        setattr(goal, key, value)
    # Nur die uebergebenen Spalten schreiben (Refs #1789-Folge) — sonst
    # ueberschreibt ein Vollsave die parallel laufende Redaktions-Kaskade.
    goal.save(update_fields=list(fields.keys()))

    if changed_fields:
        audit_event(
            AuditLog.Action.GOAL_UPDATE,
            user=user,
            facility=goal.case.facility,
            target_obj=goal,
            # Refs #1751 (Punkt 5): kein Titel, keine Beschreibung — beides ist
            # klientenbezogener Freitext (dieselbe Einordnung wie beim
            # Meilenstein-Titel in ``delete_milestone``).
            detail={"case_id": str(goal.case_id), "changed_fields": changed_fields},
        )
    return goal


@transaction.atomic
def achieve_goal(goal):
    """Mark a goal as achieved. Idempotent — returns early if already achieved."""
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(goal.case.client_id, lock=True)
    if goal.is_achieved:
        return goal
    goal.is_achieved = True
    goal.achieved_at = timezone.localdate()
    goal.save()
    return goal


@transaction.atomic
def unachieve_goal(goal):
    """Mark a goal as not achieved."""
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(goal.case.client_id, lock=True)
    goal.is_achieved = False
    goal.achieved_at = None
    goal.save()
    return goal


@transaction.atomic
def create_milestone(goal, title, sort_order=0):
    """Create a new Milestone for a goal."""
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(goal.case.client_id, lock=True)
    milestone = Milestone(
        goal=goal,
        title=title,
        sort_order=sort_order,
    )
    milestone.save()
    return milestone


@transaction.atomic
def toggle_milestone(milestone):
    """Toggle milestone completion. Sets/clears completed_at accordingly."""
    from core.services.client.restriction import assert_processing_allowed

    assert_processing_allowed(milestone.goal.case.client_id, lock=True)
    if milestone.is_completed:
        milestone.is_completed = False
        milestone.completed_at = None
    else:
        milestone.is_completed = True
        milestone.completed_at = timezone.localdate()
    milestone.save()
    return milestone


@transaction.atomic
def delete_milestone(milestone, user):
    """Delete a milestone + AuditLog + Activity.

    Milestones are lightweight, but the deletion is still a state-changing
    operation that must be auditable.
    """
    from core.services.client.restriction import assert_processing_allowed

    case = milestone.goal.case
    assert_processing_allowed(case.client_id, lock=True)
    facility = case.facility
    milestone_pk = str(milestone.pk)
    audit_event(
        AuditLog.Action.MILESTONE_DELETE,
        user=user,
        facility=facility,
        target_type="Milestone",
        target_id=milestone_pk,
        # Refs #1751 (Punkt 5): kein Titel ins detail. Meilenstein-Titel sind
        # klientenbezogener Freitext (die Anonymisierung tilgt sie an
        # ``Milestone.title``); im append-only AuditLog ueberlebten sie sie.
        # Zuordnung bleibt vollstaendig ueber target_id + case_id.
        detail={"case_id": str(case.pk)},
    )
    log_activity(
        facility=facility,
        actor=user,
        verb=Activity.Verb.DELETED,
        target=milestone,
        action=Activity.Action.MILESTONE_DELETED,
    )
    milestone.delete()
