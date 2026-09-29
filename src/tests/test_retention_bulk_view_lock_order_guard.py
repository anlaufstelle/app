"""Refs #2374: Auch die Sammelaktionen der Oberfläche sperren die ganze Auswahl vor der ersten Audit-Zeile.

Die Sammel-Views (``core:retention_bulk_approve``/``_defer``/``_reject``)
entscheiden je Vorschlag in einem eigenen Savepoint, alle in EINER
Transaktion. Die Audit-Zeile des ersten Vorschlags nimmt die Audit-Kette der
Einrichtung und hält sie bis zum Ende der Anfrage. Sperrt die Anfrage die
Zeilen des zweiten Vorschlags (Person, Ereignis, Vorschlag) erst danach, wartet
sie mit der Kette in der Hand auf eine Zeile, die eine Hold-Anlage hält —
während die Hold-Anlage auf die Kette wartet: die Datenbank bricht eine der
beiden als Verklemmung ab.

Beobachtet wird dieselbe Grenze wie am Dienst
(``test_retention_bulk_lock_order_guard.py``): im Moment der ersten Audit-Zeile
ist keine Zeile der Auswahl mehr für eine zweite Sitzung frei, und danach
sperrt die Anfrage keine Zeile, die sie nicht schon hält. Der
Nebenläufigkeitstest stellt den kritischen Ablauf über zwei Anfragen nach;
beide Ausgänge sind zulässig, eine Verklemmung ist es nicht.
"""

import threading
from datetime import date, timedelta

import pytest
from django.contrib import messages
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client as HttpClient
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, Event, LegalHold, RetentionProposal
from tests import test_retention_bulk_lock_order_guard as dienst_guard
from tests._row_lock_helpers import mitschnitt

#: Dieselbe Ausgangslage wie am Dienst: zwei Personen, je ein Ereignis, je ein offener Vorschlag.
bestand = dienst_guard.bestand

_SAMMEL_VIEWS = {
    "genehmigen": ("core:retention_bulk_approve", RetentionProposal.Status.APPROVED, True),
    "zurueckstellen": ("core:retention_bulk_defer", RetentionProposal.Status.DEFERRED, False),
    "ablehnen": ("core:retention_bulk_reject", RetentionProposal.Status.REJECTED, False),
}
_ZWEI_AKTUALISIERT = (messages.SUCCESS, "2 Löschvorschläge aktualisiert.")


def _gesperrte_zeilen(abfragen):
    """Je beobachteter Tabelle die pks, auf die die Anweisungen ihre Zeilensperre binden.

    Eine Sperre, die nicht an pks gebunden ist, zählt als ``None`` — sie könnte jede Zeile treffen.
    """
    zeilen = {}
    for abfrage in abfragen:
        for tabelle in dienst_guard._BEOBACHTETE_TABELLEN:
            if abfrage.sperrt(tabelle):
                zeilen.setdefault(tabelle, set()).update(abfrage.bedingungen.get(tabelle, {}).get("id", {None}))
    return zeilen


def _auswahl(bestand):
    return {"proposal_ids": [str(v.pk) for v in bestand.vorschlaege]}


def _gemeldet(response):
    return [(m.level, str(m)) for m in get_messages(response.wsgi_request)]


@pytest.mark.django_db(transaction=True)
class TestSammelViewSperrtDieAuswahlVorDerErstenAuditZeile:
    @pytest.mark.parametrize("weg", sorted(_SAMMEL_VIEWS))
    def test_keine_neue_zeilensperre_unter_gehaltener_audit_kette(self, bestand, client, weg):
        url_name, status_danach, mit_zielen = _SAMMEL_VIEWS[weg]
        client.force_login(bestand.lead)
        beobachtung = {}
        zeilen = dienst_guard._zeilen(bestand, mit_zielen=mit_zielen)
        moment = dienst_guard._ErsteAuditZeile(bestand.facility.pk, lambda: None)
        with connection.execute_wrapper(moment), mitschnitt() as aufzeichnung:

            def _im_moment_der_ersten_audit_zeile():
                beobachtung["grenze"] = len(aufzeichnung)
                beobachtung["frei"] = dienst_guard._sofort_sperrbar(zeilen)

            moment.aktion = _im_moment_der_ersten_audit_zeile
            response = client.post(reverse(url_name), _auswahl(bestand))

        assert response.status_code == 302
        assert _gemeldet(response) == [_ZWEI_AKTUALISIERT]
        assert [RetentionProposal.objects.get(pk=v.pk).status for v in bestand.vorschlaege] == [status_danach] * 2
        assert "grenze" in beobachtung, "Die Sammelanfrage schrieb keine Audit-Zeile"
        # Jede Zeile der Auswahl ist schon gesperrt, wenn die Audit-Kette genommen wird …
        assert beobachtung["frei"] == [], beobachtung["frei"]
        vorher = aufzeichnung[: beobachtung["grenze"]]
        folge = []
        for abfrage in vorher:
            folge.extend(t for t in dienst_guard._BEOBACHTETE_TABELLEN if abfrage.sperrt(t) and t not in folge)
        # … erst die Zielanker, dann die Vorschläge …
        assert folge == (list(dienst_guard._BEOBACHTETE_TABELLEN) if mit_zielen else ["core_retentionproposal"])
        # … und danach sperrt die Anfrage nur noch Zeilen, die sie schon hält.
        gehalten = _gesperrte_zeilen(vorher)
        for tabelle, pks in _gesperrte_zeilen(aufzeichnung[beobachtung["grenze"] :]).items():
            assert pks <= gehalten.get(tabelle, set()), (tabelle, pks - gehalten.get(tabelle, set()))


@pytest.mark.django_db(transaction=True)
class TestSammelViewUndHoldAnlageVerklemmenSichNicht:
    @pytest.mark.parametrize("weg", sorted(_SAMMEL_VIEWS))
    def test_hold_anlage_auf_den_zweiten_vorschlag_unter_gehaltener_audit_kette(self, bestand, weg):
        url_name, status_danach, _ = _SAMMEL_VIEWS[weg]
        facility_id = bestand.facility.pk
        erster, zweiter = bestand.vorschlaege
        sammel_client, hold_client = HttpClient(), HttpClient()
        sammel_client.force_login(bestand.lead)
        hold_client.force_login(bestand.lead)
        kette_gehalten = threading.Event()
        hold_sitzung, ergebnis, fehler = {}, {}, {}

        def _sammeln():
            moment = dienst_guard._ErsteAuditZeile(
                facility_id,
                lambda: dienst_guard._parken_bis_die_andere_sitzung_wartet(kette_gehalten, hold_sitzung),
            )
            with connection.execute_wrapper(moment):
                return sammel_client.post(reverse(url_name), _auswahl(bestand))

        def _hold_anlegen():
            gehalten = kette_gehalten.wait(timeout=dienst_guard._JOIN_TIMEOUT)
            assert gehalten, "Die Sammelanfrage schrieb keine Audit-Zeile"
            return hold_client.post(
                reverse("core:retention_hold", args=[zweiter.pk]),
                {"reason": "Behördenanordnung während der Sammelaktion"},
            )

        dienst_guard._laufen_lassen(
            [
                dienst_guard._in_eigenem_faden("sammelanfrage", _sammeln, ergebnis, fehler),
                dienst_guard._in_eigenem_faden("hold", _hold_anlegen, ergebnis, fehler, sitzung=hold_sitzung),
            ]
        )

        assert not fehler, fehler
        # Die Sammelanfrage hielt die Auswahl zuerst — sie kommt vollständig durch.
        assert ergebnis["sammelanfrage"].status_code == 302
        assert _gemeldet(ergebnis["sammelanfrage"]) == [_ZWEI_AKTUALISIERT]
        erster.refresh_from_db()
        zweiter.refresh_from_db()
        assert erster.status == status_danach
        aktive_holds = LegalHold.objects.filter(target_id=zweiter.target_id, dismissed_at__isnull=True)
        hold_zeilen = AuditLog.objects.filter(
            action=AuditLog.Action.LEGAL_HOLD,
            target_id=str(zweiter.target_id),
            detail__category="legal_hold_created",
        )
        if ergebnis["hold"].status_code == 200:
            assert zweiter.status == RetentionProposal.Status.HELD
            assert aktive_holds.count() == 1
            assert hold_zeilen.count() == 1
        else:
            assert ergebnis["hold"].status_code == 409, ergebnis["hold"]
            assert zweiter.status == status_danach
            assert not aktive_holds.exists()
            assert not hold_zeilen.exists()


@pytest.mark.django_db
class TestSammelgenehmigungMitGeaenderterZuordnung:
    def test_vorschlag_mit_umgehaengtem_ereignis_wird_als_uebersprungen_gemeldet(
        self, client, facility, lead_user, doc_type_contact
    ):
        """Hängt ein paralleler Vorgang ein Ereignis der Auswahl an eine andere Person, während die
        Anfrage die Personen sperrt, meldet die Oberfläche diesen Vorschlag als übersprungen — wie vorher."""
        personen = [
            Client.objects.create(
                facility=facility, pseudonym=f"View-Zuordnung-{n}", contact_stage=Client.ContactStage.IDENTIFIED
            )
            for n in range(3)
        ]
        ereignisse = [
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type_contact,
                occurred_at=timezone.now() - timedelta(days=400),
                data_json={},
            )
            for person in personen[:2]
        ]
        vorschlaege = [
            RetentionProposal.objects.create(
                facility=facility,
                target_type=RetentionProposal.TargetType.EVENT,
                target_id=ereignis.pk,
                deletion_due_at=date.today() + timedelta(days=5),
                retention_category="identified",
                status=RetentionProposal.Status.PENDING,
            )
            for ereignis in ereignisse
        ]
        client.force_login(lead_user)
        with mitschnitt() as aufzeichnung:
            aufzeichnung.nach_sperre(
                "core_client",
                lambda: Event.objects.filter(pk=ereignisse[0].pk).update(client=personen[2]),
            )
            response = client.post(
                reverse("core:retention_bulk_approve"), {"proposal_ids": [str(v.pk) for v in vorschlaege]}
            )

        assert aufzeichnung.offene_schranken() == []
        assert response.status_code == 302
        assert _gemeldet(response) == [
            (messages.SUCCESS, "1 Löschvorschlag aktualisiert."),
            (messages.WARNING, "1 Löschvorschlag übersprungen (Aufbewahrungssperre oder Status)."),
        ]
        stati = [RetentionProposal.objects.get(pk=v.pk).status for v in vorschlaege]
        assert stati == [RetentionProposal.Status.PENDING, RetentionProposal.Status.APPROVED]
        assert not AuditLog.objects.filter(
            target_id=str(ereignisse[0].pk), detail__category="retention_proposal_approved"
        ).exists()
