"""Tests for the ``check_db_roles`` management command (Refs #902).

Wir testen vor allem die Logik in :func:`check_db_roles`, nicht den
Postgres-Roundtrip — letzteres wuerde voraussetzen, dass der Test-Cluster
mit den prod-aehnlichen Rollen geseedet ist, was in pytest/Django nicht
selbstverstaendlich ist. Stattdessen mocken wir ``_query_role`` und
verifizieren, dass die Klassifikation (App=NOSUPERUSER/NOBYPASSRLS,
Admin=NOSUPERUSER/BYPASSRLS) korrekt ausgewertet wird.

Refs #1835: die Rollen-Pruef-Logik (``RoleCheck``, ``check_db_roles``,
``check_db_ownership`` sowie die privaten Query-Helfer) lebt seither in
:mod:`core.services.compliance.db_roles`; der Command re-exportiert nur noch
die vier oeffentlichen Namen. ``cmd`` bleibt fuer alles, was ueber den
Command-Pfad laeuft (``call_command``, direkte Aufrufe der re-exportierten
Funktionen); die privaten Helfer (``_query_role``/``_query_db_owner``/
``_query_table_owner``) werden am Verwendungsort — dem Service-Modul, wo
``check_db_roles()``/``check_db_ownership()`` sie unqualifiziert nachschlagen
— gepatcht, sonst liefe der Monkeypatch ins Leere.
"""

from __future__ import annotations

import pytest

from core.management.commands import check_db_roles as cmd
from core.services.compliance import db_roles


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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.setattr(db_roles, "_query_role", lambda role: (False, False))

        checks, config_errors = cmd.check_db_roles()
        # App-Check existiert weiterhin, Admin-Check entfaellt.
        assert len(checks) == 1
        assert checks[0].label == "App"
        # Admin-Fehlen kommt als Konfigurationswarnung, kein Hard-Fail.
        assert any("POSTGRES_ADMIN_USER" in msg for msg in config_errors)

    def test_nonexistent_role_appears_as_failure(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "ghost_user")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "ghost_admin")
        monkeypatch.setattr(db_roles, "_query_role", lambda role: (None, None))

        checks, _ = cmd.check_db_roles()
        assert len(checks) == 2
        assert all(not c.exists for c in checks)
        assert all(not c.ok for c in checks)


@pytest.mark.django_db
class TestCommandExitCodes:
    """Smoke: management-command exit-codes via SystemExit.

    Database access: every run also writes the ``DB_ROLES_VERIFIED`` marker
    (Refs #2415).
    """

    def test_exit_0_when_all_ok(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "admin")
        monkeypatch.setattr(
            db_roles,
            "_query_role",
            lambda role: {"app": (False, False), "admin": (False, True)}[role],
        )
        # Ownership-Guard (Refs #1026) hier neutralisieren — dieser Test isoliert
        # die Rollen-ATTRIBUT-Logik; die Ownership-Assertion hat eigene Tests
        # (TestCheckDbOwnership) und wuerde sonst die reale Test-DB abfragen.
        monkeypatch.setattr(cmd, "check_db_ownership", lambda: [])

        from django.core.management import call_command

        # Bei Erfolg darf das Kommando ohne Exception zurueckkehren.
        call_command("check_db_roles")  # kein SystemExit

    def test_exit_1_when_role_misconfigured(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "admin")
        monkeypatch.setattr(
            db_roles,
            "_query_role",
            lambda role: {"app": (True, False), "admin": (False, True)}[role],
        )
        monkeypatch.setattr(cmd, "check_db_ownership", lambda: [])

        from django.core.management import call_command

        with pytest.raises(SystemExit) as exc:
            call_command("check_db_roles")
        assert exc.value.code == 1

    def test_exit_2_when_only_config_errors(self, monkeypatch, settings):
        # Weder App- noch Admin-Rolle ableitbar: USER-Key fehlt (statt Ganzobjekt-
        # Override), damit der Settings-Fallback in check_db_roles ins Leere greift.
        monkeypatch.delitem(settings.DATABASES["default"], "USER", raising=False)
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_admin")
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_admin")
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.setattr(
            db_roles,
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


class TestCheckDbOwnership:
    """Refs #1026 (A1.4, MUST-FIX): ``check_db_roles`` prueft nur die Rollen-
    ATTRIBUTE (rolsuper/rolbypassrls), NICHT die Ownership. Auf einer Bestands-DB
    (App-Rolle war vor A1.4 Owner) laeuft Migration 0107 (ENABLE ALWAYS) gruen
    durch — aber die App-Rolle bleibt Owner und kann die Append-Only-Trigger
    weiterhin per ``ALTER TABLE ... DISABLE TRIGGER`` abschalten (stiller
    Fail-open). ``check_db_ownership`` schliesst die Luecke fail-loud.

    Wir mocken ``_query_db_owner``/``_query_table_owner`` (kein Postgres-
    Roundtrip), analog zum ``_query_role``-Muster der uebrigen Tests.
    """

    def _patch_owners(self, monkeypatch, *, db_owner: str, table_owner: str):
        monkeypatch.setattr(db_roles, "_query_db_owner", lambda: db_owner)
        monkeypatch.setattr(db_roles, "_query_table_owner", lambda table: table_owner)

    def test_app_owning_appendonly_table_is_flagged(self, monkeypatch, settings):
        # Drei-Rollen-Modell: Admin != App. App besitzt faelschlich die
        # Append-Only-Tabellen -> Bestands-DB ohne §9.6-Umzug.
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle_app")

        problems = cmd.check_db_ownership()
        assert problems, "App-Owner der Append-Only-Tabelle muss ein Problem melden."
        assert any("core_auditlog" in p for p in problems)
        assert any("anlaufstelle_app" in p for p in problems)
        # Verweis auf den Runbook-Rollout-Schritt (§ 9.6) ist Pflicht.
        assert any("9.6" in p for p in problems)

    def test_app_owning_database_is_flagged(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        # DB-Owner ist die App-Rolle, Tabellen (frisch admin-owned) sauber.
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_app", table_owner="anlaufstelle_admin")

        problems = cmd.check_db_ownership()
        assert any("Datenbank" in p and "anlaufstelle_app" in p for p in problems)

    def test_clean_three_role_ownership_passes(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        # Sauberes A1.4-Setup: Admin ist Owner von DB + Tabellen.
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle_admin")

        assert cmd.check_db_ownership() == []

    def test_uses_postgres_app_user_over_settings_in_migrate_context(self, monkeypatch, settings):
        # Migrate-Kontext: settings USER ist auf den Admin ueberschrieben,
        # POSTGRES_APP_USER traegt die echte App-Rolle. Die Ownership-Assertion
        # muss die App-Rolle (nicht den Connection-User) pruefen.
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_admin")
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle_app")

        problems = cmd.check_db_ownership()
        assert any("anlaufstelle_app" in p for p in problems)

    def test_single_role_dev_setup_skips_ownership_check(self, monkeypatch, settings):
        # Dev/Legacy-Einzelrollen-Setup: kein POSTGRES_ADMIN_USER -> die App-Rolle
        # ist bewusst Owner. Kein Falsch-Positiv, auch wenn App alles besitzt.
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle")
        monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        # Query-Helfer wuerden bei einem Bug die reale DB treffen -> hart failen.
        monkeypatch.setattr(
            db_roles, "_query_db_owner", lambda: (_ for _ in ()).throw(AssertionError("DB nicht abfragen"))
        )
        monkeypatch.setattr(
            db_roles, "_query_table_owner", lambda table: (_ for _ in ()).throw(AssertionError("DB nicht abfragen"))
        )

        assert cmd.check_db_ownership() == []

    def test_admin_equal_app_skips_ownership_check(self, monkeypatch, settings):
        # Degeneriertes Setup (Admin == App): keine Trennung moeglich -> No-op,
        # kein Falsch-Positiv.
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        monkeypatch.setattr(
            db_roles, "_query_db_owner", lambda: (_ for _ in ()).throw(AssertionError("DB nicht abfragen"))
        )
        monkeypatch.setattr(
            db_roles, "_query_table_owner", lambda table: (_ for _ in ()).throw(AssertionError("DB nicht abfragen"))
        )

        assert cmd.check_db_ownership() == []

    @pytest.mark.django_db  # the run writes the DB_ROLES_VERIFIED marker (Refs #2415)
    def test_command_exits_1_when_app_owns_appendonly_table(self, monkeypatch, settings):
        # Deploy-Gate-Verhalten: Rollen-Attribute korrekt, aber App ist noch
        # Owner (Bestands-DB) -> das Kommando MUSS mit Exit 1 abbrechen.
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")
        monkeypatch.delenv("POSTGRES_APP_USER", raising=False)
        monkeypatch.setattr(
            db_roles,
            "_query_role",
            lambda role: {
                "anlaufstelle_app": (False, False),
                "anlaufstelle_admin": (False, True),
            }[role],
        )
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle_app")

        from django.core.management import call_command

        with pytest.raises(SystemExit) as exc:
            call_command("check_db_roles")
        assert exc.value.code == 1


class TestOwnershipGateRequiresAdminOwner:
    """Refs #2086: Das Gate bewies nur „App ist NICHT Owner“, nicht „Admin IST Owner“.

    § 9.6 verlangt am Ende einen gruenen ``check_db_roles``, und der Runbook-Text
    versprach damit, dass die Ownership wirklich bei der Admin-Rolle liegt. Die
    Implementierung verglich aber ausschliesslich gegen die App-Rolle. Nach dem
    Umbau einer alten Bootstrap-Topologie (Fall C) gehoeren Altobjekte weiterhin
    der **Bootstrap**-Rolle — weder App noch Admin. Das Gate meldete dafuer
    ``[]``: gruen, obwohl der Umbau unvollstaendig war und die Admin-Rolle die
    Append-Only-Trigger gar nicht per Owner-DDL bedienen kann.

    Mock-Muster wie ``TestCheckDbOwnership`` (kein Postgres-Roundtrip).
    """

    def _patch_owners(self, monkeypatch, *, db_owner, table_owner):
        monkeypatch.setattr(db_roles, "_query_db_owner", lambda: db_owner)
        monkeypatch.setattr(db_roles, "_query_table_owner", lambda table: table_owner)

    def _three_role_env(self, monkeypatch, settings):
        monkeypatch.setitem(settings.DATABASES["default"], "USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_APP_USER", "anlaufstelle_app")
        monkeypatch.setenv("POSTGRES_ADMIN_USER", "anlaufstelle_admin")

    def test_bootstrap_owned_tables_are_flagged(self, monkeypatch, settings):
        """Der real reproduzierte Fall-C-Zwischenstand: DB umgezogen, Tabellen nicht."""
        self._three_role_env(monkeypatch, settings)
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle")

        problems = cmd.check_db_ownership()
        assert problems, (
            "Tabellen, die weder der App- noch der Admin-Rolle gehoeren, muessen "
            "auffallen — sonst ist ein halb gefahrener § 9.6-Umbau gruen."
        )
        assert any("core_auditlog" in p for p in problems)
        assert any("anlaufstelle_admin" in p for p in problems)
        assert any("9.6" in p for p in problems)

    def test_bootstrap_owned_database_is_flagged(self, monkeypatch, settings):
        self._three_role_env(monkeypatch, settings)
        self._patch_owners(monkeypatch, db_owner="anlaufstelle", table_owner="anlaufstelle_admin")

        problems = cmd.check_db_ownership()
        assert any("Datenbank" in p and "anlaufstelle_admin" in p for p in problems)

    def test_missing_table_is_not_a_finding(self, monkeypatch, settings):
        """Vor dem ersten ``migrate`` gibt es die Append-Only-Tabellen noch nicht.

        ``docker-migrate.sh`` fuehrt das Gate VOR dem Migrationslauf aus — ein
        Fund auf ``None`` wuerde jede Erstinbetriebnahme fail-closed blockieren.
        """
        self._three_role_env(monkeypatch, settings)
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner=None)

        assert cmd.check_db_ownership() == []

    def test_clean_setup_stays_green(self, monkeypatch, settings):
        self._three_role_env(monkeypatch, settings)
        self._patch_owners(monkeypatch, db_owner="anlaufstelle_admin", table_owner="anlaufstelle_admin")

        assert cmd.check_db_ownership() == []
