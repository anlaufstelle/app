"""Tests for the dashboard views and related services."""

import pytest
from django.urls import reverse

from core.models import RecentClientVisit
from core.services.clients import track_client_visit


@pytest.mark.django_db
class TestGlobalSearchView:
    def test_global_search_returns_results(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(
            reverse("core:global_search"),
            {"q": "ID-01"},
        )
        assert response.status_code == 200
        assert "Test-ID-01" in response.content.decode()

    def test_global_search_limits_results(self, client, staff_user, facility):
        """Global search cappt Klientel-Sektion bei 5; Fuzzy-Sektion leakt keinen
        icontains-Overflow (Refs #536, #580)."""
        from core.models import Client as ClientModel

        for i in range(10):
            ClientModel.objects.create(
                facility=facility,
                pseudonym=f"Batch-{i:02d}",
                created_by=staff_user,
            )
        client.force_login(staff_user)
        response = client.get(reverse("core:global_search"), {"q": "Batch"})
        content = response.content.decode()
        # Alle 10 "Batch-*" sind icontains-Match → keiner darf in Fuzzy landen.
        # Nur die 5 in der Klientel-Sektion dürfen sichtbar sein.
        assert content.count("Batch-") == 5

    def test_global_search_empty_query(self, client, staff_user):
        client.force_login(staff_user)
        response = client.get(reverse("core:global_search"), {"q": ""})
        assert response.status_code == 200

    def test_global_search_requires_auth(self, client):
        response = client.get(reverse("core:global_search"), {"q": "test"})
        assert response.status_code == 302

    def test_global_search_shows_all_results_link(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        response = client.get(reverse("core:global_search"), {"q": "ID-01"})
        content = response.content.decode()
        assert "/search/" in content


@pytest.mark.django_db
class TestTrackClientVisit:
    def test_creates_visit(self, staff_user, facility, client_identified):
        track_client_visit(staff_user, client_identified, facility)
        assert RecentClientVisit.objects.filter(user=staff_user, client=client_identified).exists()

    def test_upserts_on_revisit(self, staff_user, facility, client_identified):
        track_client_visit(staff_user, client_identified, facility)
        track_client_visit(staff_user, client_identified, facility)
        assert RecentClientVisit.objects.filter(user=staff_user, client=client_identified).count() == 1

    def test_prunes_old_visits(self, staff_user, facility):
        from core.models import Client as ClientModel

        clients = []
        for i in range(25):
            c = ClientModel.objects.create(
                facility=facility,
                pseudonym=f"Prune-{i:02d}",
                created_by=staff_user,
            )
            clients.append(c)
            track_client_visit(staff_user, c, facility)

        assert RecentClientVisit.objects.filter(user=staff_user).count() == 20

    def test_client_detail_tracks_visit(self, client, staff_user, client_identified):
        client.force_login(staff_user)
        client.get(reverse("core:client_detail", kwargs={"pk": client_identified.pk}))
        assert RecentClientVisit.objects.filter(user=staff_user, client=client_identified).exists()


@pytest.mark.django_db
class TestSearchService:
    def test_search_clients_and_events(self, staff_user, facility, client_identified):
        from core.services.search import search_clients_and_events

        clients, events = search_clients_and_events(facility, staff_user, "ID-01")
        assert len(clients) > 0
        assert clients[0].pseudonym == "Test-ID-01"

    def test_search_respects_max_limits(self, staff_user, facility):
        from core.models import Client as ClientModel
        from core.services.search import search_clients_and_events

        for i in range(10):
            ClientModel.objects.create(
                facility=facility,
                pseudonym=f"Limit-{i:02d}",
                created_by=staff_user,
            )
        clients, events = search_clients_and_events(facility, staff_user, "Limit", max_clients=3)
        assert len(clients) <= 3

    def test_search_empty_query(self, staff_user, facility):
        from core.services.search import search_clients_and_events

        clients, events = search_clients_and_events(facility, staff_user, "")
        assert clients == []
        assert events == []
