"""Sammelaktionen im Löschvorschlags-Dashboard antworten nie mit 500 (Refs #2373).

Die Sammel-Views laden die ausgewählten Vorschläge mit einem Status-Filter,
aber ohne Sperre. Wird ein Vorschlag zwischen diesem Laden und der Aktion
entschieden (zweite Leitung, zweiter Tab), weist der Dienst ihn unter der
Zeilensperre mit ``ValidationError`` ab. Das Genehmigen überspringt solche
Zeilen schon länger; Zurückstellen und Ablehnen brachen die Anfrage dagegen
mit einem Serverfehler ab. Erwartet wird für alle drei Aktionen dasselbe:
die offenen Vorschläge werden bearbeitet, der entschiedene bleibt unverändert
und wird als übersprungen gemeldet.

Das Rennen wird beobachtend nachgestellt: das Laden der View läuft
unverändert, danach wird ein geladener Vorschlag in der Datenbank entschieden.
"""

import uuid
from datetime import date, timedelta

import pytest
from django.contrib import messages
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import AuditLog, RetentionProposal
from core.views.retention import _BulkActionMixin

_AKTIONEN = [
    "core:retention_bulk_approve",
    "core:retention_bulk_defer",
    "core:retention_bulk_reject",
]

_ERWARTET = {
    "core:retention_bulk_approve": RetentionProposal.Status.APPROVED,
    "core:retention_bulk_defer": RetentionProposal.Status.DEFERRED,
    "core:retention_bulk_reject": RetentionProposal.Status.REJECTED,
}


def _vorschlag(facility):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=uuid.uuid4(),
        retention_category="anonymous",
        deletion_due_at=date.today() - timedelta(days=1),
        status=RetentionProposal.Status.PENDING,
    )


@pytest.fixture
def zwei_offene(facility):
    return [_vorschlag(facility), _vorschlag(facility)]


@pytest.fixture
def entschieden_nach_dem_laden(monkeypatch):
    """Entscheidet den zuletzt ausgewählten Vorschlag, nachdem die View geladen hat."""
    original = _BulkActionMixin._load_proposals

    def laden_und_dann_entscheiden(self, request, ids):
        geladen = original(self, request, ids)
        assert len(geladen) == 2
        RetentionProposal.objects.filter(pk=ids[-1]).update(status=RetentionProposal.Status.APPROVED)
        return geladen

    monkeypatch.setattr(_BulkActionMixin, "_load_proposals", laden_und_dann_entscheiden)


@pytest.mark.django_db
@pytest.mark.usefixtures("entschieden_nach_dem_laden")
class TestSammelaktionUeberspringtEntschiedene:
    @pytest.mark.parametrize("url_name", _AKTIONEN)
    def test_htmx_antwortet_mit_zaehlung_und_uebersprungenen(self, client, lead_user, zwei_offene, url_name):
        client.force_login(lead_user)
        client.raise_request_exception = False

        response = client.post(
            reverse(url_name),
            {"proposal_ids": [str(p.pk) for p in zwei_offene]},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 302
        assert response["HX-Redirect"] == reverse("core:retention_dashboard")
        gemeldet = [(m.level, str(m)) for m in get_messages(response.wsgi_request)]
        assert gemeldet == [
            (messages.SUCCESS, "1 Löschvorschlag aktualisiert."),
            (messages.WARNING, "1 Löschvorschlag übersprungen (Aufbewahrungssperre oder Status)."),
        ]

    @pytest.mark.parametrize("url_name", _AKTIONEN)
    def test_offener_bearbeitet_entschiedener_unveraendert(self, client, lead_user, zwei_offene, url_name):
        client.force_login(lead_user)
        client.raise_request_exception = False
        offen, entschieden = zwei_offene
        audit_vorher = AuditLog.objects.filter(target_id=str(entschieden.target_id)).count()

        client.post(reverse(url_name), {"proposal_ids": [str(p.pk) for p in zwei_offene]})

        offen.refresh_from_db()
        entschieden.refresh_from_db()
        assert offen.status == _ERWARTET[url_name]
        assert entschieden.status == RetentionProposal.Status.APPROVED
        assert entschieden.defer_count == 0
        assert AuditLog.objects.filter(target_id=str(entschieden.target_id)).count() == audit_vorher

    @pytest.mark.parametrize("url_name", _AKTIONEN)
    def test_json_meldet_nur_die_bearbeiteten(self, client, lead_user, zwei_offene, url_name):
        client.force_login(lead_user)
        client.raise_request_exception = False

        response = client.post(
            reverse(url_name),
            {"proposal_ids": [str(p.pk) for p in zwei_offene]},
            headers={"Accept": "application/json"},
        )

        assert response.status_code == 200
        assert response.json() == {"processed": 1}


@pytest.mark.django_db
class TestSammelaktionOhneRennen:
    """Gegenprobe: ohne Entscheidung dazwischen bleibt die Antwort wie bisher."""

    @pytest.mark.parametrize("url_name", _AKTIONEN)
    def test_alle_bearbeitet_keine_warnung(self, client, lead_user, zwei_offene, url_name):
        client.force_login(lead_user)

        response = client.post(
            reverse(url_name),
            {"proposal_ids": [str(p.pk) for p in zwei_offene]},
            headers={"HX-Request": "true"},
        )

        gemeldet = [(m.level, str(m)) for m in get_messages(response.wsgi_request)]
        assert gemeldet == [(messages.SUCCESS, "2 Löschvorschläge aktualisiert.")]
        for vorschlag in zwei_offene:
            vorschlag.refresh_from_db()
            assert vorschlag.status == _ERWARTET[url_name]
