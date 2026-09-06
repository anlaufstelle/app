"""Aufbewahrungssperre auf einen Merge-Tombstone (Refs #2144, Red-Team B-11).

``create_hold_for_target`` sperrte fuer ``target_type="Client"`` zwar die
Personenzeile (Refs #2108/#2122), las auf ihr aber **nicht** ``merged_into_id``.
Ein Hold auf die Quelle einer bereits vollzogenen Zusammenfuehrung wurde damit
still akzeptiert und als ``LEGAL_HOLD`` auditiert — obwohl er nichts mehr deckt:
die Ereignisse der Quelle tragen nach dem Merge ``client_id`` des ZIELS, und
alle Deckungsmengen (``collect_doomed_events``,
``exclude_clients_with_active_holds``) schauen nur auf den Tombstone. Das
Verspaetungsmerkmal meldete zu allem Ueberfluss ``history_already_redacted =
False``, weil die Quelle gar keine Ereigniszeile mehr hat (``any(()) == False``)
— ein irrefuehrendes „puenktlich" auf einem Hold ohne Deckung.

Die Gegenrichtung (Merge unter aktivem Hold) ist seit ADR-045 sauber geschlossen
(``_validate_merge``). Dieser Guard schliesst die Rueckrichtung mit derselben
Antwort: **abweisen, nicht umlenken**. Ein Hold ist ein bewusster
Beweismittel-Akt; wer ihn anordnet, soll die Kette selbst nachvollziehen und ihn
auf die Zielperson setzen.

Bewusst NICHT abgewiesen wird ein Hold auf eine Person oder einen Fall **im
Papierkorb** (``is_deleted=True``) und auf eine bereits redigierte Person
(``anonymized_at``): der Papierkorb-Hold ist genau die Zusage „ein Hold haelt
beide Stufen auf" (Refs #2123 baut darauf auf), und der Hold auf eine redigierte
Person traegt das bestehende Verspaetungsmerkmal
``history_already_redacted=True``, statt zu knallen.

Der Renn-Guard ist **beobachtend** gebaut (Welle-E-Lehre 36): zwei Faeden,
endliche Wartezeit, Auswertung NACH dem Rennen — kein ``assert`` im gesperrten
Fenster.
"""

import threading
import time
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.utils import timezone

from core.models import AuditLog, Case, Client, DocumentType, Event, LegalHold, Organization, Settings, User
from core.retention.enforcement import collect_doomed_events
from core.retention.legal_holds import (
    create_hold_for_target,
    exclude_clients_with_active_holds,
    has_active_hold,
)
from core.services.client.merge import merge_clients

#: Untergrenze, bis der Gegenspieler sicher an der Zeile haengt (Muster
#: ``test_retention_hold_toctou_rest.py``).
_BLOCKED_GRACE_SECONDS = 0.75
_JOIN_TIMEOUT = 30


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Tombstone-Quelle",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Tombstone-Ziel",
        created_by=staff_user,
    )


def _altes_ereignis(facility, person, doc_type, user):
    """Ein Ereignis jenseits der ``retention_identified_days``-Frist."""
    return Event.objects.create(
        facility=facility,
        client=person,
        document_type=doc_type,
        occurred_at=timezone.now() - timedelta(days=400),
        created_by=user,
        data_json={"freitext": "Klartext"},
    )


@pytest.mark.django_db
class TestHoldAufMergeTombstone:
    """Der Kern von #2144: Hold NACH dem Merge auf die Quelle."""

    def test_hold_auf_tombstone_wird_abgewiesen(self, facility, source, target, lead_user):
        merge_clients(source, target, lead_user)
        source.refresh_from_db()
        assert source.merged_into_id == target.pk, "Aufbau kaputt — die Quelle ist kein Tombstone"

        holds_vorher = LegalHold.objects.count()
        audit_vorher = AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).count()

        with pytest.raises(ValidationError) as excinfo:
            create_hold_for_target(
                facility,
                target_type="Client",
                target_id=source.pk,
                user=lead_user,
                reason="Behoerdenanordnung",
            )

        # Der Klartext muss die Zielperson nennen — sonst weiss die anordnende
        # Person nicht, wohin der Hold gehoert.
        meldung = " ".join(excinfo.value.messages)
        assert target.pseudonym in meldung, f"Zielpseudonym fehlt in der Meldung: {meldung!r}"

        assert LegalHold.objects.count() == holds_vorher, "Trotz Abweisung wurde ein Hold gespeichert"
        assert AuditLog.objects.filter(action=AuditLog.Action.LEGAL_HOLD).count() == audit_vorher, (
            "Trotz Abweisung wurde eine LEGAL_HOLD-Auditzeile geschrieben"
        )
        assert not has_active_hold(facility, "Client", source.pk)

    def test_hold_auf_die_zielperson_deckt_die_gewanderten_ereignisse(
        self, facility, source, target, doc_type_contact, settings_obj, staff_user, lead_user
    ):
        """Positivpfad: der richtige Hold wirkt — genau das, worauf die Abweisung hinweist."""
        ereignis = _altes_ereignis(facility, source, doc_type_contact, staff_user)
        merge_clients(source, target, lead_user)
        ereignis.refresh_from_db()
        assert ereignis.client_id == target.pk, "Aufbau kaputt — das Ereignis ist nicht gewandert"

        # Ohne Hold ist das gewanderte Ereignis in der Tilgungsmenge.
        doomed_ohne_hold = set(
            collect_doomed_events(facility, settings_obj, timezone.now()).values_list("pk", flat=True)
        )
        assert ereignis.pk in doomed_ohne_hold, "Aufbau kaputt — das Ereignis ist gar nicht faellig"

        create_hold_for_target(
            facility,
            target_type="Client",
            target_id=target.pk,
            user=lead_user,
            reason="Behoerdenanordnung auf die Zielperson",
        )

        doomed_mit_hold = set(
            collect_doomed_events(facility, settings_obj, timezone.now()).values_list("pk", flat=True)
        )
        assert ereignis.pk not in doomed_mit_hold, "Der Hold auf das Ziel deckt das gewanderte Ereignis nicht"

        uebrig = set(
            exclude_clients_with_active_holds(Client.objects.for_facility(facility), facility).values_list(
                "pk", flat=True
            )
        )
        assert target.pk not in uebrig, "Die Zielperson bleibt trotz Hold anonymisierbar"

    def test_hold_auf_person_im_papierkorb_bleibt_zulaessig(self, facility, client_identified, lead_user, staff_user):
        """Grenze der Abweisung: der Papierkorb ist KEIN Tombstone (Refs #2123)."""
        client_identified.soft_delete(user=staff_user)
        hold = create_hold_for_target(
            facility,
            target_type="Client",
            target_id=client_identified.pk,
            user=lead_user,
            reason="Anordnung waehrend der Papierkorb-Frist",
        )
        assert hold.pk is not None
        assert has_active_hold(facility, "Client", client_identified.pk)

    def test_hold_auf_unbekannte_person_bleibt_zulaessig(self, facility, other_facility, lead_user, staff_user):
        """``target_id`` bleibt ein freies Feld — die Abweisung darf das nicht verschaerfen."""
        fremd = Client.objects.create(facility=other_facility, pseudonym="Fremd", created_by=staff_user)
        hold = create_hold_for_target(
            facility,
            target_type="Client",
            target_id=fremd.pk,
            user=lead_user,
            reason="Anordnung",
        )
        assert hold.pk is not None


@pytest.mark.django_db
class TestFallHoldAufTombstonePerson:
    """Case-Zweig: der Fall selbst hat keinen Tombstone — seine Person schon."""

    def test_hold_auf_fall_einer_zusammengefuehrten_person_wird_abgewiesen(
        self, facility, source, target, staff_user, lead_user
    ):
        fall = Case.objects.create(
            facility=facility,
            client=source,
            title="Fall der Quelle",
            created_by=staff_user,
        )
        merge_clients(source, target, lead_user)
        # Der Merge haengt Faelle per ``update(client=target)`` um; fuer den
        # Guard wird der Zustand „Fall zeigt auf den Tombstone" direkt per ORM
        # hergestellt — anders ist er nicht konstruierbar (kein UI-Weg).
        Case.objects.filter(pk=fall.pk).update(client=source)

        holds_vorher = LegalHold.objects.count()
        with pytest.raises(ValidationError) as excinfo:
            create_hold_for_target(
                facility,
                target_type="Case",
                target_id=fall.pk,
                user=lead_user,
                reason="Anordnung auf den Fall",
            )
        assert target.pseudonym in " ".join(excinfo.value.messages)
        assert LegalHold.objects.count() == holds_vorher

    def test_hold_auf_fall_im_papierkorb_bleibt_zulaessig(self, facility, case_trashed, lead_user):
        """Pin fuer #2123: der Papierkorb-Fall-Hold haelt die Tilgung auf — er darf nicht abgewiesen werden."""
        hold = create_hold_for_target(
            facility,
            target_type="Case",
            target_id=case_trashed.pk,
            user=lead_user,
            reason="Anordnung waehrend der Papierkorb-Frist",
        )
        assert hold.pk is not None
        assert has_active_hold(facility, "Case", case_trashed.pk)


@pytest.mark.django_db(transaction=True)
class TestHoldWaehrendDesMergeRennens:
    """Renn-Variante: der Hold laeuft in einen laufenden Merge hinein.

    Erwartung nach dem Fix: die Hold-Anlage wartet am Personen-Anker und findet
    danach den fertigen Tombstone vor — also ``ValidationError`` statt eines
    stillen Tombstone-Holds. Beobachtend gebaut: im gesperrten Fenster wird nur
    notiert, ausgewertet wird nach dem Rennen.
    """

    @pytest.fixture
    def bestand(self, django_db_setup):
        org = Organization.objects.create(name="Tombstone-Org")
        facility = org.facilities.create(name="Tombstone-Stelle")
        Settings.objects.get_or_create(facility=facility)
        admin = User.objects.create_user(
            username="tombstone-admin",
            password="x" * 40,
            facility=facility,
            role=User.Role.FACILITY_ADMIN,
        )
        DocumentType.objects.create(
            facility=facility,
            name="Kontakt (Tombstone)",
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        quelle = Client.objects.create(
            facility=facility,
            pseudonym="Renn-Quelle",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin,
        )
        ziel = Client.objects.create(
            facility=facility,
            pseudonym="Renn-Ziel",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin,
        )
        yield facility, admin, quelle, ziel
        connections.close_all()

    def test_hold_nach_dem_merge_rennen_wird_abgewiesen(self, bestand):
        facility, admin, quelle, ziel = bestand

        merge_haelt_die_sperre = threading.Event()
        hold_ist_gestartet = threading.Event()
        hold_ist_fertig = threading.Event()
        beobachtung = {}
        fehler = {}

        def _merge_ausfuehren():
            connection.close()
            try:
                with transaction.atomic():
                    # Genau der Anker, den ``_locked_clients`` nimmt.
                    list(Client.objects.filter(pk__in=[quelle.pk, ziel.pk]).order_by("pk").select_for_update())
                    merge_haelt_die_sperre.set()
                    # NUR notieren, auch die Harness-Vorbedingung (Lehre 36
                    # woertlich): kein ``assert`` im gesperrten Fenster.
                    beobachtung["hold_ist_gestartet"] = hold_ist_gestartet.wait(timeout=_JOIN_TIMEOUT)
                    time.sleep(_BLOCKED_GRACE_SECONDS)
                    beobachtung["hold_im_fenster_fertig"] = hold_ist_fertig.is_set()
                    Client.objects.filter(pk=quelle.pk).update(merged_into=ziel, is_deleted=True)
            except Exception as exc:  # noqa: BLE001
                fehler["merge"] = exc
            finally:
                connections.close_all()

        def _hold_anlegen():
            connection.close()
            try:
                hold_ist_gestartet.set()
                create_hold_for_target(
                    facility,
                    target_type="Client",
                    target_id=quelle.pk,
                    user=admin,
                    reason="Anordnung mitten im Merge",
                )
            except Exception as exc:  # noqa: BLE001
                fehler["hold"] = exc
            finally:
                hold_ist_fertig.set()
                connections.close_all()

        faden_merge = threading.Thread(target=_merge_ausfuehren, name="merge")
        faden_merge.start()
        assert merge_haelt_die_sperre.wait(timeout=_JOIN_TIMEOUT), "Der Merge-Faden bekam die Personensperre nicht"

        faden_hold = threading.Thread(target=_hold_anlegen, name="hold")
        faden_hold.start()

        faden_merge.join(timeout=_JOIN_TIMEOUT)
        faden_hold.join(timeout=_JOIN_TIMEOUT)
        assert not faden_merge.is_alive() and not faden_hold.is_alive(), "Verklemmung zwischen Merge und Hold-Anlage"
        assert "merge" not in fehler, fehler
        assert beobachtung.get("hold_ist_gestartet") is True, "Der Hold-Faden ist nie gestartet"

        assert beobachtung["hold_im_fenster_fertig"] is False, (
            "Die Hold-Anlage committete neben der gehaltenen Personenzeile her"
        )
        assert isinstance(fehler.get("hold"), ValidationError), (
            f"Der Hold landete still auf dem fertigen Tombstone statt abgewiesen zu werden: {fehler!r}"
        )
        assert not LegalHold.objects.filter(target_type="Client", target_id=quelle.pk).exists()
