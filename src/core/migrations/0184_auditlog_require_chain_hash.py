"""Refs #2449: every new AuditLog row must carry its chain hash.

``entry_hash``/``prev_hash`` stay nullable for rows written before the HMAC
chain existed (#1070). Without a database rule, however, any role with INSERT on
``core_auditlog`` -- including the runtime app role -- could append rows
without a hash, past ``AuditLog.save()`` and therefore past the chain.

A ``BEFORE INSERT`` trigger rejects such rows: ``entry_hash`` must be non-empty,
``prev_hash`` must not be NULL (it is ``''`` at the chain start). The trigger
is ``ENABLE ALWAYS`` like the append-only triggers (0107), so
``session_replication_role = replica`` does not skip it, and only the table
owner can disable it.

Why a trigger and not ``CHECK (...) NOT VALID``: for the app role both are
equivalent -- UPDATE is already blocked by ``auditlog_immutable``. A CHECK
would additionally apply to UPDATEs on the owner's maintenance path, which
runs with ``auditlog_immutable`` disabled; the trigger leaves that path and the
existing rows untouched and only governs INSERT. Existing rows are not
checked.

Pure DDL, no data access; ``reverse_sql`` drops trigger and function.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0183_auditlog_mfa_reset_by_admin")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION require_auditlog_chain_hash()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.entry_hash IS NULL OR NEW.entry_hash = '' THEN
                        RAISE EXCEPTION 'AuditLog entries must carry an entry_hash (hash chain).'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF NEW.prev_hash IS NULL THEN
                        RAISE EXCEPTION 'AuditLog entries must carry a prev_hash (empty at the chain start).'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER auditlog_require_chain_hash
                BEFORE INSERT ON core_auditlog
                FOR EACH ROW EXECUTE FUNCTION require_auditlog_chain_hash();

                ALTER TABLE core_auditlog ENABLE ALWAYS TRIGGER auditlog_require_chain_hash;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS auditlog_require_chain_hash ON core_auditlog;
                DROP FUNCTION IF EXISTS require_auditlog_chain_hash();
            """,
        ),
    ]
