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

    def test_html_weist_die_live_datenquelle_aus(self, client, lead_user):
        """Refs #1743: sichtbarer Quellenausweis im Kopf des externen Berichts."""
        client.force_login(lead_user)
        response = client.get("/statistics/external/")
        content = response.content.decode()
        assert "Live-Abfrage zum Erstellungszeitpunkt" in content
        assert "bei jedem Aufruf neu aus den Ereignisdaten berechnet" in content

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


@pytest.mark.django_db
class TestPersonenTabellenImArtefakt:
    """K0-Fail-safe (Refs #1768) an der Ausgabegrenze: liegt die Personenzahl
    unter der Schwelle, darf die Altersverteilung weder im JSON noch im HTML
    erscheinen — und das HTML darf die Luecke nicht als „keine Daten"
    ausgeben (das waere schlicht falsch).
    """

    @pytest.fixture
    def eine_person_viele_ereignisse(self, facility, client_qualified, doc_type_contact, staff_user):
        from django.utils import timezone

        from core.models import Event

        for _index in range(9):
            Event.objects.create(
                facility=facility,
                client=client_qualified,
                document_type=doc_type_contact,
                occurred_at=timezone.now(),
                data_json={},
                created_by=staff_user,
            )
        return facility

    def test_json_ohne_personenabgeleitete_tabellen(self, client, lead_user, eine_person_viele_ereignisse):
        client.force_login(lead_user)

        data = json.loads(client.get("/statistics/external/?format=json").content)

        assert data["unique_clients"] is None
        assert "by_age_cluster" not in data
        assert "by_contact_stage" not in data
        assert data["total_contacts"] == 9

    def test_html_zeigt_die_altersgruppen_als_unterdrueckt_statt_als_leer(
        self, client, lead_user, eine_person_viele_ereignisse
    ):
        client.force_login(lead_user)

        content = client.get("/statistics/external/").content.decode()

        assert "Nach Altersgruppe" in content  # Abschnitt bleibt sichtbar
        assert "Unbekannt" not in content  # aber ohne eine einzige Zeile
        assert "Angaben nach Altersgruppe unterdrückt" in content
        assert "Keine Daten im Zeitraum." not in content.split("report-age")[-1]
