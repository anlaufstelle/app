"""Gemeinsame Strategie-Datacontainer fuer Retention (Refs #778).

Die vier Retention-Strategien (anonymous, identified, qualified,
document_type) werden frueher dreifach kopiert: in
:func:`core.retention.enforcement.collect_doomed_events`, in den vier
``enforce_*``-Funktionen und in
:func:`core.retention.proposals.create_proposals_for_facility`. Drift
zwischen den drei Stellen ist ein latenter DSGVO-Bug — wenn die
Schwellen je nach Eintrittspunkt unterschiedlich greifen, leben Events
laenger als sie duerften.

Dieses Modul exportiert :func:`iter_strategies`, das Kern-QuerySets +
Metadaten liefert. Die Konsumenten-Module sind dafuer zustaendig,
``held_ids`` (Legal Holds) auszuschliessen und ihre eigene Aktion
auszufuehren (Soft-Delete, Proposal-Anlage usw.).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterator

# Refs #818 — Inline-Imports an Modulkopf gehoben.
from core.models import Case, Client, DocumentType, Event


@dataclass(frozen=True)
class RetentionStrategy:
    """Single retention strategy materialized for one facility/now-tuple.

    ``category`` taggt den Audit-Log und die ``RetentionProposal``-Zeile —
    Werte: ``anonymous``, ``identified``, ``qualified``, ``document_type``.

    ``cutoff`` ist der Schwellenwert (datetime), unterhalb dessen Events
    geloescht werden duerfen. ``queryset`` ist der gefilterte Event-
    QuerySet; Aufrufer sollten ``.exclude(pk__in=held_ids)`` ergaenzen.
    """

    category: str
    cutoff: datetime
    queryset: object  # Event QuerySet


def iter_strategies(facility, settings_obj, now: datetime) -> Iterator[RetentionStrategy]:
    """Yield :class:`RetentionStrategy` pro aktivem Retention-Pfad.

    Die Reihenfolge entspricht :func:`core.retention.enforcement.process_facility_retention`:
    anonymous -> identified -> qualified -> document_type. Damit landet
    ein Event, das mehrere Kategorien matcht, in der hoechstpriorisierten
    (anonymous schlaegt identified usw.) — wichtig fuer die deterministische
    Categorisierung im Audit-Log.
    """
    cutoff_anon = now - timedelta(days=settings_obj.retention_anonymous_days)
    yield RetentionStrategy(
        category="anonymous",
        cutoff=cutoff_anon,
        queryset=Event.objects.filter(
            facility=facility,
            is_anonymous=True,
            is_deleted=False,
            occurred_at__lt=cutoff_anon,
        ),
    )

    cutoff_ident = now - timedelta(days=settings_obj.retention_identified_days)
    identified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    yield RetentionStrategy(
        category="identified",
        cutoff=cutoff_ident,
        queryset=Event.objects.filter(
            facility=facility,
            client__in=identified_clients,
            is_deleted=False,
            occurred_at__lt=cutoff_ident,
        ),
    )

    case_cutoff = now - timedelta(days=settings_obj.retention_qualified_days)
    qualified_clients = Client.objects.filter(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
    )
    expired_cases = Case.objects.filter(
        facility=facility,
        client__in=qualified_clients,
        status=Case.Status.CLOSED,
        closed_at__lt=case_cutoff,
    )
    yield RetentionStrategy(
        category="qualified",
        cutoff=case_cutoff,
        queryset=Event.objects.filter(
            facility=facility,
            client__in=qualified_clients,
            case__in=expired_cases,
            is_deleted=False,
        ),
    )

    doc_types_with_retention = DocumentType.objects.filter(
        facility=facility,
        retention_days__isnull=False,
    )
    for dt in doc_types_with_retention:
        cutoff_dt = now - timedelta(days=dt.retention_days)
        yield RetentionStrategy(
            category="document_type",
            cutoff=cutoff_dt,
            queryset=Event.objects.filter(
                facility=facility,
                document_type=dt,
                is_deleted=False,
                occurred_at__lt=cutoff_dt,
            ),
        )
