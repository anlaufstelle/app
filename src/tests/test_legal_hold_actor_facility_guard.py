"""Refs #2374: Die Dienste der Aufbewahrungssperre ziehen die Einrichtungsgrenze der handelnden Person selbst.

Über HTTP scopet die View, in Produktion trägt die Zeilensicherheit — der
Dienst ist trotzdem die Single Source of Truth, wie bei den
Vier-Augen-Diensten. Je Dienst: eine Leitung aus einer fremden Einrichtung und
eine Leitung ohne Einrichtung werden abgewiesen, BEVOR eine Zeile gesperrt oder
geschrieben wird (fail-closed); die Gegenprobe mit der Leitung der Einrichtung
geht durch.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, LegalHold, RetentionProposal, User
from core.retention.legal_holds import dismiss_legal_hold
from tests._row_lock_helpers import mitschnitt

pytestmark = pytest.mark.django_db

_FREMDE_AKTEURE = ["fremde_leitung", "leitung_ohne_einrichtung"]


def _leitung(username, facility):
    user = User.objects.create_user(
        username=username,
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
        can_confirm_deletion=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def fremde_leitung(second_facility):
    return _leitung("hold_fremde_leitung", second_facility)


@pytest.fixture
def leitung_ohne_einrichtung(db):
    return _leitung("hold_leitung_ohne_einrichtung", None)


def _audit_zeilen(kategorie):
    return AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD, detail__category=kategorie)


@pytest.fixture
def gehaltener_vorschlag(facility, retention_proposal, sample_event, lead_user):
    """Vorschlag unter einem aktiven Hold — der Ausgangszustand der Aufhebung."""
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


class TestHoldAufhebenPrueftDieEinrichtungDerLeitung:
    @pytest.mark.parametrize("akteur", _FREMDE_AKTEURE)
    def test_fremde_oder_einrichtungslose_leitung_wird_vor_jeder_sperre_abgewiesen(
        self, request, akteur, gehaltener_vorschlag
    ):
        hold, proposal = gehaltener_vorschlag
        user = request.getfixturevalue(akteur)

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            dismiss_legal_hold(hold, user)

        assert "gehört zu einer anderen Einrichtung" in str(excinfo.value)
        assert aufzeichnung.sperrfolge() == []
        hold.refresh_from_db()
        assert hold.dismissed_at is None
        assert hold.dismissed_by_id is None
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.HELD
        assert not _audit_zeilen("legal_hold_dismissed").exists()

    def test_leitung_der_einrichtung_hebt_den_hold_auf(self, gehaltener_vorschlag, lead_user):
        hold, proposal = gehaltener_vorschlag

        dismiss_legal_hold(hold, lead_user)

        hold.refresh_from_db()
        assert hold.dismissed_at is not None
        assert hold.dismissed_by_id == lead_user.pk
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert _audit_zeilen("legal_hold_dismissed").count() == 1
