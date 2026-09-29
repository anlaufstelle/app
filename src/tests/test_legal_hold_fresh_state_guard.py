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
from django.db import DatabaseError
from django.urls import reverse

from core.models import AuditLog, Case, Client, LegalHold, RetentionProposal, User
from core.retention.legal_holds import create_hold_for_target, create_legal_hold, dismiss_legal_hold
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


class TestHoldAnlageAmVorschlagLiestDenVorschlagGesperrtNeu:
    @pytest.mark.parametrize(
        ("zwischenstand", "meldung"),
        [
            (RetentionProposal.Status.REJECTED, "bereits bearbeitet"),
            # Gehalten heißt: für den Vorschlag besteht bereits eine Sperre —
            # dieselbe Meldung wie bisher beim Doppelklick.
            (RetentionProposal.Status.HELD, "existiert bereits ein aktiver Hold"),
        ],
    )
    def test_zwischenzeitlich_entschiedener_vorschlag_bekommt_keinen_hold(
        self, retention_proposal, lead_user, zwischenstand, meldung
    ):
        veraltet = RetentionProposal.objects.get(pk=retention_proposal.pk)
        RetentionProposal.objects.filter(pk=retention_proposal.pk).update(status=zwischenstand)

        with pytest.raises(ValidationError) as excinfo:
            create_legal_hold(veraltet, lead_user, "Gerichtsverfahren")

        assert meldung in str(excinfo.value)
        assert not LegalHold.objects.filter(target_id=retention_proposal.target_id).exists()
        assert not _audit_zeilen("legal_hold_created").exists()
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == zwischenstand

    def test_zweite_anlage_auf_den_gehaltenen_vorschlag_wird_mit_meldung_abgewiesen(
        self, retention_proposal, lead_user
    ):
        """Der reguläre Zustand ``held``: der erste Hold ist angelegt und aktiv.

        Die zweite Anlage (Doppelklick, zweites Fenster) entscheidet am
        gesperrt gelesenen Vorschlag — als fachliche Absage, nicht erst an der
        Eindeutigkeit der Hold-Zeile.
        Die Meldung nennt den bestehenden Hold.
        """
        veraltet = RetentionProposal.objects.get(pk=retention_proposal.pk)
        erster = create_legal_hold(retention_proposal, lead_user, "Erstes Verfahren")

        with pytest.raises(ValidationError) as excinfo:
            create_legal_hold(veraltet, lead_user, "Zweiter Klick")

        assert "existiert bereits ein aktiver Hold" in str(excinfo.value)
        aktive = LegalHold.objects.filter(target_id=retention_proposal.target_id, dismissed_at__isnull=True)
        assert list(aktive) == [erster]
        assert _audit_zeilen("legal_hold_created").count() == 1
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.HELD

    @pytest.mark.parametrize(
        "zwischenstand",
        [RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED, RetentionProposal.Status.DEFERRED],
    )
    def test_noch_moegliche_loeschung_laesst_sich_jederzeit_anhalten(
        self, retention_proposal, lead_user, zwischenstand
    ):
        veraltet = RetentionProposal.objects.get(pk=retention_proposal.pk)
        RetentionProposal.objects.filter(pk=retention_proposal.pk).update(status=zwischenstand)

        hold = create_legal_hold(veraltet, lead_user, "Gerichtsverfahren")

        assert hold.dismissed_at is None
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.HELD
        assert veraltet.status == RetentionProposal.Status.HELD
        assert _audit_zeilen("legal_hold_created").count() == 1

    def test_vorschlag_wird_nach_dem_zielanker_gesperrt(self, retention_proposal, lead_user):
        with mitschnitt() as aufzeichnung:
            create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren")

        folge = aufzeichnung.sperrfolge()
        assert "core_retentionproposal" in folge, folge
        assert folge.index("core_client") < folge.index("core_event") < folge.index("core_retentionproposal"), folge

    def test_scheitert_der_vorschlags_write_bleibt_weder_hold_noch_audit_zurueck(
        self, retention_proposal, lead_user, monkeypatch
    ):
        def _schreibfehler(self, *args, **kwargs):
            raise DatabaseError("Vorschlag nicht schreibbar")

        monkeypatch.setattr(RetentionProposal, "save", _schreibfehler)

        with pytest.raises(DatabaseError):
            create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren")

        assert not LegalHold.objects.filter(target_id=retention_proposal.target_id).exists()
        assert not _audit_zeilen("legal_hold_created").exists()

    def test_vorschlag_in_anderer_einrichtung_der_datenbank_bekommt_keinen_hold(
        self, retention_proposal, lead_user, second_facility
    ):
        """Die Instanz nennt die Einrichtung der Leitung, die Zeile liegt
        inzwischen in einer anderen — entschieden wird an der Zeile."""
        RetentionProposal.objects.filter(pk=retention_proposal.pk).update(facility=second_facility)

        with pytest.raises(ValidationError) as excinfo:
            create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren")

        # The locked read is facility-scoped: the moved row is as absent as a
        # removed one (under row-level security it is invisible), so it gets
        # the neutral answer of the other proposal decisions.
        assert "bereits bearbeitet" in str(excinfo.value)
        assert not LegalHold.objects.filter(target_id=retention_proposal.target_id).exists()
        assert not _audit_zeilen("legal_hold_created").exists()

    def test_post_auf_entschiedenen_vorschlag_antwortet_409_ohne_hold(self, client, retention_proposal, lead_user):
        RetentionProposal.objects.filter(pk=retention_proposal.pk).update(status=RetentionProposal.Status.REJECTED)
        client.force_login(lead_user)

        antwort = client.post(
            reverse("core:retention_hold", kwargs={"pk": retention_proposal.pk}),
            {"reason": "Gerichtsverfahren"},
        )

        assert antwort.status_code == 409
        assert "bereits bearbeitet" in antwort.content.decode()
        assert not LegalHold.objects.filter(target_id=retention_proposal.target_id).exists()
        retention_proposal.refresh_from_db()
        assert retention_proposal.status == RetentionProposal.Status.REJECTED


class TestFallHoldPrueftDieBindungAnDerGesperrtenFallzeile:
    """Der Fall-Zweig liest die Personen-ID des Falls ungesperrt vor, weil die
    Personenzeile in der kanonischen Ordnung vor der Fallzeile gesperrt wird.
    Haengt ein paralleler Vorgang den Fall waehrend der Sperrwartezeit an eine
    andere Person, haette der Hold die falsche Person als Anker — die Bindung
    wird deshalb an der gesperrten Fallzeile geprueft, wie im Ereignis-Zweig."""

    @pytest.fixture
    def andere_person(self, facility, staff_user):
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Andere-Person-01",
            created_by=staff_user,
        )

    def test_nach_der_personensperre_umgehaengter_fall_bekommt_keinen_hold(
        self, facility, case_open, andere_person, lead_user
    ):
        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre(
                "core_client", lambda: Case.objects.filter(pk=case_open.pk).update(client=andere_person)
            )
            with pytest.raises(ValidationError) as excinfo:
                create_hold_for_target(
                    facility, target_type="Case", target_id=case_open.pk, user=lead_user, reason="Verfahren"
                )

        assert aufzeichnung.offene_schranken() == []
        assert "Personenzuordnung des Falls" in str(excinfo.value)
        assert not LegalHold.objects.filter(target_id=case_open.pk).exists()
        assert not _audit_zeilen("legal_hold_created").exists()

    def test_unveraenderte_bindung_sperrt_person_fall_ereignisse_in_dieser_folge(
        self, facility, case_open, sample_event, lead_user
    ):
        with mitschnitt() as aufzeichnung:
            create_hold_for_target(
                facility, target_type="Case", target_id=case_open.pk, user=lead_user, reason="Verfahren"
            )

        assert aufzeichnung.sperrfolge()[:3] == ["core_client", "core_case", "core_event"]
        assert LegalHold.objects.filter(target_id=case_open.pk, dismissed_at__isnull=True).count() == 1
