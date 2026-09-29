"""Refs #2374: Die Reaktivierung fälliger Zurückstellungen entscheidet am Status der gesperrten Zeile.

``reactivate_deferred_proposals`` liest die fälligen, zurückgestellten
Vorschläge ungesperrt und setzt sie danach auf ``PENDING``. Wird ein
Vorschlag dazwischen gehalten (Hold-Anlage), abgelehnt oder erneut
zurückgestellt, darf der Lauf ihn nicht überschreiben: er bleibt, wie ihn der
parallele Vorgang hinterlassen hat, und bekommt keine Reaktivierungs-Zeile im
Audit-Trail. Die übrigen fälligen Vorschläge werden wie gewohnt reaktiviert.

Der Zwischenfall läuft in einer zweiten Sitzung (committet), direkt nachdem
der Lauf die Auswahl gelesen hat — erkannt an der ersten Anweisung, die
Vorschläge ohne Zeilensperre liest (nach ihrer Bedeutung, nicht nach dem
Wortlaut).
"""

import threading
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import AuditLog, Client, Event, RetentionProposal
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import defer_proposal, reactivate_deferred_proposals, reject_proposal
from tests import test_retention_bulk_lock_order_guard as dienst_guard
from tests._row_lock_helpers import mitschnitt

#: Zwei Personen, je ein Ereignis, je ein Vorschlag — sichtbar für jede Sitzung.
bestand = dienst_guard.bestand


class _NachDerAuswahl:
    """``execute_wrapper``: ruft ``aktion`` einmal, nachdem eine Anweisung Vorschläge ungesperrt gelesen hat."""

    def __init__(self, aktion):
        self.aktion = aktion
        self.aufzeichnung = None
        self.gesehen = False
        self._still = False

    def __call__(self, execute, sql, params, many, context):
        ergebnis = execute(sql, params, many, context)
        if not self.gesehen and not self._still and self.aufzeichnung:
            self._still = True
            try:
                letzte = self.aufzeichnung[-1]
                if letzte.liest("core_retentionproposal") and not letzte.sperrt("core_retentionproposal"):
                    self.gesehen = True
                    self.aktion()
            finally:
                self._still = False
        return ergebnis


def _in_zweiter_sitzung(arbeit):
    fehler = []

    def _laufen():
        try:
            with transaction.atomic():
                arbeit()
        except Exception as exc:  # noqa: BLE001 — in die Meldung, nicht verschlucken
            fehler.append(exc)
        finally:
            connections.close_all()

    faden = threading.Thread(target=_laufen, name="zweite-sitzung")
    faden.start()
    faden.join(timeout=dienst_guard._JOIN_TIMEOUT)
    assert not faden.is_alive(), "Die zweite Sitzung kam nicht zurück"
    assert not fehler, fehler


def _zwischenfall(art, vorschlag_pk, lead):
    def _arbeit():
        frisch = RetentionProposal.objects.get(pk=vorschlag_pk)
        if art == "gehalten":
            create_legal_hold(frisch, lead, "Behördenanordnung während der Reaktivierung")
        elif art == "abgelehnt":
            reject_proposal(frisch, lead)
        else:
            defer_proposal(frisch, lead, days=30)

    return lambda: _in_zweiter_sitzung(_arbeit)


_STATUS_NACH_ZWISCHENFALL = {
    "gehalten": RetentionProposal.Status.HELD,
    "abgelehnt": RetentionProposal.Status.REJECTED,
    "neu_zurueckgestellt": RetentionProposal.Status.DEFERRED,
}


@pytest.mark.django_db(transaction=True)
class TestReaktivierungUeberschreibtKeinenZwischenzeitlichenStatus:
    @pytest.mark.parametrize("faellige", [1, 2])
    @pytest.mark.parametrize("art", sorted(_STATUS_NACH_ZWISCHENFALL))
    def test_zwischenzeitlich_geaenderter_vorschlag_bleibt_unveraendert(self, bestand, art, faellige):
        dienst_guard._faellig_zurueckgestellt(bestand, automatisch=False)
        betroffen, anderer = bestand.vorschlaege
        if faellige == 1:
            RetentionProposal.objects.filter(pk=anderer.pk).update(status=RetentionProposal.Status.PENDING)
        haken = _NachDerAuswahl(_zwischenfall(art, betroffen.pk, bestand.lead))
        with connection.execute_wrapper(haken), mitschnitt() as aufzeichnung:
            haken.aufzeichnung = aufzeichnung
            ergebnis = reactivate_deferred_proposals(bestand.facility)

        assert haken.gesehen, "Der Lauf las keine Vorschläge"
        betroffen.refresh_from_db()
        assert betroffen.status == _STATUS_NACH_ZWISCHENFALL[art]
        if art == "neu_zurueckgestellt":
            assert betroffen.deferred_until > timezone.localdate()
        assert not AuditLog.objects.filter(
            target_id=str(betroffen.target_id), detail__category="retention_proposal_reactivated"
        ).exists()
        # Die übrigen fälligen Vorschläge reaktiviert der Lauf wie gewohnt.
        assert ergebnis == (faellige - 1, 0)
        anderer.refresh_from_db()
        assert anderer.status == RetentionProposal.Status.PENDING
        assert AuditLog.objects.filter(
            target_id=str(anderer.target_id), detail__category="retention_proposal_reactivated"
        ).count() == (1 if faellige == 2 else 0)


@pytest.mark.django_db
def test_ohne_zwischenfall_reaktiviert_der_lauf_jeden_faelligen_vorschlag(facility, lead_user, doc_type_contact):
    """Gegenprobe: ohne parallelen Vorgang werden alle fälligen Vorschläge reaktiviert."""
    vorschlaege = []
    for nummer in range(2):
        person = Client.objects.create(
            facility=facility, pseudonym=f"Reaktivierung-{nummer}", contact_stage=Client.ContactStage.IDENTIFIED
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type_contact,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={},
        )
        vorschlaege.append(
            RetentionProposal.objects.create(
                facility=facility,
                target_type=RetentionProposal.TargetType.EVENT,
                target_id=ereignis.pk,
                deletion_due_at=timezone.localdate() + timedelta(days=5),
                retention_category="identified",
                status=RetentionProposal.Status.DEFERRED,
                deferred_until=timezone.localdate() - timedelta(days=1),
                defer_count=1,
            )
        )

    assert reactivate_deferred_proposals(facility) == (2, 0)
    for vorschlag in vorschlaege:
        vorschlag.refresh_from_db()
        assert vorschlag.status == RetentionProposal.Status.PENDING
        zeile = AuditLog.objects.get(
            target_id=str(vorschlag.target_id), detail__category="retention_proposal_reactivated"
        )
        assert zeile.detail["defer_count"] == 1
