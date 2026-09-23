"""Service layer for OutcomeGoal and Milestone CRUD."""

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Activity, AuditLog
from core.models.outcome import Milestone, OutcomeGoal
from core.services.audit import audit_event
from core.services.audit.diff import changed_fields_vs_db
from core.services.case.state_contract import (
    lock_case_for_write,
    lock_goal_for_status_change,
    lock_goal_for_write,
    lock_milestone_for_status_change,
    lock_milestone_for_write,
)
from core.services.dashboard import log_activity

logger = logging.getLogger(__name__)


@transaction.atomic
def create_goal(case, user, title, description=""):
    """Create a new OutcomeGoal for a case.

    Art.-18-Gate (Refs #2045): Wirkungsziele und Meilensteine sind Inhalt der
    Fallakte — ihre Bearbeitung ist fachliche Nutzung, nicht Speicherung.

    Refs #2329: Das Gate allein liess ein neues Ziel an einer Akte im
    Papierkorb oder an einer bereits getilgten Akte zu. Angelegt wird jetzt
    nur an der FRISCH unter Sperre gelesenen und geprueften Fallzeile
    (Zustandsvertrag, :mod:`core.services.case.state_contract`) — dieselbe
    Personensperre wie zuvor, danach der Fall.
    """
    locked_case = lock_case_for_write(case)
    goal = OutcomeGoal(
        case=locked_case,
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

    Refs #2329: Vor jedem Schreiben laeuft der Zustandsvertrag
    (Person -> Fall -> Ziel, frisch und gesperrt). Ohne ihn schrieb die
    Bearbeitung Freitext in ein Ziel, dessen Text die Tilgung
    (``redact_case_goals``) gerade ersetzt hatte. Der Vertrag laeuft auch vor
    dem No-Op — wie zuvor das Art.-18-Gate.
    """
    lock_goal_for_write(goal)
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
    """Mark a goal as achieved. Idempotent — returns early if already achieved.

    Refs #2295: Entschieden wird auf der FRISCH unter Sperre gelesenen Zeile,
    geschrieben werden nur die beiden Statusspalten. Die uebergebene Instanz
    stammt aus einem Lesevorgang von vor dem Klick; ein voller ``save()`` auf
    ihr holte die von ``redact_case_goals`` bereits getilgten Freitexte
    zurueck. Auch die Idempotenz haengt daran: ``goal.is_achieved`` der alten
    Instanz beantwortet nicht, ob das Ziel INZWISCHEN erreicht ist.

    Das Art.-18-Gate und die Zustandspruefung liegen im Zustandsvertrag
    (:mod:`core.services.case.state_contract`), der dafuer dieselbe
    Personensperre nimmt wie zuvor.
    """
    locked = lock_goal_for_status_change(goal)
    if not locked.is_achieved:
        locked.is_achieved = True
        locked.achieved_at = timezone.localdate()
        locked.save(update_fields=["is_achieved", "achieved_at"])
    goal.refresh_from_db()
    return goal


@transaction.atomic
def unachieve_goal(goal):
    """Mark a goal as not achieved.

    Spiegel von :func:`achieve_goal` — einschliesslich Zustandsvertrag und
    spaltengenauem Schreiben (Refs #2295). Bewusst ohne Idempotenz-Ausstieg:
    die Ruecknahme eines nicht erreichten Ziels ist ein No-op auf denselben
    zwei Spalten.
    """
    locked = lock_goal_for_status_change(goal)
    locked.is_achieved = False
    locked.achieved_at = None
    locked.save(update_fields=["is_achieved", "achieved_at"])
    goal.refresh_from_db()
    return goal


@transaction.atomic
def create_milestone(goal, title, sort_order=0):
    """Create a new Milestone for a goal.

    Refs #2358: Vor dem Schreiben laeuft der Zustandsvertrag (Person -> Fall
    -> Ziel, frisch und gesperrt; :func:`lock_goal_for_write`) — wie bei der
    Anlage eines Ziels. Das Art.-18-Gate ist darin enthalten (erste Stufe,
    dieselbe Personensperre); zusaetzlich lehnt der Vertrag eine Akte im
    Papierkorb, eine getilgte Akte und die Akte einer Person im Papierkorb ab.
    Ohne ihn liess sich dort noch Meilenstein-Freitext anlegen.
    """
    locked_goal = lock_goal_for_write(goal)
    milestone = Milestone(
        goal=locked_goal,
        title=title,
        sort_order=sort_order,
    )
    milestone.save()
    return milestone


@transaction.atomic
def toggle_milestone(milestone):
    """Toggle milestone completion. Sets/clears completed_at accordingly.

    Refs #2295: Umgeschaltet wird der FRISCH unter Sperre gelesene Stand —
    eine Umschaltung, die den Stand der alten Instanz fortschreibt, kehrt bei
    zwei gleichzeitigen Klicks zweimal dieselbe Richtung um. Geschrieben
    werden nur die beiden Statusspalten; der Meilenstein-Titel ist
    klientenbezogener Freitext, den die Tilgung ersetzt (``redact_case_goals``).
    """
    locked = lock_milestone_for_status_change(milestone)
    if locked.is_completed:
        locked.is_completed = False
        locked.completed_at = None
    else:
        locked.is_completed = True
        locked.completed_at = timezone.localdate()
    locked.save(update_fields=["is_completed", "completed_at"])
    milestone.refresh_from_db()
    return milestone


@transaction.atomic
def delete_milestone(milestone, user):
    """Delete a milestone + AuditLog + Activity.

    Milestones are lightweight, but the deletion is still a state-changing
    operation that must be auditable.

    Refs #2358: Geloescht wird die FRISCH unter dem Zustandsvertrag gesperrte
    Zeile (Person -> Fall -> Ziel -> Meilenstein,
    :func:`lock_milestone_for_write`) — nicht die Instanz von vor dem Klick.
    Das Art.-18-Gate ist die erste Stufe des Vertrags; zusaetzlich lehnt er
    eine Akte im Papierkorb, eine getilgte Akte, die Akte einer Person im
    Papierkorb und einen inzwischen verschwundenen Meilenstein ab — ohne
    Audit- und Zeitstrom-Zeile.
    """
    locked = lock_milestone_for_write(milestone)
    case = locked.goal.case
    facility = case.facility
    milestone_pk = str(locked.pk)
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
        target=locked,
        action=Activity.Action.MILESTONE_DELETED,
    )
    locked.delete()
