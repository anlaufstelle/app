"""View-Tests fuer ExternalReportView (Refs #921)."""

from __future__ import annotations

import json

import pytest


@pytest.mark.django_db
class TestExternalReportView:
    """HTML- und JSON-Output unter /statistics/external/."""

    def test_html_response_for_lead(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get("/statistics/external/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Datenschutzprofil" in content
        assert "K-Anonymit" in content  # K-Anonymität, robust gegen Encoding

    def test_html_response_for_facility_admin(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get("/statistics/external/")
        assert response.status_code == 200

    def test_staff_blocked(self, client, staff_user):
        """staff darf den externen Bericht NICHT aufrufen (Lead+ only)."""
        client.force_login(staff_user)
        response = client.get("/statistics/external/", follow=False)
        # LeadOrAdminRequiredMixin redirected zu / (oder 403)
        assert response.status_code in (302, 403)

    def test_anonymous_redirects_to_login(self, client):
        response = client.get("/statistics/external/", follow=False)
        assert response.status_code == 302
        assert "/login/" in response["Location"]

    def test_json_format_returns_json(self, client, lead_user):
        client.force_login(lead_user)
        response = client.get("/statistics/external/?format=json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        data = json.loads(response.content)
        assert "metadata" in data
        assert "total_contacts" in data
        assert "top_clients" not in data

    def test_metadata_block_in_json(self, client, lead_user, facility):
        client.force_login(lead_user)
        response = client.get("/statistics/external/?format=json")
        data = json.loads(response.content)
        meta = data["metadata"]
        assert meta["facility"] == facility.name
        assert meta["privacy_profile"] == "external"
        assert meta["k_anonymity_threshold"] >= 1
