"""``approve_proposal`` prüft Status und Legal Hold unter Lock (Refs #2109).

Der Dienst setzte ``status = APPROVED`` und schrieb die Audit-Zeile, ohne die
Vorschlagszeile zu sperren, ohne den Status erneut zu lesen und ohne den Hold
auf dem Ziel zu prüfen. Zwei Folgen:

* Die Oberfläche erzeugte **APPROVED-Vorschläge unter aktivem Hold**. Gelöscht
  wurde nichts (das Enforcement schließt gehaltene Ziele aus), aber der
  Audit-Trail behauptete eine Genehmigung, die unter laufender Sperre gar nicht
  hätte ergehen dürfen — bei einer behördlichen Prüfung genau die falsche
  Aussage. Dynamisch belegt in ``/work/redteam-pocs/h9_b4_b5_retention_hold_race.py``
  Teil A.
* Zwei gleichzeitige Genehmigungen schrieben **zwei** Audit-Zeilen für einen
  Vorgang.

Muster des Fixes ist ``approve_client_merge``/``reject_client_merge``
(``services/client/merge.py``): Zeile per ``select_for_update`` neu laden,
Status erneut prüfen, sonst ``ValidationError``.
"""

import threading
import time
from datetime import date, timedelta
from unittest import mock

import pytest
from django.contrib import messages
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditLog,
    Client,
    DocumentType,
    Event,
    Organization,
    RetentionProposal,
    Settings,
    User,
)
from core.retention import legal_holds as legal_holds_mod
from core.retention import proposals as proposals_mod
from core.retention.legal_holds import create_hold_for_target, create_legal_hold
from core.retention.proposals import approve_proposal, bulk_approve_proposals

_JOIN_TIMEOUT = 30
#: Wie lange die Hold-Anlage ihre Ereignissperre haelt, nachdem der Approve
#: gestartet ist. Reine Untergrenze: laeuft der Approve langsamer an, haette
#: er die Sperre erst recht nicht ueberholt.
_SPERRFENSTER_SEKUNDEN = 0.75


def _proposal_for(facility, event, status=RetentionProposal.Status.PENDING):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=event.pk,
        deletion_due_at=date.today() + timedelta(days=5),
        status=status,
        details={},
        retention_category="identified",
    )


def _approved_audit_rows(proposal):
    return AuditLog.objects.filter(
        action=AuditLog.Action.DELETE,
        target_id=str(proposal.target_id),
        detail__category="retention_proposal_approved",
    )


@pytest.fixture
def event(facility, client_identified, doc_type_contact):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now() - timedelta(days=400),
        data_json={"freitext": "Klartext"},
    )


@pytest.mark.django_db
class TestApproveProposalHoldGuard:
    """Refs #2109: Genehmigung unter aktivem Hold ist keine gültige Entscheidung."""

    def test_event_hold_verhindert_die_genehmigung(self, facility, event, lead_user):
        proposal = _proposal_for(facility, event)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=lead_user,
            reason="Gerichtsverfahren",
        )

        with pytest.raises(ValidationError) as exc:
            approve_proposal(proposal, lead_user)

        assert "Aufbewahrungssperre" in str(exc.value)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not _approved_audit_rows(proposal).exists()

    def test_client_hold_verhindert_die_genehmigung(self, facility, event, client_identified, lead_user):
        """Refs #1376 (I10): Der Hold auf die PERSON schützt deren Ereignisse
        im Enforcement — die Genehmigungsfläche darf nicht mehr dürfen."""
        proposal = _proposal_for(facility, event)
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=lead_user,
            reason="Behördenanordnung",
        )

        with pytest.raises(ValidationError) as exc:
            approve_proposal(proposal, lead_user)

        assert "Aufbewahrungssperre" in str(exc.value)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not _approved_audit_rows(proposal).exists()

    def test_bereits_entschiedener_vorschlag_wird_abgewiesen(self, facility, event, lead_user):
        """Der Status-Re-Check unter der Sperre — Muster ``reject_client_merge``."""
        proposal = _proposal_for(facility, event)
        approve_proposal(proposal, lead_user)
        assert _approved_audit_rows(proposal).count() == 1

        stale = RetentionProposal.objects.get(pk=proposal.pk)
        stale.status = RetentionProposal.Status.PENDING  # veraltete In-Memory-Sicht

        with pytest.raises(ValidationError) as exc:
            approve_proposal(stale, lead_user)

        assert "bereits bearbeitet" in str(exc.value)
        # Kein zweiter „Genehmigt"-Eintrag fuer denselben Vorgang.
        assert _approved_audit_rows(proposal).count() == 1

    def test_zurueckgestellter_vorschlag_bleibt_genehmigbar(self, facility, event, lead_user):
        """Gegenprobe: ``RetentionBulkApproveView.allowed_statuses`` laesst
        PENDING **und** DEFERRED zu — der Re-Check darf das nicht verengen."""
        proposal = _proposal_for(facility, event, status=RetentionProposal.Status.DEFERRED)

        approve_proposal(proposal, lead_user)

        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED

    def test_ohne_hold_bleibt_alles_wie_bisher(self, facility, event, lead_user):
        proposal = _proposal_for(facility, event)

        approve_proposal(proposal, lead_user)

        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED
        assert _approved_audit_rows(proposal).count() == 1


@pytest.mark.django_db
class TestBulkApproveUeberspringtGehaltene:
    """Ein gehaltener Vorschlag darf eine Sammel-Genehmigung nicht abreissen."""

    def _drei_vorschlaege(self, facility, client_identified, doc_type_contact):
        events = [
            Event.objects.create(
                facility=facility,
                client=client_identified,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(days=400 + i),
                data_json={"freitext": f"Klartext {i}"},
            )
            for i in range(3)
        ]
        return events, [_proposal_for(facility, e) for e in events]

    def test_nur_der_gehaltene_wird_uebersprungen(self, facility, client_identified, doc_type_contact, lead_user):
        events, proposals = self._drei_vorschlaege(facility, client_identified, doc_type_contact)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=events[1].pk,
            user=lead_user,
            reason="Gerichtsverfahren",
        )

        count, skipped = bulk_approve_proposals(proposals, lead_user)

        assert (count, skipped) == (2, 1)
        for index, proposal in enumerate(proposals):
            proposal.refresh_from_db()
            erwartet = RetentionProposal.Status.PENDING if index == 1 else RetentionProposal.Status.APPROVED
            assert proposal.status == erwartet, f"Vorschlag {index} steht auf {proposal.status}"

    def test_view_meldet_die_uebersprungenen_zusaetzlich(
        self, client, facility, client_identified, doc_type_contact, lead_user
    ):
        events, proposals = self._drei_vorschlaege(facility, client_identified, doc_type_contact)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=events[1].pk,
            user=lead_user,
            reason="Gerichtsverfahren",
        )
        client.force_login(lead_user)

        response = client.post(
            reverse("core:retention_bulk_approve"),
            {"proposal_ids": [str(p.pk) for p in proposals]},
            headers={"HX-Request": "true"},
        )

        gemeldet = [(m.level, str(m)) for m in get_messages(response.wsgi_request)]
        assert (messages.SUCCESS, "2 Löschvorschläge aktualisiert.") in gemeldet
        warnungen = [text for level, text in gemeldet if level == messages.WARNING]
        assert warnungen == ["1 Löschvorschlag übersprungen (Aufbewahrungssperre oder Status)."]


@pytest.mark.django_db
class TestRetentionApproveViewUnterHold:
    """Die Einzel-Genehmigung braucht eine Fehlerfläche statt eines 500."""

    def test_post_unter_hold_meldet_die_sperre(self, client, facility, event, lead_user):
        proposal = _proposal_for(facility, event)
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=lead_user,
            reason="Gerichtsverfahren",
        )
        client.force_login(lead_user)

        response = client.post(reverse("core:retention_approve", kwargs={"pk": proposal.pk}))

        assert response.status_code == 409
        text = response.content.decode()
        assert "Aufbewahrungssperre" in text
        # Der globale htmx-Fehler-Toast (``static/js/htmx-errors.js``) zeigt den
        # Antworttext nur, wenn er kurz und markup-frei ist.
        assert len(text) <= 200 and "<" not in text
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not _approved_audit_rows(proposal).exists()


@pytest.mark.django_db(transaction=True)
class TestZweiParalleleApproves:
    """Refs #2109, Akzeptanzkriterium: zwei parallele Approves ⇒ genau ein Audit-Eintrag."""

    @pytest.fixture
    def bestand(self, django_db_setup):
        org = Organization.objects.create(name="Approve-Race-Org")
        facility = org.facilities.create(name="Approve-Race-Stelle")
        Settings.objects.get_or_create(facility=facility)
        lead = User.objects.create_user(
            username="approve-race-lead",
            password="x" * 40,
            facility=facility,
            role=User.Role.LEAD,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Approve-Race)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Approve-Race-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={"freitext": "Klartext"},
        )
        proposal = _proposal_for(facility, ereignis)
        yield facility, lead, proposal
        connections.close_all()

    def test_genau_ein_audit_eintrag(self, bestand):
        _facility, lead, proposal = bestand
        am_start = threading.Barrier(2, timeout=_JOIN_TIMEOUT)
        ergebnisse = {}
        fehler = {}

        def _genehmigen(slot):
            connection.close()
            try:
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                am_start.wait()
                approve_proposal(frisch, lead)
                ergebnisse[slot] = "ok"
            except ValidationError as exc:
                ergebnisse[slot] = str(exc)
            except Exception as exc:  # noqa: BLE001 — in den Bericht, nicht verschlucken
                fehler[slot] = exc
            finally:
                connections.close_all()

        faeden = [threading.Thread(target=_genehmigen, args=(slot,)) for slot in ("a", "b")]
        for faden in faeden:
            faden.start()
        for faden in faeden:
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not any(f.is_alive() for f in faeden), "Verklemmung zwischen zwei Genehmigungen"
        assert not fehler, fehler
        assert sorted(ergebnisse) == ["a", "b"]
        erfolge = [slot for slot, wert in ergebnisse.items() if wert == "ok"]
        assert len(erfolge) == 1, f"Beide Genehmigungen liefen durch: {ergebnisse}"
        assert _approved_audit_rows(proposal).count() == 1, "Zwei Audit-Zeilen fuer einen Vorgang"
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED


def _committet_innerhalb(beobachtet, sekunden=1.0):
    """Meldet, ob der Gegenspieler-Faden sein COMMIT im Fenster geschafft hat."""
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if beobachtet.get("committed") or beobachtet.get("fehler"):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.django_db(transaction=True)
class TestApproveKoordiniertMitDerHoldAnlage:
    """Cross-Review Runde 1 (BLOCKER): Der Hold-Check braucht den gemeinsamen Anker.

    Die erste Fassung sperrte nur ``core_retentionproposal`` und las die Holds
    danach ungesperrt. ``create_hold_for_target`` koordiniert aber über die
    **Zielzeile** — Ereignis-Holds auf ``core_event``, Personen-Holds auf
    ``core_client`` (ADR-050 D2). Ein Hold konnte deshalb nach dem Check
    committen und vor dem ``save()`` aktiv sein: APPROVED unter laufender
    Sperre, und der Audit-Trail behauptete eine unzulässige Genehmigung.
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        org = Organization.objects.create(name="Approve-Anker-Org")
        facility = org.facilities.create(name="Approve-Anker-Stelle")
        Settings.objects.get_or_create(facility=facility)
        lead = User.objects.create_user(
            username="approve-anker-lead",
            password="x" * 40,
            facility=facility,
            role=User.Role.LEAD,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Approve-Anker)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Approve-Anker-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={"freitext": "Beweis"},
        )
        yield facility, lead, person, ereignis, _proposal_for(facility, ereignis)
        connections.close_all()

    @pytest.mark.parametrize("hold_ziel", ["Event", "Client"])
    def test_hold_der_vor_der_zielsperre_committet_verhindert_das_approve(self, bestand, monkeypatch, hold_ziel):
        """Injektion am Sperrpunkt: der Hold committet, während der Approve noch
        VOR seiner Zielsperre steht — genau der Zustand nach einer Sperrwartezeit.

        Deadlock-frei, weil zu diesem Zeitpunkt noch keine Sperre gehalten wird.
        """
        facility, lead, person, ereignis, proposal = bestand
        ziel_id = ereignis.pk if hold_ziel == "Event" else person.pk
        fehler = {}
        fertig = threading.Event()

        def _hold_anlegen():
            connection.close()
            try:
                create_hold_for_target(
                    facility,
                    target_type=hold_ziel,
                    target_id=ziel_id,
                    user=lead,
                    reason=f"{hold_ziel}-Hold waehrend der Sperrwartezeit",
                )
            except Exception as exc:  # noqa: BLE001
                fehler["hold"] = exc
            finally:
                fertig.set()
                connections.close_all()

        echt = proposals_mod._lock_target_rows_for_approval

        def _vor_der_sperre_den_hold_zulassen(zu_sperren):
            faden = threading.Thread(target=_hold_anlegen, name="hold")
            faden.start()
            faden.join(timeout=_JOIN_TIMEOUT)
            assert not faden.is_alive(), "Hold-Anlage haengt, obwohl noch keine Sperre gehalten wird"
            return echt(zu_sperren)

        monkeypatch.setattr(proposals_mod, "_lock_target_rows_for_approval", _vor_der_sperre_den_hold_zulassen)

        with pytest.raises(ValidationError) as exc:
            approve_proposal(proposal, lead)

        assert not fehler, fehler
        assert "Aufbewahrungssperre" in str(exc.value)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not _approved_audit_rows(proposal).exists()

    @pytest.mark.parametrize("hold_ziel", ["Event", "Client"])
    def test_hold_kann_im_gesperrten_fenster_nicht_committen(self, bestand, monkeypatch, hold_ziel):
        """Die Gegenrichtung: sobald der Approve seine Zielzeilen hält, kommt die
        Hold-Anlage nicht mehr durch — sie wartet, statt neben ihm her zu committen.

        Genau diese Aussage konnte der Reviewer-Guard nicht formulieren: er
        ``assert``-et im gesperrten Abschnitt auf ein COMMIT, das nach dem Fix
        korrekterweise nicht mehr stattfindet. Hier wird deshalb mit endlicher
        Wartezeit **beobachtet** statt blockierend erwartet.
        """
        facility, lead, person, ereignis, proposal = bestand
        ziel_id = ereignis.pk if hold_ziel == "Event" else person.pk
        beobachtet = {}
        im_fenster = threading.Event()

        def _hold_anlegen():
            connection.close()
            try:
                im_fenster.wait(timeout=_JOIN_TIMEOUT)
                create_hold_for_target(
                    facility,
                    target_type=hold_ziel,
                    target_id=ziel_id,
                    user=lead,
                    reason=f"{hold_ziel}-Hold neben der Zielsperre",
                )
                beobachtet["committed"] = True
            except Exception as exc:  # noqa: BLE001
                beobachtet["fehler"] = exc
            finally:
                connections.close_all()

        echt = proposals_mod._assert_target_has_no_active_hold

        def _nach_der_hold_lesung_warten(geprueft, client_id):
            ergebnis = echt(geprueft, client_id)
            im_fenster.set()
            beobachtet["im_fenster"] = _committet_innerhalb(beobachtet)
            return ergebnis

        monkeypatch.setattr(proposals_mod, "_assert_target_has_no_active_hold", _nach_der_hold_lesung_warten)

        faden = threading.Thread(target=_hold_anlegen, name="hold")
        faden.start()
        try:
            approve_proposal(proposal, lead)
        finally:
            connections.close_all()
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not faden.is_alive(), "Verklemmung zwischen Hold-Anlage und Approve"
        assert not beobachtet.get("fehler"), beobachtet["fehler"]
        assert beobachtet["im_fenster"] is False, (
            f"Der {hold_ziel}-Hold committete zwischen Hold-Lesung und Schreiben — "
            "die Zielzeile ist kein gemeinsamer Sperr-Anker"
        )
        assert beobachtet["committed"] is True, "Der Hold entsteht danach"
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.APPROVED
        assert _approved_audit_rows(proposal).count() == 1

    def test_hold_anlage_ueber_create_legal_hold_gewinnt_und_verhindert_das_approve(self, bestand, monkeypatch):
        """Die Gegenprobe gegen den PROPOSAL-gebundenen Hold-Pfad (Re-Review 1).

        ``RetentionHoldView`` nimmt nicht ``create_hold_for_target``, sondern
        ``create_legal_hold`` — und zwar in **einer** ``transaction.atomic()``.
        Dieser Pfad sperrt also ``core_event`` (über ``create_hold_for_target``)
        und schreibt DANACH ``core_retentionproposal`` auf ``HELD``: genau die
        Ordnung, gegen die die Sperrreihenfolge von ``approve_proposal``
        verteidigt werden muss. Eine Gegenprobe, die nur
        ``create_hold_for_target`` ruft, nimmt den Vorschlags-Lock nie und kann
        eine Regression auf die Gegenordnung ``core_retentionproposal ->
        core_event`` nicht sehen.

        Aufbau: Die Hold-Anlage hält die Ereigniszeile (und ihre noch nicht
        committete ``LegalHold``-Zeile) und parkt VOR ``proposal.save()``. Erst
        dann startet der Approve. Damit steht fest, wer zuerst da war — der
        Approve darf nicht durchkommen.

        Vor dem Fix sperrte ``approve_proposal`` als Erstes den Vorschlag, las
        die Holds ungesperrt (die fremde, noch offene Transaktion ist unsichtbar)
        und genehmigte: eine Genehmigt-Zeile im Audit, während die Hold-Anlage
        bereits lief. Nach dem Fix wartet der Approve an ``core_event``, sieht
        anschließend ``HELD`` und bricht ab.
        """
        facility, lead, _person, ereignis, proposal = bestand
        ereignis_gesperrt = threading.Event()
        approve_laeuft = threading.Event()
        ergebnis = {}
        fehler = {}

        echt_create_hold = legal_holds_mod.create_hold_for_target

        def _nach_der_ereignissperre_parken(*args, **kwargs):
            hold = echt_create_hold(*args, **kwargs)
            ereignis_gesperrt.set()
            # Bewusst endlich und ohne Bezug auf den Abschluss des Approve:
            # nach dem Fix haengt der Approve an genau dieser Ereigniszeile,
            # ein Warten auf ihn waere die Verklemmung.
            approve_laeuft.wait(timeout=_JOIN_TIMEOUT)
            time.sleep(_SPERRFENSTER_SEKUNDEN)
            return hold

        monkeypatch.setattr(legal_holds_mod, "create_hold_for_target", _nach_der_ereignissperre_parken)

        def _hold_ueber_den_view_pfad():
            connection.close()
            try:
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                # Exakt die Klammer aus ``RetentionHoldView.post``.
                with transaction.atomic():
                    create_legal_hold(frisch, lead, "Behoerdenanordnung ueber die Oberflaeche")
                ergebnis["hold"] = "ok"
            except Exception as exc:  # noqa: BLE001 — in den Bericht, nicht verschlucken
                fehler["hold"] = exc
            finally:
                connections.close_all()

        def _approve():
            connection.close()
            try:
                assert ereignis_gesperrt.wait(timeout=_JOIN_TIMEOUT), "Hold-Anlage erreichte die Ereignissperre nicht"
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                approve_laeuft.set()
                approve_proposal(frisch, lead)
                ergebnis["approve"] = "ok"
            except ValidationError as exc:
                ergebnis["approve"] = exc
            except Exception as exc:  # noqa: BLE001
                fehler["approve"] = exc
            finally:
                connections.close_all()

        faden_hold = threading.Thread(target=_hold_ueber_den_view_pfad, name="legal-hold")
        faden_approve = threading.Thread(target=_approve, name="approve")
        faden_hold.start()
        faden_approve.start()
        for faden in (faden_hold, faden_approve):
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not faden_hold.is_alive() and not faden_approve.is_alive(), (
            "Verklemmung zwischen create_legal_hold (Event -> Proposal) und approve_proposal"
        )
        assert not fehler, fehler
        assert ergebnis["hold"] == "ok", "Die Hold-Anlage muss durchkommen — sie war zuerst da"
        proposal.refresh_from_db()
        assert isinstance(ergebnis["approve"], ValidationError), (
            "Der Approve lief durch, obwohl die Hold-Anlage die Ereigniszeile bereits hielt "
            f"(Ergebnis: {ergebnis['approve']!r}, Status: {proposal.status})"
        )
        assert proposal.status == RetentionProposal.Status.HELD
        assert not _approved_audit_rows(proposal).exists(), (
            "Genehmigt-Zeile im Audit, obwohl die Aufbewahrungssperre bereits angelegt wurde"
        )

    def test_paralleles_approve_und_create_legal_hold_enden_konsistent_ohne_verklemmung(self, bestand):
        """Liveness in beide Richtungen, ohne Injektion: ``create_legal_hold``
        nimmt ``core_event`` vor ``core_retentionproposal``, ``approve_proposal``
        ``core_client -> core_event -> core_retentionproposal``. Beide Ausgänge
        sind zulässig, eine Verklemmung ist es nicht."""
        facility, lead, _person, _ereignis, proposal = bestand
        am_start = threading.Barrier(2, timeout=_JOIN_TIMEOUT)
        ergebnis = {}
        fehler = {}

        def _approve():
            connection.close()
            try:
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                am_start.wait()
                approve_proposal(frisch, lead)
                ergebnis["approve"] = "ok"
            except ValidationError as exc:
                ergebnis["approve"] = exc
            except Exception as exc:  # noqa: BLE001
                fehler["approve"] = exc
            finally:
                connections.close_all()

        def _hold():
            connection.close()
            try:
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                am_start.wait()
                with transaction.atomic():
                    create_legal_hold(frisch, lead, "Paralleler Hold ueber die Oberflaeche")
                ergebnis["hold"] = "ok"
            except Exception as exc:  # noqa: BLE001
                fehler["hold"] = exc
            finally:
                connections.close_all()

        faeden = [threading.Thread(target=_approve), threading.Thread(target=_hold)]
        for faden in faeden:
            faden.start()
        for faden in faeden:
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not any(f.is_alive() for f in faeden), "Verklemmung (ABBA) zwischen Approve und create_legal_hold"
        assert not fehler, fehler
        assert ergebnis["hold"] == "ok", "Die Hold-Anlage muss in jedem Fall durchkommen"
        proposal.refresh_from_db()
        if isinstance(ergebnis["approve"], ValidationError):
            # Die Hold-Anlage war zuerst: keine Genehmigt-Zeile im Audit.
            assert proposal.status == RetentionProposal.Status.HELD
            assert not _approved_audit_rows(proposal).exists()
        else:
            # Der Approve war zuerst da; die Hold-Anlage hat danach auf HELD
            # gesetzt. Genau eine Genehmigung, keine zweite.
            assert proposal.status == RetentionProposal.Status.HELD
            assert _approved_audit_rows(proposal).count() == 1

    def test_merge_waehrend_der_sperrwartezeit_bricht_sauber_ab(self, bestand):
        """Die Bindungs-Revalidierung: hängt ein Merge das Ereignis zwischen
        Vorlesung und Ereignissperre an eine andere Person, ist die gesperrte
        Personenzeile die falsche — dann wird abgebrochen statt nachgesperrt
        (Sperr-Inversion, ADR-050 D5.5)."""
        facility, lead, person, ereignis, proposal = bestand
        andere = Client.objects.create(
            facility=facility,
            pseudonym="Approve-Anker-Zweite",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        echt_filter = Client.objects.filter

        def _merge_gewinnt(*args, **kwargs):
            # Genau zwischen der ungesperrten Vorlesung der Bindung und der
            # Revalidierung nach der Ereignissperre: der einzige
            # ``Client.objects.filter``-Aufruf des Pfads ist die Personensperre.
            Event.objects.filter(pk=ereignis.pk).update(client=andere)
            return echt_filter(*args, **kwargs)

        with mock.patch.object(Client.objects, "filter", side_effect=_merge_gewinnt):
            with pytest.raises(ValidationError) as exc:
                approve_proposal(proposal, lead)

        assert "Personenzuordnung" in str(exc.value)
        proposal.refresh_from_db()
        assert proposal.status == RetentionProposal.Status.PENDING
        assert not _approved_audit_rows(proposal).exists()
