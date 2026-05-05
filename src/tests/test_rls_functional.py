"""Funktionaler RLS-Cross-Tenant-Test (Refs #718, Master-Audit Blocker 5).

Bisher liefen alle RLS-Tests als Superuser-DB-User — der bypassed RLS
per Postgres-Default und macht echte Policy-Verletzungen in Tests
unsichtbar. Wenn ein Coolify-Default oder Migrations-Setup den
Django-DB-User als Superuser anlegt, wird RLS in Produktion **still**
abgeschaltet — kein CI-Alarm.

Dieser Test laedt eine dedizierte Postgres-Rolle ``rls_test_role``
(``NOSUPERUSER``), setzt sie via ``SET ROLE`` und verifiziert, dass die
``facility_isolation``-Policy Cross-Tenant-Reads tatsaechlich
unterbindet — 0-Rows-Assertion ueber alle ``core_*``-Tabellen mit
``app.current_facility_id`` einer fremden Facility.

Statistik-Materialized-View ([`0049_statistics_event_flat_mv.py`](https://github.com/tobiasnix/anlaufstelle/blob/main/src/core/migrations/0049_statistics_event_flat_mv.py))
ist bewusst ohne RLS modelliert (Aggregat-Lese, keine Pseudonyme) —
out of scope fuer diesen Test.
"""

from contextlib import contextmanager

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.utils import timezone

from core.models import Activity, AuditLog, Client, DocumentType, Event


@pytest.fixture(scope="session")
def rls_test_role(django_db_setup, django_db_blocker):
    """Erstellt eine session-globale Postgres-Rolle ohne Superuser.

    Idempotent: bei wiederholtem Lauf (oder unter pytest-xdist) wird die
    Rolle nicht doppelt angelegt. Grants auf schema + alle Tabellen +
    Sequenzen, damit die Rolle ueberhaupt SELECT/INSERT ausfuehren kann.
    """
    if connection.vendor != "postgresql":
        pytest.skip("Non-Superuser-Test erfordert PostgreSQL")

    with django_db_blocker.unblock(), connection.cursor() as cur:
        cur.execute(
            """
                DO $do$ BEGIN
                    CREATE ROLE rls_test_role NOSUPERUSER NOREPLICATION
                        INHERIT NOLOGIN;
                EXCEPTION WHEN duplicate_object THEN
                    NULL;
                END $do$;
                """
        )
        cur.execute("GRANT USAGE ON SCHEMA public TO rls_test_role")
        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rls_test_role")
        cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rls_test_role")
    return "rls_test_role"


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
        category=DocumentType.Category.CONTACT,
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
        summary="A-Created",
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
        category=DocumentType.Category.CONTACT,
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
        summary="B-Created",
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
                "SELECT COUNT(*) FROM core_activity WHERE summary = 'B-Created'",
            )
            count = cur.fetchone()[0]
        assert count == 0, "Cross-Tenant-Read auf core_activity hat B-Activities geliefert."

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
