"""Papierkorb-Aktion „Jetzt endgültig anonymisieren" (Refs #1737, Option B).

Die Anonymisierung war per UI nirgends auslösbar: sie passierte nur nächtlich
nach Ablauf von ``Settings.client_trash_days``. Damit war eine Lösch-Blockade
durch eine Person im Papierkorb ohne Datenresurrektion (Wiederherstellen) oder
30 Tage Warten nicht auflösbar.
"""

from contextlib import contextmanager

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Client, DocumentType, Event, LegalHold
from core.models.event_history import EventHistory
from core.services.client import anonymize_client_now
from core.services.events.history_diff import build_anonymization_marker
from tests.conftest import xdist_role_suffix

pytestmark = pytest.mark.django_db


@pytest.fixture
def trashed_client(client_identified, admin_user, doc_type_client):
    client_identified.document_type = doc_type_client
    client_identified.data_json = {"notiz-frei": "Klartext"}
    client_identified.notes = "Interne Notiz"
    client_identified.save(update_fields=["document_type", "data_json", "notes"])
    client_identified.soft_delete(user=admin_user)
    return client_identified


@pytest.fixture
def doc_type_client(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Personenbogen",
        slug="personenbogen",
        entity_type=DocumentType.EntityType.CLIENT,
    )


class TestAnonymizeClientNowService:
    def test_redacts_identity_and_dynamic_data(self, trashed_client, admin_user):
        anonymize_client_now(trashed_client, admin_user)
        trashed_client.refresh_from_db()
        assert trashed_client.pseudonym.startswith("Gelöscht-")
        assert trashed_client.notes == ""
        assert trashed_client.data_json == {}
        assert trashed_client.document_type_id is None
        # Der Datensatz bleibt im Papierkorb — anonymisiert, nicht auferstanden.
        assert trashed_client.is_deleted is True

    def test_writes_audit_entry_with_manual_trigger(self, trashed_client, admin_user):
        anonymize_client_now(trashed_client, admin_user)
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(trashed_client.pk),
        ).first()
        assert entry is not None
        assert entry.user_id == admin_user.pk
        assert entry.detail.get("trigger") == "manual"

    def test_refuses_client_outside_trash(self, client_identified, admin_user):
        with pytest.raises(ValidationError):
            anonymize_client_now(client_identified, admin_user)

    def test_refuses_client_under_legal_hold(self, trashed_client, admin_user, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=trashed_client.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(ValidationError):
            anonymize_client_now(trashed_client, admin_user)
        trashed_client.refresh_from_db()
        assert trashed_client.notes == "Interne Notiz"

    def test_refuses_when_an_event_is_under_legal_hold(
        self, trashed_client, admin_user, facility, lead_user, doc_type_contact
    ):
        from django.utils import timezone

        event = Event.objects.create(
            facility=facility,
            client=trashed_client,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        LegalHold.objects.create(
            facility=facility,
            target_type="Event",
            target_id=event.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        with pytest.raises(ValidationError):
            anonymize_client_now(trashed_client, admin_user)


class TestAnonymizeClientNowView:
    def _url(self, client_obj):
        return reverse("core:client_anonymize", args=[client_obj.pk])

    def test_admin_can_trigger(self, client, admin_user, trashed_client):
        client.force_login(admin_user)
        response = client.post(self._url(trashed_client))
        assert response.status_code == 302
        trashed_client.refresh_from_db()
        assert trashed_client.data_json == {}
        assert trashed_client.notes == ""

    def test_lead_is_denied(self, client, lead_user, trashed_client):
        client.force_login(lead_user)
        assert client.post(self._url(trashed_client)).status_code == 403

    def test_staff_is_denied(self, client, staff_user, trashed_client):
        client.force_login(staff_user)
        assert client.post(self._url(trashed_client)).status_code == 403

    def test_non_trashed_client_is_404(self, client, admin_user, client_identified):
        client.force_login(admin_user)
        assert client.post(self._url(client_identified)).status_code == 404

    def test_legal_hold_yields_error_message_not_500(self, client, admin_user, trashed_client, facility, lead_user):
        LegalHold.objects.create(
            facility=facility,
            target_type="Client",
            target_id=trashed_client.pk,
            reason="Ermittlungsverfahren",
            created_by=lead_user,
        )
        client.force_login(admin_user)
        response = client.post(self._url(trashed_client))
        assert response.status_code == 302
        msgs = [str(m.message) for m in response.wsgi_request._messages]
        assert any("Aufbewahrungssperre" in m or "Legal Hold" in m for m in msgs)


class TestTrashTemplateAction:
    def test_button_present_with_confirmation(self, client, admin_user, trashed_client):
        client.force_login(admin_user)
        body = client.get(reverse("core:client_trash")).content.decode()
        assert "client-anonymize-now-btn" in body
        assert "data-confirm=" in body
        assert reverse("core:client_anonymize", args=[trashed_client.pk]) in body


# ---------------------------------------------------------------------------
# Refs #2085: Der Webprozess ist NICHT Table-Owner
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestAnonymizeClientNowUnterDerAppRolle:
    """Refs #2085: die sofortige Tilgung laeuft im Webprozess unter der App-Rolle.

    Die App-Rolle ist in Prod ``NOSUPERUSER NOBYPASSRLS`` und **kein**
    Table-Owner (ADR-033, ``deploy/postgres-init/01-app-role.sh``). Der
    Direktweg ``bypass_replication_triggers()`` braucht aber Owner-Rechte fuer
    ``ALTER TABLE ... DISABLE TRIGGER`` — die gesamte Anonymisierung brach
    deshalb mit ``must be owner of table core_eventhistory`` ab und riss die
    ``@transaction.atomic``-Klammer samt Audit-Eintrag mit zurueck (HTTP 500).

    Zwei Haelften gehoeren zwingend zusammen, sonst ist der Test Scheingruen:

    1. **Rolle.** Dev/Test/E2E laufen im Legacy-Einzelrollen-Modell (der
       verbundene User ist Owner/Superuser). Ohne ``SET ROLE`` auf eine frisch
       angelegte Nicht-Owner-Rolle ist der Defekt prinzipiell nicht
       reproduzierbar.
    2. **Datenlage.** ``_redact_event_history`` steigt bei
       ``history_qs.exists()`` aus. Ohne mindestens ein Ereignis MIT
       Historienzeile erreicht der Testlauf den Bypass nie — genau deshalb sind
       die Bestandstests dieser Datei gruen.

    ``transaction=True``: CREATE/DROP ROLE und SET ROLE brauchen echte Sessions.
    ``@pytest.mark.postgres``: auf Nicht-PostgreSQL sauber uebersprungen.
    """

    # Refs #1479 (TST-03) / F-15: Postgres-Rollen sind cluster-global — alle
    # Parallele Testlaeufe und xdist-Worker teilen sich denselben Namensraum, obwohl jede
    # Datenbank getrennt ist. Ohne Suffix kollidieren zwei Laeufe beim
    # CREATE/DROP ROLE.
    @property
    def _app_role(self):
        return f"art17_app{xdist_role_suffix()}"

    def _drop_role(self, role):
        """Idempotenter DROP — ``DROP OWNED BY`` vor ``DROP ROLE``, sonst blockt
        eine GRANT-Abhaengigkeit auf fremd-owned Tabellen."""
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute(
                "DO $$ BEGIN "
                f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
                f"  EXECUTE format('DROP OWNED BY %I', '{role}'); "
                f"  EXECUTE format('DROP ROLE %I', '{role}'); "
                "END IF; END $$;"
            )

    def _create_role(self, role):
        """Legt eine Prod-analoge App-Rolle an: DML ja, Ownership nein.

        **Kein** ``GRANT EXECUTE`` auf irgendeine Funktion — die App-Rolle
        bekommt seit dem Entscheid fuer den nachgelagerten Pfad kein neues
        Recht (ADR-033 bleibt unangetastet).
        """
        with connection.cursor() as cur:
            cur.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
            cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
            cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")

    @pytest.fixture(autouse=True)
    def _roles(self, db):
        """Beide Rollen vor **und** nach dem Test wegraeumen (F-15): eine
        stehengebliebene cluster-globale Rolle risse den naechsten Lauf — auch
        den eines anderen Testmoduls — mit ``role already exists``."""
        self._drop_role(self._app_role)
        self._create_role(self._app_role)
        try:
            yield
        finally:
            self._drop_role(self._app_role)

    @contextmanager
    def _as_role(self, role, facility_id):
        """Prod-Runtime nachgestellt: Nicht-Owner-Rolle + gesetzte Facility-GUC.

        ``FacilityScopeMiddleware`` setzt ``app.current_facility_id`` je Request
        auf Session-Ebene (``is_local=false``); die Definer-Funktion prueft
        genau diese GUC (P-5). Der ``finally``-Zweig raeumt beides weg — die
        Connection wird von Folgetests im selben Prozess weiterbenutzt.
        """
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.current_facility_id', %s, false)", [str(facility_id)])
            cur.execute(f"SET ROLE {role}")
        try:
            yield
        finally:
            with connection.cursor() as cur:
                cur.execute("RESET ROLE")
                cur.execute("SELECT set_config('app.current_facility_id', '', false)")

    def _trashed_client_with_history(self, facility, doc_type, staff, admin, pseudonym):
        """Person im Papierkorb MIT Ereignis und Historienzeile.

        Die Historienzeile ist die Vorbedingung, an der alles haengt (F-13):
        ohne sie steigt ``_redact_event_history`` bei ``history_qs.exists()``
        aus und der Test waere vor wie nach dem Fix gruen.
        """
        from core.services.events import create_event

        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff,
        )
        create_event(
            facility=facility,
            user=staff,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "Klartext-Notiz"},
            client=person,
        )
        assert EventHistory.objects.filter(event__client=person).exists(), (
            "Vorbedingung verletzt: ohne EventHistory-Zeile erreicht der Lauf den Trigger-Bypass nie"
        )
        person.soft_delete(user=admin)
        return person

    @staticmethod
    def _trigger_states():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid IN ('core_eventhistory'::regclass, 'core_auditlog'::regclass) "
                "AND NOT tgisinternal"
            )
            return dict(cur.fetchall())

    def test_sofortige_tilgung_laeuft_unter_der_nicht_owner_app_rolle_durch(
        self, facility, admin_user, staff_user, doc_type_contact
    ):
        """Die Kernzusage des Tickets: **kein HTTP 500 mehr.**

        Roter Vorzustand war
        ``django.db.utils.ProgrammingError: must be owner of table
        core_eventhistory`` — der Trigger-Bypass ist Owner-DDL, und die
        ``@transaction.atomic``-Klammer riss die gesamte Anonymisierung samt
        Nachweiseintrag zurueck.

        Die Person ist danach **sofort** anonymisiert; offen bleibt allein die
        Ereignis-Historie, die der owner-seitige Lauf nachtraegt (ADR-050 M-8).
        Genau das prueft dieser Test — und ausdruecklich auch, dass die Historie
        hier noch NICHT redigiert ist: eine Zusage, die die Oberflaeche
        entsprechend formulieren muss.
        """
        person = self._trashed_client_with_history(facility, doc_type_contact, staff_user, admin_user, "Art17-Haupt")
        event_ids = list(Event.objects.filter(client=person).values_list("id", flat=True))
        assert event_ids

        with self._as_role(self._app_role, facility.pk):
            anonymize_client_now(person, admin_user)

        person.refresh_from_db()
        assert person.pseudonym.startswith("Gelöscht-")
        assert person.is_deleted is True
        # Der Stempel, an dem der nachgelagerte Lauf die Historie findet.
        assert person.anonymized_at is not None
        # Der Live-Inhalt ist sofort weg — nur die frueheren Fassungen stehen aus.
        assert Event.objects.filter(pk__in=event_ids, data_json={}).count() == len(event_ids)

        rows = list(EventHistory.objects.filter(event_id__in=event_ids))
        assert rows, "Historienzeilen sind verschwunden — der Request darf nichts loeschen"
        assert all(row.redacted_at is None for row in rows), (
            "Der Request hat die Historie selbst angefasst — unter der App-Rolle "
            "ist das Owner-DDL und genau der Defekt aus #2085."
        )

        entries = AuditLog.objects.filter(
            action=AuditLog.Action.CLIENT_ANONYMIZED,
            target_id=str(person.pk),
        )
        assert entries.count() == 1
        assert entries.first().detail.get("trigger") == "manual"

        states = self._trigger_states()
        assert states["eventhistory_no_update"] == "A"
        assert states["eventhistory_no_delete"] == "A"

    def test_der_owner_seitige_lauf_holt_die_historie_nach(
        self, facility, settings_obj, admin_user, staff_user, doc_type_contact
    ):
        """Die zweite Haelfte der Zusage: nachgelagert heisst nicht „nie".

        Der Sweep laeuft als Owner (Wartungs-/Cron-Rolle) — hier also ohne
        ``SET ROLE``, so wie der ``maintenance``-Service in Produktion.
        """
        from core.retention.eventhistory import enforce_eventhistory_retention

        person = self._trashed_client_with_history(facility, doc_type_contact, staff_user, admin_user, "Art17-Nachlauf")
        event_ids = list(Event.objects.filter(client=person).values_list("id", flat=True))

        with self._as_role(self._app_role, facility.pk):
            anonymize_client_now(person, admin_user)

        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        rows = list(EventHistory.objects.filter(event_id__in=event_ids))
        assert rows
        for row in rows:
            assert row.redacted_at is not None
            for payload in (row.data_before, row.data_after):
                assert payload is None or payload.get("_redacted") is True

    def test_die_app_rolle_bekommt_kein_neues_ausfuehrungsrecht(self, facility):
        """Gegenprobe zum verworfenen Definer-Weg: die App-Rolle darf im Schema
        ``public`` keine einzige Funktion ausfuehren duerfen, die ihr eigens
        gegrantet wurde. Es gibt dort keine SECURITY-DEFINER-Funktion mehr, und
        es darf auch keine geben (ADR-033)."""
        with connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.prosecdef"
            )
            (definer,) = cur.fetchone()
        assert definer == 0, f"{definer} SECURITY-DEFINER-Funktion(en) im Schema public — s. #2085"

    def test_nur_die_eigene_person_faellt_in_den_nachlauf(
        self, facility, settings_obj, admin_user, staff_user, doc_type_contact
    ):
        """Der Nachlauf haengt am Stempel der EINEN Person: eine zweite, ebenfalls
        im Papierkorb liegende, aber nicht anonymisierte Person derselben
        Einrichtung behaelt ihre Historieninhalte."""
        from core.retention.eventhistory import enforce_eventhistory_retention

        person = self._trashed_client_with_history(facility, doc_type_contact, staff_user, admin_user, "Art17-G4a")
        andere = self._trashed_client_with_history(facility, doc_type_contact, staff_user, admin_user, "Art17-G4b")
        andere_ids = list(Event.objects.filter(client=andere).values_list("id", flat=True))

        with self._as_role(self._app_role, facility.pk):
            anonymize_client_now(person, admin_user)
        enforce_eventhistory_retention(facility, settings_obj, timezone.now(), dry_run=False)

        for row in EventHistory.objects.filter(event_id__in=andere_ids):
            assert row.redacted_at is None
            assert row.data_after != build_anonymization_marker()


@pytest.mark.django_db
class TestAnonymisiertIstTerminal:
    """Refs #2085 (Cross-Review Runde 2, MEDIUM 2): „Eine Wiederherstellung ist
    danach nicht mehr möglich" muss auch stimmen.

    Der Bestätigungsdialog sagt es zu, `restore_client` prüfte aber nur
    ``merged_into`` und ``is_deleted`` — nicht den terminalen Stempel
    ``anonymized_at``. Eine getilgte Person liess sich damit wieder aktivieren,
    und der nachgelagerte Sweep redigierte ihre Historie anschliessend an einem
    **aktiven** Datensatz.

    Der Fall-Pfad hat den Guard laengst (``restore_case``,
    ``services/case/deletion.py``); dort steht als Begruendung, die Person habe
    ihr ``is_active=False`` als Backstop. Das ist ein Anzeigefilter, keine
    Invariante — und seit #2085 ist der Zustand im Produktions-Rollenmodell
    erstmals wirklich erreichbar.
    """

    def test_restore_lehnt_eine_anonymisierte_person_ab(self, trashed_client, admin_user):
        from core.services.client import restore_client

        anonymize_client_now(trashed_client, admin_user)
        trashed_client.refresh_from_db()
        assert trashed_client.anonymized_at is not None

        with pytest.raises(ValidationError) as exc:
            restore_client(trashed_client, admin_user)

        assert "anonymisiert" in " ".join(exc.value.messages).lower()
        trashed_client.refresh_from_db()
        assert trashed_client.is_deleted is True, "Die Person wurde trotz Abweisung reaktiviert"

    def test_restore_view_gibt_die_meldung_aus_statt_zu_reaktivieren(self, client, admin_user, trashed_client):
        anonymize_client_now(trashed_client, admin_user)
        client.force_login(admin_user)

        response = client.post(reverse("core:client_restore", args=[trashed_client.pk]))

        assert response.status_code == 302
        msgs = [str(m.message) for m in response.wsgi_request._messages]
        assert any("anonymisiert" in m.lower() for m in msgs), msgs
        trashed_client.refresh_from_db()
        assert trashed_client.is_deleted is True

    def test_papierkorb_zeigt_fuer_eine_anonymisierte_person_kein_wiederherstellen(
        self, client, admin_user, trashed_client
    ):
        """Der Knopf darf gar nicht erst erscheinen — eine Aktion anzubieten, die
        der Service danach abweist, ist eine zweite Unwahrheit."""
        client.force_login(admin_user)
        vorher = client.get(reverse("core:client_trash")).content.decode()
        assert reverse("core:client_restore", args=[trashed_client.pk]) in vorher

        anonymize_client_now(trashed_client, admin_user)

        nachher = client.get(reverse("core:client_trash")).content.decode()
        assert reverse("core:client_restore", args=[trashed_client.pk]) not in nachher
        # Die Zeile selbst bleibt sichtbar — sie verschwindet nicht aus dem Papierkorb.
        assert reverse("core:client_anonymize", args=[trashed_client.pk]) in nachher

    def test_restore_bleibt_fuer_eine_nicht_anonymisierte_person_moeglich(self, trashed_client, admin_user):
        """Gegenprobe: der Guard darf den regulaeren Rueckweg nicht zusperren."""
        from core.services.client import restore_client

        restore_client(trashed_client, admin_user)

        trashed_client.refresh_from_db()
        assert trashed_client.is_deleted is False
