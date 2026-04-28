"""DeletionRequest, RetentionProposal and LegalHold seeding."""

import random
from datetime import timedelta

from django.utils import timezone

from core.models import DeletionRequest, Event, Facility, LegalHold, RetentionProposal, User


def seed_deletion_requests(facility: Facility, users: list[User], cfg: dict) -> int:
    """Create DeletionRequests for a random sample of events. Returns newly created count."""
    count = cfg.get("deletion_requests", 0)
    if count == 0:
        return 0
    existing = DeletionRequest.objects.filter(facility=facility).count()
    if existing >= count:
        return 0

    events = list(Event.objects.filter(facility=facility, is_deleted=False)[: count * 2])
    if not events:
        return 0

    reasons = [
        "Klientel hat Löschung gemäß Art. 17 DSGVO beantragt.",
        "Fehlerhafter Eintrag — falscher Klientel zugeordnet.",
        "Doppelter Eintrag — bereits unter anderem Datum erfasst.",
        "Aufbewahrungsfrist abgelaufen.",
    ]

    to_create = []
    for i in range(min(count - existing, len(events))):
        event = events[i]
        requester = random.choice(users)
        status = random.choices(
            [
                DeletionRequest.Status.PENDING,
                DeletionRequest.Status.APPROVED,
                DeletionRequest.Status.REJECTED,
            ],
            weights=[0.5, 0.3, 0.2],
        )[0]

        reviewer = None
        reviewed_at = None
        if status != DeletionRequest.Status.PENDING:
            # Constraint: requested_by != reviewed_by
            other_users = [u for u in users if u != requester]
            if other_users:
                reviewer = random.choice(other_users)
                reviewed_at = timezone.now() - timedelta(days=random.randint(1, 30))
            else:
                status = DeletionRequest.Status.PENDING

        to_create.append(
            DeletionRequest(
                facility=facility,
                target_type=DeletionRequest.TargetType.EVENT,
                target_id=event.id,
                reason=random.choice(reasons),
                status=status,
                requested_by=requester,
                reviewed_by=reviewer,
                reviewed_at=reviewed_at,
            )
        )

    if to_create:
        DeletionRequest.objects.bulk_create(to_create, batch_size=500)
    return len(to_create)


def seed_retention_proposals(facility: Facility, users: list[User], cfg: dict) -> tuple[int, int]:
    """Create RetentionProposals and associated LegalHolds.

    Returns ``(proposals, holds)``.
    """
    count = cfg.get("retention_proposals", 0)
    if count == 0:
        return (0, 0)
    existing = RetentionProposal.objects.filter(facility=facility).count()
    if existing >= count:
        return (0, 0)

    events = list(Event.objects.filter(facility=facility, is_deleted=False)[: count * 2])
    if not events:
        return (0, 0)

    categories = ["anonymous", "identified", "qualified", "document_type"]
    now = timezone.now()

    proposals_to_create: list[RetentionProposal] = []
    holds_to_create: list[tuple[Event, LegalHold]] = []
    for i in range(min(count - existing, len(events))):
        event = events[i]
        category = random.choice(categories)
        status = random.choices(
            [
                RetentionProposal.Status.PENDING,
                RetentionProposal.Status.APPROVED,
                RetentionProposal.Status.HELD,
            ],
            weights=[0.5, 0.2, 0.3],
        )[0]

        deletion_due = (now + timedelta(days=random.randint(-10, 60))).date()
        details = {
            "document_type": event.document_type.name if event.document_type else None,
            "occurred_at": str(event.occurred_at),
        }
        if event.client:
            details["pseudonym"] = event.client.pseudonym
            details["contact_stage"] = event.client.contact_stage

        proposal = RetentionProposal(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            deletion_due_at=deletion_due,
            status=status,
            details=details,
            retention_category=category,
        )
        proposals_to_create.append(proposal)

        if status == RetentionProposal.Status.HELD:
            creator = random.choice(users)
            expires = (now + timedelta(days=random.randint(30, 180))).date() if random.random() > 0.3 else None
            holds_to_create.append(
                (
                    event,
                    LegalHold(
                        facility=facility,
                        target_type="Event",
                        target_id=event.pk,
                        reason=random.choice(
                            [
                                "Laufendes Gerichtsverfahren.",
                                "Jugendamt-Überprüfung noch nicht abgeschlossen.",
                                "Klientel hat Widerspruch eingelegt.",
                                "Anfrage der Aufsichtsbehörde.",
                            ]
                        ),
                        expires_at=expires,
                        created_by=creator,
                    ),
                )
            )

    if proposals_to_create:
        RetentionProposal.objects.bulk_create(proposals_to_create, batch_size=500)
    if holds_to_create:
        LegalHold.objects.bulk_create([h for _, h in holds_to_create], batch_size=500)
    return (len(proposals_to_create), len(holds_to_create))
