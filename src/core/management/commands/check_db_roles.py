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

A1.4 (Refs #1026): Zusaetzlich verifiziert :func:`check_db_ownership`, dass die
App-Runtime-Rolle im Drei-Rollen-Modell NICHT DB-/Objekt-Owner der Append-Only-
Tabellen ist — sonst koennte sie deren Immutability-Trigger per Owner-DDL
abschalten (stiller Fail-open auf Bestands-DBs, siehe ops-runbook § 9.6).

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


# A1.4 (Refs #1026): Append-Only-Tabellen, deren Ownership NICHT bei der
# App-Runtime-Rolle liegen darf — sonst kann sie die Immutability-Trigger per
# ``ALTER TABLE ... DISABLE TRIGGER`` abschalten (Owner-DDL). Muss mit
# ``_db_admin._APPEND_ONLY_TRIGGERS`` konsistent bleiben.
_APPEND_ONLY_TABLES = ("core_auditlog", "core_eventhistory")


def _query_db_owner() -> str | None:
    """Owner der aktuellen Datenbank (``pg_database.datdba``) als Rollenname."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
        row = cur.fetchone()
    return row[0] if row else None


def _query_table_owner(table: str) -> str | None:
    """Owner einer Tabelle als Rollenname oder ``None``, wenn sie (noch) fehlt."""
    with connection.cursor() as cur:
        cur.execute("SELECT tableowner FROM pg_tables WHERE tablename = %s", [table])
        row = cur.fetchone()
    return row[0] if row else None


def check_db_ownership() -> list[str]:
    """A1.4 (Refs #1026): Verifiziert, dass die App-Runtime-Rolle NICHT Owner der
    Datenbank oder der Append-Only-Tabellen ist.

    :func:`check_db_roles` prueft nur die Rollen-ATTRIBUTE (``rolsuper``/
    ``rolbypassrls``) — NICHT die Ownership. Auf einer **Bestands-DB** (die
    App-Rolle war vor A1.4 DB-Owner) laeuft Migration ``0107`` (``ENABLE
    ALWAYS``) gruen durch und das Attributprofil bleibt korrekt; der Deploy wird
    gruen — aber die App-Rolle bleibt **Owner** und kann die Append-Only-Trigger
    weiterhin per ``ALTER TABLE ... DISABLE TRIGGER`` abschalten. Das ist ein
    stiller Fail-open. Dieser Check macht den fehlenden Ownership-Umzug
    (ops-runbook § 9.6) **fail-loud** — Verletzung => das Kommando bricht mit
    Exit 1 ab und blockiert den Deploy.

    Aktiv nur im **Drei-Rollen-Modell** (``POSTGRES_ADMIN_USER`` gesetzt und
    verschieden von der App-Rolle). Im Legacy-Einzelrollen-/Dev-Setup ohne
    separate Admin-Rolle ist die App-Rolle bewusst Owner (Fallback in
    ``01-app-role.sh``) — dort meldet der Check nichts, damit die normale Test-/
    Dev-Umgebung nicht falsch-positiv wird. No-op auf Nicht-PostgreSQL.
    """
    if connection.vendor != "postgresql":
        return []

    # Stabiler App-Rollenname wie in check_db_roles (Refs #1017): der Migrate-Job
    # connectet als Admin (POSTGRES_USER-Override), die zu schuetzende Rolle ist
    # aber die App-Rolle aus POSTGRES_APP_USER.
    app_role = os.environ.get("POSTGRES_APP_USER") or settings.DATABASES.get("default", {}).get("USER")
    admin_role = os.environ.get("POSTGRES_ADMIN_USER") or ""
    # Ownership-Trennung ist nur im Drei-Rollen-Modell definiert. Ohne separate
    # Admin-Rolle (Dev/Legacy) ist die App-Rolle legitimer Owner.
    if not app_role or not admin_role or admin_role == app_role:
        return []

    problems: list[str] = []
    if _query_db_owner() == app_role:
        problems.append(
            f"Datenbank-Owner ist die App-Runtime-Rolle {app_role!r} — im "
            f"Drei-Rollen-Modell (A1.4) muss die Admin-/Wartungs-Rolle {admin_role!r} "
            "Owner sein."
        )
    for table in _APPEND_ONLY_TABLES:
        if _query_table_owner(table) == app_role:
            problems.append(
                f"Append-Only-Tabelle {table!r} gehoert der App-Runtime-Rolle "
                f"{app_role!r} — als Owner koennte sie die Immutability-Trigger per "
                "'ALTER TABLE ... DISABLE TRIGGER' abschalten (A1.4)."
            )
    if problems:
        problems.append(
            "Ownership-Umzug auf die Admin-Rolle noch nicht durchgefuehrt "
            "(Fail-open verhindert). Ablauf: docs/ops-runbook.md § 9.6 (Refs #1026)."
        )
    return problems


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

        # A1.4 (Refs #1026): zusaetzlich zur Attribut-Pruefung die Ownership
        # verifizieren — eine Bestands-DB mit korrektem Rollenprofil, aber noch
        # App-owned Tabellen, ist sonst still fail-open (§ 9.6).
        ownership_problems = check_db_ownership()
        for problem in ownership_problems:
            self.stderr.write(self.style.ERROR(f"FAIL  {problem}"))

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
