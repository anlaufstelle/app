"""Regression tests for flushing merged clients."""

import pytest

from core.models import Client
from core.seed.flush import flush_seed_data


@pytest.mark.django_db
def test_flush_removes_a_chain_of_merged_clients(facility, staff_user) -> None:
    client_a = Client.objects.create(facility=facility, pseudonym="Flush-A", created_by=staff_user)
    client_b = Client.objects.create(facility=facility, pseudonym="Flush-B", created_by=staff_user)
    client_c = Client.objects.create(facility=facility, pseudonym="Flush-C", created_by=staff_user)
    Client.objects.create(facility=facility, pseudonym="Flush-unverbunden", created_by=staff_user)
    Client.objects.filter(pk=client_a.pk).update(merged_into=client_b)
    Client.objects.filter(pk=client_b.pk).update(merged_into=client_c)

    flush_seed_data()

    assert not Client.objects.exists()


@pytest.mark.django_db
def test_flush_without_merged_clients_remains_empty(facility, staff_user) -> None:
    Client.objects.create(facility=facility, pseudonym="Flush-ohne-Merge", created_by=staff_user)

    flush_seed_data()

    assert not Client.objects.exists()
