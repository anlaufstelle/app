"""Refs #2374: Hold-Anlage und Vorschlagsentscheidungen sperren in EINER Folge.

Wer einen Löschvorschlag entscheidet (genehmigen, zurückstellen, ablehnen)
oder unter eine Aufbewahrungssperre stellt, sperrt die Vorschlagszeile und
schreibt danach eine Audit-Zeile. Die Audit-Zeile nimmt die Sperre der
Audit-Kette der Einrichtung (``pg_advisory_xact_lock``) und hält sie bis zum
Ende der Transaktion. Sperrt ein Weg den Vorschlag erst NACH der Audit-Kette,
ein anderer VOR ihr, warten beide aufeinander: die Datenbank bricht einen der
beiden als Verklemmung ab.

Beobachtet wird deshalb, ob die Transaktion die Audit-Kette in dem Moment
schon hält, in dem die Vorschlagszeile gesperrt wird (``pg_locks`` der eigenen
Sitzung) — nicht der Wortlaut einer Anweisung. Der Nebenläufigkeitstest stellt
den kritischen Ablauf mit zwei Verbindungen nach: die Entscheidung hält den
Vorschlag, die Hold-Anlage läuft auf eine Sperre auf, erst dann schreibt die
Entscheidung ihre Audit-Zeile. Beide Ausgänge sind zulässig, eine
Verklemmung ist es nicht.
"""

import threading
import time
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
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
from core.retention.proposals import approve_proposal, defer_proposal, reject_proposal
from core.services.audit.chain import advisory_lock_name
from tests._row_lock_helpers import mitschnitt

_AUDIT_KETTE = "audit-kette"
_BEOBACHTETE_TABELLEN = ("core_client", "core_case", "core_event", "core_retentionproposal")
_JOIN_TIMEOUT = 30
#: Wie lange die Entscheidung höchstens darauf wartet, dass die Hold-Anlage auf
#: eine Sperre aufläuft. Läuft sie nicht auf, geht der Test trotzdem weiter.
_WARTEFRIST_SEKUNDEN = 10


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


def _sperrfolge_mit_audit_kette(facility_id, aktion):
    """Zeilensperren der beobachteten Tabellen in ihrer Folge, dazu die Stelle der Audit-Kette."""
    assert not _haelt_audit_kette(facility_id), "Vorbedingung: die Testtransaktion hält die Audit-Kette noch nicht"
    kette_schon_gehalten = {}
    with mitschnitt() as aufzeichnung:
        for tabelle in _BEOBACHTETE_TABELLEN:
            aufzeichnung.nach_sperre(
                tabelle,
                lambda t=tabelle: kette_schon_gehalten.__setitem__(t, _haelt_audit_kette(facility_id)),
            )
        aktion()
    folge = [t for t in aufzeichnung.sperrfolge() if t in _BEOBACHTETE_TABELLEN]
    davor = [t for t in folge if not kette_schon_gehalten[t]]
    danach = [t for t in folge if kette_schon_gehalten[t]]
    kette = [_AUDIT_KETTE] if _haelt_audit_kette(facility_id) else []
    return davor + kette + danach


@pytest.mark.django_db
class TestVorschlagWirdVorDerAuditKetteGesperrt:
    @pytest.mark.parametrize("weg", ["hold_anlegen", "genehmigen", "zurueckstellen", "ablehnen"])
    def test_jeder_weg_sperrt_den_vorschlag_vor_der_audit_kette(self, retention_proposal, lead_user, weg):
        aktionen = {
            "hold_anlegen": lambda: create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren"),
            "genehmigen": lambda: approve_proposal(retention_proposal, lead_user),
            "zurueckstellen": lambda: defer_proposal(retention_proposal, lead_user),
            "ablehnen": lambda: reject_proposal(retention_proposal, lead_user),
        }

        folge = _sperrfolge_mit_audit_kette(retention_proposal.facility_id, aktionen[weg])

        assert [s for s in folge if s in ("core_retentionproposal", _AUDIT_KETTE)] == [
            "core_retentionproposal",
            _AUDIT_KETTE,
        ], folge

    def test_hold_anlage_sperrt_ziel_dann_vorschlag_dann_audit_kette(self, retention_proposal, lead_user):
        folge = _sperrfolge_mit_audit_kette(
            retention_proposal.facility_id,
            lambda: create_legal_hold(retention_proposal, lead_user, "Gerichtsverfahren"),
        )

        assert folge == ["core_client", "core_event", "core_retentionproposal", _AUDIT_KETTE]


def _backend_pid():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


def _wartet_auf_eine_sperre(pid):
    with connection.cursor() as cursor:
        cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)", [pid])
        return cursor.fetchone()[0]


def _hold_audit_zeilen(proposal):
    return AuditLog.objects.filter(
        action=AuditLog.Action.LEGAL_HOLD,
        target_id=str(proposal.target_id),
        detail__category="legal_hold_created",
    )


@pytest.mark.django_db(transaction=True)
class TestHoldAnlageUndEntscheidungVerklemmenSichNicht:
    @pytest.fixture
    def bestand(self, django_db_setup):
        org = Organization.objects.create(name="Hold-Folge-Org")
        facility = org.facilities.create(name="Hold-Folge-Stelle")
        Settings.objects.get_or_create(facility=facility)
        lead = User.objects.create_user(
            username="hold-folge-lead",
            password="x" * 40,
            facility=facility,
            role=User.Role.LEAD,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Hold-Folge)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Hold-Folge-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now() - timedelta(days=400),
            data_json={"freitext": "Klartext"},
        )
        proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=ereignis.pk,
            deletion_due_at=date.today() + timedelta(days=5),
            status=RetentionProposal.Status.PENDING,
            details={},
            retention_category="identified",
        )
        yield lead, proposal
        connections.close_all()

    @pytest.mark.parametrize(
        ("entscheidung", "status_danach"),
        [
            (reject_proposal, RetentionProposal.Status.REJECTED),
            (defer_proposal, RetentionProposal.Status.DEFERRED),
        ],
        ids=["ablehnen", "zurueckstellen"],
    )
    def test_hold_anlage_waehrend_die_entscheidung_den_vorschlag_haelt(self, bestand, entscheidung, status_danach):
        lead, proposal = bestand
        vorschlag_gesperrt = threading.Event()
        hold_sitzung = {}
        ergebnis = {}
        fehler = {}

        def _warten_bis_die_hold_anlage_aufgelaufen_ist():
            vorschlag_gesperrt.set()
            frist = time.monotonic() + _WARTEFRIST_SEKUNDEN
            while time.monotonic() < frist:
                pid = hold_sitzung.get("pid")
                if pid is not None and _wartet_auf_eine_sperre(pid):
                    return
                time.sleep(0.05)

        def _entscheiden():
            connection.close()
            try:
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                with mitschnitt() as aufzeichnung:
                    aufzeichnung.nach_sperre("core_retentionproposal", _warten_bis_die_hold_anlage_aufgelaufen_ist)
                    entscheidung(frisch, lead)
                ergebnis["entscheidung"] = "ok"
            except ValidationError as exc:
                ergebnis["entscheidung"] = exc
            except Exception as exc:  # noqa: BLE001 — in die Meldung, nicht verschlucken
                fehler["entscheidung"] = exc
            finally:
                connections.close_all()

        def _hold_anlegen():
            connection.close()
            try:
                hold_sitzung["pid"] = _backend_pid()
                assert vorschlag_gesperrt.wait(timeout=_JOIN_TIMEOUT), "Die Entscheidung erreichte den Vorschlag nicht"
                frisch = RetentionProposal.objects.get(pk=proposal.pk)
                # Dieselbe Klammer wie in ``RetentionHoldView.post``.
                with transaction.atomic():
                    create_legal_hold(frisch, lead, "Behördenanordnung über die Oberfläche")
                ergebnis["hold"] = "ok"
            except ValidationError as exc:
                ergebnis["hold"] = exc
            except Exception as exc:  # noqa: BLE001
                fehler["hold"] = exc
            finally:
                connections.close_all()

        faeden = [
            threading.Thread(target=_entscheiden, name="entscheidung"),
            threading.Thread(target=_hold_anlegen, name="hold-anlage"),
        ]
        for faden in faeden:
            faden.start()
        for faden in faeden:
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not any(f.is_alive() for f in faeden), "Hold-Anlage und Entscheidung hängen aneinander"
        assert not fehler, fehler
        # Die Entscheidung hielt den Vorschlag zuerst — sie kommt durch.
        assert ergebnis["entscheidung"] == "ok", ergebnis
        proposal.refresh_from_db()
        aktive_holds = LegalHold.objects.filter(target_id=proposal.target_id, dismissed_at__isnull=True)
        if ergebnis["hold"] == "ok":
            # Die Entscheidung liess eine Löschung offen, der Hold hält sie an.
            assert proposal.status == RetentionProposal.Status.HELD
            assert aktive_holds.count() == 1
            assert _hold_audit_zeilen(proposal).count() == 1
        else:
            # Nach der Ablehnung gibt es nichts anzuhalten: weder Hold noch Audit-Zeile.
            assert isinstance(ergebnis["hold"], ValidationError), ergebnis
            assert proposal.status == status_danach
            assert not aktive_holds.exists()
            assert not _hold_audit_zeilen(proposal).exists()
