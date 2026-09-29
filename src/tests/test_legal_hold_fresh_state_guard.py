"""Refs #2374: Die Dienste der Aufbewahrungssperre entscheiden am frisch gesperrten Zustand.

Der Aufrufer hält Hold, Vorschlag oder Fall womöglich seit Minuten im
Speicher (die Karten im Löschfristen-Dashboard aktualisieren sich nicht von
selbst). Entschieden wird deshalb an der gesperrt neu gelesenen Zeile, nicht
an der mitgegebenen Instanz. Der veraltete Zustand wird in jedem Test über eine
zweite Instanz bzw. ein ``update()`` in der Datenbank hergestellt, nie nur im
Speicher.
"""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import AuditLog, LegalHold, RetentionProposal, User
from core.retention.legal_holds import create_legal_hold, dismiss_legal_hold
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db


def _audit_zeilen(kategorie):
    return AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD, detail__category=kategorie)


@pytest.fixture
def zweite_leitung(facility):
    user = User.objects.create_user(
        username="hold_zweite_leitung",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def gehaltener_vorschlag(facility, retention_proposal, sample_event, lead_user):
    hold = LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="Laufendes Verfahren",
        created_by=lead_user,
    )
    retention_proposal.status = RetentionProposal.Status.HELD
    retention_proposal.save(update_fields=["status"])
    return hold, retention_proposal


class TestHoldAufhebenLiestDenHoldGesperrtNeu:
    def test_zweite_aufhebung_ueber_veraltete_instanz_wird_abgelehnt(
        self, gehaltener_vorschlag, lead_user, zweite_leitung
    ):
        hold, _proposal = gehaltener_vorschlag
        veraltet = LegalHold.objects.get(pk=hold.pk)
        dismiss_legal_hold(hold, lead_user)
        hold.refresh_from_db()
        erster_zeitpunkt = hold.dismissed_at

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            dismiss_legal_hold(veraltet, zweite_leitung)

        assert "bereits aufgehoben" in str(excinfo.value)
        # Beobachtet: die Hold-Zeile wird unter Sperre neu gelesen, bevor
        # entschieden wird — und nichts sonst wird gesperrt.
        assert aufzeichnung.sperrfolge() == ["core_legalhold"]
        hold.refresh_from_db()
        assert hold.dismissed_at == erster_zeitpunkt
        assert hold.dismissed_by_id == lead_user.pk
        assert _audit_zeilen("legal_hold_dismissed").count() == 1

    def test_veraltete_aufhebung_setzt_den_vorschlag_unter_neuem_hold_nicht_zurueck(
        self, gehaltener_vorschlag, lead_user, zweite_leitung
    ):
        """Nach der ersten Aufhebung ordnet jemand einen neuen Hold an. Die
        veraltete Aufhebung des alten Holds darf den Vorschlag nicht wieder
        auf ``pending`` stellen — der neue Hold gilt."""
        hold, proposal = gehaltener_vorschlag
        veraltet = LegalHold.objects.get(pk=hold.pk)
        dismiss_legal_hold(hold, lead_user)
        proposal.refresh_from_db()
        neuer_hold = create_legal_hold(proposal, lead_user, "Neues Verfahren")

        with pytest.raises(ValidationError):
            dismiss_legal_hold(veraltet, zweite_leitung)

        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.HELD
        neuer_hold.refresh_from_db()
        assert neuer_hold.dismissed_at is None
        assert _audit_zeilen("legal_hold_dismissed").count() == 1

    def test_zweiter_post_auf_die_aufhebung_antwortet_409_ohne_ueberschreiben(
        self, client, gehaltener_vorschlag, lead_user, zweite_leitung
    ):
        hold, _proposal = gehaltener_vorschlag
        url = reverse("core:retention_dismiss_hold", kwargs={"pk": hold.pk})
        client.force_login(lead_user)
        assert client.post(url).status_code == 200
        hold.refresh_from_db()
        erster_zeitpunkt = hold.dismissed_at

        client.force_login(zweite_leitung)
        antwort = client.post(url)

        assert antwort.status_code == 409
        assert "bereits aufgehoben" in antwort.content.decode()
        hold.refresh_from_db()
        assert hold.dismissed_at == erster_zeitpunkt
        assert hold.dismissed_by_id == lead_user.pk
        assert _audit_zeilen("legal_hold_dismissed").count() == 1
