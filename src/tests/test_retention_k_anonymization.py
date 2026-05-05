"""RF-T06: Charakterisierungstests fuer K-Anonymisierung (Refs #776).

Drei Cases:

1. ``k=5`` mit ≥5 Klienten in derselben Aequivalenzklasse → ``is_k_anonymous``
   liefert True.
2. ``k=5`` mit <5 Klienten → ``is_k_anonymous`` liefert False.
3. Negativtest: ``Settings.retention_use_k_anonymization=False`` (Default)
   tut **nichts** im Retention-Pfad. Das Setting ist heute dead code; der
   Pipeline ruft ``client.anonymize()`` (Hard-Anonymize) auf, nie
   ``k_anonymize_client``. Dieser Test verankert den Status Quo, damit
   ein zukuenftiger Aktivierungs-PR (Sprint 2/3) bewusst gegen das
   Charakterisierungsverhalten arbeitet, statt es heimlich zu kippen.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from core.models import Client, Event, Settings
from core.retention.anonymization import anonymize_clients
from core.services.k_anonymization import is_k_anonymous


def _make_client(
    facility, *, pseudonym, age_cluster=Client.AgeCluster.AGE_27_PLUS, stage=Client.ContactStage.IDENTIFIED
):
    return Client.objects.create(
        facility=facility,
        contact_stage=stage,
        age_cluster=age_cluster,
        pseudonym=pseudonym,
    )


@pytest.mark.django_db
class TestKAnonymity:
    def test_pos_bucket_at_or_above_k_returns_true(self, facility):
        # 5 Klienten mit denselben (age_cluster, contact_stage) → bucket=5
        for i in range(5):
            _make_client(facility, pseudonym=f"P-{i}")
        target = _make_client(facility, pseudonym="P-target")
        # bucket umfasst alle 6 Klienten in dieser Aequivalenzklasse
        assert is_k_anonymous(target, k=5) is True

    def test_neg_bucket_below_k_returns_false(self, facility):
        # Nur 2 Klienten (target + 1) → bucket=2 < k=5
        _make_client(facility, pseudonym="P-1")
        target = _make_client(facility, pseudonym="P-target")
        assert is_k_anonymous(target, k=5) is False

    def test_setting_retention_use_k_anonymization_is_no_op_today(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Charakterisierung: das Setting ``retention_use_k_anonymization``
        wird heute vom Retention-Pfad nicht gelesen.

        Setup: ein Klient mit einem soft-deleted Event → der
        Anonymisierungs-Trigger feuert. Auch wenn das Setting auf False
        steht (Default), wird der Klient via ``client.anonymize()``
        anonymisiert (Pseudonym beginnt mit ``Gelöscht-``), nicht via
        K-Anonymize (Pseudonym beginnt mit ``anon-``).

        Dieser Test wird beim spaeteren Aktivierungs-PR wissentlich
        umgestossen — er signalisiert, dass das Setting bisher dead code
        ist, und zwingt eine bewusste Entscheidung.
        """
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=True,  # << heute irrelevant
            k_anonymity_threshold=5,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,  # alle Events soft-deleted → Trigger greift
            created_by=staff_user,
        )

        anonymize_clients(facility, dry_run=False)

        client_identified.refresh_from_db()
        # Status Quo: Hard-Anonymize, nicht K-Anonymize.
        assert client_identified.pseudonym.startswith("Gelöscht-"), (
            "Aktueller Pfad: client.anonymize() → Pseudonym 'Gelöscht-...'"
        )
        assert not client_identified.pseudonym.startswith("anon-"), (
            "K-Anonymize-Pfad ist nicht angeschlossen — wenn dieser Assert "
            "kippt, hat ein PR den Aktivierungs-Schritt vorgenommen, ohne "
            "RF-T06 mit zu aktualisieren. Refs #776."
        )
        # k_anonymized-Flag bleibt entsprechend False.
        assert client_identified.k_anonymized is False
