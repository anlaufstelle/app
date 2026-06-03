"""Tests for the ``check_db_roles`` management command (Refs #902).

Wir testen vor allem die Logik in :func:`check_db_roles`, nicht den
Postgres-Roundtrip — letzteres wuerde voraussetzen, dass der Test-Cluster
mit den prod-aehnlichen Rollen geseedet ist, was in pytest/Django nicht
selbstverstaendlich ist. Stattdessen mocken wir ``_query_role`` und
verifizieren, dass die Klassifikation (App=NOSUPERUSER/NOBYPASSRLS,
Admin=NOSUPERUSER/BYPASSRLS) korrekt ausgewertet wird.
"""

from __future__ import annotations

import pytest

from core.management.commands import check_db_roles as cmd


class TestRoleCheckDataclass:
    def test_ok_when_expected_matches_actual(self):
        check = cmd.RoleCheck(
            role="app",
            label="App",
            expected_super=False,
            expected_bypassrls=False,
            actual_super=False,
            actual_bypassrls=False,
        )
        assert check.exists
        assert check.ok
        assert check.problems() == []

    def test_not_ok_when_super_mismatch(self):
        check = cmd.RoleCheck(
            role="app",
            label="App",
            expected_super=False,
            expected_bypassrls=False,
            actual_super=True,
            actual_bypassrls=False,
        )
        assert check.exists
        assert not check.ok
        problems = check.problems()
        assert len(problems) == 1
        assert "rolsuper=True" in problems[0]

    def test_not_ok_when_bypass_mismatch(self):
        check = cmd.RoleCheck(
            role="app",
            label="App",
            expected_super=False,
            expected_bypassrls=False,
            actual_super=False,
            actual_bypassrls=True,
        )
        assert not check.ok
        assert any("rolbypassrls=True" in p for p in check.problems())

    def test_admin_role_expects_bypassrls(self):
        check = cmd.RoleCheck(
            role="admin",
            label="Admin",
            expected_super=False,
            expected_bypassrls=True,
            actual_super=False,
            actual_bypassrls=True,
        )
        assert check.ok

    def test_admin_without_bypass_is_failure(self):
        check = cmd.RoleCheck(
            role="admin",
            label="Admin",
            expected_super=False,
            expected_bypassrls=True,
            actual_super=False,
            actual_bypassrls=False,
        )
        assert not check.ok
        assert any("rolbypassrls=False" in p for p in check.problems())

    def test_nonexistent_role_is_failure(self):
        check = cmd.RoleCheck(
            role="ghost",
            label="App",
            expected_super=False,
            expected_bypassrls=False,
            actual_super=None,
            actual_bypassrls=None,
        )
        assert not check.exists
        assert not check.ok
        assert "existiert nicht" in check.problems()[0]


class TestCheckDbRolesFunction:
    """Pruefe die Top-Level-Auswertung mit gemocktem ``_query_role``."""

    def test_app_role_correctly_classified_when_secure(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, False),
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, config_errors = cmd.check_db_roles()
        assert config_errors == []
        assert len(checks) == 2
        assert all(c.ok for c in checks)

    def test_app_role_as_superuser_is_flagged(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (True, False),  # <-- problematisch
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, _ = cmd.check_db_roles()
        app_check = next(c for c in checks if c.role == "anlaufstelle_app")
        assert not app_check.ok
        assert any("rolsuper=True" in p for p in app_check.problems())

    def test_app_role_with_bypassrls_is_flagged(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, True),  # <-- problematisch
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, _ = cmd.check_db_roles()
        app_check = next(c for c in checks if c.role == "anlaufstelle_app")
        assert not app_check.ok
        assert any("rolbypassrls=True" in p for p in app_check.problems())

    def test_admin_role_without_bypass_is_flagged(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, False),
                "anlaufstelle_admin": (False, False),  # <-- Admin ohne BYPASSRLS
            }[role],
        )

        checks, _ = cmd.check_db_roles()
        admin_check = next(c for c in checks if c.role == "anlaufstelle_admin")
        assert not admin_check.ok

    def test_missing_admin_env_is_config_error_but_not_fatal(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.setattr(cmd, "_query_role", lambda role: (False, False))

        checks, config_errors = cmd.check_db_roles()
        # App-Check existiert weiterhin, Admin-Check entfaellt.
        assert len(checks) == 1
        assert checks[0].label == "App"
        # Admin-Fehlen kommt als Konfigurationswarnung, kein Hard-Fail.
        assert any("POSTGRES_ADMIN_USER" in msg for msg in config_errors)

    def test_nonexistent_role_appears_as_failure(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "ghost_user"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "ghost_admin")
        monkeypatch.setattr(cmd, "_query_role", lambda role: (None, None))

        checks, _ = cmd.check_db_roles()
        assert len(checks) == 2
        assert all(not c.exists for c in checks)
        assert all(not c.ok for c in checks)


class TestCommandExitCodes:
    """Smoke: management-command exit-codes via SystemExit."""

    def test_exit_0_when_all_ok(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {"app": (False, False), "admin": (False, True)}[role],
        )

        from django.core.management import call_command

        # Bei Erfolg darf das Kommando ohne Exception zurueckkehren.
        call_command("check_db_roles")  # kein SystemExit

    def test_exit_1_when_role_misconfigured(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "app"}}
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {"app": (True, False), "admin": (False, True)}[role],
        )

        from django.core.management import call_command

        with pytest.raises(SystemExit) as exc:
            call_command("check_db_roles")
        assert exc.value.code == 1

    def test_exit_2_when_only_config_errors(self, monkeypatch, settings):
        # Weder App- noch Admin-Rolle ableitbar.
        settings.DATABASES = {"default": {}}
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)

        from django.core.management import call_command

        with pytest.raises(SystemExit) as exc:
            call_command("check_db_roles")
        assert exc.value.code == 2


class TestAppRoleIdentification:
    """Refs #1017: Der App-Rollenname muss aus ``POSTGRES_APP_USER`` kommen, damit
    der Migrate-Job (Connection als Admin via ``POSTGRES_USER``-Override, Refs
    #863) trotzdem die *echte* App-Rolle prueft statt der gerade verbundenen
    Admin-Rolle. Fallback bleibt ``settings.DATABASES['default']['USER']``."""

    def test_app_role_from_postgres_app_user_when_connection_is_admin(self, monkeypatch, settings):
        # Migrate-Kontext: settings USER ist auf den Admin-User ueberschrieben,
        # POSTGRES_APP_USER traegt die echte App-Rolle.
        settings.DATABASES = {"default": {"USER": "anlaufstelle_admin"}}
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, False),
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, config_errors = cmd.check_db_roles()
        assert config_errors == []
        app_check = next(c for c in checks if c.label == "App")
        # App-Check trifft die App-Rolle, NICHT den Admin-Connection-User:
        assert app_check.role == "anlaufstelle_app"
        assert app_check.ok
        assert all(c.ok for c in checks)

    def test_app_role_falls_back_to_settings_user_without_env(self, monkeypatch, settings):
        settings.DATABASES = {"default": {"USER": "anlaufstelle_app"}}
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, False),
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, _ = cmd.check_db_roles()
        app_check = next(c for c in checks if c.label == "App")
        assert app_check.role == "anlaufstelle_app"
        assert app_check.ok

    def test_app_misconfig_still_caught_in_migrate_context(self, monkeypatch, settings):
        # #902-Schutz bleibt: eine zu privilegierte App-Rolle wird auch im
        # Migrate-Kontext (Connection=Admin) ueber POSTGRES_APP_USER erkannt.
        settings.DATABASES = {"default": {"USER": "anlaufstelle_admin"}}
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            cmd,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (True, False),  # App faelschlich Superuser
                "anlaufstelle_admin": (False, True),
            }[role],
        )

        checks, _ = cmd.check_db_roles()
        app_check = next(c for c in checks if c.label == "App")
        assert app_check.role == "anlaufstelle_app"
        assert not app_check.ok
        assert any("rolsuper=True" in p for p in app_check.problems())
