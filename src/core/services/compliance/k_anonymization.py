"""K-anonymization service (Refs #535).

K-anonymization is an alternative to hard-delete for GDPR compliance: instead
of destroying a record, identifying fields are generalized so the record is no
longer re-identifiable, while aggregated trends (e.g. age_cluster distribution)
are preserved for long-term statistics.

This service is *additive* to :meth:`Client.anonymize` — the latter keeps the
existing semantics of clearing cascading personal data in cases, episodes and
workitems. ``k_anonymize_client`` targets the client record itself and maps
the pseudonym to a stable, non-reversible hash bucket.
"""

from __future__ import annotations

import hashlib

from django.db import transaction


def _pseudonym_hash(client) -> str:
    """Return a short, stable, non-reversible bucket id for a client.

    Uses SHA-256 over the client's primary key (UUID). Deterministic so that
    repeated k-anonymization of the same record produces the same bucket id,
    but not reversible to the original pseudonym.
    """
    digest = hashlib.sha256(str(client.pk).encode("utf-8")).hexdigest()
    return f"anon-{digest[:12]}"


@transaction.atomic
def k_anonymize_client(client, k: int = 5):
    """Generalize identifying fields of ``client`` for k-anonymity.

    Generalization rules:

    * ``pseudonym`` → ``anon-<sha256[:12]>`` (non-reversible hash bucket)
    * ``notes`` → ``""`` (free text can leak identity)
    * ``age_cluster`` stays as-is (already bucketed)
    * ``contact_stage`` stays as-is (low-cardinality category)
    * ``is_active`` → ``False``
    * ``k_anonymized`` → ``True``

    Linked cases / episodes / workitems are *not* modified — use
    :meth:`Client.anonymize` for the cascading case, or combine both modes.

    The ``k`` parameter is passed through for call-site logging and can be
    checked with :func:`is_k_anonymous` after generalization.
    """
    client.pseudonym = _pseudonym_hash(client)
    client.notes = ""
    client.is_active = False
    client.k_anonymized = True
    client.save(
        update_fields=[
            "pseudonym",
            "notes",
            "is_active",
            "k_anonymized",
        ]
    )
    return client


def count_clients_in_bucket(facility, age_cluster, contact_stage=None) -> int:
    """Count clients of ``facility`` sharing the given equivalence class.

    The equivalence class is ``(age_cluster, contact_stage)``. When
    ``contact_stage`` is ``None`` only ``age_cluster`` is matched.
    Used as input for the k-anonymity check.
    """
    from core.models.client import Client

    qs = Client.objects.filter(facility=facility, age_cluster=age_cluster)
    if contact_stage is not None:
        qs = qs.filter(contact_stage=contact_stage)
    return qs.count()


def is_k_anonymous(client, k: int = 5) -> bool:
    """Check whether ``client``'s equivalence class already has at least ``k`` records.

    Equivalence class is ``(age_cluster, contact_stage)`` within the same facility.
    Returns ``True`` if the bucket size is ``>= k`` (i.e. the record is already
    indistinguishable among at least ``k`` peers and can safely be k-anonymized).
    """
    bucket_size = count_clients_in_bucket(
        facility=client.facility,
        age_cluster=client.age_cluster,
        contact_stage=client.contact_stage,
    )
    return bucket_size >= k
