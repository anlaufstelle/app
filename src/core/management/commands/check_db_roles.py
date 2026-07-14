"""CLI-Tool: Prueft Postgres-Rollenattribute fuer App- und Admin-Rolle.

Refs #902: Selbst-Hosting-Installationen koennen mit zu
privilegiertem App-DB-User laufen, wenn das offizielle ``postgres:18``-
Image den per ``POSTGRES_USER`` angelegten Login automatisch als
Superuser erstellt. RLS-Policies (Migration 0047) und BYPASSRLS-
Annahmen werden dann unbemerkt unterlaufen.

Dieses Kommando verifiziert die Rollen-Topologie aus
``deploy/postgres-init/01-app-role.sh`` zur Laufzeit:

- App-Rolle (``POSTGRES_APP_USER`` env, Fallback ``settings.DATABASES['default']['USER']``):
  ``rolsuper=false``, ``rolbypassrls=false``.
- Admin-Rolle (``POSTGRES_ADMIN_USER`` aus env, falls gesetzt):
  ``rolsuper=false``, ``rolbypassrls=true``.

Exit-Codes:

- 0: Alles ok.
- 1: Mindestens eine Rolle hat das falsche Attributprofil.
- 2: Konfiguration unvollstaendig (z.B. fehlende env-Variable).

Wird vom Compliance-Dashboard (#919) ueber dieselbe Hilfsfunktion
:func:`check_db_roles` konsumiert.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection


@dataclass(frozen=True)
class RoleCheck:
    """Ergebnis eines einzelnen Rollencheck."""

    role: str
    label: str  # "App" / "Admin"
    expected_super: bool
    expected_bypassrls: bool
    actual_super: bool | None
    actual_bypassrls: bool | None

    @property
    def exists(self) -> bool:
        return self.actual_super is not None

    @property
    def ok(self) -> bool:
        if not self.exists:
            return False
        return self.actual_super == self.expected_super and self.actual_bypassrls == self.expected_bypassrls

    def problems(self) -> list[str]:
        if not self.exists:
            return [f"Rolle {self.role!r} existiert nicht in pg_roles."]
        issues = []
        if self.actual_super != self.expected_super:
            issues.append(f"Rolle {self.role!r}: rolsuper={self.actual_super} (erwartet: {self.expected_super}).")
        if self.actual_bypassrls != self.expected_bypassrls:
            issues.append(
                f"Rolle {self.role!r}: rolbypassrls={self.actual_bypassrls} (erwartet: {self.expected_bypassrls})."
            )
        return issues


def _query_role(role: str) -> tuple[bool | None, bool | None]:
    """Liefert ``(rolsuper, rolbypassrls)`` fuer ``role`` oder ``(None, None)``."""
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", [role])
        row = cur.fetchone()
    if not row:
        return None, None
    return bool(row[0]), bool(row[1])


def current_connection_bypasses_rls() -> bool:
    """True, wenn die AKTUELLE DB-Connection Row Level Security umgeht.

    L10 (Refs #1375): :func:`check_db_roles` prueft nur, DASS App-/Admin-Rolle
    mit den richtigen Attributen in ``pg_roles`` existieren — NICHT, dass der
    laufende Migrate-Job selbst als der BYPASSRLS-Admin verbindet. Diese
    Laufzeit-Assertion (via ``docker-migrate.sh``) schliesst die Luecke: laeuft
    der Migrate versehentlich als die NOBYPASSRLS-App-Rolle, uebersehen
    Daten-Migrationen unter RLS Zeilen und ``normalize_db_ownership``
    (``REASSIGN OWNED``) schlaegt fehl — beides soll fail-fast abbrechen, bevor
    neue Web-Replicas live gehen.

    "Umgeht RLS" = Superuser ODER ``rolbypassrls`` — ein Superuser umgeht RLS
    per Definition, auch wenn das ``rolbypassrls``-Attribut im Katalog false ist.
    """
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
    if not row:
        return False
    return bool(row[0]) or bool(row[1])


def check_db_roles() -> tuple[list[RoleCheck], list[str]]:
    """Fuehre den Rollencheck aus.

    Returns ``(checks, config_errors)``:

    - ``checks``: Liste der durchgefuehrten Rollenchecks.
    - ``config_errors``: Liste von Konfigurationsproblemen (z.B. fehlende
      Admin-Rolle in env). Falls leer und alle Checks ``ok=True``, ist
      die Topologie sauber.
    """
    config_errors: list[str] = []
    checks: list[RoleCheck] = []

    # Refs #1017: App-Rollenname aus POSTGRES_APP_USER (stabile Quelle), Fallback
    # auf den Connection-User. Der Migrate-Job (docker-migrate.sh) connectet als
    # Admin (POSTGRES_USER-Override, Refs #863); ohne die stabile Quelle pruefte
    # das Gate faelschlich die Admin- statt der App-Rolle.
    app_role = os.environ.get("POSTGRES_APP_USER") or settings.DATABASES.get("default", {}).get("USER")
    if not app_role:
        config_errors.append(
            "App-Rolle nicht bestimmbar (weder POSTGRES_APP_USER noch settings.DATABASES['default']['USER'] gesetzt)."
        )
    else:
        actual_super, actual_bypass = _query_role(app_role)
        checks.append(
            RoleCheck(
                role=app_role,
                label="App",
                expected_super=False,
                expected_bypassrls=False,
                actual_super=actual_super,
                actual_bypassrls=actual_bypass,
            )
        )

    admin_role = os.environ.get("POSTGRES_ADMIN_USER") or ""
    if not admin_role:
        config_errors.append(
            "POSTGRES_ADMIN_USER ist nicht gesetzt — Admin-/Maintenance-Rolle nicht pruefbar. "
            "Refs #902: Drei-Rollen-Modell in docker-compose.prod.yml."
        )
    else:
        actual_super, actual_bypass = _query_role(admin_role)
        checks.append(
            RoleCheck(
                role=admin_role,
                label="Admin",
                expected_super=False,
                expected_bypassrls=True,
                actual_super=actual_super,
                actual_bypassrls=actual_bypass,
            )
        )

    return checks, config_errors


class Command(BaseCommand):
    help = "Prueft Postgres-Rollenattribute fuer App-/Admin-Rolle (Refs #902)."

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

        if config_errors and not checks:
            # Nur Konfigurationsfehler, keine sinnvollen Checks moeglich.
            return self._exit(2)

        if not all_ok or any(not c.exists for c in checks):
            return self._exit(1)

        return self._exit(0)

    def _exit(self, code: int) -> None:
        """SystemExit ueber Command-Pfad — sys.exit ist hier sauberer als CommandError."""
        if code != 0:
            raise SystemExit(code)
