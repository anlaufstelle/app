"""Migration 0184: INSERT guard for the AuditLog hash chain (Refs #2449).

Checks the trigger as it exists on a database built from the migrations (the
test database), its ``ENABLE ALWAYS`` mode, that the app role can neither
disable nor bypass it, and that the migration is reversible.

The reversibility test migrates back to the leaf nodes determined at test start
in a ``finally`` -- an aborted test must not leave the schema on an
intermediate state.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

APP = "core"
TRIGGER = "auditlog_require_chain_hash"
FUNCTION = "require_auditlog_chain_hash"
TARGET_BACKWARD = [(APP, "0183_auditlog_mfa_reset_by_admin")]
TARGET_FORWARD = [(APP, "0184_auditlog_require_chain_hash")]


def _require_postgres():
    assert connection.vendor == "postgresql", "AuditLog trigger requires PostgreSQL"


def _trigger_state():
    """``tgenabled`` of the trigger (``None`` if missing) and whether the function exists."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgname = %s AND tgrelid = 'core_auditlog'::regclass",
            [TRIGGER],
        )
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM pg_proc WHERE proname = %s", [FUNCTION])
        functions = cur.fetchone()[0]
    return (row[0] if row else None), functions


def _raw_insert(cur, entry_hash, prev_hash):
    cur.execute(
        "INSERT INTO core_auditlog (id, facility_id, user_id, action, target_type, target_id, "
        "detail, ip_address, timestamp, entry_hash, prev_hash) "
        "VALUES (%s, NULL, NULL, 'security_violation', '', 'probe-0184', '{}', NULL, NOW(), %s, %s)",
        [uuid.uuid4(), entry_hash, prev_hash],
    )


def _try(cur, sql):
    try:
        with transaction.atomic():
            cur.execute(sql)
    except DatabaseError as exc:
        return type(exc).__name__
    return "ok"


@pytest.mark.django_db
def test_trigger_is_installed_enable_always():
    _require_postgres()
    assert _trigger_state() == ("A", 1)


@pytest.mark.django_db
def test_raw_insert_without_hash_is_rejected_for_the_migrating_role():
    """The guard is not limited to the app role: a raw INSERT by the role
    running the migrations fails as well."""
    _require_postgres()
    with connection.cursor() as cur:
        with pytest.raises(IntegrityError), transaction.atomic():
            _raw_insert(cur, None, None)
        with transaction.atomic():
            _raw_insert(cur, "e" * 64, "")


@pytest.mark.django_db
def test_app_role_can_neither_disable_nor_skip_the_trigger(facility, rls_test_role):  # noqa: F811
    _require_postgres()
    with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
        disable = _try(cur, f"ALTER TABLE core_auditlog DISABLE TRIGGER {TRIGGER}")
        drop = _try(cur, f"DROP TRIGGER {TRIGGER} ON core_auditlog")
        replica = _try(cur, "SET LOCAL session_replication_role = replica")
    assert disable != "ok"
    assert drop != "ok"
    assert replica != "ok"
    assert _trigger_state() == ("A", 1)


@pytest.mark.django_db(transaction=False)
def test_migration_0184_is_reversible():
    _require_postgres()
    leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
    assert _trigger_state() == ("A", 1)
    try:
        MigrationExecutor(connection).migrate(TARGET_BACKWARD)
        assert _trigger_state() == (None, 0)
        with connection.cursor() as cur, transaction.atomic():
            _raw_insert(cur, None, None)
            # Fire the deferred FK checks of the probe row now; pending trigger
            # events would block the ALTER TABLE of the forward migration.
            cur.execute("SET CONSTRAINTS ALL IMMEDIATE")

        MigrationExecutor(connection).migrate(TARGET_FORWARD)
        assert _trigger_state() == ("A", 1)
    finally:
        MigrationExecutor(connection).migrate(leaves)
