"""Funktionaler RLS-Cross-Tenant-Test (Refs #718).

Bisher liefen alle RLS-Tests als Superuser-DB-User — der bypassed RLS
per Postgres-Default und macht echte Policy-Verletzungen in Tests
unsichtbar. Wenn ein Plattform-Default oder Migrations-Setup den
Django-DB-User als Superuser anlegt, wird RLS in Produktion **still**
abgeschaltet — kein CI-Alarm.

Dieser Test laedt eine dedizierte Postgres-Rolle ``rls_test_role``
(``NOSUPERUSER``), setzt sie via ``SET ROLE`` und verifiziert, dass die
``facility_isolation``-Policy Cross-Tenant-Reads tatsaechlich
unterbindet — 0-Rows-Assertion ueber alle ``core_*``-Tabellen mit
``app.current_facility_id`` einer fremden Facility.

Statistik-Materialized-View (``src/core/migrations/0049_statistics_event_flat_mv.py``)
ist bewusst ohne RLS modelliert (Aggregat-Lese, keine Pseudonyme) —
out of scope fuer diesen Test.
"""

import logging
import os
from contextlib import contextmanager

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DocumentType, Event, FieldCondition, FieldGroup
from core.models.document_type_field import DocumentTypeField
from core.models.event_history import EventHistory
from core.models.field_template import FieldTemplate
from core.services.client import anonymize_client_now
from core.services.events import create_event
from core.services.events.history_diff import build_anonymization_marker
from core.services.system import current_connection_owns
from tests.conftest import xdist_role_suffix

# Unter mutmut überspringen (Refs #1388/#930). Die ``rls_test_role``-Fixture ist
# session-scoped und räumt im Teardown per ``django_db_blocker.unblock()`` +
# Roh-SQL auf. mutmut ruft ``pytest.main()`` über mehrere Phasen auf (in-process:
# stats; Subprozess: clean/forced-fail/pro-Mutant) — pytest-djangos
# DatabaseBlocker leakt dabei zwischen den Sessions (bekanntes Problem, siehe
# ``scripts/dev/run_mutmut.py``). Beim Session-Teardown ist der Blocker dann
# korrumpiert, ``unblock()`` greift nicht mehr und der Roh-SQL-Zugriff wirft
# ``RuntimeError: Database access not allowed`` → mutmut bricht die Phase ab
# (``-x``) und der Voll-Lauf zählt null Mutanten. Der Test prüft RLS-Policies auf
# DB-Ebene und killt keine ``core.services``/``core.forms``-Mutanten; sein
# Ausschluss aus dem Mutanten-Testpool kostet keine Mutation-Coverage.
#
# PRÄSENZ-Check, nicht Truthy-Check: mutmut setzt ``MUTANT_UNDER_TEST`` in JEDER
# Phase — aber in der Clean-Phase auf den LEEREN String (``""``, falsy). Ein
# ``os.environ.get(...)``-Truthy-Test würde dort nicht greifen und der Teardown
# liefe im Clean-Subprozess erneut auf. ``make ci`` setzt die Variable nie.
if "MUTANT_UNDER_TEST" in os.environ:
    pytest.skip(
        "RLS-Funktionaltest inkompatibel mit mutmut-Lauf (DatabaseBlocker-Leak im Session-Teardown, Refs #1388)",
        allow_module_level=True,
    )


@pytest.fixture(scope="session")
def rls_test_role(django_db_setup, django_db_blocker):
    """Erstellt eine session-globale Postgres-Rolle ohne Superuser.

    Der Rollenname traegt unter pytest-xdist einen Worker-Suffix
    (``rls_test_role_gw0`` …), weil Postgres-Rollen cluster-global sind und
    zwei Worker sonst beim ``CREATE``/``DROP`` kollidieren (Refs #1479, TST-03).
    Idempotent: bei wiederholtem Lauf wird die Rolle nicht doppelt angelegt.
    Grants auf schema + alle Tabellen + Sequenzen, damit die Rolle ueberhaupt
    SELECT/INSERT ausfuehren kann.

    Yield-Teardown mit **REVOKE vor DROP**: die Grants erzeugen sonst eine
    Abhaengigkeit (``role cannot be dropped because some objects depend on
    it``). Ohne expliziten Finalizer blieb die Rolle nach dem Lauf cluster-
    global stehen und liess Folgelaeufe unter ``--reuse-db`` auf
    ``role already exists`` auflaufen.
    """
    if connection.vendor != "postgresql":
        pytest.skip("Non-Superuser-Test erfordert PostgreSQL")

    role = f"rls_test_role{xdist_role_suffix()}"

    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute(
            f"""
                DO $do$ BEGIN
                    CREATE ROLE {role} NOSUPERUSER NOREPLICATION
                        INHERIT NOLOGIN;
                EXCEPTION WHEN duplicate_object THEN
                    NULL;
                END $do$;
                """
        )
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        cur.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        cur.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")

    yield role

    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute("RESET ROLE")
        cur.execute(
            f"""
                DO $do$ BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                        REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public FROM {role};
                        REVOKE SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public FROM {role};
                        REVOKE USAGE ON SCHEMA public FROM {role};
                        BEGIN
                            DROP ROLE {role};
                        EXCEPTION WHEN dependent_objects_still_exist THEN
                            -- In einem GETEILTEN Test-Cluster (mehrere Worktrees,
                            -- viele zurueckgelassene Test-DBs) traegt eine
                            -- un-suffixte ``rls_test_role`` noch Grants in FREMDEN
                            -- Datenbanken; REVOKE wirkt nur DB-lokal, der DROP
                            -- schlaegt dann fehl. Kein harter Teardown-Fehler:
                            -- der idempotente CREATE oben faengt einen Rest-
                            -- bestand beim naechsten Lauf ab. Unter ``-n auto``
                            -- ist der Name worker-suffixt/frisch → DROP gelingt.
                            NULL;
                        END;
                    END IF;
                END $do$;
                """
        )


@contextmanager
def as_rls_role(role_name, facility_id=None):
    """Switch zur Non-Superuser-Rolle und optional ``app.current_facility_id``.

    ``RESET ROLE`` im finally — wenn der Test failt, wird der DB-User
    nicht in der falschen Rolle stecken gelassen.
    """
    with connection.cursor() as cur:
        if facility_id is not None:
            cur.execute(
                "SELECT set_config('app.current_facility_id', %s, false)",
                [str(facility_id) if facility_id else ""],
            )
        cur.execute(f"SET ROLE {role_name}")
        try:
            yield cur
        finally:
            cur.execute("RESET ROLE")


@pytest.fixture
def facility_a_with_data(facility, admin_user):
    """Facility A mit mind. 1 Client + Event + AuditLog."""
    cli = Client.objects.create(
        facility=facility,
        pseudonym="A-Client",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    dt = DocumentType.objects.create(
        facility=facility,
        name="A-Doc",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    Event.objects.create(
        facility=facility,
        client=cli,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={"note": "A"},
        created_by=admin_user,
    )
    AuditLog.objects.create(
        facility=facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    Activity.objects.create(
        facility=facility,
        actor=admin_user,
        verb=Activity.Verb.CREATED,
        target_type=ContentType.objects.get_for_model(Client),
        target_id=cli.pk,
        context={"schema": 1, "action": Activity.Action.CLIENT_CREATED},
    )
    return facility


@pytest.fixture
def facility_b_with_data(second_facility, admin_user):
    """Facility B mit mind. 1 Client + Event + AuditLog — fuer Cross-Tenant-Probe."""
    cli = Client.objects.create(
        facility=second_facility,
        pseudonym="B-Client",
        contact_stage=Client.ContactStage.IDENTIFIED,
    )
    dt = DocumentType.objects.create(
        facility=second_facility,
        name="B-Doc",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    Event.objects.create(
        facility=second_facility,
        client=cli,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={"note": "B"},
        created_by=admin_user,
    )
    AuditLog.objects.create(
        facility=second_facility,
        user=admin_user,
        action=AuditLog.Action.LOGIN,
    )
    Activity.objects.create(
        facility=second_facility,
        actor=admin_user,
        verb=Activity.Verb.CREATED,
        target_type=ContentType.objects.get_for_model(Client),
        target_id=cli.pk,
        context={"schema": 1, "action": Activity.Action.CLIENT_CREATED},
    )
    return second_facility


@pytest.mark.django_db(transaction=True)
class TestRLSCrossTenantIsolation:
    """Cross-Tenant-SELECT unter NOSUPERUSER muss 0 Zeilen aus Facility B
    zurueckgeben, wenn ``app.current_facility_id`` auf Facility A steht.

    ``transaction=True`` ist noetig: ``SET ROLE`` und Statement-Cache
    spielen mit der pytest-django-Default-Savepoint-Strategie nicht
    zusammen — die Rolle muss session-weit aktiv sein, bevor der erste
    SELECT laeuft.
    """

    def test_cross_tenant_client_returns_zero_rows(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        # app.current_facility_id auf Facility A — RLS-Policy filtert
        # core_client.facility_id = current_setting(...).
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT pseudonym FROM core_client WHERE pseudonym = 'B-Client'")
            rows = cur.fetchall()
        assert rows == [], (
            "RLS hat versagt: B-Client ist trotz facility_a-Setting "
            "sichtbar. Pruefe, ob der Test-DB-User wirklich kein Superuser "
            "ist UND ob FORCE ROW LEVEL SECURITY auf core_client aktiv ist."
        )

    def test_cross_tenant_event_returns_zero_rows(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT pk_id FROM (SELECT id AS pk_id FROM core_event WHERE data_json->>'note' = 'B') q")
            rows = cur.fetchall()
        assert rows == [], "Cross-Tenant-Read auf core_event hat B's Event geliefert."

    def test_cross_tenant_auditlog_returns_zero_rows(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        # Nur Facility-B-Logs zaehlen — die LOGIN-Logs von Facility A
        # sind unter facility_a-Setting natuerlich sichtbar.
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute(
                "SELECT COUNT(*) FROM core_auditlog WHERE facility_id = %s",
                [str(facility_b_with_data.pk)],
            )
            count = cur.fetchone()[0]
        assert count == 0, (
            f"AuditLog-Cross-Tenant-Read hat {count} Zeilen aus Facility B geliefert — RLS ist nicht aktiv."
        )

    def test_cross_tenant_activity_returns_zero_rows(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute(
                "SELECT COUNT(*) FROM core_activity WHERE facility_id = %s",
                [str(facility_b_with_data.pk)],
            )
            count = cur.fetchone()[0]
        assert count == 0, "Cross-Tenant-Read auf core_activity hat B-Activities geliefert."

    def test_cross_tenant_fieldgroup_returns_zero_rows(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        """FieldGroup (M0, Refs #1056) ist transitiv facility-gescopt
        (``fieldgroup.document_type.facility_id``, Migration 0111). Unter
        facility_a-Setting darf keine Gruppe aus Facility B sichtbar sein,
        die eigene aber schon.
        """
        dt_a = DocumentType.objects.get(facility=facility_a_with_data, name="A-Doc")
        dt_b = DocumentType.objects.get(facility=facility_b_with_data, name="B-Doc")
        FieldGroup.objects.create(document_type=dt_a, name="A-Group")
        FieldGroup.objects.create(document_type=dt_b, name="B-Group")

        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT name FROM core_fieldgroup WHERE name = 'B-Group'")
            leaked = cur.fetchall()
            cur.execute("SELECT name FROM core_fieldgroup WHERE name = 'A-Group'")
            own = cur.fetchall()
        assert leaked == [], "Cross-Tenant-Read auf core_fieldgroup hat B's Gruppe geliefert."
        assert own == [("A-Group",)], (
            f"Eigene FieldGroup unter facility_a-Setting nicht sichtbar (false negative). Rows: {own}"
        )

    def test_cross_facility_fieldgroup_insert_violates_policy(
        self, rls_test_role, facility_a_with_data, facility_b_with_data
    ):
        """INSERT-Seite der USING-only-Policy (Migration 0111): Postgres nutzt
        das ``USING``-Praedikat implizit als ``WITH CHECK``. Unter
        facility_a-Setting darf keine FieldGroup fuer einen facility_b-
        DocumentType eingefuegt werden — der INSERT muss an der RLS-Policy
        scheitern (``new row violates row-level security policy``), sonst
        koennte ein Tenant Konfiguration in einen fremden Scope schreiben.
        """
        import uuid as _uuid

        from django.db import Error as _DjangoDBError

        dt_b = DocumentType.objects.get(facility=facility_b_with_data, name="B-Doc")
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            with pytest.raises(_DjangoDBError) as exc:
                cur.execute(
                    "INSERT INTO core_fieldgroup "
                    "(id, document_type_id, name, slug, sort_order, is_collapsible) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [str(_uuid.uuid4()), str(dt_b.pk), "Injected-B-Group", "injected-b-group", 0, False],
                )
        assert "row-level security" in str(exc.value).lower(), (
            f"Cross-Facility-INSERT auf core_fieldgroup wurde nicht durch die RLS-Policy "
            f"abgelehnt (USING-only greift nicht als WITH CHECK?). Fehler: {exc.value!r}"
        )

    def test_fieldcondition_cross_tenant_invisible(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        """FieldCondition (M1, Refs #1057) ist doppelt-transitiv facility-gescopt
        (``fieldcondition.target_field.document_type.facility_id``, Migration
        0114). Unter facility_a-Setting darf keine Bedingung aus Facility B
        sichtbar sein.
        """
        dt_b = DocumentType.objects.get(facility=facility_b_with_data, name="B-Doc")
        ft_source = FieldTemplate.objects.create(
            facility=facility_b_with_data, name="B-Quelle", field_type=FieldTemplate.FieldType.NUMBER
        )
        ft_target = FieldTemplate.objects.create(
            facility=facility_b_with_data, name="B-Ziel", field_type=FieldTemplate.FieldType.TEXTAREA
        )
        dtf_source = DocumentTypeField.objects.create(document_type=dt_b, field_template=ft_source, sort_order=0)
        dtf_target = DocumentTypeField.objects.create(document_type=dt_b, field_template=ft_target, sort_order=1)
        condition = FieldCondition.objects.create(
            target_field=dtf_target,
            depends_on_field=dtf_source,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=10,
            group_index=0,
        )

        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT id FROM core_fieldcondition WHERE id = %s", [str(condition.pk)])
            leaked = cur.fetchall()
        assert leaked == [], "Cross-Tenant-Read auf core_fieldcondition hat B's Bedingung geliefert."

        with as_rls_role(rls_test_role, facility_id=facility_b_with_data.pk) as cur:
            cur.execute("SELECT operator FROM core_fieldcondition WHERE id = %s", [str(condition.pk)])
            own = cur.fetchall()
        assert own == [("greater_than",)], (
            f"Eigene FieldCondition unter facility_b-Setting nicht sichtbar (false negative). Rows: {own}"
        )

    def test_cross_facility_fieldcondition_insert_violates_policy(
        self, rls_test_role, facility_a_with_data, facility_b_with_data
    ):
        """INSERT-Seite der USING-only-Policy (Migration 0114), analog
        ``test_cross_facility_fieldgroup_insert_violates_policy``: Postgres nutzt
        das ``USING``-Praedikat implizit als ``WITH CHECK``. Unter
        facility_a-Setting darf keine FieldCondition fuer facility_b-Zielfelder
        eingefuegt werden.
        """
        import uuid as _uuid

        from django.db import Error as _DjangoDBError

        dt_b = DocumentType.objects.get(facility=facility_b_with_data, name="B-Doc")
        ft_source = FieldTemplate.objects.create(
            facility=facility_b_with_data, name="B-Quelle-Insert", field_type=FieldTemplate.FieldType.NUMBER
        )
        ft_target = FieldTemplate.objects.create(
            facility=facility_b_with_data, name="B-Ziel-Insert", field_type=FieldTemplate.FieldType.TEXTAREA
        )
        dtf_source = DocumentTypeField.objects.create(document_type=dt_b, field_template=ft_source, sort_order=2)
        dtf_target = DocumentTypeField.objects.create(document_type=dt_b, field_template=ft_target, sort_order=3)

        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            with pytest.raises(_DjangoDBError) as exc:
                cur.execute(
                    "INSERT INTO core_fieldcondition "
                    "(id, target_field_id, depends_on_field_id, operator, value, group_index) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [str(_uuid.uuid4()), str(dtf_target.pk), str(dtf_source.pk), "greater_than", "10", 0],
                )
        assert "row-level security" in str(exc.value).lower(), (
            f"Cross-Facility-INSERT auf core_fieldcondition wurde nicht durch die RLS-Policy "
            f"abgelehnt (USING-only greift nicht als WITH CHECK?). Fehler: {exc.value!r}"
        )

    def test_own_tenant_client_visible(self, rls_test_role, facility_a_with_data):
        """Smoke: A-Client ist unter facility_a-Setting sichtbar — sonst
        koennten die Cross-Tenant-Tests false negatives sein."""
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT pseudonym FROM core_client WHERE pseudonym = 'A-Client'")
            rows = cur.fetchall()
        assert len(rows) == 1, (
            f"A-Client ist unter eigenem facility-Setting nicht sichtbar — "
            f"die Policy filtert zu aggressiv (oder Daten fehlen). Rows: {rows}"
        )

    def test_unset_facility_returns_zero_rows(self, rls_test_role, facility_a_with_data):
        """Defense-in-Depth: ohne app.current_facility_id (=NULL) muss
        die Policy ALLE Zeilen ausschliessen — Connection-Pool-Reuse
        ohne expliziten Reset darf keinen Tenant leaken.
        """
        with as_rls_role(rls_test_role, facility_id="") as cur:
            cur.execute("SELECT COUNT(*) FROM core_client")
            count = cur.fetchone()[0]
        assert count == 0, f"Ohne facility-Setting hat RLS {count} Client-Zeilen geliefert. Erwartet: 0 (NULL-Compare)."


@pytest.mark.django_db(transaction=True)
class TestAttachmentRLSIsolation:
    """Direkte facility-Policy auf ``core_attachment`` (M24, Migration 0131,
    Refs #1738): Cross-Tenant je Richtung, INSERT-Seite (USING wirkt implizit
    als WITH CHECK) und der GUC-los-Fall — vorher war die Tabelle join-gescopt
    ueber ``core_event`` (0047/0085), Zeilen ohne Event waren policy-los.
    """

    @staticmethod
    def _make_attachment_row(facility, marker, **owner):
        import uuid as _uuid

        from core.models import Attachment
        from core.models.field_template import FieldTemplate

        ft = FieldTemplate.objects.create(
            facility=facility,
            name=f"RLS-Anhang {marker}",
            field_type=FieldTemplate.FieldType.FILE,
        )
        return Attachment.objects.create(
            facility=facility,
            field_template=ft,
            storage_filename=f"rls-{marker}-{_uuid.uuid4().hex}.enc",
            original_filename_encrypted={"v": 1},
            file_size=1,
            mime_type="application/pdf",
            **owner,
        )

    @pytest.fixture
    def attachments_a_b(self, facility_a_with_data, facility_b_with_data):
        from core.models import Client, Event

        event_a = Event.objects.get(facility=facility_a_with_data)
        client_b = Client.objects.get(facility=facility_b_with_data)
        att_a = self._make_attachment_row(facility_a_with_data, "a", event=event_a)
        # B-Seite bewusst client-owned: unter der alten Join-Policy waere die
        # Zeile policy-los gewesen — genau der Defense-in-Depth-Gewinn von 0131.
        att_b = self._make_attachment_row(facility_b_with_data, "b", client=client_b)
        return att_a, att_b

    def test_cross_tenant_a_sieht_b_nicht(
        self, rls_test_role, facility_a_with_data, facility_b_with_data, attachments_a_b
    ):
        att_a, att_b = attachments_a_b
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute(
                "SELECT storage_filename FROM core_attachment WHERE storage_filename = %s", [att_b.storage_filename]
            )
            leaked = cur.fetchall()
            cur.execute(
                "SELECT storage_filename FROM core_attachment WHERE storage_filename = %s", [att_a.storage_filename]
            )
            own = cur.fetchall()
        assert leaked == [], "Cross-Tenant-Read auf core_attachment hat B's (client-owned) Anhang geliefert."
        assert len(own) == 1, f"Eigener Anhang unter facility_a-Setting nicht sichtbar (false negative). Rows: {own}"

    def test_cross_tenant_b_sieht_a_nicht(
        self, rls_test_role, facility_a_with_data, facility_b_with_data, attachments_a_b
    ):
        att_a, _att_b = attachments_a_b
        with as_rls_role(rls_test_role, facility_id=facility_b_with_data.pk) as cur:
            cur.execute(
                "SELECT storage_filename FROM core_attachment WHERE storage_filename = %s", [att_a.storage_filename]
            )
            leaked = cur.fetchall()
        assert leaked == [], "Cross-Tenant-Read auf core_attachment hat A's Anhang geliefert."

    def test_cross_facility_insert_violates_policy(self, rls_test_role, facility_a_with_data, facility_b_with_data):
        import uuid as _uuid

        from django.db import Error as _DjangoDBError

        from core.models import Client
        from core.models.field_template import FieldTemplate

        client_b = Client.objects.get(facility=facility_b_with_data)
        ft_b = FieldTemplate.objects.create(
            facility=facility_b_with_data, name="RLS-Injected", field_type=FieldTemplate.FieldType.FILE
        )
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            with pytest.raises(_DjangoDBError) as exc:
                cur.execute(
                    "INSERT INTO core_attachment "
                    "(id, entry_id, sort_order, facility_id, client_id, field_template_id, "
                    " storage_filename, original_filename_encrypted, file_size, mime_type, "
                    " detected_mime, is_current, created_at) "
                    "VALUES (%s, %s, 0, %s, %s, %s, %s, '{}'::jsonb, 1, 'x/y', '', true, NOW())",
                    [
                        str(_uuid.uuid4()),
                        str(_uuid.uuid4()),
                        str(facility_b_with_data.pk),
                        str(client_b.pk),
                        str(ft_b.pk),
                        f"injected-{_uuid.uuid4().hex}.enc",
                    ],
                )
        assert "row-level security" in str(exc.value).lower(), (
            f"Cross-Facility-INSERT auf core_attachment wurde nicht durch die RLS-Policy "
            f"abgelehnt (USING-only greift nicht als WITH CHECK?). Fehler: {exc.value!r}"
        )

    def test_ohne_guc_keine_zeilen(self, rls_test_role, facility_a_with_data, attachments_a_b):
        with as_rls_role(rls_test_role, facility_id="") as cur:
            cur.execute("SELECT COUNT(*) FROM core_attachment")
            count = cur.fetchone()[0]
        assert count == 0, f"Ohne facility-Setting hat RLS {count} Attachment-Zeilen geliefert. Erwartet: 0."

    def test_positivkontrolle_client_owned_eigene_facility_sichtbar(
        self, rls_test_role, facility_a_with_data, facility_b_with_data, attachments_a_b
    ):
        """Diskriminator zur alten Join-Policy (G2-Befund: die Negativproben
        oben bestehen auch unter 0047/0085): eine client-eigene Zeile der
        EIGENEN Facility ist unter der direkten Policy sichtbar — unter der
        Join-Policy waere sie es nicht (``event_id IS NULL`` ⇒ NULL ⇒ nicht
        true). Wird die Policy versehentlich zurueckgedreht, wird dieser Test
        rot, nicht erst die Produktion."""
        _att_a, att_b = attachments_a_b
        with as_rls_role(rls_test_role, facility_id=facility_b_with_data.pk) as cur:
            cur.execute("SELECT count(*) FROM core_attachment WHERE storage_filename = %s", [att_b.storage_filename])
            count = cur.fetchone()[0]
        assert count == 1, (
            "client-eigene Zeile der eigenen Facility unsichtbar — die Policy verhaelt sich wie die alte Join-Policy."
        )

    def test_positivkontrolle_insert_eigene_facility_erlaubt(self, rls_test_role, facility_a_with_data):
        import uuid as _uuid

        from core.models import Client
        from core.models.field_template import FieldTemplate

        client_a = Client.objects.get(facility=facility_a_with_data)
        ft_a = FieldTemplate.objects.create(
            facility=facility_a_with_data, name="RLS-Own-Insert", field_type=FieldTemplate.FieldType.FILE
        )
        marker = f"own-insert-{_uuid.uuid4().hex}.enc"
        with as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute(
                "INSERT INTO core_attachment "
                "(id, entry_id, sort_order, facility_id, client_id, field_template_id, "
                " storage_filename, original_filename_encrypted, file_size, mime_type, "
                " detected_mime, is_current, created_at) "
                "VALUES (%s, %s, 0, %s, %s, %s, %s, '{}'::jsonb, 1, 'x/y', '', true, NOW())",
                [
                    str(_uuid.uuid4()),
                    str(_uuid.uuid4()),
                    str(facility_a_with_data.pk),
                    str(client_a.pk),
                    str(ft_a.pk),
                    marker,
                ],
            )
            cur.execute("SELECT count(*) FROM core_attachment WHERE storage_filename = %s", [marker])
            count = cur.fetchone()[0]
        assert count == 1, "INSERT mit eigener facility_id muss unter der direkten Policy gelingen."


# ===========================================================================
# Refs #2159 (ADR-050 M-8): Deferral-Zweig der Ereignishistorie-Redaktion
# ===========================================================================

DEFERRAL_LOGGER = "core.services.client.main"
#: Praefix der Ops-Zeile. Gefiltert wird auf ihn, weil derselbe Logger in
#: derselben Kaskade auch ``client_anonymized`` schreibt — der Vertrag ist
#: die Deferral-Zeile, nicht „dieser Logger schweigt sonst".
DEFERRAL_PREFIX = "eventhistory_redaction_deferred"


def _deferral_messages(caplog):
    """Alle fertig formatierten Deferral-Meldungen des Laufs."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == DEFERRAL_LOGGER and DEFERRAL_PREFIX in record.getMessage()
    ]


@pytest.fixture
def art17_person_with_history(facility, admin_user, staff_user, doc_type_contact):
    """Person im Papierkorb MIT Ereignis UND Historienzeile.

    Die Historienzeile ist die Vorbedingung, an der alles haengt:
    ``_redact_event_history`` steigt bei ``history_qs.exists()`` aus — ohne sie
    erreicht der Lauf den Owner-DDL-Zweig nie und der Test waere Scheingruen.
    """

    def _make(pseudonym):
        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff_user,
        )
        create_event(
            facility=facility,
            user=staff_user,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "Klartext-Notiz"},
            client=person,
        )
        assert EventHistory.objects.filter(event__client=person).exists(), (
            "Vorbedingung verletzt: ohne EventHistory-Zeile erreicht der Lauf den Deferral-Zweig nie"
        )
        person.soft_delete(user=admin_user)
        return person

    return _make


@contextmanager
def _reset_facility_guc():
    """Setzt ``app.current_facility_id`` nach dem Block zurueck.

    ``as_rls_role`` setzt die GUC session-weit (``is_local=false``) und raeumt
    im ``finally`` nur die Rolle weg. Fuer die Owner-Gegenprobe im selben
    Prozess darf kein Facility-Rest aus dem vorherigen Test stehenbleiben.
    """
    try:
        yield
    finally:
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
            cur.execute("SELECT set_config('app.current_facility_id', '', false)")


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestEventHistoryRedactionDeferralUnderAppRole:
    """Der Aufschub der Historien-Redaktion, belegt unter der ECHTEN App-Rolle.

    ADR-050 M-8: ``_redact_event_history`` braucht fuer den Trigger-Bypass
    Owner-DDL (``ALTER TABLE ... DISABLE TRIGGER``). Der Webprozess laeuft unter
    einer Rolle ohne Tabellen-Eigentum (ADR-033), nimmt den Bypass deshalb
    nicht und **meldet** den uebersprungenen Schritt als Ops-Zeile
    ``eventhistory_redaction_deferred rows=… reason=connection_is_not_table_owner``;
    der owner-seitige Nachlauf holt die Redaktion.

    Diese Ops-Zeile ist der einzige Beleg, dass etwas aufgeschoben wurde —
    gepinnt war sie bisher nur ueber einen ``monkeypatch``-Stub des Praedikats
    ``current_connection_owns`` (``test_mutation_pins_094_main_1.py``), weil die
    Testverbindung stets Tabelleneigentuemerin ist. Hier laeuft derselbe Pfad
    unter ``rls_test_role``: ``NOSUPERUSER``, ``NOBYPASSRLS`` (Default), nur
    DML-Grants, **kein** Ownership — der Zweig wird also von der Datenbank
    ausgewaehlt, nicht von einem Test-Stub.

    ``transaction=True`` wie in der Cross-Tenant-Klasse: ``SET ROLE`` und
    Statement-Cache vertragen sich nicht mit der Savepoint-Strategie.
    """

    def test_app_role_does_not_own_the_history_table(self, rls_test_role, facility_a_with_data):
        """Vorbedingung, ohne die die beiden folgenden Tests nichts belegen:
        unter ``SET ROLE`` beantwortet PostgreSQL selbst die Eigentumsfrage mit
        Nein — ``pg_has_role(current_user, relowner, 'USAGE')`` ist genau die
        Bedingung, die der Server auch fuer Owner-DDL stellt."""
        with _reset_facility_guc(), as_rls_role(rls_test_role, facility_id=facility_a_with_data.pk) as cur:
            cur.execute("SELECT current_user")
            (role_in_session,) = cur.fetchone()
            cur.execute(
                "SELECT pg_catalog.pg_has_role(current_user, c.relowner, 'USAGE') "
                "FROM pg_catalog.pg_class c WHERE c.oid = 'core_eventhistory'::regclass"
            )
            (has_owner_role,) = cur.fetchone()
            predicate = current_connection_owns("core_eventhistory")

        assert role_in_session == rls_test_role
        assert has_owner_role is False, (
            "Die Testrolle ist Mitglied der Owner-Rolle — dann waere der "
            "Deferral-Zweig unter ihr gar nicht erreichbar und der Nachweis wertlos."
        )
        assert predicate is False

    def test_immediate_art17_path_defers_and_logs_the_row_count(
        self, rls_test_role, facility, admin_user, art17_person_with_history, caplog
    ):
        """Der oeffentliche Sofortpfad unter der App-Rolle: Person sofort
        anonymisiert, Historie unangetastet, Aufschub als Ops-Zeile gemeldet.

        Geprueft wird die fertig formatierte Meldung — Text UND eingesetzte
        Zeilenzahl —, und die Zahl gegen den tatsaechlichen Bestand.
        """
        person = art17_person_with_history("Art17-RLS-Deferral")
        event_ids = list(Event.objects.filter(client=person).values_list("id", flat=True))
        assert event_ids

        with caplog.at_level(logging.INFO, logger=DEFERRAL_LOGGER):
            with _reset_facility_guc(), as_rls_role(rls_test_role, facility_id=facility.pk):
                anonymize_client_now(person, admin_user)

        rows = EventHistory.objects.filter(event_id__in=event_ids)
        row_count = rows.count()
        assert row_count >= 1

        assert _deferral_messages(caplog) == [
            f"eventhistory_redaction_deferred rows={row_count} reason=connection_is_not_table_owner"
        ]

        # Die Person ist sofort getilgt — nur die frueheren Fassungen stehen aus.
        person.refresh_from_db()
        assert person.anonymized_at is not None
        assert person.pseudonym.startswith("Gelöscht-")
        for row in rows:
            assert row.redacted_at is None
            assert row.data_after != build_anonymization_marker()

    def test_owner_connection_redacts_without_a_deferral_line(
        self, facility, admin_user, art17_person_with_history, caplog
    ):
        """Gegenprobe: dieselbe Funktion, dieselben Daten — nur ohne
        ``SET ROLE``. Die Verbindung ist Eigentuemerin, der Bypass greift, die
        Historie ist sofort redigiert und es wird **nichts** aufgeschoben.

        Ohne diese Haelfte belegte der Test oben nur, dass irgendetwas nicht
        passiert ist.
        """
        person = art17_person_with_history("Art17-RLS-Owner")
        event_ids = list(Event.objects.filter(client=person).values_list("id", flat=True))

        with caplog.at_level(logging.INFO, logger=DEFERRAL_LOGGER):
            with _reset_facility_guc():
                assert current_connection_owns("core_eventhistory") is True
                anonymize_client_now(person, admin_user)

        assert _deferral_messages(caplog) == []

        rows = list(EventHistory.objects.filter(event_id__in=event_ids))
        assert rows
        marker = build_anonymization_marker()
        for row in rows:
            assert row.data_before == marker
            assert row.data_after == marker
