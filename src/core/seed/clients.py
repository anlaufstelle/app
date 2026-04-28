"""Client seeding: ``small`` uses fixed pseudonyms, bulk uses a random pool."""

import random

from core.models import Client, Facility, User
from core.seed.constants import SMALL_CLIENTS, SPITZNAMEN


def seed_clients_small(facility: Facility, users: list[User]) -> None:
    """Create the seven hard-coded demo clients for the small scale."""
    admin = users[0]
    for pseudonym, stage, age in SMALL_CLIENTS:
        Client.objects.get_or_create(
            facility=facility,
            pseudonym=pseudonym,
            defaults={
                "contact_stage": stage,
                "age_cluster": age,
                "created_by": admin,
            },
        )


def seed_clients_bulk(facility: Facility, users: list[User], cfg: dict) -> list[Client]:
    """Create clients via ``bulk_create``. Returns all clients for the facility."""
    count = cfg["clients_per_facility"]
    existing = set(Client.objects.filter(facility=facility).values_list("pseudonym", flat=True))
    admin = users[0]
    stages = list(Client.ContactStage.values)
    ages = list(Client.AgeCluster.values)

    # Pure nicknames for the first len(SPITZNAMEN) clients, suffix for overflow.
    available = list(SPITZNAMEN)
    random.shuffle(available)
    pseudonyms = available[:count]
    if count > len(available):
        for i in range(count - len(available)):
            pseudonyms.append(f"{random.choice(available)}-{i + 1}")

    to_create = []
    for pseudonym in pseudonyms:
        if pseudonym in existing:
            continue
        to_create.append(
            Client(
                facility=facility,
                pseudonym=pseudonym,
                contact_stage=random.choice(stages),
                age_cluster=random.choice(ages),
                created_by=admin,
            )
        )

    if to_create:
        Client.objects.bulk_create(to_create, batch_size=1000)

    return list(Client.objects.filter(facility=facility))
