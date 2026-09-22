"""DB-Rollen-Checks fuer Compliance-Dashboard (Refs #919, #958-M3).

Drei Checks: App-NOSUPERUSER, App-NOBYPASSRLS, Admin-Rolle vorhanden.

Traegt seit Refs #1835 auch die Rollen-Pruef-Infrastruktur selbst
(``RoleCheck``, ``check_db_roles``, ``check_db_ownership`` u.a.) — vorher lag
sie im ``manage.py check_db_roles``-Command und wurde von hier per Lazy-Import
konsumiert (invertierte Abhaengigkeit: ein Service importierte aus
``core.management.commands``, der einzige solche Import im Nicht-Test-Code).
``core/management/commands/check_db_roles.py`` re-exportiert die vier
oeffentlichen Namen (``RoleCheck``, ``check_db_roles``, ``check_db_ownership``,
``current_connection_bypasses_rls``) fuer den CLI-Aufruf und behaelt nur noch
``handle()``/Exit-Codes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from django.conf import settings
from django.db import connection

from core.services.compliance._types import ComplianceCheck, ComplianceStatus
from core.utils.migration_guards import connection_bypasses_rls


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

    Die eigentliche Abfrage liegt seit Refs #1746 in
    :func:`core.utils.migration_guards.connection_bypasses_rls` — dieselbe Quelle
    nutzt der In-Migration-Guard ``assert_migration_role_bypasses_rls``, damit
    Deploy-Gate und Migrations-Guard nicht auseinanderlaufen.
    """
    return connection_bypasses_rls(connection)


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

    Geprueft wird beides: die App-Rolle darf **nicht** Owner sein, und die
    Admin-Rolle **muss** es sein (Refs #2086 — vorher fiel ein Owner, der weder
    App noch Admin ist, durch; genau das hinterlaesst ein unvollstaendiger
    § 9.6-Umbau einer Bootstrap-Topologie).

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
    # Refs #2086: Bis dahin wurde nur gegen die App-Rolle verglichen — das Gate
    # bewies „App ist NICHT Owner", nicht „Admin IST Owner". Nach dem Umbau
    # einer alten Bootstrap-Topologie (Runbook § 9.6 Fall C) gehoeren Altobjekte
    # weiterhin der Bootstrap-Rolle: weder App noch Admin. Das war still gruen,
    # obwohl die Admin-Rolle die Append-Only-Trigger dann gar nicht per
    # Owner-DDL bedienen kann. ``None`` heisst „Objekt existiert noch nicht"
    # (Erstinbetriebnahme vor dem ersten ``migrate``) und ist kein Fund —
    # docker-migrate.sh fuehrt dieses Gate VOR dem Migrationslauf aus.
    db_owner = _query_db_owner()
    if db_owner is not None and db_owner != admin_role:
        if db_owner == app_role:
            problems.append(
                f"Datenbank-Owner ist die App-Runtime-Rolle {app_role!r} — im "
                f"Drei-Rollen-Modell (A1.4) muss die Admin-/Wartungs-Rolle {admin_role!r} "
                "Owner sein."
            )
        else:
            problems.append(
                f"Datenbank-Owner ist {db_owner!r} — im Drei-Rollen-Modell (A1.4) "
                f"muss die Admin-/Wartungs-Rolle {admin_role!r} Owner sein."
            )
    for table in _APPEND_ONLY_TABLES:
        table_owner = _query_table_owner(table)
        if table_owner is None or table_owner == admin_role:
            continue
        if table_owner == app_role:
            problems.append(
                f"Append-Only-Tabelle {table!r} gehoert der App-Runtime-Rolle "
                f"{app_role!r} — als Owner koennte sie die Immutability-Trigger per "
                "'ALTER TABLE ... DISABLE TRIGGER' abschalten (A1.4)."
            )
        else:
            problems.append(
                f"Append-Only-Tabelle {table!r} gehoert {table_owner!r} — weder der "
                f"App- noch der Admin-Rolle. Owner muss {admin_role!r} sein, sonst "
                "kann der Wartungspfad die Immutability-Trigger nicht bedienen (A1.4)."
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


def _db_role_checks() -> list[ComplianceCheck]:
    """Drei Checks: App-NOSUPERUSER, App-NOBYPASSRLS, Admin-Rolle vorhanden."""
    role_checks, config_errors = check_db_roles()
    out: list[ComplianceCheck] = []

    for rc in role_checks:
        if rc.label == "App":
            # Zwei Checks aus einer Rolle (rolsuper, rolbypassrls).
            out.append(_db_role_attribute_check(rc, "rolsuper", "db_role_app_nosuperuser", "App-DB-Rolle NOSUPERUSER"))
            out.append(
                _db_role_attribute_check(rc, "rolbypassrls", "db_role_app_nobypassrls", "App-DB-Rolle kein BYPASSRLS")
            )
        elif rc.label == "Admin":
            out.append(_db_role_admin_check(rc))

    # Config-Errors (z.B. POSTGRES_ADMIN_USER fehlt): ob das ein Befund oder
    # nur die dokumentierte Secret-Grenze ist, entscheidet die Prozessrolle —
    # siehe :func:`_db_role_admin_missing_check` (Refs #2310).
    if config_errors and not any(rc.label == "Admin" for rc in role_checks):
        out.append(_db_role_admin_missing_check(config_errors))

    return out


# Refs #1962: Der ``web``-Dienst in docker-compose.prod.yml bekommt eine
# explizite Environment-Allowlist OHNE ``POSTGRES_ADMIN_USER``/
# ``POSTGRES_ADMIN_PASSWORD`` — die Admin-Zugangsdaten liegen absichtlich nur
# im ``maintenance``-Pfad. ``ANLAUFSTELLE_PROCESS_ROLE`` trennt die beiden
# Prozessrollen; der Default folgt ``anlaufstelle/settings/prod.py``
# ("runtime"), damit Dev-/Testlaeufe ohne gesetzte Variable wie die Web-Runtime
# bewertet werden.
_MAINTENANCE_PROCESS_ROLE = "maintenance"  # pragma: no mutate


def _runs_as_maintenance_process() -> bool:
    """True, wenn dieser Prozess der Wartungsdienst ist (Refs #2310)."""
    process_role = os.environ.get("ANLAUFSTELLE_PROCESS_ROLE", "runtime")  # pragma: no mutate
    return process_role.strip().lower() == _MAINTENANCE_PROCESS_ROLE


def _db_role_admin_missing_check(config_errors: list[str]) -> ComplianceCheck:
    """Karte "keine Admin-/Maintenance-Rolle konfiguriert" (Refs #919, #2310).

    Refs #2310: Bis 0.99.1 war das immer eine Warnung — und damit auf **jeder**
    korrekt nach Anleitung installierten Instanz eine stehende gelbe Karte, denn
    im Web-Container fehlen die Admin-Zugangsdaten per Deployment-Design
    (Refs #1962). Die Rollenpruefung selbst laeuft im ``maintenance``-Dienst
    (``manage.py check_db_roles``) und ist dort gruen; das Dashboard sieht deren
    Ergebnis nicht. Eine Ampel, die dauerhaft gelb steht, wird ignoriert.

    Deshalb: in der **Web-Runtime** ist "nicht konfiguriert" nicht
    unterscheidbar von "nicht sichtbar" — der Status ist ``unknown`` (neutral),
    der Text nennt den Wartungsdienst als Pruefweg. Im **Wartungs-Kontext**, wo
    die Zugangsdaten vorhanden sein MUESSEN, bleibt es eine echte Warnung.

    Der Deploy-Gate-Pfad ist davon unberuehrt: ``manage.py check_db_roles``
    wertet ``check_db_roles()`` direkt aus und bricht weiterhin mit Exit 2 ab,
    wenn die Admin-Rolle nicht konfiguriert ist.
    """
    if _runs_as_maintenance_process():
        return ComplianceCheck(
            key="db_role_admin_missing",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.WARNING,
            message="Admin-/Maintenance-Rolle nicht konfiguriert.",  # pragma: no mutate
            detail="; ".join(config_errors),
            action_hint=(
                "POSTGRES_ADMIN_USER und POSTGRES_ADMIN_PASSWORD in der .env setzen "
                "(Drei-Rollen-Modell, docs/ops-runbook.md § 9)."
            ),  # pragma: no mutate
        )
    return ComplianceCheck(
        key="db_role_admin_missing",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.UNKNOWN,
        message="Im Web-Container nicht prüfbar — Prüfung über den Wartungsdienst.",  # pragma: no mutate
        detail="; ".join(config_errors),
        action_hint=(
            "Im web-Container nicht pruefbar — Prüfung über "
            "docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles."
        ),  # pragma: no mutate
    )


def _db_role_attribute_check(role_check, attr: str, key: str, label: str) -> ComplianceCheck:
    """Hilfsfunktion: ein einzelnes Attribut der App-Rolle (rolsuper / rolbypassrls)."""
    expected = False  # App-Rolle: beide Attribute False
    actual = getattr(role_check, f"actual_{attr.replace('rol', '')}")
    if actual is None:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if actual == expected:
        return ComplianceCheck(
            key=key,
            label=label,
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Korrekt konfiguriert.",  # pragma: no mutate
            detail=f"{role_check.role}: {attr}={actual}",
        )
    return ComplianceCheck(
        key=key,
        label=label,
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message=f"Rolle '{role_check.role}' hat falsches Attributprofil.",  # pragma: no mutate
        detail=f"{attr}={actual} (erwartet: {expected})",
        action_hint="manage.py check_db_roles + Rollen-Init-Script pruefen (Refs #902).",  # pragma: no mutate
    )


def _db_role_admin_check(role_check) -> ComplianceCheck:
    """Admin-Rolle: NOSUPERUSER + BYPASSRLS erwartet."""
    if role_check.actual_super is None:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Admin-Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if role_check.ok:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category="Datenbank",  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS).",  # pragma: no mutate
            detail=f"{role_check.role}: rolsuper={role_check.actual_super}, rolbypassrls={role_check.actual_bypassrls}",
        )
    return ComplianceCheck(
        key="db_role_admin",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category="Datenbank",  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message="Admin-Rolle hat falsches Attributprofil.",  # pragma: no mutate
        detail="; ".join(role_check.problems()),
        action_hint="deploy/postgres-init/01-app-role.sh pruefen (Refs #902).",  # pragma: no mutate
    )
