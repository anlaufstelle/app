"""Tests für ``services.retention.prune_auditlog`` (Refs #129 Teil B, Refs #733).

Verifiziert die AuditLog-Retention:
- Alte AuditLog-Eintraege werden geloescht
- Junge bleiben erhalten
- ``--dry-run`` loescht nichts
- ``auditlog_retention_months=0`` ist No-op
- Trigger ``auditlog_immutable`` ist nach Pruning weiterhin aktiv
"""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from core.models import AuditLog
from core.services.retention import prune_auditlog


@pytest.mark.django_db(transaction=True)
class TestPruneAuditlog:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("AuditLog-Trigger und prune_auditlog erfordern PostgreSQL")

    def _create_log_with_timestamp(self, facility, ts):
        """Erstellt AuditLog und ueberschreibt timestamp via Raw SQL.

        ``timestamp = auto_now_add`` ist beim Insert nicht steuerbar — fuer
        Tests, die alte Eintraege simulieren, brauchen wir Raw SQL. Trigger
        kurz deaktivieren, da er auch UPDATE blockt.
        """
        log = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN)
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("ALTER TABLE core_auditlog DISABLE TRIGGER auditlog_immutable")
            try:
                cur.execute(
                    "UPDATE core_auditlog SET timestamp = %s WHERE id = %s",
                    [ts, str(log.pk)],
                )
            finally:
                cur.execute("ALTER TABLE core_auditlog ENABLE TRIGGER auditlog_immutable")
        return log

    def test_prune_deletes_entries_older_than_cutoff(self, facility, settings_obj):
        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        old = self._create_log_with_timestamp(facility, now - timedelta(days=24 * 30 + 1))
        young = self._create_log_with_timestamp(facility, now - timedelta(days=10))

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 1
        assert not AuditLog.objects.filter(pk=old.pk).exists()
        assert AuditLog.objects.filter(pk=young.pk).exists()

    def test_prune_dry_run_does_not_delete(self, facility, settings_obj):
        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        old = self._create_log_with_timestamp(facility, now - timedelta(days=24 * 30 + 1))

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=True)

        assert result["count"] == 1
        assert AuditLog.objects.filter(pk=old.pk).exists(), "Dry-Run darf nichts loeschen — Count nur als Vorschau."

    def test_prune_disabled_when_months_zero(self, facility, settings_obj):
        settings_obj.auditlog_retention_months = 0
        settings_obj.save()
        now = timezone.now()
        old = self._create_log_with_timestamp(facility, now - timedelta(days=10 * 365))

        result = prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        assert result["count"] == 0
        assert AuditLog.objects.filter(pk=old.pk).exists()

    def test_trigger_active_after_pruning(self, facility, settings_obj):
        # Sicherheitsrelevant: prune_auditlog umgeht den Trigger
        # transaktional. Nach dem Lauf MUSS er wieder greifen — sonst
        # waere AuditLog-Immutability-Schutz weg.
        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        self._create_log_with_timestamp(facility, now - timedelta(days=24 * 30 + 1))

        prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        # Trigger muss wieder aktiv sein — Raw UPDATE muss scheitern.
        new_log = AuditLog.objects.create(facility=facility, action=AuditLog.Action.LOGIN)
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "UPDATE core_auditlog SET action = %s WHERE id = %s",
                ["logout", str(new_log.pk)],
            )
        assert "immutable" in str(excinfo.value).lower()

    def test_trigger_tgenabled_stays_origin_after_pruning(self, facility, settings_obj):
        """Refs #781 (C-13): Health-Check-aequivalenter Test —
        ``pg_trigger.tgenabled`` muss immer ``'O'`` sein.

        Vor dem Fix lief ``ALTER TABLE ... DISABLE TRIGGER`` und
        ``... ENABLE TRIGGER`` als Klammer. Bei SIGKILL zwischen den
        Statements blieb der Trigger disabled.

        Mit ``bypass_replication_triggers()`` veraendert nur
        ``session_replication_role`` die Trigger-Sicht — auf Tabellen-Ebene
        bleibt ``tgenabled='O'`` durchgaengig erhalten.
        """
        settings_obj.auditlog_retention_months = 24
        settings_obj.save()
        now = timezone.now()
        self._create_log_with_timestamp(facility, now - timedelta(days=24 * 30 + 1))

        prune_auditlog(facility, settings_obj, now=now, dry_run=False)

        with connection.cursor() as cur:
            cur.execute(
                "SELECT tgenabled FROM pg_trigger WHERE tgname = %s",
                ["auditlog_immutable"],
            )
            row = cur.fetchone()
        assert row is not None, "Trigger 'auditlog_immutable' fehlt"
        assert row[0] == "O", (
            f"pg_trigger.tgenabled fuer auditlog_immutable ist '{row[0]}', "
            "erwartet 'O' (origin/local). Refs #781 — der neue Pfad nutzt "
            "session_replication_role, nicht ALTER TABLE DISABLE TRIGGER."
        )
