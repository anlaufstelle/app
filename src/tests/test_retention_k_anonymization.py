"""Wire-Up: K-Anonymisierung im Retention-Pfad (Refs #776, #780).

Vier Cases:

1. ``k=5`` mit ≥5 Klienten in derselben Aequivalenzklasse → ``is_k_anonymous``
   liefert True.
2. ``k=5`` mit <5 Klienten → ``is_k_anonymous`` liefert False.
3. ``Settings.retention_use_k_anonymization=False`` (Default) → Pipeline ruft
   ``client.anonymize()`` (Hard-Anonymize), Pseudonym beginnt mit ``Gelöscht-``.
4. ``Settings.retention_use_k_anonymization=True`` → Pipeline ruft
   ``k_anonymize_client(client, k=facility.settings.k_anonymity_threshold)``,
   Pseudonym beginnt mit ``anon-`` und ``k_anonymized=True``.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.models import Client, Event, Settings
from core.retention.anonymization import anonymize_clients
from core.services.compliance import is_k_anonymous


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

    def test_setting_disabled_uses_hard_anonymize(self, facility, staff_user, doc_type_contact, client_identified):
        """Setting=False (Default) → ``client.anonymize()``-Pfad (Refs #780)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=False,
            k_anonymity_threshold=5,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        anonymize_clients(facility, dry_run=False)

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("Gelöscht-")
        assert not client_identified.pseudonym.startswith("anon-")
        assert client_identified.k_anonymized is False

    def test_setting_enabled_uses_k_anonymize(self, facility, staff_user, doc_type_contact, client_identified):
        """Setting=True → ``k_anonymize_client()``-Pfad mit Schwelle aus
        ``k_anonymity_threshold`` (Refs #780, Pfad A im Issue-Body)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=True,
            k_anonymity_threshold=5,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        anonymize_clients(facility, dry_run=False)

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("anon-"), "K-Anon-Pfad muss greifen, sobald Setting aktiv ist."
        assert not client_identified.pseudonym.startswith("Gelöscht-")
        assert client_identified.k_anonymized is True
        assert client_identified.notes == ""
        assert client_identified.is_active is False

    def test_enforce_retention_command_uses_k_anon_when_enabled(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """End-to-End: ``call_command('enforce_retention')`` wendet den
        K-Anon-Pfad an, wenn das Facility-Setting es verlangt (Refs #780)."""
        Settings.objects.create(
            facility=facility,
            retention_use_k_anonymization=True,
            k_anonymity_threshold=5,
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )

        call_command("enforce_retention")

        client_identified.refresh_from_db()
        assert client_identified.pseudonym.startswith("anon-")
        assert client_identified.k_anonymized is True
