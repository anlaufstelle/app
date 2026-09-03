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
from datetime import date, timedelta

import pytest
from django.contrib import messages
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection, connections
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
from core.retention.legal_holds import create_hold_for_target
from core.retention.proposals import approve_proposal, bulk_approve_proposals

_JOIN_TIMEOUT = 30


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
