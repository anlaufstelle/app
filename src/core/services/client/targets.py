"""Gemeinsame Ziel-Queries fuer Art.-15-Auskunft und Art.-17-Redaktion."""

from collections.abc import Collection
from uuid import UUID

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from core.models.activity import Activity
from core.models.case import Case
from core.models.client import Client
from core.models.event import Event
from core.models.workitem import WorkItem


def client_and_event_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Ziele einer Person und der explizit uebergebenen Ereignisse."""
    targets = Q(target_type="Client", target_id=client.pk)
    if event_ids:
        targets |= Q(target_type="Event", target_id__in=event_ids)
    return targets


def client_activity_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Activity-Ziele einer Person samt Ereignissen, Aufgaben und Faellen."""
    targets = Q(
        target_type=ContentType.objects.get_for_model(Client),
        target_id=client.pk,
    )
    if event_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Event),
            target_id__in=event_ids,
        )

    workitem_ids: list[UUID] = list(client.work_items.values_list("pk", flat=True))
    if workitem_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(WorkItem),
            target_id__in=workitem_ids,
        )

    case_ids: list[UUID] = list(client.cases.values_list("pk", flat=True))
    if case_ids:
        targets |= Q(
            target_type=ContentType.objects.get_for_model(Case),
            target_id__in=case_ids,
        )
    return targets


def client_and_case_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Ziele einer Person, ihrer Ereignisse und ihrer **Faelle** (Refs #2071).

    Erweiterung von :func:`client_and_event_targets_q` um den Fall-Zweig, den
    ADR-044 (Refs #1818) eingefuehrt hat: seither koennen ``LegalHold`` und
    ``DeletionRequest`` einen Fall als Ziel tragen (``target_type="Case"``,
    ``target_id`` = ``Case.pk``).

    Der Zweig gehoert hierher und nicht in die einzelnen Verbraucher, weil
    Art.-15-Auskunft und Art.-17-Redaktion dieselbe Menge treffen muessen: hat
    nur eine Seite den Fall-Zweig, ist entweder die Auskunft unvollstaendig
    oder Klartext-Freitext ueberlebt die Anonymisierung. Genau so lief es bei
    LegalHold (#1990) und bei DeletionRequest (#2071) auseinander.

    Die Fall-IDs kommen aus ``client.cases`` — ohne Soft-Delete-Filter, wie
    ``_redact_cases_and_episodes`` sie einsammelt: ein Fall im Papierkorb ist
    weiterhin gespeicherte Information ueber die Person.
    """
    targets = client_and_event_targets_q(client, event_ids)
    case_ids: list[UUID] = list(client.cases.values_list("pk", flat=True))
    if case_ids:
        targets |= Q(target_type="Case", target_id__in=case_ids)
    return targets


def client_legal_hold_targets_q(client: Client, event_ids: Collection[UUID]) -> Q:
    """Legal-Hold-Ziele einer Person (Refs #1990).

    ``LegalHold.target_type`` ist ein freies ``CharField`` ohne ``choices``
    und traegt in ``target_id`` die **PK des Ziels** (nicht die PK des
    Ziel-Ziels). Vier Zieltypen sind vorgesehen:

    * ``Client``/``Event`` — vorschlagsgebundener Weg
      (:func:`core.retention.legal_holds.create_legal_hold`; ``RetentionProposal``
      kennt nur ``EVENT``, der ``Client``-Zweig deckt Bestands-/Direktzeilen),
    * ``Case`` — seit ADR-044 ueber
      :func:`core.retention.legal_holds.create_hold_for_target` (Refs #1818),
    * ``Activity`` — verbraucht von
      ``get_active_hold_target_ids(facility, "Activity")`` im Aktivitaeten-
      Enforcement (``core/retention/enforcement.py``, L12/Refs #1375);
      ``target_id`` ist dort die ``Activity.pk``.

    Warum EIN gemeinsamer Builder statt je einer Zeile in Auskunft und
    Redaktion: ``LegalHold.reason`` ist bestimmungsgemaess Freitext mit
    Fall-/Klientenkontext. Die Art.-15-Auskunft muss ihn ausweisen, die
    Art.-17-Anonymisierung muss ihn redigieren — laufen die Mengen
    auseinander, ist entweder die Auskunft unvollstaendig oder Klartext
    bleibt stehen. Genau diese Drift war #1990: Art. 17 hatte den
    Case-Zweig (Refs #1818), Art. 15 nicht.

    Der Fall-Zweig kommt aus :func:`client_and_case_targets_q` (geteilt mit
    den Loeschantraegen, Refs #2071); die ``Activity``-Ziele loest
    :func:`client_activity_targets_q` auf — dieselbe Menge, die auch der
    Zeitstrom der Auskunft ausweist. ``for_facility`` ist Defense-in-Depth
    zusaetzlich zu RLS; die IDs stammen ohnehin aus den Ereignissen, Aufgaben
    und Faellen dieser Person.
    """
    targets = client_and_case_targets_q(client, event_ids)

    activity_ids: list[UUID] = list(
        Activity.objects.for_facility(client.facility)
        .filter(client_activity_targets_q(client, event_ids))
        .values_list("pk", flat=True)
    )
    if activity_ids:
        targets |= Q(target_type="Activity", target_id__in=activity_ids)
    return targets


__all__ = [
    "client_activity_targets_q",
    "client_and_case_targets_q",
    "client_and_event_targets_q",
    "client_legal_hold_targets_q",
]
