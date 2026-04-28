"""Tests for K-anonymization service (Refs #535)."""

import pytest

from core.models import Client
from core.services.k_anonymization import (
    count_clients_in_bucket,
    is_k_anonymous,
    k_anonymize_client,
)


@pytest.mark.django_db
class TestKAnonymizeClient:
    """Cover the identifying-field generalization of ``k_anonymize_client``."""

    def test_k_anonymize_replaces_pseudonym_with_hash(self, client_identified):
        original_pseudonym = client_identified.pseudonym

        k_anonymize_client(client_identified, k=5)
        client_identified.refresh_from_db()

        assert client_identified.pseudonym != original_pseudonym
        assert client_identified.pseudonym.startswith("anon-")
        # stable, non-reversible hash bucket (sha256 prefix = 12 hex chars)
        assert len(client_identified.pseudonym) == len("anon-") + 12

    def test_k_anonymize_keeps_age_cluster(self, client_identified):
        client_identified.age_cluster = Client.AgeCluster.AGE_18_26
        client_identified.notes = "Sensible Notiz"
        client_identified.save(update_fields=["age_cluster", "notes"])

        k_anonymize_client(client_identified, k=5)
        client_identified.refresh_from_db()

        # age_cluster is the equivalence-class feature and must stay intact
        assert client_identified.age_cluster == Client.AgeCluster.AGE_18_26
        # free-text notes are cleared, record is deactivated, k_anonymized flag set
        assert client_identified.notes == ""
        assert client_identified.is_active is False
        assert client_identified.k_anonymized is True

    def test_k_anonymize_is_deterministic(self, client_identified):
        """Re-running k-anonymization produces the same bucket id."""
        k_anonymize_client(client_identified, k=5)
        first_pseudonym = client_identified.pseudonym

        k_anonymize_client(client_identified, k=5)
        client_identified.refresh_from_db()

        assert client_identified.pseudonym == first_pseudonym

    def test_model_method_delegates_to_service(self, client_identified):
        """``Client.k_anonymize()`` is a thin wrapper around the service."""
        client_identified.k_anonymize(k=5)
        client_identified.refresh_from_db()

        assert client_identified.pseudonym.startswith("anon-")
        assert client_identified.k_anonymized is True


@pytest.mark.django_db
class TestIsKAnonymous:
    """Equivalence-class checks for k-anonymity threshold."""

    def test_is_k_anonymous_below_threshold_returns_false(self, facility, staff_user):
        # Only 2 clients share the bucket — below k=5
        for i in range(2):
            Client.objects.create(
                facility=facility,
                pseudonym=f"below-{i}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                age_cluster=Client.AgeCluster.AGE_18_26,
                created_by=staff_user,
            )
        target = Client.objects.filter(age_cluster=Client.AgeCluster.AGE_18_26).first()

        assert is_k_anonymous(target, k=5) is False

    def test_is_k_anonymous_at_threshold_returns_true(self, facility, staff_user):
        # Exactly 5 clients share the bucket — meets k=5
        for i in range(5):
            Client.objects.create(
                facility=facility,
                pseudonym=f"at-{i}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
                created_by=staff_user,
            )
        target = Client.objects.filter(age_cluster=Client.AgeCluster.AGE_27_PLUS).first()

        assert is_k_anonymous(target, k=5) is True

    def test_count_clients_in_bucket_matches_equivalence_class(self, facility, staff_user):
        for i in range(3):
            Client.objects.create(
                facility=facility,
                pseudonym=f"bucket-a-{i}",
                contact_stage=Client.ContactStage.IDENTIFIED,
                age_cluster=Client.AgeCluster.U18,
                created_by=staff_user,
            )
        # A different bucket — must not be counted
        Client.objects.create(
            facility=facility,
            pseudonym="bucket-b",
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.U18,
            created_by=staff_user,
        )

        count = count_clients_in_bucket(
            facility=facility,
            age_cluster=Client.AgeCluster.U18,
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        assert count == 3
