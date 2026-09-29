"""Refs #2374: Sammelentscheidungen über Löschvorschläge sperren die ganze Auswahl vor der ersten Audit-Zeile.

Jeder Einzelweg (Hold-Anlage, Genehmigen, Zurückstellen, Ablehnen) sperrt in
EINER Folge: Zielanker (Person → Ereignis), dann den Vorschlag, dann die
Audit-Kette der Einrichtung (``pg_advisory_xact_lock``, gehalten bis zum Ende
der Transaktion). Eine Sammelaktion bearbeitet mehrere Vorschläge in einer
Transaktion. Sperrt sie den zweiten Vorschlag (oder sein Ziel) erst, nachdem
die Audit-Zeile des ersten die Audit-Kette genommen hat, wartet sie mit der
Kette in der Hand auf eine Zeile — und eine Hold-Anlage oder eine zweite
Sammelaktion, die diese Zeile hält, wartet auf die Kette: die Datenbank bricht
eine der beiden als Verklemmung ab.

Beobachtet wird deshalb im Moment, in dem die Transaktion die Audit-Kette zum
ersten Mal hält: kann eine zweite Sitzung eine Zeile der Auswahl jetzt noch
sofort sperren (``FOR UPDATE NOWAIT``), hat die Sammelaktion sie noch nicht —
sie müsste sie später unter gehaltener Kette nehmen. Die Nebenläufigkeitstests
stellen die kritischen Abläufe mit zwei Verbindungen nach. Beide Ausgänge sind
zulässig, eine Verklemmung ist es nicht.
"""

import threading
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection, connections, transaction
from django.utils import timezone

from core.models import (
    AuditLog,
    Client,
    DocumentType,
    Event,
    LegalHold,
    Organization,
    RetentionProposal,
    Settings,
    User,
)
from core.retention.legal_holds import create_legal_hold
from core.retention.proposals import (
    bulk_approve_proposals,
    bulk_defer_proposals,
    bulk_reject_proposals,
    reactivate_deferred_proposals,
)
from core.services.audit.chain import advisory_lock_name
from tests._row_lock_helpers import mitschnitt

_BEOBACHTETE_TABELLEN = ("core_client", "core_event", "core_retentionproposal")
_JOIN_TIMEOUT = 30
#: Wie lange die erste Sammelaktion höchstens darauf wartet, dass die zweite
#: Sitzung auf eine Sperre aufläuft. Läuft sie nicht auf, geht der Test weiter.
_WARTEFRIST_SEKUNDEN = 10

_SAMMELWEGE = {
    "genehmigen": (bulk_approve_proposals, RetentionProposal.Status.APPROVED, "retention_proposal_approved"),
    "zurueckstellen": (bulk_defer_proposals, RetentionProposal.Status.DEFERRED, "retention_proposal_deferred"),
    "ablehnen": (bulk_reject_proposals, RetentionProposal.Status.REJECTED, "retention_proposal_rejected"),
}


def _haelt_audit_kette(facility_id):
    """Hält die eigene Sitzung gerade die Sperre der Audit-Kette dieser Einrichtung?"""
    schluessel = advisory_lock_name(facility_id)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_locks
                WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted AND objsubid = 1
                  AND classid::text::bigint = (hashtext(%s)::bigint >> 32) & 4294967295
                  AND objid::text::bigint = hashtext(%s)::bigint & 4294967295
            )
            """,
            [schluessel, schluessel],
        )
        return cursor.fetchone()[0]


class _ErsteAuditZeile:
    """``execute_wrapper``: ruft ``aktion`` einmal, sobald die Sitzung die Audit-Kette hält."""

    def __init__(self, facility_id, aktion):
        self.facility_id = facility_id
        self.aktion = aktion
        self.gesehen = False
        self._still = False

    def __call__(self, execute, sql, params, many, context):
        ergebnis = execute(sql, params, many, context)
        if not self.gesehen and not self._still:
            self._still = True
            try:
                if _haelt_audit_kette(self.facility_id):
                    self.gesehen = True
                    self.aktion()
            finally:
                self._still = False
        return ergebnis


def _sofort_sperrbar(zeilen):
    """Welche Zeilen kann eine zweite Sitzung jetzt sofort sperren (``FOR UPDATE NOWAIT``)?"""
    frei = []

    def _pruefen():
        try:
            for bezeichnung, modell, pk in zeilen:
                try:
                    with transaction.atomic():
                        list(modell._base_manager.select_for_update(nowait=True).filter(pk=pk).values_list("pk"))
                except OperationalError:
                    continue
                frei.append(bezeichnung)
        finally:
            connections.close_all()

    faden = threading.Thread(target=_pruefen, name="zweite-sitzung")
    faden.start()
    faden.join(timeout=_JOIN_TIMEOUT)
    assert not faden.is_alive(), "Die zweite Sitzung kam nicht zurück"
    return frei


def _backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _wartet_auf_eine_sperre(pid):
    with connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)", [pid])
        return cursor.fetchone()[0]


@pytest.fixture
def bestand(django_db_setup):
    """Zwei Personen mit je einem Ereignis und einem offenen Löschvorschlag — sichtbar für jede Sitzung."""
    org = Organization.objects.create(name="Sammel-Folge-Org")
    facility = org.facilities.create(name="Sammel-Folge-Stelle")
    Settings.objects.get_or_create(facility=facility)
    lead = User.objects.create_user(
        username="sammel-folge-lead",
        password="x" * 40,
        facility=facility,
        role=User.Role.LEAD,
    )
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Kontakt (Sammel-Folge)",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    personen, ereignisse, vorschlaege = [], [], []
    for nummer in (1, 2):
        person = Client.objects.create(
            facility=facility,
            pseudonym=f"Sammel-Folge-Person-{nummer}",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={"freitext": "Klartext"},
        )
        vorschlag = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=ereignis.pk,
            deletion_due_at=date.today() + timedelta(days=5),
            status=RetentionProposal.Status.PENDING,
            details={},
            retention_category="identified",
        )
        personen.append(person)
        ereignisse.append(ereignis)
        vorschlaege.append(vorschlag)
    yield SimpleNamespace(
        facility=facility, lead=lead, personen=personen, ereignisse=ereignisse, vorschlaege=vorschlaege
    )
    connections.close_all()


def _faellig_zurueckgestellt(bestand, *, automatisch):
    """Beide Vorschläge stehen zurückgestellt und fällig; ``automatisch`` schaltet die Auto-Genehmigung."""
    Settings.objects.filter(facility=bestand.facility).update(
        retention_auto_approve_after_defer=automatisch, retention_max_defer_count=2
    )
    RetentionProposal.objects.filter(pk__in=[v.pk for v in bestand.vorschlaege]).update(
        status=RetentionProposal.Status.DEFERRED,
        deferred_until=timezone.localdate() - timedelta(days=1),
        defer_count=3,
    )
    bestand.facility.refresh_from_db()


def _zeilen(bestand, *, mit_zielen):
    zeilen = [(f"vorschlag {n}", RetentionProposal, v.pk) for n, v in enumerate(bestand.vorschlaege, 1)]
    if mit_zielen:
        zeilen += [(f"person {n}", Client, p.pk) for n, p in enumerate(bestand.personen, 1)]
        zeilen += [(f"ereignis {n}", Event, e.pk) for n, e in enumerate(bestand.ereignisse, 1)]
    return zeilen


@pytest.mark.django_db(transaction=True)
class TestSammelwegSperrtDieAuswahlVorDerErstenAuditZeile:
    @pytest.mark.parametrize(
        ("weg", "mit_zielen"),
        [
            ("genehmigen", True),
            ("zurueckstellen", False),
            ("ablehnen", False),
            ("reaktivieren", False),
            ("auto_genehmigen", True),
        ],
    )
    def test_keine_zeile_der_auswahl_ist_bei_der_ersten_audit_zeile_noch_frei(self, bestand, weg, mit_zielen):
        lead = bestand.lead
        if weg in _SAMMELWEGE:
            vorschlaege = [RetentionProposal.objects.get(pk=v.pk) for v in bestand.vorschlaege]

            def aktion():
                _SAMMELWEGE[weg][0](vorschlaege, lead)
        else:
            _faellig_zurueckgestellt(bestand, automatisch=weg == "auto_genehmigen")

            def aktion():
                reactivate_deferred_proposals(bestand.facility)

        beobachtung = {}
        zeilen = _zeilen(bestand, mit_zielen=mit_zielen)
        moment = _ErsteAuditZeile(bestand.facility.pk, lambda: None)
        with connection.execute_wrapper(moment), mitschnitt() as aufzeichnung:

            def _im_moment_der_ersten_audit_zeile():
                beobachtung["grenze"] = len(aufzeichnung)
                beobachtung["frei"] = _sofort_sperrbar(zeilen)

            moment.aktion = _im_moment_der_ersten_audit_zeile
            aktion()

        assert "grenze" in beobachtung, "Die Sammelaktion schrieb keine Audit-Zeile"
        # Jede Zeile der Auswahl ist schon gesperrt, wenn die Audit-Kette genommen wird.
        assert beobachtung["frei"] == [], beobachtung["frei"]
        folge = []
        for abfrage in aufzeichnung[: beobachtung["grenze"]]:
            folge.extend(t for t in _BEOBACHTETE_TABELLEN if abfrage.sperrt(t) and t not in folge)
        erwartet = list(_BEOBACHTETE_TABELLEN) if mit_zielen else ["core_retentionproposal"]
        # Erst die Zielanker, dann die Vorschläge.
        assert folge == erwartet, folge


def _parken_bis_die_andere_sitzung_wartet(signal, andere_sitzung):
    """Signal geben und warten, bis die andere Sitzung auf eine Sperre aufläuft (höchstens die Wartefrist)."""
    signal.set()
    frist = time.monotonic() + _WARTEFRIST_SEKUNDEN
    while time.monotonic() < frist:
        pid = andere_sitzung.get("pid")
        if pid is not None and _wartet_auf_eine_sperre(pid):
            return
        time.sleep(0.05)


def _in_eigenem_faden(name, arbeit, ergebnis, fehler, sitzung=None):
    def _laufen():
        connection.close()
        try:
            if sitzung is not None:
                sitzung["pid"] = _backend_pid()
            ergebnis[name] = arbeit()
        except ValidationError as exc:
            ergebnis[name] = exc
        except Exception as exc:  # noqa: BLE001 — in die Meldung, nicht verschlucken
            fehler[name] = exc
        finally:
            connections.close_all()

    return threading.Thread(target=_laufen, name=name)


def _laufen_lassen(faeden):
    for faden in faeden:
        faden.start()
    for faden in faeden:
        faden.join(timeout=_JOIN_TIMEOUT)
    assert not any(f.is_alive() for f in faeden), "Die beiden Sitzungen hängen aneinander"


@pytest.mark.django_db(transaction=True)
class TestSammelaktionUndHoldAnlageVerklemmenSichNicht:
    @pytest.mark.parametrize("weg", sorted(_SAMMELWEGE))
    def test_hold_anlage_auf_den_zweiten_vorschlag_unter_gehaltener_audit_kette(self, bestand, weg):
        sammelaktion, status_danach, _ = _SAMMELWEGE[weg]
        lead, facility_id = bestand.lead, bestand.facility.pk
        erster, zweiter = bestand.vorschlaege
        kette_gehalten = threading.Event()
        hold_sitzung, ergebnis, fehler = {}, {}, {}

        def _sammeln():
            vorschlaege = [RetentionProposal.objects.get(pk=v.pk) for v in (erster, zweiter)]
            moment = _ErsteAuditZeile(
                facility_id, lambda: _parken_bis_die_andere_sitzung_wartet(kette_gehalten, hold_sitzung)
            )
            with connection.execute_wrapper(moment):
                return sammelaktion(vorschlaege, lead)

        def _hold_anlegen():
            assert kette_gehalten.wait(timeout=_JOIN_TIMEOUT), "Die Sammelaktion erreichte keine Audit-Zeile"
            frisch = RetentionProposal.objects.get(pk=zweiter.pk)
            # Dieselbe Klammer wie in ``RetentionHoldView.post``.
            with transaction.atomic():
                create_legal_hold(frisch, lead, "Behördenanordnung während der Sammelaktion")
            return "ok"

        _laufen_lassen(
            [
                _in_eigenem_faden("sammelaktion", _sammeln, ergebnis, fehler),
                _in_eigenem_faden("hold", _hold_anlegen, ergebnis, fehler, sitzung=hold_sitzung),
            ]
        )

        assert not fehler, fehler
        # Die Sammelaktion hielt die Auswahl zuerst — sie kommt vollständig durch.
        assert ergebnis["sammelaktion"] in ((2, 0), 2), ergebnis
        zweiter.refresh_from_db()
        aktive_holds = LegalHold.objects.filter(target_id=zweiter.target_id, dismissed_at__isnull=True)
        hold_zeilen = AuditLog.objects.filter(
            action=AuditLog.Action.LEGAL_HOLD,
            target_id=str(zweiter.target_id),
            detail__category="legal_hold_created",
        )
        if ergebnis["hold"] == "ok":
            assert zweiter.status == RetentionProposal.Status.HELD
            assert aktive_holds.count() == 1
            assert hold_zeilen.count() == 1
        else:
            assert isinstance(ergebnis["hold"], ValidationError), ergebnis
            assert zweiter.status == status_danach
            assert not aktive_holds.exists()
            assert not hold_zeilen.exists()


@pytest.mark.django_db(transaction=True)
class TestZweiSammelaktionenMitGegenlaeufigerAuswahlVerklemmenSichNicht:
    @pytest.mark.parametrize(
        ("weg", "audit_zeilen_je_vorschlag"),
        [("genehmigen", 1), ("zurueckstellen", 2), ("ablehnen", 1)],
    )
    def test_gegenlaeufige_auswahl(self, bestand, weg, audit_zeilen_je_vorschlag):
        sammelaktion, status_danach, kategorie = _SAMMELWEGE[weg]
        lead, facility_id = bestand.lead, bestand.facility.pk
        erster, zweiter = bestand.vorschlaege
        kette_gehalten = threading.Event()
        zweite_sitzung, ergebnis, fehler = {}, {}, {}

        def _vorwaerts():
            vorschlaege = [RetentionProposal.objects.get(pk=v.pk) for v in (erster, zweiter)]
            moment = _ErsteAuditZeile(
                facility_id, lambda: _parken_bis_die_andere_sitzung_wartet(kette_gehalten, zweite_sitzung)
            )
            with connection.execute_wrapper(moment):
                return sammelaktion(vorschlaege, lead)

        def _rueckwaerts():
            assert kette_gehalten.wait(timeout=_JOIN_TIMEOUT), "Die erste Sammelaktion erreichte keine Audit-Zeile"
            vorschlaege = [RetentionProposal.objects.get(pk=v.pk) for v in (zweiter, erster)]
            return sammelaktion(vorschlaege, lead)

        _laufen_lassen(
            [
                _in_eigenem_faden("vorwaerts", _vorwaerts, ergebnis, fehler),
                _in_eigenem_faden("rueckwaerts", _rueckwaerts, ergebnis, fehler, sitzung=zweite_sitzung),
            ]
        )

        assert not fehler, fehler
        # Die erste Sammelaktion hielt die Auswahl zuerst — sie kommt vollständig durch.
        assert ergebnis["vorwaerts"] in ((2, 0), 2), ergebnis
        # Die zweite endet regulär: sie bearbeitet, überspringt oder weist ab.
        assert not isinstance(ergebnis["rueckwaerts"], Exception) or isinstance(
            ergebnis["rueckwaerts"], ValidationError
        ), ergebnis
        for vorschlag in (erster, zweiter):
            vorschlag.refresh_from_db()
            assert vorschlag.status == status_danach
            assert (
                AuditLog.objects.filter(target_id=str(vorschlag.target_id), detail__category=kategorie).count()
                == audit_zeilen_je_vorschlag
            )
