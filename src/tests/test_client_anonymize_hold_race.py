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
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import Client, DocumentType, Event, LegalHold, Organization, Settings, User
from core.retention.legal_holds import create_hold_for_target, has_active_hold
from core.services.client import anonymize_client_now
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
        Client.objects.filter(pk=person.pk).update(deleted_at=timezone.now() - timezone.timedelta(days=400))
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
