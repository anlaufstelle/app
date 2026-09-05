"""Hold-TOCTOU-Restfundstellen der Retention-Pfade (Refs #2122).

Drei Fundstellen derselben Wurzel „Hold einmal lesen, dann schreiben", die
#2107 (Ereignis-Sweep), #2108 (Sofortpfad/Nacht-Batch) und #2109 (Approve)
uebrig gelassen haben:

1. **K-Anon-Bridge** ``core.retention.anonymization.anonymize_clients`` —
   filterte Holds nur beim Queryset-Bau und iterierte danach ohne Zeilensperre
   und ohne Re-Check. Ein waehrend des Laufs angeordneter Hold erreichte die
   bereits eingesammelten Personen nicht mehr.
2. **Auto-Approve** in ``core.retention.proposals.reactivate_deferred_proposals``
   — setzte ``APPROVED`` direkt und umging damit die komplette Sperr-/Check-
   Sequenz, die #2109 fuer ``approve_proposal`` eingezogen hat.
3. **Fall-Holds ohne Anker** — ``create_hold_for_target`` sperrte fuer
   ``target_type="Case"`` gar nichts. Ein Fall-Hold zaehlt in
   ``client_has_active_hold`` mit, hatte aber keinen gemeinsamen
   Koordinationspunkt mit Sofortpfad, Nacht-Batch, Bridge, Sweep und Approve —
   er konnte neben einer laufenden Anonymisierung her committen.

Eigene Datei, weil die Renn-Nachweise ``django_db(transaction=True)`` brauchen
(echte Nebenlaeufigkeit auf zwei Verbindungen) und die uebrigen Retention-Tests
im schnellen Transaktions-Rollback laufen.

Die Renn-Guards sind **beobachtend** gebaut (Welle-E-Lehre 36): zwei Faeden,
endliche Wartezeit, Auswertung NACH dem Rennen — kein ``assert`` innerhalb des
gesperrten Fensters.
"""

import re
import threading
import time
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    AuditLog,
    Case,
    Client,
    DocumentType,
    Event,
    LegalHold,
    Organization,
    RetentionProposal,
    Settings,
    User,
)
from core.retention import anonymization as anonymization_mod
from core.retention.anonymization import anonymize_clients
from core.retention.legal_holds import create_hold_for_target, has_active_hold
from core.retention.proposals import reactivate_deferred_proposals
from core.services.client import anonymize_client_now

#: Untergrenze, bis der Gegenspieler sicher an der Zeile haengt. Bewusst nur
#: eine Untergrenze: laeuft er langsamer, ist der Test trotzdem aussagekraeftig.
_BLOCKED_GRACE_SECONDS = 0.75
_JOIN_TIMEOUT = 30

#: ``FOR UPDATE``-Statements verraten die Sperr-Reihenfolge (Muster
#: ``test_restriction_lock_order.py``).
_LOCK_RE = re.compile(r'FROM "(?P<table>core_\w+)"[^;]*FOR UPDATE', re.IGNORECASE | re.DOTALL)


def _lock_sequence(captured):
    sequence = []
    for query in captured.captured_queries:
        match = _LOCK_RE.search(query["sql"])
        if match:
            sequence.append(match.group("table"))
    return sequence


def _committed_bestand(*, mit_fall=False, im_papierkorb=False, events=5):
    """Committeter Bestand fuer die Faden-Tests.

    Die conftest-Fixtures reichen dort nicht: die Faeden bringen eigene
    Verbindungen mit und sehen nur, was committet ist.
    """
    org = Organization.objects.create(name="TOCTOU-Org")
    facility = org.facilities.create(name="TOCTOU-Stelle")
    Settings.objects.get_or_create(facility=facility)
    admin = User.objects.create_user(
        username="toctou-admin",
        password="x" * 40,
        facility=facility,
        role=User.Role.FACILITY_ADMIN,
    )
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Kontakt (TOCTOU)",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    person = Client.objects.create(
        facility=facility,
        pseudonym="TOCTOU-Person",
        contact_stage=Client.ContactStage.IDENTIFIED,
        notes="Interne Notiz im Klartext",
    )
    fall = None
    if mit_fall:
        fall = Case.objects.create(
            facility=facility,
            client=person,
            title="Fall unter Anordnung",
            created_by=admin,
        )
    for i in range(events):
        Event.objects.create(
            facility=facility,
            client=person,
            case=fall,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"freitext": f"Klartext {i}"},
            is_deleted=not im_papierkorb,
        )
    if im_papierkorb:
        person.soft_delete(user=admin)
    return facility, admin, person, fall


@pytest.mark.django_db(transaction=True)
class TestKAnonBridgeHoldRace:
    """Fundstelle 1: die Bridge muss den Hold UNTER der Personensperre lesen."""

    @pytest.fixture
    def bestand(self, django_db_setup):
        facility, admin, person, _ = _committed_bestand()
        yield facility, admin, person
        connections.close_all()

    def test_hold_der_waehrend_der_sperrwartezeit_committet_haelt_die_bridge_auf(self, bestand):
        """Faden A haelt die Personenzeile und legt darunter den Hold an.

        Faden B faehrt die Bridge und muss an genau dieser Zeile warten, BEVOR
        er den Hold liest — sonst liest er den Stand von vor der Sperrwartezeit.
        """
        facility, admin, person = bestand

        a_haelt_die_sperre = threading.Event()
        b_ist_gestartet = threading.Event()
        fehler = {}
        ergebnis = {}

        def _hold_anlegen():
            connection.close()
            try:
                with transaction.atomic():
                    Client.objects.filter(pk=person.pk).select_for_update().only("pk").first()
                    a_haelt_die_sperre.set()
                    assert b_ist_gestartet.wait(timeout=_JOIN_TIMEOUT), "Faden B ist nie gestartet"
                    # B haengt jetzt entweder an der Sperre (nach dem Fix) oder
                    # hat seine ungesperrte Hold-Lesung laengst hinter sich.
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    create_hold_for_target(
                        facility,
                        target_type="Client",
                        target_id=person.pk,
                        user=admin,
                        reason="Behoerdenanordnung waehrend des Retention-Laufs",
                    )
            except Exception as exc:  # noqa: BLE001 — in den Bericht, nicht verschlucken
                fehler["a"] = exc
            finally:
                connections.close_all()

        def _bridge_fahren():
            connection.close()
            try:
                b_ist_gestartet.set()
                ergebnis["b"] = anonymize_clients(facility, dry_run=False)
            except Exception as exc:  # noqa: BLE001
                fehler["b"] = exc
            finally:
                connections.close_all()

        faden_a = threading.Thread(target=_hold_anlegen, name="hold")
        faden_a.start()
        assert a_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Faden A bekam die Personensperre nicht"

        faden_b = threading.Thread(target=_bridge_fahren, name="bridge")
        faden_b.start()

        faden_a.join(timeout=_JOIN_TIMEOUT)
        faden_b.join(timeout=_JOIN_TIMEOUT)
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung zwischen Hold und Bridge"
        assert not fehler, fehler

        person.refresh_from_db()
        assert has_active_hold(facility, "Client", person.pk), "Der Hold von Faden A fehlt — Aufbau kaputt"
        assert person.pseudonym == "TOCTOU-Person", (
            "Die K-Anon-Bridge hat trotz eines waehrend der Sperrwartezeit committeten Holds anonymisiert (Spoliation)"
        )
        assert person.k_anonymized is False
        assert person.notes == "Interne Notiz im Klartext"
        assert ergebnis["b"]["count"] == 0
        assert ergebnis["b"]["skipped_holds"] == 1


@pytest.mark.django_db
class TestKAnonBridgeHoldRecheck:
    """Fundstelle 1, funktional: Hold zwischen Sammeln und Verarbeiten."""

    def _bestand(self, facility, staff_user, doc_type_contact, pseudonym):
        person = Client.objects.create(
            facility=facility,
            pseudonym=pseudonym,
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            is_deleted=True,
            created_by=staff_user,
        )
        return person

    def test_hold_zwischen_sammeln_und_verarbeiten_ueberspringt_die_person(
        self, facility, staff_user, lead_user, doc_type_contact, monkeypatch
    ):
        """Der Hold entsteht NACH dem Queryset-Bau — genau das Fenster des Befunds.

        Der Haken haengt an ``exclude_clients_with_active_holds``, also an der
        Stelle, an der die Bridge ihre Hold-Menge EINMAL liest. Danach ist der
        Hold aktiv; ohne Re-Check unter der Sperre wird er nie gesehen.
        """
        Settings.objects.get_or_create(facility=facility)
        gehalten = self._bestand(facility, staff_user, doc_type_contact, "P-Nachtraeglich")
        frei = self._bestand(facility, staff_user, doc_type_contact, "P-Frei")

        echte_filterung = anonymization_mod.exclude_clients_with_active_holds
        ausgeloest = {"done": False}

        def _dann_hold_anlegen(queryset, fac):
            gefiltert = echte_filterung(queryset, fac)
            if not ausgeloest["done"]:
                ausgeloest["done"] = True
                LegalHold.objects.create(
                    facility=fac,
                    target_type="Client",
                    target_id=gehalten.pk,
                    reason="Anordnung waehrend des Laufs",
                    created_by=lead_user,
                )
            return gefiltert

        monkeypatch.setattr(anonymization_mod, "exclude_clients_with_active_holds", _dann_hold_anlegen)

        result = anonymize_clients(facility, dry_run=False)

        assert ausgeloest["done"], "Das Fenster wurde nicht geoeffnet — Aufbau kaputt"
        gehalten.refresh_from_db()
        frei.refresh_from_db()
        assert gehalten.pseudonym == "P-Nachtraeglich", "Person unter aktivem Hold wurde anonymisiert (Spoliation)"
        assert frei.pseudonym.startswith("Gelöscht-"), "Positiv-Kontrolle: der Lauf agiert real"
        assert result["skipped_holds"] == 1
        assert result["count"] == 1, "``count`` muss die tatsaechlich anonymisierten Personen melden"


@pytest.mark.django_db
class TestAutoApproveRespectsHold:
    """Fundstelle 2: Auto-Approve muss dieselbe Sequenz wie ``approve_proposal`` fahren."""

    def _proposal(self, facility, client, doc_type, staff_user, *, defer_count=3):
        event = Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 1},
            created_by=staff_user,
        )
        proposal = RetentionProposal.objects.create(
            facility=facility,
            target_type=RetentionProposal.TargetType.EVENT,
            target_id=event.pk,
            retention_category="identified",
            deletion_due_at=timezone.localdate(),
            status=RetentionProposal.Status.DEFERRED,
            deferred_until=timezone.localdate() - timedelta(days=1),
            defer_count=defer_count,
        )
        return event, proposal

    def test_auto_approve_ueberspringt_vorschlag_unter_aktivem_hold(
        self, facility, client_identified, doc_type_contact, staff_user, lead_user
    ):
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"retention_auto_approve_after_defer": True, "retention_max_defer_count": 2},
        )
        gehaltenes_event, gehalten = self._proposal(
            facility, client_identified, doc_type_contact, staff_user, defer_count=3
        )
        _, frei = self._proposal(facility, client_identified, doc_type_contact, staff_user, defer_count=3)

        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=gehaltenes_event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )

        reactivated, auto_approved = reactivate_deferred_proposals(facility)

        gehalten.refresh_from_db()
        frei.refresh_from_db()
        assert gehalten.status == RetentionProposal.Status.DEFERRED, (
            "Ein Vorschlag unter aktivem Hold wurde auto-genehmigt (Refs #2122)"
        )
        assert frei.status == RetentionProposal.Status.APPROVED, "Positiv-Kontrolle: der Lauf agiert real"
        assert auto_approved == 1
        assert reactivated == 0
        assert not AuditLog.objects.filter(
            target_id=str(gehalten.target_id),
            detail__category="retention_proposal_auto_approved",
        ).exists(), "Audit behauptet eine Auto-Genehmigung unter laufender Sperre"
        assert AuditLog.objects.filter(
            target_id=str(gehalten.target_id),
            detail__category="retention_proposal_auto_approve_skipped",
        ).exists(), "Die Zurueckstellung muss nachweisbar sein (nicht geloescht != nicht angeschaut)"

    def test_client_hold_blockiert_auto_approve_ebenso(
        self, facility, client_identified, doc_type_contact, staff_user, lead_user
    ):
        """Refs #1376 (I10): der Hold auf die PERSON schuetzt deren Ereignisse."""
        Settings.objects.update_or_create(
            facility=facility,
            defaults={"retention_auto_approve_after_defer": True, "retention_max_defer_count": 2},
        )
        _, gehalten = self._proposal(facility, client_identified, doc_type_contact, staff_user, defer_count=3)
        gehalten.deferred_until = timezone.localdate() - timedelta(days=1)
        gehalten.save(update_fields=["deferred_until"])
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=client_identified.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )

        _, auto_approved = reactivate_deferred_proposals(facility)

        gehalten.refresh_from_db()
        assert gehalten.status == RetentionProposal.Status.DEFERRED
        assert auto_approved == 0


@pytest.mark.django_db
class TestCaseHoldLockOrder:
    """Fundstelle 3, Ordnungs-Pin: ``core_client`` -> ``core_case`` -> ``core_event``."""

    def test_case_hold_locks_client_then_case_then_events(self, facility, client_identified, staff_user, lead_user):
        fall = Case.objects.create(
            facility=facility,
            client=client_identified,
            title="Fall mit Anker",
            created_by=staff_user,
        )
        with CaptureQueriesContext(connection) as captured:
            create_hold_for_target(
                facility,
                target_type="Case",
                target_id=fall.pk,
                user=lead_user,
                reason="Anordnung",
            )
        sequence = _lock_sequence(captured)
        assert "core_client" in sequence, f"Fall-Hold nahm keinen Personen-Anker: {sequence}"
        assert "core_case" in sequence, f"Fall-Hold sperrte die Fallzeile nicht: {sequence}"
        assert sequence.index("core_client") < sequence.index("core_case"), (
            f"kanonische Ordnung verdreht (core_client vor core_case): {sequence}"
        )
        # Die Ereignissperre wird auch ohne Ereignisse abgesetzt (SELECT ... FOR UPDATE
        # auf core_event) — sie muss also immer in der Sequenz stehen (kimi-k3 B-6).
        assert "core_event" in sequence, f"Fall-Hold sperrte die Ereigniszeilen nicht: {sequence}"
        assert sequence.index("core_case") < sequence.index("core_event"), (
            f"kanonische Ordnung verdreht (core_case vor core_event): {sequence}"
        )

    def test_hold_auf_fall_fremder_einrichtung_bleibt_zulaessig(self, facility, other_facility, lead_user, staff_user):
        """``target_id`` ist ein freies Feld — ein unbekannter Fall darf nicht knallen."""
        fremd = Client.objects.create(facility=other_facility, pseudonym="Fremd", created_by=staff_user)
        fall = Case.objects.create(
            facility=other_facility,
            client=fremd,
            title="Fremder Fall",
            created_by=staff_user,
        )
        hold = create_hold_for_target(
            facility,
            target_type="Case",
            target_id=fall.pk,
            user=lead_user,
            reason="Anordnung",
        )
        assert hold.pk is not None


@pytest.mark.django_db(transaction=True)
class TestCaseHoldAnchorRace:
    """Fundstelle 3, Renn-Nachweis: der Fall-Hold muss am Personen-Anker warten.

    Die Richtung ist bewusst die umgekehrte des #2108-Guards: der Fall-Hold
    hatte GAR KEINE Sperre, er konnte also neben einer laufenden Anonymisierung
    her committen. Beobachtet wird deshalb, ob die Hold-Anlage im gesperrten
    Fenster fertig wird — nicht, ob sie ein bestimmtes Ergebnis liefert.
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        facility, admin, person, fall = _committed_bestand(mit_fall=True, im_papierkorb=True)
        yield facility, admin, person, fall
        connections.close_all()

    def test_fall_hold_wartet_am_personen_anker(self, bestand):
        facility, admin, person, fall = bestand

        a_haelt_die_sperre = threading.Event()
        b_ist_gestartet = threading.Event()
        b_ist_fertig = threading.Event()
        beobachtung = {}
        fehler = {}

        def _anonymisierung_simulieren():
            """Haelt genau die Zeile, die der Sofortpfad/Nacht-Batch haelt."""
            connection.close()
            try:
                with transaction.atomic():
                    Client.objects.filter(pk=person.pk).select_for_update().only("pk").first()
                    a_haelt_die_sperre.set()
                    assert b_ist_gestartet.wait(timeout=_JOIN_TIMEOUT), "Faden B ist nie gestartet"
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    # Nur BEOBACHTEN, nicht behaupten (Lehre 36): die Auswertung
                    # liegt nach dem Rennen.
                    beobachtung["hold_im_fenster_fertig"] = b_ist_fertig.is_set()
            except Exception as exc:  # noqa: BLE001
                fehler["a"] = exc
            finally:
                connections.close_all()

        def _fall_hold_anlegen():
            connection.close()
            try:
                b_ist_gestartet.set()
                create_hold_for_target(
                    facility,
                    target_type="Case",
                    target_id=fall.pk,
                    user=admin,
                    reason="Anordnung auf den Fall",
                )
            except Exception as exc:  # noqa: BLE001
                fehler["b"] = exc
            finally:
                b_ist_fertig.set()
                connections.close_all()

        faden_a = threading.Thread(target=_anonymisierung_simulieren, name="anonymize")
        faden_a.start()
        assert a_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Faden A bekam die Personensperre nicht"

        faden_b = threading.Thread(target=_fall_hold_anlegen, name="case-hold")
        faden_b.start()

        faden_a.join(timeout=_JOIN_TIMEOUT)
        faden_b.join(timeout=_JOIN_TIMEOUT)
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung zwischen Anonymisierung und Fall-Hold"
        assert not fehler, fehler

        assert beobachtung["hold_im_fenster_fertig"] is False, (
            "Die Fall-Hold-Anlage committete neben der gehaltenen Personenzeile her — "
            "es gibt keinen gemeinsamen Anker mit der Anonymisierung (Refs #2122)"
        )
        assert has_active_hold(facility, "Case", fall.pk), "Der Fall-Hold fehlt — Aufbau kaputt"

    def test_sofortpfad_sieht_den_fall_hold_unter_der_sperre(self, bestand):
        """Gegenrichtung: Fall-Hold zuerst, dann der Sofortpfad — er muss abbrechen."""
        from django.core.exceptions import ValidationError

        facility, admin, person, fall = bestand
        create_hold_for_target(
            facility,
            target_type="Case",
            target_id=fall.pk,
            user=admin,
            reason="Anordnung auf den Fall",
        )
        with pytest.raises(ValidationError):
            anonymize_client_now(person, admin)
        person.refresh_from_db()
        assert person.anonymized_at is None


# ---------------------------------------------------------------------------
# Refs #2123 — Fall-Tilgung: Anker + Re-Check, Attachment-Purge unter der Sperre
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestCaseAnonymizeNowHoldRace:
    """Der Fall-Sofortpfad muss am selben Anker warten wie die Hold-Anlage.

    ``anonymize_case_now`` las ``case_has_active_hold`` vollkommen ungesperrt —
    die eigene ``@transaction.atomic`` beginnt erst in ``anonymize_case``. Ein
    waehrend der Redaktion angeordneter Fall-Hold kam damit zu spaet, der Fall
    war schon irreversibel redigiert (Spoliation, Muster #2108).

    Beobachtend gebaut: Faden A haelt die Personenzeile — genau die Zeile, die
    ``create_hold_for_target`` fuer einen Fall-Hold als erste Stufe sperrt — und
    legt in dieser Zeit den Hold an. Im gesperrten Fenster wird nur notiert.
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        facility, admin, person, fall = _committed_bestand(mit_fall=True, im_papierkorb=True)
        Case.objects.filter(pk=fall.pk).update(is_deleted=True, deleted_at=timezone.now())
        fall.refresh_from_db()
        yield facility, admin, person, fall
        connections.close_all()

    def test_sofortpfad_wartet_am_anker_und_sieht_den_hold(self, bestand):
        from django.core.exceptions import ValidationError

        from core.services.case.deletion import anonymize_case_now

        facility, admin, person, fall = bestand

        a_haelt_die_sperre = threading.Event()
        b_ist_gestartet = threading.Event()
        b_ist_fertig = threading.Event()
        beobachtung = {}
        fehler = {}

        def _hold_anlage_simulieren():
            """Haelt die Personenzeile und legt den Fall-Hold an — wie ``create_hold_for_target``."""
            connection.close()
            try:
                with transaction.atomic():
                    Client.objects.filter(pk=person.pk).select_for_update().only("pk").first()
                    a_haelt_die_sperre.set()
                    assert b_ist_gestartet.wait(timeout=_JOIN_TIMEOUT), "Faden B ist nie gestartet"
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    beobachtung["tilgung_im_fenster_fertig"] = b_ist_fertig.is_set()
                    LegalHold.objects.create(
                        facility=facility,
                        target_type="Case",
                        target_id=fall.pk,
                        reason="Anordnung waehrend der Tilgung",
                        created_by=admin,
                    )
            except Exception as exc:  # noqa: BLE001
                fehler["a"] = exc
            finally:
                connections.close_all()

        def _sofort_tilgen():
            connection.close()
            try:
                b_ist_gestartet.set()
                anonymize_case_now(Case.objects.get(pk=fall.pk), admin)
            except Exception as exc:  # noqa: BLE001
                fehler["b"] = exc
            finally:
                b_ist_fertig.set()
                connections.close_all()

        faden_a = threading.Thread(target=_hold_anlage_simulieren, name="case-hold")
        faden_a.start()
        assert a_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Faden A bekam die Personensperre nicht"

        faden_b = threading.Thread(target=_sofort_tilgen, name="anonymize-case")
        faden_b.start()

        faden_a.join(timeout=_JOIN_TIMEOUT)
        faden_b.join(timeout=_JOIN_TIMEOUT)
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung zwischen Hold-Anlage und Fall-Tilgung"
        assert "a" not in fehler, fehler

        assert beobachtung["tilgung_im_fenster_fertig"] is False, (
            "Die Fall-Tilgung lief neben der gehaltenen Personenzeile durch — kein gemeinsamer Anker (Refs #2123)"
        )
        assert isinstance(fehler.get("b"), ValidationError), (
            f"Die Tilgung sah den waehrenddessen angeordneten Hold nicht: {fehler!r}"
        )
        fall.refresh_from_db()
        assert fall.anonymized_at is None, "Der Fall wurde trotz aktivem Hold getilgt (Spoliation)"


@pytest.mark.django_db
class TestCaseBatchRechecktHoldUnterSperre:
    """Der Nacht-Batch der Fall-Tilgung muss den Hold ein zweites Mal lesen.

    ``anonymize_eligible_soft_deleted_cases`` filterte Holds nur beim
    Queryset-Bau und iterierte danach ohne Zeilensperre und ohne Re-Check —
    dieselbe Luecke, die #2013/#2108 beim Personen-Pendant geschlossen haben.
    """

    def _fall_im_papierkorb(self, facility, staff_user, titel, tage):
        person = Client.objects.create(
            facility=facility,
            pseudonym=f"Person {titel}",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        fall = Case.objects.create(
            facility=facility,
            client=person,
            title=titel,
            created_by=staff_user,
        )
        Case.objects.filter(pk=fall.pk).update(is_deleted=True, deleted_at=timezone.now() - timedelta(days=tage))
        fall.refresh_from_db()
        return fall

    def test_hold_zwischen_sammeln_und_verarbeiten_ueberspringt_den_fall(
        self, facility, settings_obj, staff_user, lead_user, monkeypatch
    ):
        from core.services.case import deletion as deletion_mod

        gehalten = self._fall_im_papierkorb(facility, staff_user, "Fall gehalten", settings_obj.client_trash_days + 5)
        frei = self._fall_im_papierkorb(facility, staff_user, "Fall frei", settings_obj.client_trash_days + 5)

        echte_filterung = deletion_mod.exclude_cases_with_active_holds
        ausgeloest = {"done": False}

        def _dann_hold_anlegen(queryset, fac):
            gefiltert = echte_filterung(queryset, fac)
            if not ausgeloest["done"]:
                ausgeloest["done"] = True
                LegalHold.objects.create(
                    facility=fac,
                    target_type="Case",
                    target_id=gehalten.pk,
                    reason="Anordnung waehrend des Laufs",
                    created_by=lead_user,
                )
            return gefiltert

        monkeypatch.setattr(deletion_mod, "exclude_cases_with_active_holds", _dann_hold_anlegen)

        count = deletion_mod.anonymize_eligible_soft_deleted_cases(facility, settings_obj)

        assert ausgeloest["done"], "Das Fenster wurde nicht geoeffnet — Aufbau kaputt"
        gehalten.refresh_from_db()
        frei.refresh_from_db()
        assert gehalten.anonymized_at is None, "Fall unter aktivem Hold wurde getilgt (Spoliation)"
        assert gehalten.title == "Fall gehalten", "Der gehaltene Fall wurde redigiert"
        assert frei.anonymized_at is not None, "Positiv-Kontrolle: der Lauf agiert real"
        assert count == 1, "``count`` muss die tatsaechlich getilgten Faelle melden"


@pytest.mark.django_db
class TestCaseTilgungSperrtInKanonischerOrdnung:
    """SQL-Sequenz-Pin: ``core_client`` -> ``core_case`` -> ``core_event`` (ADR-050 D5.5)."""

    def test_sofortpfad_sperrt_in_kanonischer_ordnung(self, facility, case_trashed, admin_user):
        from core.services.case.deletion import anonymize_case_now

        Event.objects.create(
            facility=facility,
            client=case_trashed.client,
            case=case_trashed,
            document_type=DocumentType.objects.create(
                facility=facility,
                name="Ordnungs-Typ",
                sensitivity=DocumentType.Sensitivity.NORMAL,
            ),
            occurred_at=timezone.now(),
            data_json={"freitext": "Klartext"},
            created_by=admin_user,
        )
        with CaptureQueriesContext(connection) as captured:
            anonymize_case_now(case_trashed, admin_user)
        sequence = _lock_sequence(captured)
        assert "core_client" in sequence, f"Fall-Tilgung nahm keinen Personen-Anker: {sequence}"
        assert "core_case" in sequence, f"Fall-Tilgung sperrte die Fallzeile nicht: {sequence}"
        assert "core_event" in sequence, f"Fall-Tilgung sperrte die Ereigniszeilen nicht: {sequence}"
        assert sequence.index("core_client") < sequence.index("core_case"), (
            f"kanonische Ordnung verdreht (core_client vor core_case): {sequence}"
        )
        assert sequence.index("core_case") < sequence.index("core_event"), (
            f"kanonische Ordnung verdreht (core_case vor core_event): {sequence}"
        )


@pytest.mark.django_db
class TestAttachmentPurgePrueftHoldUnterSperre:
    """Zweite Fundstelle derselben Klasse: ``purge_attachment_chain``.

    Der Hold-Check lag VOR der ``atomic``-Klammer und damit vor der
    Client-Zeilensperre, die ``assert_processing_allowed(lock=True)`` nimmt —
    das Art.-18-Gate war seit ``cccb048f`` unter der Sperre, der Hold-Check
    nicht. Der Haken haengt deshalb am Art.-18-Gate: alles, was danach passiert,
    laeuft unter der Sperre.
    """

    def test_hold_zwischen_sperre_und_pruefung_blockiert_die_vernichtung(
        self, facility, client_identified, staff_user, lead_user, monkeypatch, _encryption_key
    ):
        from core.models.attachment import Attachment
        from core.services.attachments.crud import AttachmentPurgeBlockedError, purge_attachment_chain
        from core.services.client import restriction as restriction_mod
        from tests.conftest import _make_attachment

        anhang = _make_attachment(facility, client_identified, staff_user, ft_name="Purge-Renn-Datei")

        echtes_gate = restriction_mod.assert_processing_allowed
        ausgeloest = {"done": False}

        def _dann_hold_anlegen(client_id, *, lock=False):
            ergebnis = echtes_gate(client_id, lock=lock)
            if not ausgeloest["done"]:
                ausgeloest["done"] = True
                LegalHold.objects.create(
                    facility=facility,
                    target_type="Client",
                    target_id=client_identified.pk,
                    reason="Anordnung waehrend der Vernichtung",
                    created_by=lead_user,
                )
            return ergebnis

        monkeypatch.setattr(restriction_mod, "assert_processing_allowed", _dann_hold_anlegen)

        with pytest.raises(AttachmentPurgeBlockedError):
            purge_attachment_chain(client_identified, anhang.entry_id)

        assert ausgeloest["done"], "Das Fenster wurde nicht geoeffnet — Aufbau kaputt"
        assert Attachment.objects.filter(pk=anhang.pk).exists(), (
            "Die Kette wurde trotz des unter der Sperre angeordneten Holds vernichtet (Spoliation)"
        )


# ---------------------------------------------------------------------------
# Refs #2124 — Activity-Holds: Anker, Re-Check und kanonische Ordnung
# ---------------------------------------------------------------------------


def _aktivitaet(facility, person, actor, *, alter_tage=400):
    """Eine Aktivitaet mit Personen-Ziel, jenseits der Aktivitaeten-Frist."""
    from django.contrib.contenttypes.models import ContentType

    from core.models import Activity

    aktivitaet = Activity.objects.create(
        facility=facility,
        actor=actor,
        verb=Activity.Verb.CREATED,
        target_type=ContentType.objects.get_for_model(Client),
        target_id=person.pk,
        context={"action": Activity.Action.CLIENT_CREATED},
    )
    Activity.objects.filter(pk=aktivitaet.pk).update(occurred_at=timezone.now() - timedelta(days=alter_tage))
    aktivitaet.refresh_from_db()
    return aktivitaet


@pytest.mark.django_db(transaction=True)
class TestActivityHoldAnchorRace:
    """Die Aktivitaets-Hold-Anlage hatte GAR KEINE Sperre (``else``-Zweig).

    Der Aktivitaeten-Sweep loeschte per Bulk-``DELETE``; es gab also weder ein
    Sperrobjekt noch einen Re-Check. Beobachtet wird, ob die Hold-Anlage im
    gesperrten Fenster fertig wird — ohne Behauptung im Fenster (Lehre 36).
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        facility, admin, person, _ = _committed_bestand(events=0)
        aktivitaet = _aktivitaet(facility, person, admin)
        yield facility, admin, person, aktivitaet
        connections.close_all()

    def test_activity_hold_wartet_an_der_aktivitaetszeile(self, bestand):
        from core.models import Activity

        facility, admin, person, aktivitaet = bestand

        a_haelt_die_sperre = threading.Event()
        b_ist_gestartet = threading.Event()
        b_ist_fertig = threading.Event()
        beobachtung = {}
        fehler = {}

        def _sweep_simulieren():
            """Haelt genau die Zeilen, die der Sweep nach dem Umbau haelt."""
            connection.close()
            try:
                with transaction.atomic():
                    Client.objects.filter(pk=person.pk).select_for_update().only("pk").first()
                    Activity.objects.filter(pk=aktivitaet.pk).select_for_update().only("pk").first()
                    a_haelt_die_sperre.set()
                    assert b_ist_gestartet.wait(timeout=_JOIN_TIMEOUT), "Faden B ist nie gestartet"
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    beobachtung["hold_im_fenster_fertig"] = b_ist_fertig.is_set()
            except Exception as exc:  # noqa: BLE001
                fehler["a"] = exc
            finally:
                connections.close_all()

        def _activity_hold_anlegen():
            connection.close()
            try:
                b_ist_gestartet.set()
                create_hold_for_target(
                    facility,
                    target_type="Activity",
                    target_id=aktivitaet.pk,
                    user=admin,
                    reason="Anordnung auf die Aktivitaet",
                )
            except Exception as exc:  # noqa: BLE001
                fehler["b"] = exc
            finally:
                b_ist_fertig.set()
                connections.close_all()

        faden_a = threading.Thread(target=_sweep_simulieren, name="activity-sweep")
        faden_a.start()
        assert a_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Faden A bekam die Sperren nicht"

        faden_b = threading.Thread(target=_activity_hold_anlegen, name="activity-hold")
        faden_b.start()

        faden_a.join(timeout=_JOIN_TIMEOUT)
        faden_b.join(timeout=_JOIN_TIMEOUT)
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung zwischen Sweep und Hold-Anlage"
        assert not fehler, fehler

        assert beobachtung["hold_im_fenster_fertig"] is False, (
            "Die Aktivitaets-Hold-Anlage committete neben den gehaltenen Zeilen her — "
            "es gibt keinen gemeinsamen Anker mit dem Sweep (Refs #2124)"
        )
        assert has_active_hold(facility, "Activity", aktivitaet.pk), "Der Activity-Hold fehlt — Aufbau kaputt"


@pytest.mark.django_db
class TestActivitySweepRechecktHoldUnterSperre:
    """Der Sweep muss den Hold ein zweites Mal lesen — unter der Zeilensperre."""

    def test_hold_zwischen_sammeln_und_loeschen_stellt_die_aktivitaet_zurueck(
        self, facility, client_identified, staff_user, lead_user, monkeypatch
    ):
        from core.models import Activity
        from core.retention import enforcement as enforcement_mod

        settings_obj, _ = Settings.objects.get_or_create(facility=facility)
        settings_obj.retention_activities_days = 365
        settings_obj.save(update_fields=["retention_activities_days"])

        gehalten = _aktivitaet(facility, client_identified, staff_user)
        frei = _aktivitaet(facility, client_identified, staff_user)

        echte_lesung = enforcement_mod.get_active_hold_target_ids
        ausgeloest = {"done": False}

        def _dann_hold_anlegen(fac, target_type):
            ergebnis = echte_lesung(fac, target_type)
            if target_type == "Client" and not ausgeloest["done"]:
                ausgeloest["done"] = True
                LegalHold.objects.create(
                    facility=fac,
                    target_type="Activity",
                    target_id=gehalten.pk,
                    reason="Anordnung waehrend des Laufs",
                    created_by=lead_user,
                )
            return ergebnis

        monkeypatch.setattr(enforcement_mod, "get_active_hold_target_ids", _dann_hold_anlegen)

        result = enforcement_mod.enforce_activities(facility, settings_obj, timezone.now(), dry_run=False)

        assert ausgeloest["done"], "Das Fenster wurde nicht geoeffnet — Aufbau kaputt"
        assert Activity.objects.filter(pk=gehalten.pk).exists(), (
            "Aktivitaet unter aktivem Hold wurde hart geloescht (Spoliation)"
        )
        assert not Activity.objects.filter(pk=frei.pk).exists(), "Positiv-Kontrolle: der Lauf agiert real"
        assert result["count"] == 1, "``count`` muss die tatsaechlich geloeschten Aktivitaeten melden"
        audit = AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.DELETE,
            detail__category="activities",
        ).latest("timestamp")
        assert audit.detail.get("deferred") == 1, (
            f"Die Zurueckstellung muss nachweisbar sein (nicht geloescht != nicht angeschaut): {audit.detail}"
        )

    def test_client_hold_unter_der_sperre_stellt_die_aktivitaet_ebenso_zurueck(
        self, facility, client_identified, staff_user, lead_user, monkeypatch
    ):
        """Refs #1376 (I10) unter der Sperre: der Personen-Hold schuetzt die Aktivitaet ihres Ziels."""
        from core.models import Activity
        from core.retention import enforcement as enforcement_mod

        settings_obj, _ = Settings.objects.get_or_create(facility=facility)
        settings_obj.retention_activities_days = 365
        settings_obj.save(update_fields=["retention_activities_days"])

        gehalten = _aktivitaet(facility, client_identified, staff_user)

        echte_lesung = enforcement_mod.get_active_hold_target_ids
        ausgeloest = {"done": False}

        def _dann_hold_anlegen(fac, target_type):
            ergebnis = echte_lesung(fac, target_type)
            if target_type == "Client" and not ausgeloest["done"]:
                ausgeloest["done"] = True
                LegalHold.objects.create(
                    facility=fac,
                    target_type="Client",
                    target_id=client_identified.pk,
                    reason="Anordnung waehrend des Laufs",
                    created_by=lead_user,
                )
            return ergebnis

        monkeypatch.setattr(enforcement_mod, "get_active_hold_target_ids", _dann_hold_anlegen)

        result = enforcement_mod.enforce_activities(facility, settings_obj, timezone.now(), dry_run=False)

        assert Activity.objects.filter(pk=gehalten.pk).exists(), (
            "Aktivitaet unter aktivem Personen-Hold wurde hart geloescht (Spoliation)"
        )
        assert result["count"] == 0


@pytest.mark.django_db
class TestActivityLockOrder:
    """Ordnungs-Pin: ``core_client`` vor ``core_activity``, und ``core_activity`` zuletzt."""

    def test_hold_anlage_sperrt_client_vor_activity(self, facility, client_identified, staff_user, lead_user):
        aktivitaet = _aktivitaet(facility, client_identified, staff_user)
        with CaptureQueriesContext(connection) as captured:
            create_hold_for_target(
                facility,
                target_type="Activity",
                target_id=aktivitaet.pk,
                user=lead_user,
                reason="Anordnung",
            )
        sequence = _lock_sequence(captured)
        assert "core_activity" in sequence, f"Der Activity-Hold nahm keinen Anker: {sequence}"
        assert "core_client" in sequence, f"Der Activity-Hold sperrte die Personenzeile des Ziels nicht: {sequence}"
        assert sequence.index("core_client") < sequence.index("core_activity"), (
            f"kanonische Ordnung verdreht (core_client vor core_activity): {sequence}"
        )
        assert sequence.index("core_activity") == len(sequence) - 1, (
            f"core_activity ist das LETZTE Glied der kanonischen Ordnung: {sequence}"
        )

    def test_sweep_sperrt_client_vor_activity(self, facility, client_identified, staff_user):
        from core.retention.enforcement import enforce_activities

        settings_obj, _ = Settings.objects.get_or_create(facility=facility)
        settings_obj.retention_activities_days = 365
        settings_obj.save(update_fields=["retention_activities_days"])
        _aktivitaet(facility, client_identified, staff_user)

        with CaptureQueriesContext(connection) as captured:
            enforce_activities(facility, settings_obj, timezone.now(), dry_run=False)
        sequence = _lock_sequence(captured)
        assert "core_client" in sequence, f"Der Sweep sperrte die Personenzeile des Ziels nicht: {sequence}"
        assert "core_activity" in sequence, f"Der Sweep sperrte die Aktivitaetszeile nicht: {sequence}"
        assert sequence.index("core_client") < sequence.index("core_activity"), (
            f"kanonische Ordnung verdreht (core_client vor core_activity): {sequence}"
        )

    def test_hold_auf_unbekannte_aktivitaet_bleibt_zulaessig(self, facility, lead_user):
        """``target_id`` ist ein freies Feld — Bestandsverhalten der uebrigen Zweige."""
        import uuid

        hold = create_hold_for_target(
            facility,
            target_type="Activity",
            target_id=uuid.uuid4(),
            user=lead_user,
            reason="Anordnung",
        )
        assert hold.pk is not None
