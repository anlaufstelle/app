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
from core.retention.legal_holds import create_hold_for_target, create_legal_hold, dismiss_legal_hold
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

    def test_grenze_gilt_fuer_die_zeile_in_der_datenbank_nicht_fuer_die_instanz(
        self, gehaltener_vorschlag, lead_user, second_facility
    ):
        """Die Instanz im Speicher nennt die Einrichtung der Leitung, die Zeile
        in der Datenbank liegt in einer anderen — entschieden wird an der
        Zeile."""
        hold, _proposal = gehaltener_vorschlag
        LegalHold.objects.filter(pk=hold.pk).update(facility=second_facility)

        with pytest.raises(ValidationError) as excinfo:
            dismiss_legal_hold(hold, lead_user)

        assert "gehört zu einer anderen Einrichtung" in str(excinfo.value)
        hold.refresh_from_db()
        assert hold.dismissed_at is None
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


class TestHoldAnlageAmVorschlagPrueftDieEinrichtungDerLeitung:
    @pytest.mark.parametrize("akteur", _FREMDE_AKTEURE)
    def test_fremde_oder_einrichtungslose_leitung_wird_vor_jeder_sperre_abgewiesen(
        self, request, akteur, retention_proposal
    ):
        user = request.getfixturevalue(akteur)

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            create_legal_hold(retention_proposal, user, "Gerichtsverfahren")

        assert "Löschvorschlag gehört zu einer anderen Einrichtung" in str(excinfo.value)
        # Weder der Zielanker (Person, Ereignis) noch der Vorschlag wurden gesperrt.
        assert aufzeichnung.sperrfolge() == []
        assert not LegalHold.objects.filter(target_id=retention_proposal.target_id).exists()
        assert not _audit_zeilen("legal_hold_created").exists()
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.PENDING

    def test_leitung_der_einrichtung_legt_den_hold_an(self, retention_proposal, lead_user):
        hold = create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren")

        assert hold.created_by_id == lead_user.pk
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.HELD
        assert _audit_zeilen("legal_hold_created").count() == 1


#: Je Hold-Zweig mit Zielsperre ein lokales Ziel (Fixture-Name).
_LOKALE_ZIELE = {"Client": "client_identified", "Case": "case_open", "Event": "sample_event"}


class TestHoldAnlageAmZielPrueftDieEinrichtungDerLeitung:
    """Die Einrichtung, in der der Hold entsteht, ist die der Leitung — die
    Ziel-ID darf dagegen frei sein (auch eine unbekannte oder fremde Zeile
    bleibt zulaessig, dort gibt es nur nichts zu koordinieren)."""

    @pytest.mark.parametrize("akteur", _FREMDE_AKTEURE)
    @pytest.mark.parametrize("target_type", sorted(_LOKALE_ZIELE))
    def test_fremde_oder_einrichtungslose_leitung_sperrt_und_schreibt_nichts(
        self, request, akteur, target_type, facility
    ):
        ziel = request.getfixturevalue(_LOKALE_ZIELE[target_type])
        user = request.getfixturevalue(akteur)

        with mitschnitt() as aufzeichnung, pytest.raises(ValidationError) as excinfo:
            create_hold_for_target(facility, target_type=target_type, target_id=ziel.pk, user=user, reason="Verfahren")

        assert "Aufbewahrungssperre gehört zu einer anderen Einrichtung" in str(excinfo.value)
        assert aufzeichnung.sperrfolge() == []
        assert not LegalHold.objects.filter(target_id=ziel.pk).exists()
        assert not _audit_zeilen("legal_hold_created").exists()

    @pytest.mark.parametrize("target_type", sorted(_LOKALE_ZIELE))
    def test_leitung_der_einrichtung_legt_den_hold_an(self, request, target_type, facility, lead_user):
        ziel = request.getfixturevalue(_LOKALE_ZIELE[target_type])

        hold = create_hold_for_target(
            facility, target_type=target_type, target_id=ziel.pk, user=lead_user, reason="Verfahren"
        )

        assert hold.facility_id == facility.pk
        assert hold.created_by_id == lead_user.pk
        assert _audit_zeilen("legal_hold_created").count() == 1
