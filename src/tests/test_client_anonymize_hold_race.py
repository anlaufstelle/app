"""Hold-TOCTOU am Art.-17-Sofortpfad und am Papierkorb-Nachtlauf (Refs #2108).

``anonymize_client_now`` las ``client_has_active_hold`` genau einmal zu
Laufbeginn und redigierte danach ohne jede Sperre. ``create_hold_for_target``
sperrt seit ADR-050 D2 die Personenzeile, bevor die ``LegalHold``-Zeile
committet — der Sofortpfad nahm denselben Anker aber nicht mit. Ein Hold, der
waehrend der (bei vielen Ereignissen sekundenlangen) Redaktion angeordnet
wurde, kam damit zu spaet: die Person war schon irreversibel redigiert.
Spoliation trotz aktivem Hold, dynamisch bewiesen in
``/work/redteam-pocs/r3_anonymize_now_hold_race.py``.

Derselbe Fehler, nur schwaecher, im naechtlichen Papierkorb-Lauf
``anonymize_eligible_soft_deleted_clients``: er sperrt die Personenzeile zwar,
prueft unter der Sperre aber nur ``anonymized_at``/``is_deleted`` — nicht den
Hold. Der ``exclude_clients_with_active_holds``-Filter lief einmal beim
Queryset-Bau, vor der pk-Materialisierung.

Eigene Datei, weil der Renn-Nachweis ``django_db(transaction=True)`` braucht
(echte Nebenlaeufigkeit auf zwei DB-Verbindungen) und die uebrigen
Anonymisierungs-Tests in ``test_client_anonymize_now.py`` im schnellen
Transaktions-Rollback laufen.
"""

import threading
import time
from datetime import timedelta
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, LegalHold, Organization, Settings, User
from core.retention.legal_holds import create_hold_for_target, has_active_hold
from core.services.client import anonymize_client_now
from core.services.client import main as client_main_mod
from core.services.client.main import anonymize_eligible_soft_deleted_clients

#: Wartezeit, bis der Gegenspieler sicher an der Personenzeile haengt. Bewusst
#: eine reine Untergrenze: laeuft er langsamer, ist der Test trotzdem
#: aussagekraeftig (er haette den Hold dann erst recht nicht gesehen).
_BLOCKED_GRACE_SECONDS = 0.75
_JOIN_TIMEOUT = 30


@pytest.mark.django_db(transaction=True)
class TestAnonymizeClientNowHoldRace:
    """Refs #2108 (Red-Team B-9): Sofortpfad muss den Hold UNTER der Sperre lesen."""

    @pytest.fixture
    def komplett(self, django_db_setup):
        """Eigener, committeter Bestand — Fixtures aus ``conftest`` reichen hier
        nicht, weil die Threads eigene Verbindungen brauchen und nur sehen, was
        committet ist."""
        org = Organization.objects.create(name="Race-Org")
        facility = org.facilities.create(name="Race-Stelle")
        Settings.objects.get_or_create(facility=facility)
        admin = User.objects.create_user(
            username="race-admin",
            password="x" * 40,
            facility=facility,
            role=User.Role.FACILITY_ADMIN,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Race)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Race-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
            notes="Interne Notiz im Klartext",
        )
        for i in range(5):
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={"freitext": f"Klartext {i}"},
            )
        person.soft_delete(user=admin)
        yield facility, admin, person
        # Aufraeumen: transaction=True laesst committete Zeilen stehen, bis
        # pytest-django die Tabellen leert — die Verbindungen der Threads
        # muessen aber in jedem Fall zu sein.
        connections.close_all()

    def test_hold_der_waehrend_der_sperrwartezeit_committet_bricht_ab(self, komplett):
        """Der eigentliche Renn-Nachweis.

        Faden A haelt die Personenzeile in einer offenen Hold-Transaktion.
        Faden B ruft ``anonymize_client_now`` und muss an genau dieser Zeile
        warten, BEVOR er den Hold liest — sonst liest er den Stand von vorhin.
        """
        facility, admin, person = komplett

        a_haelt_die_sperre = threading.Event()
        b_ist_gestartet = threading.Event()
        fehler = {}

        def _hold_anlegen():
            connection.close()
            try:
                with transaction.atomic():
                    Client.objects.filter(pk=person.pk).select_for_update().only("pk").first()
                    a_haelt_die_sperre.set()
                    assert b_ist_gestartet.wait(timeout=_JOIN_TIMEOUT), "Faden B ist nie gestartet"
                    # Faden B haengt jetzt entweder an der Sperre (nach dem Fix)
                    # oder hat seine ungesperrte Hold-Lesung laengst hinter sich
                    # (vor dem Fix) — genau das ist der Unterschied.
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    create_hold_for_target(
                        facility,
                        target_type="Client",
                        target_id=person.pk,
                        user=admin,
                        reason="Behoerdenanordnung waehrend der Sofortloeschung",
                    )
            except Exception as exc:  # noqa: BLE001 — in den Bericht, nicht verschlucken
                fehler["a"] = exc
            finally:
                connections.close_all()

        ergebnis = {}

        def _anonymisieren():
            connection.close()
            try:
                b_ist_gestartet.set()
                anonymize_client_now(person, admin)
                ergebnis["b"] = None
            except ValidationError as exc:
                ergebnis["b"] = exc
            except Exception as exc:  # noqa: BLE001
                fehler["b"] = exc
            finally:
                connections.close_all()

        faden_a = threading.Thread(target=_hold_anlegen, name="hold")
        faden_a.start()
        assert a_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Faden A bekam die Personensperre nicht"

        faden_b = threading.Thread(target=_anonymisieren, name="anonymize")
        faden_b.start()

        faden_a.join(timeout=_JOIN_TIMEOUT)
        faden_b.join(timeout=_JOIN_TIMEOUT)
        assert not faden_a.is_alive() and not faden_b.is_alive(), "Verklemmung zwischen Hold und Anonymisierung"
        assert not fehler, fehler

        person.refresh_from_db()
        assert has_active_hold(facility, "Client", person.pk), "Der Hold von Faden A fehlt — Aufbau kaputt"
        assert isinstance(ergebnis["b"], ValidationError), (
            "Die Sofort-Anonymisierung lief trotz eines waehrend der Sperrwartezeit "
            "committeten Holds durch (Spoliation)"
        )
        assert "Aufbewahrungssperre" in str(ergebnis["b"])
        assert person.anonymized_at is None
        assert person.notes == "Interne Notiz im Klartext"
        assert Event.objects.filter(client=person, data_json__freitext__startswith="Klartext").count() == 5

    def test_spiegelfall_die_hold_anlage_wartet_und_kommt_dokumentiert_zu_spaet(self, komplett):
        """Gegenrichtung — bewusst KEIN Fehlerfall, sondern der dokumentierte Ausgang.

        Laeuft die Anonymisierung zuerst, wartet die Hold-Anlage an derselben
        Personenzeile und entsteht danach auf einer bereits redigierten Person.
        Der Hold wird nicht abgelehnt (er bindet die Zeile weiter), sondern als
        verspaetet gekennzeichnet — ``history_already_redacted`` an der
        ``LEGAL_HOLD``-Audit-Zeile (ADR-050 D2). Diese Antwort ist die Zusage
        des Systems und darf sich durch den Fix nicht aendern.
        """
        facility, admin, person = komplett

        anonymize_client_now(person, admin)
        person.refresh_from_db()
        assert person.anonymized_at is not None

        hold = create_hold_for_target(
            facility,
            target_type="Client",
            target_id=person.pk,
            user=admin,
            reason="Behoerdenanordnung nach der Sofortloeschung",
        )
        assert LegalHold.objects.filter(pk=hold.pk, dismissed_at__isnull=True).exists()
        assert has_active_hold(facility, "Client", person.pk)
        # Die Person bleibt redigiert — der Hold kam zu spaet und sagt das auch.
        person.refresh_from_db()
        assert person.anonymized_at is not None


@pytest.mark.django_db
class TestNachtSweepReChecktDenHoldUnterDerSperre:
    """Refs #2108 (Sweep-Fund der Recon, R-N1-3): dieselbe Wurzel im
    Papierkorb-Fristablauf.

    ``anonymize_eligible_soft_deleted_clients`` sperrt die Personenzeile und
    prueft unter der Sperre ``anonymized_at``/``is_deleted`` erneut — den Hold
    aber nicht. Der ``exclude_clients_with_active_holds``-Filter lief einmal
    beim Queryset-Bau; ein danach angeordneter Hold erreichte die bereits
    materialisierten pks nicht mehr.

    Der stale Zustand wird deterministisch nachgestellt, indem der Ausschluss
    beim Queryset-Bau durchgereicht wird (genau der Stand, den ein Lauf sieht,
    dessen Sammlung VOR der Hold-Anlage lief). Das Rennen selbst braucht dafuer
    keine echte Nebenlaeufigkeit.
    """

    def _abgelaufene_person(self, facility, admin_user, doc_type):
        person = Client.objects.create(
            facility=facility,
            pseudonym="Sweep-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
            notes="Interne Notiz im Klartext",
        )
        Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"freitext": "Klartext"},
        )
        person.soft_delete(user=admin_user)
        Client.objects.filter(pk=person.pk).update(deleted_at=timezone.now() - timedelta(days=400))
        person.refresh_from_db()
        return person

    def test_hold_nach_der_sammlung_stellt_die_person_zurueck(
        self, facility, settings_obj, admin_user, doc_type_contact
    ):
        person = self._abgelaufene_person(facility, admin_user, doc_type_contact)
        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=person.pk,
            user=admin_user,
            reason="Behoerdenanordnung nach der Sammlung",
        )

        with mock.patch(
            "core.services.client.main.exclude_clients_with_active_holds",
            side_effect=lambda qs, fac: qs,
        ):
            count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        person.refresh_from_db()
        assert count == 0
        assert person.anonymized_at is None, "Person unter aktivem Hold wurde anonymisiert (Spoliation)"
        assert person.notes == "Interne Notiz im Klartext"

    def test_event_hold_nach_der_sammlung_stellt_die_person_ebenfalls_zurueck(
        self, facility, settings_obj, admin_user, doc_type_contact
    ):
        person = self._abgelaufene_person(facility, admin_user, doc_type_contact)
        event = Event.objects.filter(client=person).first()
        create_hold_for_target(
            facility,
            target_type="Event",
            target_id=event.pk,
            user=admin_user,
            reason="Ereignis-Hold nach der Sammlung",
        )

        with mock.patch(
            "core.services.client.main.exclude_clients_with_active_holds",
            side_effect=lambda qs, fac: qs,
        ):
            count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        person.refresh_from_db()
        assert count == 0
        assert person.anonymized_at is None

    def test_ohne_hold_laeuft_der_sweep_unveraendert_durch(self, facility, settings_obj, admin_user, doc_type_contact):
        """Gegenprobe: der Re-Check darf den Regelfall nicht bremsen."""
        person = self._abgelaufene_person(facility, admin_user, doc_type_contact)

        count = anonymize_eligible_soft_deleted_clients(facility, settings_obj)

        person.refresh_from_db()
        assert count == 1
        assert person.anonymized_at is not None


@pytest.fixture
def person_im_papierkorb(facility, client_identified, admin_user, doc_type_contact):
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"freitext": "Klartext"},
    )
    client_identified.notes = "Interne Notiz im Klartext"
    client_identified.save(update_fields=["notes"])
    client_identified.soft_delete(user=admin_user)
    return client_identified


@pytest.mark.django_db
class TestSofortpfadPrueftAufDerGesperrtenZeile:
    """Cross-Review Runde 1 (HOCH): Vorbedingungen gehören auf die gesperrte Zeile.

    Der Fix aus dem ersten Durchgang nahm die Sperre zwar, verwarf ihr Ergebnis
    aber und prüfte Papierkorb-Zustand und Hold weiter auf der **übergebenen**
    Instanz. Die stammt womöglich aus einem Lesevorgang von vor der
    Sperrwartezeit — wurde die Person in dieser Zeit wiederhergestellt,
    anonymisierte der Sofortpfad einen aktiven Datensatz und unterlief damit
    genau die Papierkorb-Vorbedingung, die er zusichert (Refs #1737).
    """

    def test_stale_instanz_einer_wiederhergestellten_person_wird_abgewiesen(self, person_im_papierkorb, admin_user):
        from core.services.client import restore_client

        stale = Client.objects.get(pk=person_im_papierkorb.pk)  # Sicht von vor der Sperrwartezeit

        wiederhergestellt = Client.objects.get(pk=person_im_papierkorb.pk)
        restore_client(wiederhergestellt, admin_user)
        wiederhergestellt.refresh_from_db()
        assert wiederhergestellt.is_deleted is False

        with pytest.raises(ValidationError) as exc:
            anonymize_client_now(stale, admin_user)

        assert "Papierkorb" in str(exc.value)
        wiederhergestellt.refresh_from_db()
        assert wiederhergestellt.anonymized_at is None, "Aktive Person wurde anonymisiert"
        assert wiederhergestellt.notes == "Interne Notiz im Klartext"

    def test_zweite_tilgung_derselben_person_wird_abgewiesen(self, person_im_papierkorb, admin_user):
        anonymize_client_now(person_im_papierkorb, admin_user)
        person_im_papierkorb.refresh_from_db()
        assert person_im_papierkorb.anonymized_at is not None

        stale = Client.objects.get(pk=person_im_papierkorb.pk)
        with pytest.raises(ValidationError) as exc:
            anonymize_client_now(stale, admin_user)

        assert "bereits endgültig anonymisiert" in str(exc.value)
        # Kein zweiter CLIENT_ANONYMIZED-Eintrag im append-only AuditLog.
        assert (
            AuditLog.objects.filter(
                action=AuditLog.Action.CLIENT_ANONYMIZED,
                target_id=str(person_im_papierkorb.pk),
            ).count()
            == 1
        )

    def test_die_gesperrte_instanz_wird_redigiert_und_zurueckgegeben(self, person_im_papierkorb, admin_user):
        """Die Kaskade läuft auf ``locked``, nicht auf der übergebenen Instanz."""
        stale = Client.objects.get(pk=person_im_papierkorb.pk)

        ergebnis = anonymize_client_now(stale, admin_user)

        assert ergebnis.pk == person_im_papierkorb.pk
        assert ergebnis.anonymized_at is not None
        assert ergebnis.pseudonym.startswith("Gelöscht-")
        person_im_papierkorb.refresh_from_db()
        assert person_im_papierkorb.notes == ""


def _committet_innerhalb(beobachtet, sekunden=1.0):
    """Meldet, ob der Gegenspieler-Faden sein COMMIT im Fenster geschafft hat."""
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if beobachtet.get("committed") or beobachtet.get("fehler"):
            return True
        time.sleep(0.02)
    return False


@pytest.mark.django_db(transaction=True)
class TestEreignisHoldWirdMitgesperrt:
    """Cross-Review Runde 1: Ein EREIGNIS-Hold koordiniert über ``core_event``.

    ``client_has_active_hold`` zählt einen Hold auf irgendein Ereignis der
    Person mit (Refs #1066/#1756), ``create_hold_for_target`` sperrt dafür aber
    die Ereignis-, nicht die Personenzeile. Die Personensperre allein hielt
    einen parallel entstehenden Ereignis-Hold deshalb gar nicht auf: er
    committete neben ihr her, und die Anonymisierung redigierte genau das
    Ereignis, das er sichern soll.

    Geprüft wird die Koordination selbst — kann der Gegenspieler im Fenster
    zwischen Hold-Lesung und Schreiben committen? Bewusst mit endlicher
    Wartezeit statt einer Assertion im gesperrten Abschnitt: ein ``assert``
    dort machte aus der Aussage eine Verklemmung.
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        org = Organization.objects.create(name="Event-Hold-Org")
        facility = org.facilities.create(name="Event-Hold-Stelle")
        settings_obj, _created = Settings.objects.get_or_create(facility=facility)
        admin = User.objects.create_user(
            username="event-hold-admin",
            password="x" * 40,
            facility=facility,
            role=User.Role.FACILITY_ADMIN,
        )
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Event-Hold)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        person = Client.objects.create(
            facility=facility,
            pseudonym="Event-Hold-Person",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        ereignis = Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"freitext": "Beweis"},
        )
        person.soft_delete(user=admin)
        yield facility, settings_obj, admin, person, ereignis
        connections.close_all()

    def _fahre_mit_gegenspieler(self, monkeypatch, facility, admin, ereignis, aktion):
        """Führt ``aktion`` aus, während ein Ereignis-Hold parallel entstehen will."""
        beobachtet = {}
        im_fenster = threading.Event()

        def _hold_anlegen():
            connection.close()
            try:
                im_fenster.wait(timeout=_JOIN_TIMEOUT)
                create_hold_for_target(
                    facility,
                    target_type="Event",
                    target_id=ereignis.pk,
                    user=admin,
                    reason="Ereignis-Hold neben der Personensperre",
                )
                beobachtet["committed"] = True
            except Exception as exc:  # noqa: BLE001 — in den Bericht, nicht verschlucken
                beobachtet["fehler"] = exc
            finally:
                connections.close_all()

        echt = client_main_mod.client_has_active_hold

        def _nach_der_hold_lesung_warten(geprueft):
            ergebnis = echt(geprueft)
            im_fenster.set()
            beobachtet["im_fenster"] = _committet_innerhalb(beobachtet)
            return ergebnis

        monkeypatch.setattr(client_main_mod, "client_has_active_hold", _nach_der_hold_lesung_warten)

        faden = threading.Thread(target=_hold_anlegen, name="event-hold")
        faden.start()
        try:
            beobachtet["ergebnis"] = aktion()
        finally:
            connections.close_all()
            faden.join(timeout=_JOIN_TIMEOUT)

        assert not faden.is_alive(), "Verklemmung zwischen Ereignis-Hold und Anonymisierung"
        assert not beobachtet.get("fehler"), beobachtet["fehler"]
        return beobachtet

    def test_sofortpfad_sperrt_die_ereigniszeilen_mit(self, bestand, monkeypatch):
        facility, _settings_obj, admin, person, ereignis = bestand

        beobachtet = self._fahre_mit_gegenspieler(
            monkeypatch, facility, admin, ereignis, lambda: anonymize_client_now(person, admin)
        )

        assert beobachtet["im_fenster"] is False, (
            "Der Ereignis-Hold committete neben der Personensperre — die "
            "Ereigniszeilen stehen nicht unter derselben Sperre"
        )
        assert beobachtet["committed"] is True, "Der Hold entsteht danach — dokumentiert verspaetet"
        person.refresh_from_db()
        assert person.anonymized_at is not None

    def test_nacht_sweep_sperrt_die_ereigniszeilen_mit(self, bestand, monkeypatch):
        facility, settings_obj, admin, person, ereignis = bestand
        Client.objects.filter(pk=person.pk).update(deleted_at=timezone.now() - timedelta(days=400))

        beobachtet = self._fahre_mit_gegenspieler(
            monkeypatch,
            facility,
            admin,
            ereignis,
            lambda: anonymize_eligible_soft_deleted_clients(facility, settings_obj),
        )

        assert beobachtet["im_fenster"] is False, (
            "Der Ereignis-Hold committete neben der Personensperre des Nacht-Sweeps"
        )
        assert beobachtet["committed"] is True
        assert beobachtet["ergebnis"] == 1
