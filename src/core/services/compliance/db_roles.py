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
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.audit import audit_event
from core.services.compliance import _clock
from core.services.compliance._types import ComplianceCategory, ComplianceCheck, ComplianceStatus
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


# ---------------------------------------------------------------------------
# DB_ROLES_VERIFIED marker (Refs #2415)
# ---------------------------------------------------------------------------
# The web container cannot check the admin role (no credentials, Refs #1962).
# ``manage.py check_db_roles`` runs in the maintenance service on every deploy
# and records its outcome as a system AuditLog row; the dashboard card reads
# the latest one. The row is append-only and HMAC-chained, so it carries only
# the fixed vocabularies below — never a role name, a password or a DSN.

DB_ROLES_RESULT_OK = "ok"
DB_ROLES_RESULT_PROBLEMS = "problems"
DB_ROLES_RESULT_INCOMPLETE = "incomplete"
DB_ROLES_RESULTS = (DB_ROLES_RESULT_OK, DB_ROLES_RESULT_PROBLEMS, DB_ROLES_RESULT_INCOMPLETE)

DB_ROLES_PROBLEM_CODES = (
    "app_role_not_configured",
    "app_role_missing",
    "app_role_superuser",
    "app_role_bypassrls",
    "admin_role_not_configured",
    "admin_role_missing",
    "admin_role_superuser",
    "admin_role_no_bypassrls",
    "ownership",
)

# (RoleCheck.label, code for "not configured", "missing", "rolsuper differs", "rolbypassrls differs")
_ROLE_CODES = (
    ("App", "app_role_not_configured", "app_role_missing", "app_role_superuser", "app_role_bypassrls"),
    ("Admin", "admin_role_not_configured", "admin_role_missing", "admin_role_superuser", "admin_role_no_bypassrls"),
)

_MARKER_TARGET_TYPE = "DbRoleCheck"  # pragma: no mutate


def db_roles_result(checks: list[RoleCheck], ownership_problems: list[str]) -> tuple[str, list[str]]:
    """Map one ``check_db_roles`` run onto ``(result, problem codes)``.

    ``ok`` only when both roles were configured, found and correct and the
    ownership check is clean. A role that could not be checked at all (its
    name is not configured) makes the run ``incomplete`` — the command exits 0
    in that case when the app role is fine, which must not read as verified.
    Any wrong or missing role and any ownership finding make it ``problems``.
    """
    by_label = {check.label: check for check in checks}
    findings: list[str] = []
    unconfigured: list[str] = []
    for label, not_configured, missing, superuser, bypassrls in _ROLE_CODES:
        check = by_label.get(label)
        if check is None:
            unconfigured.append(not_configured)
            continue
        if not check.exists:
            findings.append(missing)
            continue
        if check.actual_super != check.expected_super:
            findings.append(superuser)
        if check.actual_bypassrls != check.expected_bypassrls:
            findings.append(bypassrls)
    if ownership_problems:
        findings.append("ownership")
    if findings:
        return DB_ROLES_RESULT_PROBLEMS, findings + unconfigured
    if unconfigured:
        return DB_ROLES_RESULT_INCOMPLETE, unconfigured
    return DB_ROLES_RESULT_OK, []


def record_db_roles_marker(checks: list[RoleCheck], ownership_problems: list[str]) -> str | None:
    """Write the ``DB_ROLES_VERIFIED`` marker; return why it was skipped, or ``None``.

    Never raises for the expected deployment states, because the caller is
    the deploy gate and its exit code must stay the role result alone:

    * no AuditLog table yet — ``check_db_roles`` runs before the first
      ``migrate`` (``docker-migrate.sh``, ``scripts/initial-setup.sh``);
    * no RLS bypass on this connection and the result is ``ok`` — only the
      admin role (or a super-admin GUC) may vouch for a healthy topology.
      ``problems`` and ``incomplete`` are always written: the maintenance
      service connects as the admin role, so once that role loses BYPASSRLS
      the run reporting it has no bypass either, and its finding must still
      supersede an older green marker. A system row (``facility=None``) is
      allowed by the AuditLog insert policy without bypass, and such a row
      can only make the card worse, never green;
    * the INSERT fails — the gate runs before ``migrate``, so the AuditLog
      columns of the new code may not exist yet. The failure is reported and
      the dashboard simply keeps the previous marker.
    """
    if AuditLog._meta.db_table not in connection.introspection.table_names():
        return "DB_ROLES_VERIFIED marker skipped: the AuditLog table does not exist yet (run migrate first)."
    # Lazy import: core.services.system imports core.services.compliance.
    from core.services.system import has_rls_bypass_context

    result, problems = db_roles_result(checks, ownership_problems)
    if result == DB_ROLES_RESULT_OK and not has_rls_bypass_context():
        return (
            "DB_ROLES_VERIFIED marker skipped: an ok result needs a connection that bypasses RLS "
            "(run check_db_roles in the maintenance service)."
        )
    try:
        with transaction.atomic():
            audit_event(
                AuditLog.Action.DB_ROLES_VERIFIED,
                user=None,
                facility=None,
                target_type=_MARKER_TARGET_TYPE,
                detail={"result": result, "problems": problems},
            )
    except DatabaseError as exc:
        # Class name only: the driver message may echo SQL parameters.
        return f"DB_ROLES_VERIFIED marker not written ({exc.__class__.__name__}); the role check result is unaffected."
    return None


def _latest_db_roles_marker() -> AuditLog | None:
    """Latest system-wide ``DB_ROLES_VERIFIED`` row; facility-bound rows never count."""
    return (
        AuditLog.objects.filter(action=AuditLog.Action.DB_ROLES_VERIFIED, facility__isnull=True)
        .order_by("-timestamp")
        .only("timestamp", "detail")
        .first()
    )


def _db_role_marker_check(entry) -> ComplianceCheck:
    """Admin role card from the latest maintenance result (Refs #2415).

    Green only for a fresh row whose result is exactly ``ok`` with no problem
    codes. Anything this function does not recognise is critical: the row can
    only come from ``record_db_roles_marker``, so a malformed one is itself a
    finding, never a pass.
    """
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    result = detail.get("result")
    problems = detail.get("problems")
    codes_readable = isinstance(problems, list) and all(isinstance(code, str) for code in problems)
    checked_at = f"{timezone.localtime(entry.timestamp):%Y-%m-%d %H:%M}"
    checked = _("Geprüft am %(checked_at)s mit manage.py check_db_roles im Wartungsdienst.") % {
        "checked_at": checked_at
    }
    findings = ", ".join(problems) if codes_readable and problems else "—"
    rerun_hint = _("docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles erneut ausführen.")

    if result == DB_ROLES_RESULT_OK and problems == []:
        age = _clock.now() - entry.timestamp
        if age <= timedelta(days=ops_thresholds.DB_ROLES_CHECK_WARN_DAYS):
            return ComplianceCheck(
                key="db_role_admin",
                label="Admin-DB-Rolle",  # pragma: no mutate
                category=ComplianceCategory.DATABASE,  # pragma: no mutate
                status=ComplianceStatus.OK,
                message=_("Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS), App-Rolle und Ownership ebenfalls."),
                detail=checked,
            )
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.WARNING,
            message=_("Letzte Rollenprüfung ist älter als %(days)s Tage.")
            % {"days": ops_thresholds.DB_ROLES_CHECK_WARN_DAYS},
            detail=checked,
            action_hint=rerun_hint,
        )
    if result == DB_ROLES_RESULT_INCOMPLETE and codes_readable:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.WARNING,
            message=_("Rollenprüfung unvollständig: nicht jede Rolle ist konfiguriert."),
            detail=_("%(checked)s Befunde: %(findings)s") % {"checked": checked, "findings": findings},
            action_hint=_(
                "POSTGRES_ADMIN_USER und POSTGRES_ADMIN_PASSWORD in der .env setzen "
                "(Drei-Rollen-Modell, docs/ops-runbook.md § 9)."
            ),
        )
    return ComplianceCheck(
        key="db_role_admin",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category=ComplianceCategory.DATABASE,  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message=_("Letzte Rollenprüfung meldet Befunde."),
        detail=_("%(checked)s Befunde: %(findings)s") % {"checked": checked, "findings": findings},
        action_hint=_("Ausgabe von manage.py check_db_roles im Wartungsdienst prüfen (docs/ops-runbook.md § 9)."),
    )


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
    # Refs #2415: in the web runtime the latest maintenance result stands in
    # for the check this process cannot run. The maintenance process must have
    # the credentials itself, so there the marker is never consulted.
    if config_errors and not any(rc.label == "Admin" for rc in role_checks):
        marker = None if _runs_as_maintenance_process() else _latest_db_roles_marker()
        out.append(_db_role_admin_missing_check(config_errors) if marker is None else _db_role_marker_check(marker))

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
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
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
        category=ComplianceCategory.DATABASE,  # pragma: no mutate
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
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if actual == expected:
        return ComplianceCheck(
            key=key,
            label=label,
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Korrekt konfiguriert.",  # pragma: no mutate
            detail=f"{role_check.role}: {attr}={actual}",
        )
    return ComplianceCheck(
        key=key,
        label=label,
        category=ComplianceCategory.DATABASE,  # pragma: no mutate
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
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.UNKNOWN,
            message=f"Admin-Rolle '{role_check.role}' nicht in pg_roles gefunden.",  # pragma: no mutate
            action_hint="DB-Rollen-Setup pruefen — siehe deploy/postgres-init/01-app-role.sh.",  # pragma: no mutate
        )
    if role_check.ok:
        return ComplianceCheck(
            key="db_role_admin",
            label="Admin-DB-Rolle",  # pragma: no mutate
            category=ComplianceCategory.DATABASE,  # pragma: no mutate
            status=ComplianceStatus.OK,
            message="Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS).",  # pragma: no mutate
            detail=f"{role_check.role}: rolsuper={role_check.actual_super}, rolbypassrls={role_check.actual_bypassrls}",
        )
    return ComplianceCheck(
        key="db_role_admin",
        label="Admin-DB-Rolle",  # pragma: no mutate
        category=ComplianceCategory.DATABASE,  # pragma: no mutate
        status=ComplianceStatus.CRITICAL,
        message="Admin-Rolle hat falsches Attributprofil.",  # pragma: no mutate
        detail="; ".join(role_check.problems()),
        action_hint="deploy/postgres-init/01-app-role.sh pruefen (Refs #902).",  # pragma: no mutate
    )
