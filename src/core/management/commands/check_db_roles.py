"""CLI-Tool: Prueft Postgres-Rollenattribute fuer App- und Admin-Rolle.

Refs #902: Selbst-Hosting-Installationen koennen mit zu
privilegiertem App-DB-User laufen, wenn das offizielle ``postgres:18``-
Image den per ``POSTGRES_USER`` angelegten Login automatisch als
Superuser erstellt. RLS-Policies (Migration 0047) und BYPASSRLS-
Annahmen werden dann unbemerkt unterlaufen.

Thin wrapper (Refs #1835, Muster ``enforce_retention.py``/
``reencrypt_fields.py``): die eigentliche Rollen-Pruef-Logik (``RoleCheck``,
``check_db_roles``, ``check_db_ownership``, ``current_connection_bypasses_rls``)
lebt in :mod:`core.services.compliance.db_roles` — vorher umgekehrt: der
Compliance-Service importierte per Lazy-Import aus diesem Command (der
einzige Command-Import im Nicht-Test-Code). Hier bleiben nur noch
Argument-lose CLI-Ausgabe und Exit-Codes.

Exit-Codes:

- 0: Alles ok.
- 1: Mindestens eine Rolle hat das falsche Attributprofil.
- 2: Konfiguration unvollstaendig (z.B. fehlende env-Variable).

Wird vom Compliance-Dashboard (#919) ueber dieselbe Hilfsfunktion
:func:`core.services.compliance.db_roles.check_db_roles` konsumiert.

Refs #2415: every run that queried the roles also leaves a system AuditLog
marker with its outcome (:func:`core.services.compliance.db_roles.record_db_roles_marker`)
— the web container cannot check the admin role itself and shows that marker
instead. Writing the marker never changes the exit code; a skipped marker is
reported on stderr.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from core.services.compliance.db_roles import (
    RoleCheck,  # noqa: F401 — Re-Export, Bestandsimporte (Tests) bleiben lauffaehig
    check_db_ownership,
    check_db_roles,
    current_connection_bypasses_rls,  # noqa: F401 — Re-Export, siehe test_connection_bypassrls.py
    record_db_roles_marker,
)


class Command(BaseCommand):
    help = "Prüft Postgres-Rollenattribute für App-/Admin-Rolle (Refs #902)."

    def handle(self, *args, **options):
        checks, config_errors = check_db_roles()

        if config_errors:
            for msg in config_errors:
                self.stderr.write(self.style.WARNING(f"WARN  {msg}"))

        all_ok = True
        for check in checks:
            if check.ok:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"OK    {check.label}-Rolle {check.role!r}: "
                        f"rolsuper={check.actual_super}, rolbypassrls={check.actual_bypassrls}"
                    )
                )
            else:
                all_ok = False
                for problem in check.problems():
                    self.stderr.write(self.style.ERROR(f"FAIL  {problem}"))

        # A1.4 (Refs #1026): zusaetzlich zur Attribut-Pruefung die Ownership
        # verifizieren — eine Bestands-DB mit korrektem Rollenprofil, aber noch
        # App-owned Tabellen, ist sonst still fail-open (§ 9.6).
        ownership_problems = check_db_ownership()
        for problem in ownership_problems:
            self.stderr.write(self.style.ERROR(f"FAIL  {problem}"))

        # Refs #2415: record the outcome for the dashboard before deciding the
        # exit code; the marker never influences it.
        skipped = record_db_roles_marker(checks, ownership_problems)
        if skipped:
            self.stderr.write(self.style.WARNING(f"WARN  {skipped}"))

        if config_errors and not checks:
            # Nur Konfigurationsfehler, keine sinnvollen Checks moeglich.
            return self._exit(2)

        if not all_ok or any(not c.exists for c in checks) or ownership_problems:
            return self._exit(1)

        return self._exit(0)

    def _exit(self, code: int) -> None:
        """SystemExit ueber Command-Pfad — sys.exit ist hier sauberer als CommandError."""
        if code != 0:
            raise SystemExit(code)
