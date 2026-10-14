"""Real dashboard branches and status pins across locales (Refs #2530)."""

import html
import re
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone, translation

from core.models import AuditLog, EncryptedTOTPDevice, Settings, User
from core.services import compliance
from core.services.compliance.db_roles import RoleCheck
from core.services.system import bypass_replication_triggers
from tests._job_run_helpers import SUCCESSFUL_JOB_RUN

# Each fixture exercises the real aggregate service; only external observations
# (clock, PostgreSQL role inspection, backup files and ClamAV) are substituted.
SCENARIOS = {
    "fresh": {},
    "missing": {"missing": True},
    "warn_boundary": {
        "daily_hours": 36,
        "restore_days": 90,
        "snapshot_days": 35,
        "scan_hours": 3,
        "refresh_hours": 2,
        "signature_days": 7,
    },
    "warning": {
        "daily_hours": 36.01,
        "restore_days": 91,
        "snapshot_days": 36,
        "scan_hours": 3.01,
        "refresh_hours": 2.01,
        "signature_days": 8,
        "events": 1,
        "mfa_count": 8,
        "approvers": 1,
    },
    "critical_boundary": {
        "daily_hours": 48,
        "restore_days": 180,
        "snapshot_days": 65,
        "scan_hours": 24,
        "refresh_hours": 6,
        "events": 5,
    },
    "critical": {
        "daily_hours": 48.01,
        "restore_days": 181,
        "snapshot_days": 66,
        "scan_hours": 24.01,
        "refresh_hours": 6.01,
        "events": 6,
        "mfa_count": 7,
        "approvers": 0,
        "offenders": 6,
        "pending": 6,
        "role_bad": True,
        "ping": False,
    },
    "future_backup": {"future": True},
    "signature_missing_online": {"signature_missing": True},
    "signature_unknown_date": {"signature_days": None},
    "roles_missing": {"role_absent": True},
    "admin_missing_runtime": {"admin_missing": True},
    "admin_missing_maintenance": {"admin_missing": True, "maintenance": True},
    "marker_fresh": {"marker": "ok"},
    "marker_boundary": {"marker": "ok", "marker_days": 30},
    "marker_stale": {"marker": "ok", "marker_days": 30.01},
    "marker_incomplete": {"marker": "incomplete"},
    "marker_failed": {"marker": "problems"},
    "marker_malformed": {"marker": "malformed"},
    "helper_error": {"helper_error": True},
    "demo_missing": {"demo": True, "missing": True},
    "demo_with_evidence": {"demo": True},
    "no_enforced_roles": {"no_enforced_roles": True},
    "finite_retention": {"finite": True},
}

BASE_STATUSES = {
    # This fixture gives the deletion right to staff, never to retention reviewers.
    "retention_approvers": "critical",
    "db_role_app_nosuperuser": "ok",
    "db_role_app_nobypassrls": "ok",
    "db_role_admin": "ok",
    "app_user_no_django_superuser": "ok",
    "backup_age": "ok",
    "clamav_reachable": "ok",
    "clamav_signature": "ok",
    "retention_last_run": "ok",
    "auditlog_retention_unlimited": "warning",
    "audit_chain_last_run": "ok",
    "snapshot_last_run": "ok",
    "breach_scan_last_run": "ok",
    "mv_refresh_last_run": "ok",
    "restore_verified": "ok",
    "mfa_privileged_quote": "ok",
    "deletion_approvers": "ok",
    "migrations_pending": "ok",
    "versions": "ok",
    "critical_audit_events_24h": "ok",
}

STATUS_OVERRIDES = {
    "missing": dict.fromkeys(
        (
            "backup_age",
            "clamav_signature",
            "retention_last_run",
            "audit_chain_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
            "restore_verified",
        ),
        "unknown",
    )
    | {"clamav_reachable": "critical"},
    "warning": dict.fromkeys(
        (
            "backup_age",
            "clamav_signature",
            "retention_last_run",
            "audit_chain_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
            "restore_verified",
            "mfa_privileged_quote",
            "deletion_approvers",
            "critical_audit_events_24h",
        ),
        "warning",
    ),
    "critical_boundary": dict.fromkeys(
        (
            "backup_age",
            "retention_last_run",
            "audit_chain_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
            "restore_verified",
            "critical_audit_events_24h",
        ),
        "warning",
    ),
    "critical": dict.fromkeys(
        (
            "db_role_app_nosuperuser",
            "db_role_app_nobypassrls",
            "db_role_admin",
            "app_user_no_django_superuser",
            "backup_age",
            "clamav_reachable",
            "retention_last_run",
            "audit_chain_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
            "restore_verified",
            "mfa_privileged_quote",
            "deletion_approvers",
            "migrations_pending",
            "critical_audit_events_24h",
        ),
        "critical",
    )
    | {"clamav_signature": "ok"},
    "future_backup": {"backup_age": "warning"},
    "signature_missing_online": {"clamav_signature": "unknown"},
    "signature_unknown_date": {"clamav_signature": "unknown"},
    "roles_missing": dict.fromkeys(("db_role_app_nosuperuser", "db_role_app_nobypassrls", "db_role_admin"), "unknown"),
    "admin_missing_runtime": {"db_role_admin": None, "db_role_admin_missing": "unknown"},
    "admin_missing_maintenance": {"db_role_admin": None, "db_role_admin_missing": "warning"},
    "marker_stale": {"db_role_admin": "warning"},
    "marker_incomplete": {"db_role_admin": "warning"},
    "marker_failed": {"db_role_admin": "critical"},
    "marker_malformed": {"db_role_admin": "critical"},
    "helper_error": {"versions": None, "_internal__version_checks": "unknown"},
    "demo_missing": dict.fromkeys(
        (
            "backup_age",
            "clamav_signature",
            "retention_last_run",
            "audit_chain_last_run",
            "snapshot_last_run",
            "breach_scan_last_run",
            "mv_refresh_last_run",
            "restore_verified",
        ),
        "unknown",
    )
    | {"clamav_reachable": "critical"},
    "demo_with_evidence": dict.fromkeys(("backup_age", "retention_last_run", "restore_verified"), "unknown"),
    "no_enforced_roles": {"mfa_privileged_quote": "unknown"},
    "finite_retention": {"auditlog_retention_unlimited": None},
}


def _expected_statuses(scenario):
    expected = BASE_STATUSES | STATUS_OVERRIDES.get(scenario, {})
    return {key: value for key, value in expected.items() if value is not None}


@pytest.fixture(params=SCENARIOS)
def dashboard_scenario(request, settings, monkeypatch, facility, super_admin_user):
    scenario = request.param
    options = SCENARIOS[scenario]
    facility.name = "Example facility"
    facility.save(update_fields=["name"])
    Settings.objects.create(facility=facility, auditlog_retention_months=24 if options.get("finite") else 0)
    settings.DEMO_MODE = options.get("demo", False)
    settings.MFA_ENFORCED_ROLES_IMMEDIATE = () if options.get("no_enforced_roles") else (User.Role.SUPER_ADMIN,)
    settings.MFA_ENFORCED_ROLES_GRACE = ()
    monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", "maintenance" if options.get("maintenance") else "runtime")
    for index in range(options.get("approvers", 2)):
        User.objects.create_user(
            username=f"approver{index}", facility=facility, role=User.Role.STAFF, can_confirm_deletion=True
        )
    users = [super_admin_user] + [
        User.objects.create_user(username=f"privileged{index}", role=User.Role.SUPER_ADMIN) for index in range(9)
    ]
    for user in users[: options.get("mfa_count", 10)]:
        EncryptedTOTPDevice.objects.create(user=user, confirmed=True)
    for index in range(options.get("offenders", 0)):
        user = User.objects.create_user(username=f"offender{index}", facility=facility, role=User.Role.STAFF)
        # Reproduce corrupted legacy data without the model's write-time guard.
        User.objects.filter(pk=user.pk).update(is_superuser=True)

    now = timezone.now()
    markers = [
        (AuditLog.Action.RETENTION_RUN_COMPLETED, options.get("daily_hours", 1)),
        (AuditLog.Action.AUDIT_CHAIN_VERIFIED, options.get("daily_hours", 1)),
        (AuditLog.Action.SNAPSHOT_RUN_COMPLETED, options.get("snapshot_days", 1) * 24),
        (AuditLog.Action.BREACH_SCAN_COMPLETED, options.get("scan_hours", 1)),
        (AuditLog.Action.MV_REFRESH_COMPLETED, options.get("refresh_hours", 1)),
        (AuditLog.Action.RESTORE_VERIFIED, options.get("restore_days", 1) * 24),
    ]
    if not options.get("missing"):
        for action, age_hours in markers:
            entry = AuditLog.objects.create(
                action=action,
                facility=None,
                target_type="MaintenanceResult",
                detail={"job_run": SUCCESSFUL_JOB_RUN, **{"note": "Example note"}},
            )
            with bypass_replication_triggers():
                AuditLog.objects.filter(pk=entry.pk).update(timestamp=now - timedelta(hours=age_hours))
    for _ in range(options.get("events", 0)):
        AuditLog.objects.create(action=AuditLog.Action.MFA_FAILED, facility=None, target_type="User")
    marker = options.get("marker")
    if marker:
        detail = {"result": marker, "problems": [] if marker == "ok" else ["app_role_superuser"]}
        if marker == "malformed":
            detail = ["invalid"]
        entry = AuditLog.objects.create(
            action=AuditLog.Action.DB_ROLES_VERIFIED, facility=None, target_type="DBRolesVerification", detail=detail
        )
        with bypass_replication_triggers():
            AuditLog.objects.filter(pk=entry.pk).update(timestamp=now - timedelta(days=options.get("marker_days", 1)))

    absent = options.get("role_absent")
    bad = options.get("role_bad", False)
    roles = [RoleCheck("app_role", "App", False, False, None if absent else bad, None if absent else bad)]
    monkeypatch.setenv("POSTGRES_APP_USER", "app_role")
    monkeypatch.setenv("POSTGRES_ADMIN_USER", "" if options.get("admin_missing") or marker else "admin_role")
    roles.append(RoleCheck("admin_role", "Admin", False, True, None if absent else bad, None if absent else not bad))
    observed = {role.role: (role.actual_super, role.actual_bypassrls) for role in roles}
    backup = (
        None
        if options.get("missing")
        else {
            "path": "backup.sql.gz",
            "mtime": now,
            "age_hours": options.get("daily_hours", 1),
            "in_future": options.get("future", False),
        }
    )
    signature = (
        None
        if options.get("missing") or options.get("signature_missing")
        else {
            "version": None if options.get("signature_days", 1) is None else "1.4",
            "age_days": options.get("signature_days", 1),
        }
    )
    with ExitStack() as stack:
        stack.enter_context(patch("core.services.compliance._clock.now", return_value=now))
        stack.enter_context(patch("core.services.compliance.db_roles._query_role", side_effect=observed.__getitem__))
        stack.enter_context(patch("core.services.system.health.last_backup_info", return_value=backup))
        stack.enter_context(
            patch(
                "core.services.file_vault.virus_scan.ping",
                return_value=options.get("ping", not options.get("missing", False)),
            )
        )
        stack.enter_context(patch("core.services.file_vault.virus_scan.signature_info", return_value=signature))
        stack.enter_context(
            patch(
                "core.services.system.health.pending_migrations",
                return_value=[("core", f"pending_{index}") for index in range(options.get("pending", 0))],
            )
        )
        stack.enter_context(
            patch(
                "core.services.system.health.app_versions",
                return_value={"app_version": "1.0", "django_version": "6.1", "python_version": "3.14"},
            )
        )
        if options.get("helper_error"):

            def _version_checks():
                raise RuntimeError("Example failure")

            stack.enter_context(patch.object(compliance, "_version_checks", _version_checks))
        yield scenario


@pytest.mark.django_db
def test_german_card_statuses_match_original_thresholds(dashboard_scenario):
    with translation.override("de"):
        checks = compliance.aggregate_checks()
    assert {check.key: check.status.value for check in checks} == _expected_statuses(dashboard_scenario)


# Include variable/helper/detail vocabulary, not just titles. Technical role
# names, command flags and action codes are intentionally absent from this list.
GERMAN_WORDS = re.compile(
    r"\b(?:Datenbank|Hintergrundjobs|Berechtigungen|Warnung|Kritisch|Unbekannt|"
    r"Letzte[rsn]?|Lauf|Läufe|Laeufe|erwartet|Einrichtung\w*|Genehmiger|Lösch\w*|"
    r"pr[üu]e?fen|Pr[üu]e?fung|ausgef[üu]e?hrt|ausf[üu]e?hren|"
    r"Kein[ae]?|Keine|Noch|nicht|konfiguriert|vorhanden|gefunden|Rolle\w*|"
    r"Attributprofil|erreichbar|Signatur(?!es?\b)\w*|Schwelle|Notiz|Gemonitorte|Aktionen|"
    r"kritische|Tag\w*|Tagen|Monat\w*|stündlich|taeglich|täglich|"
    r"angewendet|ausstehende|weitere|Versionen|Migrationen|Letztes|"
    r"Interner|Fehler|einsehen|Empfohlen|unbekannt|unbegrenzt|Aufbewahrung|"
    r"Verwaltung|Zugänge|Einstellungen|erzeugen|erzeugt|eingerichtet|"
    r"Lücke|schliessen|Risiko|Nur|haben|traegt|ausgefallen|Auffrischung|"
    r"Zeitstempel|Alter|Zukunft|messbar|antwortet|deaktiviert|abrufbar|ermittelbar|"
    r"Quelle|Aktualisierung|jedem|mit|filtern|Vorfall|Meldung|Aufsichtsbehörde|nachholen|dokumentieren|Statistik|"
    r"Verbleibende|Aktivierung|anhalten|aktivieren|erzwingen|oder)\b",
    re.IGNORECASE,
)


@pytest.fixture
def english_dashboard_response(client, super_admin_user, dashboard_scenario):
    super_admin_user.preferred_language = "en"
    super_admin_user.save(update_fields=["preferred_language"])
    client.force_login(super_admin_user)
    session = client.session
    session["mfa_verified"] = True
    session.save()
    with translation.override("en"):
        return client.get(reverse("core:system_compliance"))


@pytest.mark.django_db
def test_english_request_renders_all_expected_cards(english_dashboard_response, dashboard_scenario):
    response = english_dashboard_response
    assert response.status_code == 200
    assert response.wsgi_request.LANGUAGE_CODE == "en"
    content = response.content.decode()
    cards = re.findall(r'data-testid="compliance-check-([^\"]+)" data-status="([^\"]+)"', content)
    assert len(cards) == len(_expected_statuses(dashboard_scenario))
    assert dict(cards) == _expected_statuses(dashboard_scenario)


@pytest.mark.django_db
def test_english_request_has_no_german_card_text(english_dashboard_response):
    content = english_dashboard_response.content.decode()
    sections = re.findall(r'<section[^>]+data-testid="compliance-group-[^\"]+"[^>]*>(.*?)</section>', content, re.S)
    assert sections
    footnote = re.search(r"<p[^>]*>\s*[^<]*<code>core/services/compliance/</code>.*?</p>", content, re.S)
    assert footnote
    text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(sections) + footnote.group()))
    assert not GERMAN_WORDS.findall(text), GERMAN_WORDS.findall(text)


@pytest.mark.django_db
def test_card_text_uses_active_translation_backend(dashboard_scenario):
    """Exercise all branches without depending on the pending English catalog."""

    def translate(message):
        return "Translated " + " ".join(re.findall(r"%\([a-zA-Z0-9_]+\)s", message))

    with translation.override("en"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        checks = compliance.aggregate_checks()
        for check in checks:
            text = " ".join(
                str(value)
                for value in (check.label, check.message, check.detail, check.action_hint)
                if value is not None
            )
            assert not GERMAN_WORDS.findall(text), (check.key, text)
    assert {check.key: check.status.value for check in checks} == _expected_statuses(dashboard_scenario)


@pytest.mark.django_db
@pytest.mark.parametrize("dashboard_scenario", ["fresh"], indirect=True)
def test_full_mfa_quote_formats_translated_percentage(dashboard_scenario):
    def translate(message):
        if message.endswith("der MFA-pflichtigen Rollen haben MFA aktiv."):
            return "All %(percent)s %% of required roles have MFA active."
        return message

    with translation.override("en"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        card = next(check for check in compliance.aggregate_checks() if check.key == "mfa_privileged_quote")
    assert card.status == compliance.ComplianceStatus.OK
    assert card.message == "All 100 % of required roles have MFA active."
    with translation.override("de"):
        card = next(check for check in compliance.aggregate_checks() if check.key == "mfa_privileged_quote")
    assert card.message == "100 % der MFA-pflichtigen Rollen haben MFA aktiv."


@pytest.mark.django_db
@pytest.mark.parametrize("dashboard_scenario", ["fresh", "critical", "future_backup"], indirect=True)
def test_catalog_receives_readable_placeholders(dashboard_scenario):
    messages = []

    def translate(message):
        messages.append(message)
        return message

    with translation.override("de"), patch("django.utils.translation._trans.gettext", side_effect=translate):
        cards = compliance.aggregate_checks()
    assert {card.key: card.status.value for card in cards} == _expected_statuses(dashboard_scenario)
    names = {name for message in messages for name in re.findall(r"%\(([^)]+)\)s", message)}
    assert not {
        name
        for name in names
        if name.startswith(
            ("entry_", "ops_thresholds_", "self_", "role_check_", "versions_", "len_", "value_", "info_")
        )
    }
    assert all(not message.startswith(" ") for message in messages)
