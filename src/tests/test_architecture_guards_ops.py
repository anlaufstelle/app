"""Architecture-Guards — dev-ops-Wartungsskripte (Refs #1047)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture


def _deployment_compose_paths() -> tuple[Path, ...]:
    """Return public stacks plus the private demo stack when it is present."""
    prod = Path("docker-compose.prod.yml")
    demo = Path("docker-compose.demo.yml")
    dev = Path("docker-compose.dev.yml")
    return (prod, demo, dev) if demo.exists() else (prod, dev)


class TestRunAsAdminPullPolicy:
    """Refs #1047: ``run-as-admin.sh`` läuft via systemd als root — root hat
    keine GHCR-Credentials (``/root/.docker/config.json`` fehlt).
    ``docker-compose.dev.yml`` setzt für ``web`` ``pull_policy: always``,
    daher versucht jedes ``docker compose run`` ohne ``--pull never`` einen
    Registry-Pull und bricht mit ``unauthorized`` (Exit 1) ab —
    Retention/Breach/MV-Refresh liefen dann nie. ``--pull never`` ist
    sicher: das ``web``-Image liegt nach jedem Deploy lokal im
    (userunabhängigen) Docker-Daemon.
    """

    _SCRIPT = Path("dev-ops/deploy/run-as-admin.sh")

    def test_compose_run_uses_pull_never(self) -> None:
        # ``dev-ops/`` ist im Public-/Stage-Snapshot ausgeschlossen
        # (build-release.sh), der Test selbst (``src/tests/``) wird aber
        # mitgeliefert. Dort ist der Guard moot — überspringen statt failen.
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard nur im Dev-Repo relevant (Refs #1047)")
        # Kommentarzeilen ausfiltern (der Skript-Header erwähnt ``docker
        # compose run`` in Prosa) und Zeilenfortsetzungen auflösen, damit
        # der mehrzeilige Aufruf als ein Statement matchbar ist.
        code_lines = [
            line for line in self._SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        ]
        flat = "\n".join(code_lines).replace("\\\n", " ")
        run_call = re.search(r"docker compose\s.*?\brun\b[^\n]*", flat)
        assert run_call is not None, f"kein 'docker compose run'-Aufruf in {self._SCRIPT} gefunden"
        assert "--pull never" in run_call.group(0), (
            f"'docker compose run' in {self._SCRIPT} muss '--pull never' "
            "setzen — sonst erzwingt 'pull_policy: always' einen GHCR-Pull "
            "als root -> unauthorized, Wartungsjobs failen (Refs #1047)"
        )


class TestBackgroundJobTimers:
    """Security N3: Die HMAC-Audit-Integritätskette (``services/audit/chain.py``)
    ist nur wirksam, wenn sie regelmäßig verifiziert wird. ``verify_audit_chain``
    muss daher als eigener systemd-Timer geplant sein — und zwar über die
    BYPASSRLS-Admin-Rolle (``$ADMIN_RUN``/``run-as-admin.sh``), weil das Kommando
    ohne Bypass-Kontext fail-loud abbricht (0 sichtbare Zeilen). Ohne diesen
    Timer bliebe Tamper an der Audit-Tabelle unentdeckt.
    """

    _SCRIPT = Path("dev-ops/deploy/install-timers.sh")

    def _code(self) -> str:
        if not self._SCRIPT.exists():
            # Voller Pfad in der Skip-Zeile: verify_release_test_guard verlangt den
            # ausgeschlossenen Prefix (deploy/install-timers.sh) auf der Gate-Zeile (Refs #1137).
            pytest.skip("dev-ops/deploy/install-timers.sh nicht im Public-Snapshot (Refs #1047)")
        lines = [
            line for line in self._SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        ]
        # Zeilenfortsetzungen auflösen, damit der mehrzeilige install_timer-Aufruf
        # (name/schedule/desc \ cmd) als ein Statement matchbar ist.
        return "\n".join(lines).replace("\\\n", " ")

    def test_verify_audit_chain_timer_runs_as_admin_role(self) -> None:
        code = self._code()
        call = re.search(r"install_timer\s+\S+[^\n]*verify_audit_chain[^\n]*", code)
        assert call is not None, (
            "install-timers.sh muss einen Timer planen, der 'verify_audit_chain' "
            "ausführt (N3: sonst wird die Audit-HMAC-Kette nie verifiziert)."
        )
        assert "$ADMIN_RUN" in call.group(0), (
            "Der verify_audit_chain-Timer muss über $ADMIN_RUN (BYPASSRLS-Admin-"
            "Rolle) laufen — sonst bricht das Kommando fail-loud ab (Refs #1070)."
        )

    def test_verify_audit_chain_timer_is_enabled_on_restart(self) -> None:
        code = self._code()
        loop = re.search(r"for\s+t\s+in\s+([^\n;]+);\s*do", code)
        assert loop is not None, "kein Timer-Restart-Loop in install-timers.sh gefunden"
        assert "audit-verify" in loop.group(1), (
            "Der 'audit-verify'-Timer muss im Restart-/Enable-Loop stehen — sonst "
            "wird die Unit angelegt, aber nie gestartet."
        )


class TestPublicBackupScriptsRlsRoles:
    """Security N4 (H3-Residuum): ``scripts/ops/backup.sh`` — der Public-/
    Self-Hoster-Pfad — dumpte als App-Rolle (NOBYPASSRLS). Alle Facility-
    Tabellen stehen unter ``FORCE ROW LEVEL SECURITY``: ohne BYPASSRLS matcht
    die Policy null Zeilen, ``pg_dump`` bricht ab und unter ``set -euo
    pipefail`` entsteht KEIN Backup (DSGVO-Backup-Luecke fuer Self-Hoster).
    Der Dump muss wie ``dev-ops/deploy/backup.sh`` ueber
    ``POSTGRES_ADMIN_USER`` laufen; der ``--verify``-Zweig braucht den
    Bootstrap-Superuser (beide App-Rollen sind NOCREATEDB) und muss eine
    RLS-Tabelle zaehlen — ``core_facility`` (ohne RLS) erkennt einen
    RLS-leeren Dump nicht. Kein pytest.skip: ``scripts/ops/`` ist im
    Public-Snapshot enthalten.
    """

    _BACKUP = Path("scripts/ops/backup.sh")
    _RESTORE = Path("scripts/ops/restore.sh")

    @staticmethod
    def _flat(path: Path) -> str:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
        return "\n".join(lines).replace("\\\n", " ")

    def test_backup_dump_runs_as_admin_role(self) -> None:
        flat = self._flat(self._BACKUP)
        assert 'DUMP_USER="${POSTGRES_ADMIN_USER:-$POSTGRES_USER}"' in flat, (
            "backup.sh muss den Dump-User per POSTGRES_ADMIN_USER-Fallback "
            "aufloesen (Review N4) — App-Rolle scheitert an FORCE RLS."
        )
        call = re.search(r"pg_dump\s+-U\s+\"\$DUMP_USER\"[^\n]*", flat)
        assert call is not None, "pg_dump in backup.sh muss als $DUMP_USER (BYPASSRLS) laufen (Review N4)."

    def test_backup_verify_probes_rls_table_as_superuser(self) -> None:
        flat = self._flat(self._BACKUP)
        assert 'SU_USER="${POSTGRES_SUPERUSER:-postgres}"' in flat, (
            "--verify braucht den Bootstrap-Superuser: beide App-Rollen sind "
            "NOCREATEDB (01-app-role.sh), CREATE DATABASE schlaegt sonst fehl."
        )
        assert "core_client" in flat, (
            "--verify muss eine RLS-Tabelle (core_client) zaehlen — "
            "core_facility (ohne RLS) erkennt einen RLS-leeren Dump nicht (Review N4)."
        )

    def test_restore_runs_as_superuser(self) -> None:
        flat = self._flat(self._RESTORE)
        assert 'SU_USER="${POSTGRES_SUPERUSER:-postgres}"' in flat, (
            "restore.sh muss den Plain-SQL-Dump als Superuser einspielen: "
            "OWNER-TO-/FORCE-RLS-Statements scheitern unter der App-Rolle (Review N4)."
        )
        call = re.search(r"psql\s+-U\s+\"\$SU_USER\"[^\n]*", flat)
        assert call is not None, 'Restore-Pipe in restore.sh muss psql -U "$SU_USER" nutzen (Review N4).'


class TestDeployChecksWiredIntoStartup:
    """Security N6: Platzhalter-/Fehlkonfigurations-Guards muessen beim
    Container-Start greifen — nicht erst, wenn jemand manuell prueft."""

    def test_entrypoint_runs_check_deploy(self) -> None:
        content = Path("docker-entrypoint.sh").read_text(encoding="utf-8")
        assert "check --deploy" in content, (
            "docker-entrypoint.sh muss 'manage.py check --deploy' vor gunicorn "
            "ausfuehren (Review N6) — set -e stoppt den Start bei Fehlern."
        )

    def test_backup_scripts_reject_placeholder_key(self) -> None:
        common = Path("scripts/ops/_backup_common.sh").read_text(encoding="utf-8")
        assert "backup_require_real_key" in common, (
            "_backup_common.sh muss backup_require_real_key definieren — ein "
            "change-me-BACKUP_ENCRYPTION_KEY verschluesselt faktisch nicht (Review N6)."
        )
        # scripts/ops/ liegt vollstaendig im Public-Snapshot — kein Skip noetig.
        # restore-drill.sh liest denselben Schluessel und muss den Guard ebenso
        # aufrufen (Refs #1441), sonst ist ein "change-me"-Backup faktisch
        # unverschluesselt und wird trotzdem als restore-faehig gedrillt.
        for script in ("scripts/ops/backup.sh", "scripts/ops/restore.sh", "scripts/ops/restore-drill.sh"):
            body = Path(script).read_text(encoding="utf-8")
            assert "backup_require_real_key" in body, (
                f"{script} muss backup_require_real_key aufrufen (Review N6, Refs #1441)."
            )

    def test_dev_ops_backup_rejects_placeholder_key(self) -> None:
        """Security (Refs #1441): das dev-only ``dev-ops/deploy/backup.sh`` liest
        denselben ``BACKUP_ENCRYPTION_KEY`` und muss den Platzhalter ebenfalls
        fail-closed abweisen. Es sourcet ``scripts/ops/`` bewusst NICHT (eigener
        Stack) — daher ein Inline-Case-Guard statt ``backup_require_real_key``.
        """
        # dev-ops/ ist im Public-Snapshot ausgeschlossen (build-release.sh) — der
        # Guard ist dort moot, also skippen statt failen. Voller Pfad in der
        # Skip-Zeile: verify_release_test_guard verlangt den ausgeschlossenen
        # Prefix (dev-ops/deploy/backup.sh) auf der Gate-Zeile (Refs #1137, #1441).
        script = Path("dev-ops/deploy/backup.sh")
        if not script.exists():
            pytest.skip("dev-ops/deploy/backup.sh nicht im Public-Snapshot (Refs #1441)")
        body = script.read_text(encoding="utf-8")
        assert "change-me*" in body, (
            "dev-ops/deploy/backup.sh muss den Platzhalter-Schluessel (change-me*) "
            "per Case-Guard fail-closed abweisen — ein oeffentlich bekannter Key "
            "verschluesselt faktisch nicht (Refs #1441)."
        )


class TestInitialSetupScript:
    """C-25 (Refs #793): ``scripts/initial-setup.sh`` ist der One-Command-Pfad
    fuer die Erstinbetriebnahme (`docker compose up` -> lauffaehige Installation).

    Die Falle, die das Skript schliesst: ohne es muessen Betreiber:innen den
    Migrate-One-Shot, den Bootstrap-Super-Admin und die Verifikation der
    DB-Rollen-Posture aus der Doku zusammensuchen. Wird die Rollen-Pruefung
    vergessen, laeuft die Runtime womoeglich als Superuser und Row Level
    Security (Migration 0047) ist **still** wirkungslos.

    ``scripts/`` liegt im Public-Snapshot (nur ``scripts/dev/`` wird von
    ``build-release.sh`` gestrippt) — kein ``pytest.skip`` noetig.
    """

    _SCRIPT = Path("scripts/initial-setup.sh")

    def _body(self) -> str:
        return self._SCRIPT.read_text(encoding="utf-8")

    def _flat(self) -> str:
        """Skript ohne Kommentarzeilen, Zeilenfortsetzungen aufgeloest."""
        lines = [line for line in self._body().splitlines() if not line.lstrip().startswith("#")]
        return "\n".join(lines).replace("\\\n", " ")

    def test_script_exists_and_is_executable(self) -> None:
        assert self._SCRIPT.exists(), "scripts/initial-setup.sh fehlt (Refs #793)."
        assert self._SCRIPT.stat().st_mode & 0o111, (
            "scripts/initial-setup.sh muss ausfuehrbar sein (Git-Executable-Bit) — "
            "sonst scheitert der dokumentierte './scripts/initial-setup.sh'-Aufruf."
        )

    def test_script_is_fail_loud(self) -> None:
        head = self._body().splitlines()[:3]
        assert any("set -euo pipefail" in line for line in head), (
            "initial-setup.sh muss 'set -euo pipefail' direkt nach dem Shebang "
            "setzen — ein halb durchgelaufenes Setup darf nicht als Erfolg enden."
        )

    def test_runs_migrate_one_shot(self) -> None:
        # Auf den tatsaechlichen Aufruf gematcht, nicht auf die Erwaehnung in
        # einer Statusmeldung — sonst haelt der Guard, waehrend der Schritt fehlt.
        assert re.search(r"--entrypoint=/app/docker-migrate\.sh", self._flat()), (
            "initial-setup.sh muss das Schema ueber den One-Shot-Job "
            "docker-migrate.sh anlegen (Refs #802) — Migrationen laufen bewusst "
            "NICHT im Web-Entrypoint."
        )

    def test_verifies_db_role_posture(self) -> None:
        assert re.search(r"^\s*manage(\.py)?\s+check_db_roles\b", self._flat(), re.MULTILINE), (
            "initial-setup.sh muss 'manage.py check_db_roles' ausfuehren — das ist "
            "der Kern von #793: die Verifikation, dass die Runtime-Rolle RLS nicht "
            "umgeht (Refs #902/#1026)."
        )

    def test_runs_check_deploy(self) -> None:
        assert re.search(r"^\s*manage(\.py)?\s+check\s+--deploy\b", self._flat(), re.MULTILINE), (
            "initial-setup.sh muss 'manage.py check --deploy' ausfuehren (Refs #793)."
        )

    def test_does_not_alter_db_roles_itself(self) -> None:
        """Abgleich mit #1026/ADR-033: das Skript VERIFIZIERT die Rollen, es
        REPARIERT sie nicht.

        Der urspruengliche #793-Entwurf sah ein manuelles ``ALTER ROLE
        anlaufstelle_user NOSUPERUSER;`` vor. Seit dem Drei-Rollen-Modell
        (ADR-020) legt ``deploy/postgres-init/01-app-role.sh`` die Attribute
        beim ersten Cluster-Start korrekt an; auf Bestands-Clustern ist der
        Ownership-Umzug ein eigener, Maintainer-gefuehrter Vorgang
        (ops-runbook § 9.6). Ein blindes ALTER ROLE aus dem Setup-Skript waere
        dort wirkungslos bis schaedlich — und wuerde einen echten
        Fehlkonfigurations-Alarm stillschweigend uebertuenchen.
        """
        flat = self._flat().upper()
        assert "ALTER ROLE" not in flat, (
            "initial-setup.sh darf keine Rollen-DDL ausfuehren — Rollen-Attribute "
            "setzt deploy/postgres-init/01-app-role.sh, das Skript prueft nur "
            "(ADR-020/ADR-033, Refs #1026)."
        )
        assert "REASSIGN OWNED" not in flat, (
            "Der Ownership-Umzug auf Bestandsinstallationen ist Maintainer-gefuehrt "
            "(ops-runbook § 9.6) und gehoert nicht in das Erstinbetriebnahme-Skript."
        )

    def test_never_passes_a_password_non_interactively(self) -> None:
        """ADR-018: kein Default-Passwort, Passworteingabe nur ueber ``getpass``.

        Ein ``--password``-Flag oder eine Passwort-Env im Setup-Skript landete
        sonst in Shell-History, Prozessliste und CI-Logs.
        """
        flat = self._flat()
        # Bewusst eng gefasst: ``POSTGRES_ADMIN_PASSWORD`` darf als .env-Pflichtfeld
        # in den Preflight-Meldungen vorkommen — verboten ist die Weitergabe eines
        # *Anwendungs*-Passworts an den Bootstrap-Befehl.
        for forbidden in ("--password", "DJANGO_SUPERUSER_PASSWORD", "${ADMIN_PASSWORD"):
            assert forbidden not in flat, (
                f"initial-setup.sh darf {forbidden!r} nicht verwenden — Passwoerter "
                "werden ausschliesslich interaktiv per getpass gesetzt (ADR-018)."
            )
        bootstrap_lines = re.findall(r"[^\n]*create_super_admin[^\n]*", flat)
        assert bootstrap_lines, "initial-setup.sh muss create_super_admin aufrufen (ADR-018)."
        for line in bootstrap_lines:
            for piped in ("<<<", "echo ", "printf "):
                assert piped not in line, (
                    "Der create_super_admin-Aufruf darf keine Eingabe hineinpipen — "
                    "das Passwort wird interaktiv per getpass abgefragt (ADR-018)."
                )

    def test_closing_note_says_the_admin_path_equips_the_facility(self) -> None:
        """Refs #1894/#1970: Die Abschlussmeldung schickt in Schritt 1 in den
        Django-Admin — den einzigen Laufzeit-Anlagepfad fuer eine Einrichtung
        (``core/admin/organization.py``). Seit Refs #1970 stattet dieser Weg die
        neue Einrichtung selbst aus (``ensure_facility_baseline``: Standard-
        katalog, Schichtfenster, Schnellvorlagen).

        Die Meldung nannte den Standardkatalog aber ausschliesslich am
        ``setup_facility``-Fallback weiter unten. Wer Schritt 1 folgte, konnte
        daraus nur schliessen, der Admin-Weg lasse die Einrichtung leer — genau
        die Rateaufgabe, die #1894 abstellt.
        """
        parts = self._body().split("Erstinbetriebnahme abgeschlossen.", 1)
        assert len(parts) == 2, "initial-setup.sh muss eine Abschlussmeldung ausgeben (Refs #793)."
        # Alles vor der ersten Nennung des CLI-Fallbacks = die Beschreibung des
        # Admin-Wegs. Der Fallback darf seinen eigenen Katalog-Satz behalten.
        admin_step = parts[1].split("setup_facility", 1)[0]
        assert "/admin-mgmt/" in admin_step, (
            "Die Abschlussmeldung muss den Django-Admin als Anlagepfad nennen — "
            "der /system/-Bereich ist fuer Organisation und Einrichtungen eine "
            "reine Leseansicht (core/views/system/organization.py, Refs #1883)."
        )
        assert "Standardkatalog" in admin_step, (
            "Die Abschlussmeldung muss schon beim Admin-Weg sagen, dass die neue "
            "Einrichtung den Standardkatalog mitbekommt (Refs #1970) — sonst liest "
            "sich der Katalog als Sonderleistung des setup_facility-Fallbacks."
        )


class TestDeploymentDbSecretBoundary:
    """Refs #1962: privilegierte DB-Secrets gehoeren nicht in ``web``.

    ``env_file`` ist fuer HTTP-Runtime-Container zu breit: die ausgelieferten
    Dateien enthalten neben dem App-Passwort auch Admin-/Bootstrap-Zugaenge.
    Jeder Deployment-Stack muss deshalb eine explizite Runtime-Allowlist und
    fuer Migrationen/Operator-Kommandos einen separaten Maintenance-Service
    bereitstellen.
    """

    _COMPOSES = _deployment_compose_paths()
    _ENV_TEMPLATES = {
        Path("docker-compose.prod.yml"): Path(".env.example"),
        Path("docker-compose.demo.yml"): Path(".env.demo.example"),
        Path("docker-compose.dev.yml"): Path(".env.dev.example"),
    }
    _OPERATOR_DOCS = (
        Path("docs/ops-runbook.md"),
        Path("docs/packages.md"),
        Path("docs/coolify-deployment.md"),
        Path("docs/disaster-recovery.md"),
        Path("docs/admin-guide.md"),
        Path("docs/en/admin-guide.md"),
    )
    _FORBIDDEN = {
        "POSTGRES_ADMIN_USER",
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_BOOTSTRAP_PASSWORD",
    }

    def test_compose_selection_tolerates_private_stack_missing_from_public_snapshot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        Path("docker-compose.prod.yml").touch()
        Path("docker-compose.dev.yml").touch()

        assert _deployment_compose_paths() == (
            Path("docker-compose.prod.yml"),
            Path("docker-compose.dev.yml"),
        )

    @staticmethod
    def _services(path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]

    @pytest.mark.parametrize("compose_path", _COMPOSES)
    def test_web_uses_explicit_runtime_allowlist(self, compose_path: Path) -> None:
        web = self._services(compose_path)["web"]
        assert "env_file" not in web, (
            f"{compose_path}: web darf keine vollstaendige Env-Datei laden — "
            "sie enthaelt Admin-/Bootstrap-DB-Secrets (Refs #1962)."
        )
        environment = web.get("environment", {})
        leaked = self._FORBIDDEN.intersection(environment)
        assert not leaked, f"{compose_path}: privilegierte DB-Variablen in web: {sorted(leaked)}"

    @pytest.mark.parametrize("compose_path", _COMPOSES)
    def test_documented_runtime_variables_stay_in_allowlist(self, compose_path: Path) -> None:
        """Drift-Guard fuer die explizite Allowlist aus Option (b), Refs #1962."""
        template = self._ENV_TEMPLATES[compose_path]
        if template == Path(".env.demo.example") and not template.exists():
            pytest.skip(".env.demo.example nicht im Public-Snapshot — Guard nur im Dev-Repo relevant (Refs #1962)")
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", template.read_text(encoding="utf-8"), re.MULTILINE))
        infrastructure_only = {
            "DOMAIN",
            "ACME_EMAIL",
            "BACKUP_ENCRYPTION_KEY",
            "BACKUP_HOST_DIR",
            "BACKUP_RETENTION_DAYS",
        }
        expected = documented - self._FORBIDDEN - infrastructure_only
        actual = set(self._services(compose_path)["web"].get("environment", {}))
        missing = expected - actual
        assert not missing, (
            f"{compose_path}: dokumentierte Runtime-Variablen fehlen in der web-Allowlist: {sorted(missing)}"
        )

    @pytest.mark.parametrize("compose_path", _COMPOSES)
    def test_privileged_commands_have_dedicated_maintenance_service(self, compose_path: Path) -> None:
        maintenance = self._services(compose_path).get("maintenance")
        assert maintenance is not None, f"{compose_path}: separater maintenance-Service fehlt (Refs #1962)."
        assert "maintenance" in maintenance.get("profiles", []), (
            f"{compose_path}: maintenance muss opt-in per Compose-Profil bleiben."
        )
        environment = maintenance.get("environment", {})
        assert environment.get("ANLAUFSTELLE_PROCESS_ROLE") == "maintenance"
        assert environment.get("POSTGRES_USER") == "${POSTGRES_ADMIN_USER}"
        assert environment.get("POSTGRES_PASSWORD") == "${POSTGRES_ADMIN_PASSWORD}"
        assert environment.get("POSTGRES_ADMIN_PASSWORD") == "${POSTGRES_ADMIN_PASSWORD}"
        assert environment.get("POSTGRES_BOOTSTRAP_PASSWORD") == "${POSTGRES_BOOTSTRAP_PASSWORD}"

    def test_initial_setup_migrates_via_maintenance_service(self) -> None:
        body = Path("scripts/initial-setup.sh").read_text(encoding="utf-8")
        call = re.search(r"compose run[^\n]*--entrypoint=/app/docker-migrate\.sh[^\n]*", body)
        assert call is not None, "initial-setup.sh muss den Migrate-One-Shot starten."
        assert "maintenance" in call.group(0) and " web" not in call.group(0), (
            "initial-setup.sh darf Admin-Secrets nur in den maintenance-One-Shot laden (Refs #1962)."
        )

    def test_postgres_init_fails_loud_without_required_admin_role(self) -> None:
        result = subprocess.run(
            ["/bin/sh", "deploy/postgres-init/01-app-role.sh"],
            env={
                "PATH": os.environ.get("PATH", ""),
                "REQUIRE_ADMIN_DB_ROLE": "true",
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "b" * 40,
                "POSTGRES_DB": "anlaufstelle",
                "APP_DB_USER": "anlaufstelle",
                "APP_DB_PASSWORD": "a" * 40,
                "ADMIN_DB_USER": "",
                "ADMIN_DB_PASSWORD": "",
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "ADMIN_DB_USER / ADMIN_DB_PASSWORD missing" in result.stderr

    def test_postgres_init_rejects_placeholder_bootstrap_password(self) -> None:
        result = subprocess.run(
            ["/bin/sh", "deploy/postgres-init/01-app-role.sh"],
            env={
                "PATH": os.environ.get("PATH", ""),
                "REQUIRE_ADMIN_DB_ROLE": "true",
                "POSTGRES_USER": "postgres",
                "POSTGRES_PASSWORD": "change-me-bootstrap",
                "POSTGRES_DB": "anlaufstelle",
                "APP_DB_USER": "anlaufstelle",
                "APP_DB_PASSWORD": "a" * 40,
                "ADMIN_DB_USER": "anlaufstelle_admin",
                "ADMIN_DB_PASSWORD": "c" * 40,
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr and "placeholder" in result.stderr

    @pytest.mark.parametrize("doc_path", _OPERATOR_DOCS)
    def test_operator_docs_never_inject_admin_secrets_into_web(self, doc_path: Path) -> None:
        text = doc_path.read_text(encoding="utf-8")
        code_blocks = re.findall(r"```[^\n]*\n(.*?)\n```", text, re.DOTALL)
        for block in code_blocks:
            injects_privileged = "POSTGRES_ADMIN_PASSWORD" in block or "POSTGRES_BOOTSTRAP_PASSWORD" in block
            targets_web = bool(re.search(r"(?:exec|run)[^\n]*\bweb\b", block))
            assert not (injects_privileged and targets_web), (
                f"{doc_path}: Codebeispiel injiziert privilegierte DB-Secrets in web (Refs #1962)."
            )
            assert not re.search(r"docker-migrate\.sh\s+web\b", block), (
                f"{doc_path}: Migrate-One-Shot muss maintenance statt web nutzen (Refs #1962)."
            )


class TestReadmeQuickStart:
    """Refs #1894/#1977/#1062: Der Compose-Quick-Start muss wirklich funktionieren.

    Die lokale Compose mountet den Quellbaum nach ``/app``. Deshalb muss sie den
    dort verdeckten Image-Entrypoint neutralisieren, den versprochenen
    ``runserver`` selbst starten und das im Image gebaute CSS aus einem nicht
    verdeckten Pfad in den Bind-Mount kopieren. Seit dem One-Command-Startup
    (Refs #1062) uebernimmt der ``init``-Dienst Migration und Seed — der
    Quick-Start darf sie darum weglassen, aber nur solange es diesen Dienst
    wirklich gibt.
    """

    _READMES = (Path("README.md"), Path("README.en.md"))
    _MIGRATE_CALL = re.compile(r"manage\.py\s+migrate\b")
    _MIGRATE_STEP = re.compile(r"(?:manage\.py|make)\s+migrate\b")
    _SEED_STEP = re.compile(r"(?:manage\.py|make)\s+seed\b")
    _QUICKSTART_HEADING = re.compile(r"^### Quick[- ]Start$", re.MULTILINE)
    _COMPOSE_CALL = re.compile(r"docker compose\s+(?P<args>[^\n`]*)")
    # ``src/manage.py`` ist auf dem Host richtig und im Container falsch —
    # verboten ist deshalb nur die Kombination mit einem Container-Aufruf.
    _CONTAINERISED_SRC_MANAGE_PY = re.compile(r"docker (?:compose\s+)?(?:exec|run)[^\n]*\bsrc/manage\.py")

    @classmethod
    def _quickstart(cls, readme: Path) -> str:
        """Nur der Quick-Start-Abschnitt — der Rest der Projektuebersicht
        beschreibt den Prod-Stack und darf ``docker compose up`` nennen."""
        text = readme.read_text(encoding="utf-8")
        heading = cls._QUICKSTART_HEADING.search(text)
        assert heading is not None, f"{readme}: Abschnitt '### Quick-Start' nicht gefunden."
        rest = text[heading.end() :]
        following = re.search(r"^### ", rest, re.MULTILINE)
        return rest[: following.start()] if following else rest

    @staticmethod
    def _targets_the_web_service(args: str) -> bool:
        tokens = args.split()
        if "web" in tokens:
            return True
        if "up" in tokens:
            # ``up`` ohne Dienstliste zieht jeden Dienst hoch, auch ``web``.
            return not [token for token in tokens[tokens.index("up") + 1 :] if not token.startswith("-")]
        return False

    @staticmethod
    def _compose() -> dict:
        return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    @classmethod
    def _local_stack_bootstraps_itself(cls) -> bool:
        """Uebernimmt der lokale Stack Migration und Seed selbst (Refs #1062)?

        Genau dann, wenn es den einmaligen ``init``-Dienst gibt UND ``web``
        auf dessen erfolgreiches Ende wartet. Ohne die Wartebedingung kaeme
        der Webdienst vor dem Schema hoch — das Versprechen waere keins.
        """
        services = cls._compose()["services"]
        wartet = services["web"].get("depends_on", {}).get("init", {})
        return "init" in services and wartet.get("condition") == "service_completed_successfully"

    def test_shared_entrypoint_never_migrates(self) -> None:
        """Refs #802/#1062: Der Auto-Bootstrap gehoert in den lokalen
        ``init``-Dienst, NICHT in den geteilten Image-Entrypoint.

        Der Entrypoint laeuft in jeder Web-Replica jedes Stacks. Migrierte er,
        wuerden parallele Replicas beim Deploy um den Migrate-Lock konkurrieren
        und die Deploy-Gates aus ``docker-migrate.sh`` (Rollen-Posture,
        Ownership) waeren umgangen.
        """
        entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")
        code = "\n".join(line for line in entrypoint.splitlines() if not line.lstrip().startswith("#"))
        assert not self._MIGRATE_CALL.search(code), (
            "docker-entrypoint.sh migriert wieder — der Auto-Pfad gehoert ausschliesslich "
            "in den lokalen init-Dienst (Refs #802/#1062)."
        )
        assert re.search(r"^exec gunicorn\b", code, re.MULTILINE), (
            "docker-entrypoint.sh startet nicht mehr gunicorn — Startpfad und README-Quick-Start pruefen (Refs #802)."
        )

    def test_quickstart_matches_the_actual_startup_path(self) -> None:
        """Der Quick-Start darf Migrate und Seed nur weglassen, solange der
        lokale Stack sie selbst ausfuehrt. Faellt der ``init``-Dienst weg,
        faellt dieser Guard — und der README-Text gehoert mit umgestellt
        (Refs #1894/#1977/#1062)."""
        bootstrappt = self._local_stack_bootstraps_itself()
        for readme in self._READMES:
            quickstart = self._quickstart(readme)
            assert bootstrappt or self._MIGRATE_STEP.search(quickstart), (
                f"{readme}: Ohne Auto-Bootstrap muss der Quick-Start den Migrate-Schritt "
                "ausdruecklich nennen — sonst laeuft die Anwendung in einen "
                "Datenbankfehler statt hochzukommen (Refs #1894)."
            )
            assert bootstrappt or self._SEED_STEP.search(quickstart), (
                f"{readme}: Ohne Auto-Bootstrap muss der Quick-Start den Seed-Schritt "
                "ausdruecklich nennen — er laeuft dann nicht implizit (Refs #1977)."
            )

    def test_local_startup_needs_no_prepared_environment(self) -> None:
        """Positive Richtung, immer scharf (Refs #1062): ``docker compose up``
        muss OHNE vorbereitete Umgebung durchlaufen.

        Eine Pflicht-Interpolation (``${VAR:?…}``) bricht Compose ab, bevor
        irgendetwas startet — sie waere ein Setup-Schritt vor dem ersten Start
        und damit das Gegenteil eines Ein-Kommando-Starts.
        """
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        pflichtvariablen = re.findall(r"\$\{([A-Z_][A-Z0-9_]*):\?", compose)
        assert not pflichtvariablen, (
            f"docker-compose.yml verlangt gesetzte Umgebungsvariablen {sorted(set(pflichtvariablen))} — "
            "'docker compose up' muss auf einem frischen Klon ohne Vorbereitung laufen (Refs #1062)."
        )

    def test_quickstart_drops_the_manual_key_export_once_it_is_obsolete(self) -> None:
        """Gegenstueck zur Lockerung oben (Refs #1062).

        Solange der Quick-Start noch den manuellen Vor-#1062-Weg beschreibt
        (Migrate-Schritt vorhanden), passt ein ``export ENCRYPTION_KEY`` dazu.
        Sobald der Text auf den Ein-Kommando-Weg umgestellt ist, waere derselbe
        ``export`` ein Pflichtschritt, den es nicht mehr gibt — dann faellt
        dieser Guard. Ohne ihn koennte der Quick-Start beide Haelften verlieren
        und trotzdem gruen bleiben.
        """
        for readme in self._READMES:
            quickstart = self._quickstart(readme)
            beschreibt_alten_weg = bool(self._MIGRATE_STEP.search(quickstart))
            assert beschreibt_alten_weg or "export ENCRYPTION_KEY" not in quickstart, (
                f"{readme}: Der Quick-Start fuehrt kein 'export ENCRYPTION_KEY' mehr als "
                "Pflichtschritt — der lokale Stack erzeugt den Schluessel selbst (Refs #1062)."
            )

    def test_quickstart_starts_the_web_service_of_the_dev_compose(self) -> None:
        for readme in self._READMES:
            calls = list(self._COMPOSE_CALL.finditer(self._quickstart(readme)))
            assert any(self._targets_the_web_service(call.group("args")) for call in calls), (
                f"{readme}: Der reparierte Quick-Start muss den web-Dienst der Dev-Compose starten (Refs #1977)."
            )

    def test_quickstart_never_calls_src_manage_py_inside_a_container(self) -> None:
        """Im Image liegt ``manage.py`` auf ``/app/manage.py``: das Dockerfile
        kopiert ``src/`` unmittelbar ins ``WORKDIR /app``. Ein in einen Container
        hineingereichtes ``python src/manage.py`` scheitert dort mit
        ``No such file or directory`` — auf dem Host ist derselbe Pfad richtig."""
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^WORKDIR /app$", dockerfile, re.MULTILINE), (
            "Dockerfile-WORKDIR geaendert — README-Quick-Start-Pfade pruefen."
        )
        assert re.search(r"^COPY src/ \./$", dockerfile, re.MULTILINE), (
            "Dockerfile kopiert src/ nicht mehr direkt ins WORKDIR — README-Quick-Start-Pfade pruefen."
        )
        for readme in self._READMES:
            text = readme.read_text(encoding="utf-8")
            assert not self._CONTAINERISED_SRC_MANAGE_PY.search(text), (
                f"{readme}: 'src/manage.py' gibt es im Container nicht — "
                "manage.py liegt dort auf /app/manage.py (Refs #1894)."
            )

    def test_dev_compose_runs_the_bound_source_with_a_neutral_entrypoint(self) -> None:
        web = self._compose()["services"]["web"]
        assert web.get("entrypoint") == [], (
            "Dev-web muss den von ./src:/app verdeckten Image-Entrypoint ausdruecklich neutralisieren (Refs #1977)."
        )
        command = " ".join(web.get("command", [])) if isinstance(web.get("command"), list) else web.get("command", "")
        assert "exec python manage.py runserver 0.0.0.0:8000" in command, (
            "Dev-web muss runserver selbst per exec starten; der Image-Entrypoint "
            "reicht das Compose-command bewusst nicht weiter (Refs #1977)."
        )
        assert "./src:/app" in web.get("volumes", []), "Dev-web muss weiterhin den Quellbaum live mounten."

    def test_dev_compose_ships_no_fixed_encryption_key(self) -> None:
        """Refs #1977/#1062: Kein ausgeliefertes Fest-Geheimnis — aber auch
        kein Setup-Schritt vor dem ersten Start.

        Die Compose reicht ENCRYPTION_KEY nur durch; fehlt er, erzeugt das
        Bootstrap-Skript einen zufaelligen Schluessel auf DIESER Maschine.
        Ein in der Datei stehender Literal-Wert waere fuer alle Klone
        derselbe und damit kein Geheimnis mehr.
        """
        services = self._compose()["services"]
        for name in ("web", "init"):
            value = services[name]["environment"]["ENCRYPTION_KEY"]
            assert value == "${ENCRYPTION_KEY:-}", (
                f"{name} muss ENCRYPTION_KEY unveraendert aus der Umgebung durchreichen "
                f"(gefunden: {value!r}) — kein ausgelieferter Festschluessel (Refs #1977)."
            )

    def test_dev_compose_healthcheck_treats_http_200_as_liveness(self) -> None:
        """Refs #1982: Die Dev-DB wird mit der Bootstrap-Rolle initialisiert.

        Sie ist erwartungsgemaess Superuser und laesst ``/health/`` deshalb
        ``degraded`` melden. Das allgemeine Image muss diesen Betriebsbefund
        weiter streng behandeln; nur die Dev-Compose prueft reine Liveness.
        """
        healthcheck = self._compose()["services"]["web"]["healthcheck"]
        test = healthcheck.get("test", [])
        assert test[:2] == ["CMD", "python"], (
            "Dev-web muss den Image-Healthcheck mit einem echten Python-HTTP-Check ueberschreiben (Refs #1982)."
        )
        command = " ".join(test[2:])
        assert "urllib.request.urlopen('http://localhost:8000/health/'" in command, (
            "Der Dev-Liveness-Check muss /health/ wirklich per HTTP erreichen (Refs #1982)."
        )
        assert "json.loads" not in command and "status') == 'ok'" not in command, (
            "Nur Dev darf HTTP 200 trotz status=degraded als live behandeln; "
            "die strenge Statusauswertung bleibt im Image (Refs #1982)."
        )

        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        assert "json.loads(body).get('status') == 'ok'" in dockerfile, (
            "Der allgemeine Image-/Produktions-Healthcheck darf fuer #1982 nicht aufgeweicht werden."
        )

    def test_built_css_survives_the_source_bind_mount(self) -> None:
        artifact = "/opt/anlaufstelle/static/css/styles.css"
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        assert re.search(rf"^COPY --from=node\s+\S+\s+{re.escape(artifact)}$", dockerfile, re.MULTILINE), (
            "Das Image muss das gebaute CSS ausserhalb /app vorhalten, damit "
            "der Source-Mount es nicht verdeckt (Refs #1977)."
        )
        web = self._compose()["services"]["web"]
        command = " ".join(web.get("command", [])) if isinstance(web.get("command"), list) else web.get("command", "")
        assert f"cp {artifact} /app/static/css/styles.css" in command, (
            "Dev-web muss das Image-CSS vor dem Serverstart in den gemounteten Quellbaum kopieren (Refs #1977)."
        )
        assert command.index(f"cp {artifact}") < command.index("exec python manage.py runserver"), (
            "CSS-Kopie muss vor dem Start des Dev-Servers laufen (Refs #1977)."
        )

    def test_e2e_runbook_starts_only_the_database_service(self) -> None:
        runbook = Path("docs/e2e-runbook.md").read_text(encoding="utf-8")
        calls = re.findall(r"docker compose up -d(?:\s+\w+)?", runbook)
        assert calls, "E2E-Runbook muss den Start der Docker-Datenbank dokumentieren."
        assert all(call.endswith(" db") for call in calls), (
            "E2E startet seinen eigenen gunicorn: docker compose up -d darf dort "
            "nur den db-Dienst starten (Refs #1977)."
        )


class TestLocalOneCommandBootstrap:
    """Refs #1062 (ADR-049): ``docker compose up`` genuegt — lokal und nur lokal.

    Der einmalige ``init``-Dienst migriert und seedet den lokalen Stack. Genau
    diese Bequemlichkeit waere auf einem Produktionssystem fatal: sie legt
    Konten mit dokumentiertem Passwort an. Die Guards halten den Auto-Pfad
    deshalb an der Grenze des lokalen ``docker-compose.yml`` fest.
    """

    _SCRIPT = Path("scripts/local/bootstrap.sh")
    _MOUNT = "./scripts/local/bootstrap.sh:/opt/anlaufstelle/bin/bootstrap.sh:ro"
    # Liegt im ``localstate``-Volume, das auf das Home des Container-Benutzers
    # gemountet wird — siehe ``test_generated_key_survives_a_restart``.
    _KEY_FILE = "/home/appuser/encryption.key"
    # Die Stacks, die NIEMALS automatisch bootstrappen duerfen. ``staging`` und
    # ``demo`` fehlen im Public-Snapshot und werden dort uebersprungen.
    _DEPLOYMENT_COMPOSES = (
        Path("docker-compose.prod.yml"),
        Path("docker-compose.staging.yml"),
        Path("docker-compose.dev.yml"),
        Path("docker-compose.demo.yml"),
    )

    @staticmethod
    def _compose() -> dict:
        return yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))

    @classmethod
    def _init(cls) -> dict:
        services = cls._compose()["services"]
        assert "init" in services, "Der lokale Bootstrap-Dienst 'init' fehlt (Refs #1062)."
        return services["init"]

    def test_init_waits_for_a_healthy_database(self) -> None:
        assert self._init()["depends_on"]["db"]["condition"] == "service_healthy", (
            "init muss auf eine bereite Datenbank warten — sonst scheitert die "
            "erste Migration am noch startenden Postgres (Refs #1062)."
        )

    def test_init_is_a_one_shot(self) -> None:
        """``service_completed_successfully`` setzt einen Container voraus, der
        sich beendet. Eine Restart-Policy wuerde ihn endlos neu starten und
        ``web`` warten lassen."""
        assert self._init().get("restart") == "no", (
            'init muss ausdruecklich als One-Shot markiert bleiben (restart: "no", Refs #1062).'
        )

    def test_web_starts_only_after_a_successful_bootstrap(self) -> None:
        wartet = self._compose()["services"]["web"]["depends_on"]["init"]
        assert wartet["condition"] == "service_completed_successfully", (
            "web darf erst nach erfolgreichem Bootstrap starten — sonst zeigt die "
            "versprochene Login-Seite einen Datenbankfehler (Refs #1062)."
        )

    def test_bootstrap_script_is_mounted_where_it_is_called(self) -> None:
        """Der ``./src:/app``-Mount verdeckt alles unterhalb ``/app`` (Refs #1977).
        Das Skript muss deshalb ausserhalb liegen — und beide Dienste, die es
        aufrufen, muessen es auch wirklich hineinreichen."""
        services = self._compose()["services"]
        for name in ("init", "web"):
            volumes = services[name].get("volumes", [])
            assert self._MOUNT in volumes, f"{name}: Bind-Mount des Bootstrap-Skripts fehlt (Refs #1062)."
            befehl = " ".join(services[name]["command"])
            assert "/opt/anlaufstelle/bin/bootstrap.sh" in befehl, (
                f"{name}: ruft das Bootstrap-Skript nicht auf (Refs #1062)."
            )

    def test_bootstrap_migrates_and_seeds_only_an_empty_database(self) -> None:
        body = self._SCRIPT.read_text(encoding="utf-8")
        assert "python manage.py migrate --noinput" in body, (
            "Der lokale Bootstrap muss das Schema anlegen (Refs #1062)."
        )
        assert "manage.py seed --only-if-empty" in body, (
            "Der lokale Bootstrap darf nur eine leere Datenbank seeden — sonst "
            "ueberschriebe jedes 'docker compose up' die eigene Arbeit (Refs #1062)."
        )
        assert "--flush" not in body, (
            "Der lokale Bootstrap darf niemals flushen: er laeuft bei JEDEM Start (Refs #1062)."
        )

    def test_bootstrap_generates_the_local_key_instead_of_shipping_one(self) -> None:
        """Refs #1977: Ein in der Datei stehender Schluessel waere fuer jeden
        Klon derselbe. Erzeugt wird er zufaellig, beim ersten Start."""
        body = self._SCRIPT.read_text(encoding="utf-8")
        assert "Fernet.generate_key()" in body, "Der lokale Schluessel muss zufaellig erzeugt werden (Refs #1977)."
        assert not re.search(r"ENCRYPTION_KEY=[\"']?[A-Za-z0-9_-]{20,}", body), (
            "Kein fest verdrahteter Schluessel im Bootstrap-Skript (Refs #1977)."
        )

    def test_every_process_in_the_container_sees_the_same_key(self) -> None:
        """Refs #1062: ``docker compose exec``/``run`` erbt die Container-
        KONFIGURATION, nicht die Umgebung von PID 1.

        Ein nur in die Startshell exportierter Schluessel waere fuer jedes
        spaetere ``manage.py``-Kommando unsichtbar: es liefe still im
        Klartext-Modus von ``settings/dev.py``, schriebe Art.-9-Felder
        unverschluesselt und braeche auf den Lesepfaden mit
        ``EncryptionKeyMissing`` ab. Weitergereicht wird deshalb der PFAD, den
        jeder Prozess selbst liest.
        """
        services = self._compose()["services"]
        for name in ("init", "web"):
            pfad = services[name]["environment"].get("LOCAL_ENCRYPTION_KEY_FILE")
            assert pfad == self._KEY_FILE, (
                f"{name}: LOCAL_ENCRYPTION_KEY_FILE fehlt oder zeigt woandershin ({pfad!r}) — "
                "exec/run saehen den Schluessel dann nicht (Refs #1062)."
            )
            befehl = " ".join(services[name]["command"])
            assert "export ENCRYPTION_KEY" not in befehl, (
                f"{name}: Ein in die Startshell exportierter Schluessel erreicht nur PID 1 — "
                "der Pfad in LOCAL_ENCRYPTION_KEY_FILE erreicht alle Prozesse (Refs #1062)."
            )

        dev_settings = Path("src/anlaufstelle/settings/dev.py").read_text(encoding="utf-8")
        assert "LOCAL_ENCRYPTION_KEY_FILE" in dev_settings, (
            "settings/dev.py liest den Datei-Schluessel nicht mehr — der lokale Stack liefe "
            "in ``exec``/``run`` still im Klartext-Modus (Refs #1062)."
        )

    def test_init_and_web_share_one_image(self) -> None:
        """Gleicher Build-Kontext, gleiches Tag: ohne ``image:`` legte Compose
        zwei Tags an und baute denselben Kontext zweimal."""
        services = self._compose()["services"]
        tag = services["init"].get("image")
        assert tag, "init braucht ein ausdrueckliches Image-Tag (Refs #1062)."
        assert services["web"].get("image") == tag, (
            "init und web muessen sich ein Image-Tag teilen — sonst baut Compose "
            "denselben Kontext zweimal (Refs #1062)."
        )

    def test_generated_key_survives_a_restart(self) -> None:
        """Ein bei jedem Start neu erzeugter Schluessel liesse bereits
        verschluesselte Entwicklungsdaten unlesbar zurueck."""
        services = self._compose()["services"]
        volumes = self._compose().get("volumes", {})
        assert "localstate" in volumes, "Benanntes Volume fuer den lokalen Schluessel fehlt (Refs #1062)."
        for name in ("init", "web"):
            assert "localstate:/home/appuser" in services[name].get("volumes", []), (
                f"{name}: sieht den persistierten Schluessel nicht (Refs #1062)."
            )

    def test_local_stack_runs_settings_that_allow_seeding(self) -> None:
        """Der Seed ist fail-closed ueber ``SEED_ALLOWED`` gesperrt (Refs #1040).
        Der lokale Auto-Seed haengt also am gewaehlten Settings-Modul."""
        services = self._compose()["services"]
        for name in ("init", "web"):
            assert services[name]["environment"]["DJANGO_SETTINGS_MODULE"] == "anlaufstelle.settings.dev"
        dev_settings = Path("src/anlaufstelle/settings/dev.py").read_text(encoding="utf-8")
        assert re.search(r"^SEED_ALLOWED = True$", dev_settings, re.MULTILINE), (
            "settings/dev.py erlaubt kein Seeding mehr — der lokale Auto-Seed waere tot (Refs #1040/#1062)."
        )

    def test_production_settings_never_allow_seeding(self) -> None:
        prod_settings = Path("src/anlaufstelle/settings/prod.py").read_text(encoding="utf-8")
        assert "SEED_ALLOWED" not in prod_settings, (
            "settings/prod.py fasst SEED_ALLOWED an — der Default aus base.py (False) "
            "ist die einzige richtige Antwort fuer Produktion (Refs #1040)."
        )

    @pytest.mark.parametrize("compose_path", _DEPLOYMENT_COMPOSES)
    def test_deployment_stacks_neither_seed_nor_auto_migrate(self, compose_path: Path) -> None:
        """Der Auto-Pfad bleibt local-only: kein Deployment-Stack bekommt einen
        Bootstrap-Dienst, ein ``SEED_ALLOWED``, einen Seed-Aufruf oder eine
        Migration im Startpfad seiner Web-Replicas."""
        if not compose_path.exists():
            pytest.skip(f"{compose_path} nicht im Public-Snapshot — Guard nur im Dev-Repo relevant.")
        body = compose_path.read_text(encoding="utf-8")
        assert "SEED_ALLOWED" not in body, f"{compose_path}: SEED_ALLOWED gehoert nicht in einen Deployment-Stack."
        assert "manage.py seed" not in body, f"{compose_path}: kein Auto-Seed in einem Deployment-Stack."
        assert "bootstrap.sh" not in body, (
            f"{compose_path}: das lokale Bootstrap-Skript gehoert nicht in einen Deployment-Stack (Refs #1062)."
        )
        services = yaml.safe_load(body)["services"]
        assert "init" not in services, f"{compose_path}: kein Bootstrap-Dienst in einem Deployment-Stack (Refs #1062)."

        # Der Migrate-One-Shot bleibt ein eigener, ausdruecklich gestarteter
        # Job (``docker-migrate.sh`` im maintenance-Profil, Refs #802/#1962).
        # Im Startpfad einer Web-Replica haette er nichts verloren: parallele
        # Replicas konkurrierten um den Lock und die Deploy-Gates fielen weg.
        web = services["web"]
        startpfad = " ".join(
            teil if isinstance(teil, str) else " ".join(teil)
            for teil in (web.get("entrypoint", ""), web.get("command", ""))
        )
        assert "migrate" not in startpfad, (
            f"{compose_path}: web migriert im Startpfad ({startpfad!r}) — Migrationen gehoeren "
            "in den separaten One-Shot-Job (Refs #802/#1062)."
        )


class TestShippedShellScriptsSyntax:
    """Refs #793: Bis C-25 gab es keinen Guard, der die ausgelieferten
    Shell-Skripte ueberhaupt auf Syntax prueft — ein Tippfehler in
    ``scripts/ops/backup.sh`` waere erst beim naechtlichen Backup aufgefallen.

    Bewusst KEINE Modul-Level-Sammlung der Pfade: ``src/tests/**`` wird in den
    mutmut-``mutants/``-Tree kopiert, ``scripts/`` nicht — ein Import-Zeit-
    Zugriff wuerde dort das gesamte Test-Listing killen (Refs #1600).
    """

    @staticmethod
    def _shipped_scripts() -> list[Path]:
        scripts = sorted(Path("scripts").rglob("*.sh"))
        scripts += [p for p in (Path("docker-entrypoint.sh"), Path("docker-migrate.sh")) if p.exists()]
        return scripts

    def test_scripts_parse_with_bash_n(self) -> None:
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash nicht verfuegbar — Syntax-Guard nicht ausfuehrbar")
        scripts = self._shipped_scripts()
        assert scripts, "keine Shell-Skripte unter scripts/ gefunden — Glob kaputt?"
        failures = []
        for script in scripts:
            result = subprocess.run(  # noqa: S603 — fester bash-Pfad, Repo-eigene Dateien
                [bash, "-n", str(script)], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                failures.append(f"{script}: {result.stderr.strip()}")
        assert not failures, "Shell-Syntaxfehler:\n" + "\n".join(failures)

    def test_scripts_are_shellcheck_clean(self) -> None:
        """Laeuft nur, wenn shellcheck installiert ist (nicht Teil der
        Dev-Dependencies) — sonst Skip statt Fehlalarm."""
        shellcheck = shutil.which("shellcheck")
        if shellcheck is None:
            pytest.skip("shellcheck nicht installiert — optionaler Lint")
        failures = []
        for script in self._shipped_scripts():
            result = subprocess.run(  # noqa: S603 — fester Pfad, Repo-eigene Dateien
                [shellcheck, "--severity=error", str(script)], capture_output=True, text=True, check=False
            )
            if result.returncode != 0:
                failures.append(f"{script}:\n{result.stdout.strip()}")
        assert not failures, "shellcheck (severity=error):\n" + "\n".join(failures)


class TestCaddyUploadBodyLimit:
    """Refs #1363 (N10, Review-Nachschlag): Der Caddy ``request_body max_size``-
    Cap (Defense-in-Depth gegen Disk-Fill/OOM) war urspruenglich nur auf EINE
    Datei + kleine Multipart-Marge dimensioniert und ignorierte
    ``FILE_VAULT_MAX_UPLOAD_FILES`` (Default 20 Dateien/Feld,
    ``MultipleFileInput``/Refs #622) — ein bereits bestehendes, explizit
    unterstuetztes Feature. Ein legitimer Multi-File-Upload nahe dem
    Datei-Anzahl-Limit wurde am Edge mit 413 abgewiesen, obwohl jede einzelne
    Datei alle App-seitigen Groessen-/Typ-Checks bestanden haette.

    Dieser Guard haelt fest, dass der statische Caddy-Cap in JEDER der vier
    Caddyfile-Varianten mindestens den Worst-Case eines vollen Multi-File-
    Felds deckt (``FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES``)
    und alle Varianten synchron bleiben — schlaegt fehl, sobald eines der
    beiden Settings ohne Nachziehen des Caddy-Werts geaendert wird.
    """

    _CADDYFILES = (
        Path("Caddyfile"),
        Path("Caddyfile.dev"),
        Path("Caddyfile.demo"),
        Path("Caddyfile.staging"),
    )

    _UNITS = {"B": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3}

    @classmethod
    def _max_size_bytes(cls, path: Path) -> int:
        content = path.read_text(encoding="utf-8")
        match = re.search(r"max_size\s+(\d+)\s*(KiB|MiB|GiB|B)\b", content)
        assert match is not None, f"kein 'request_body max_size' in {path} gefunden"
        value, unit = match.groups()
        return int(value) * cls._UNITS[unit.upper()]

    @staticmethod
    def _skip_if_stripped_in_public_snapshot() -> None:
        # Caddyfile.staging und Caddyfile.demo strippt der Release-Build
        # (dev-ops/release/build-release.sh) aus dem Public-/Stage-Snapshot;
        # dort existieren nur Caddyfile + Caddyfile.dev, der Guard ist moot.
        if not Path("Caddyfile.staging").exists():
            pytest.skip("Caddyfile.staging nicht im Public-Snapshot (Refs #1047, #1363)")
        if not Path("Caddyfile.demo").exists():
            pytest.skip("Caddyfile.demo nicht im Public-Snapshot (Refs #1047, #1363)")

    def test_all_variants_cover_a_full_multi_file_field(self) -> None:
        import anlaufstelle.settings.base as base_settings

        self._skip_if_stripped_in_public_snapshot()
        worst_case_bytes = base_settings.FILE_VAULT_MAX_UPLOAD_BYTES * base_settings.FILE_VAULT_MAX_UPLOAD_FILES
        for path in self._CADDYFILES:
            caddy_cap_bytes = self._max_size_bytes(path)
            assert caddy_cap_bytes >= worst_case_bytes, (
                f"{path}: request_body max_size ({caddy_cap_bytes} Bytes) deckt "
                "nicht den Worst-Case eines vollen Multi-File-Felds "
                "(FILE_VAULT_MAX_UPLOAD_BYTES * FILE_VAULT_MAX_UPLOAD_FILES = "
                f"{worst_case_bytes} Bytes) — legitime, bereits unterstuetzte "
                "Multi-File-Uploads werden sonst am Edge mit 413 abgewiesen, "
                "obwohl sie alle App-seitigen Checks bestehen wuerden (Refs #1363)."
            )

    def test_all_variants_share_the_same_cap(self) -> None:
        self._skip_if_stripped_in_public_snapshot()
        caps = {str(path): self._max_size_bytes(path) for path in self._CADDYFILES}
        assert len(set(caps.values())) == 1, (
            f"Caddyfile-Varianten sind auseinandergedriftet: {caps} — alle vier "
            "muessen denselben request_body-Cap tragen (Refs #1363)."
        )


# Redaktions-Sonden (Refs #1963): reale Abfragen der Anwendung, an denen sich
# jede Redaktionsregel messen lassen muss — Volltextsuche, Objekt-IDs, ?next=.
_QUERY_SONDEN = (
    "/clients/?q=Suchbegriff&client=PSEUDOID&next=/cases/",
    "/statistics/?date_from=2026-01-01&assigned_to=PSEUDOID",
)
_QUERY_VERBOTEN = ("Suchbegriff", "PSEUDOID", "q=", "client=", "assigned_to=", "next=", "date_from=")
# Die drei Felder, die dieselbe Adresse jeweils erneut transportieren.
_REDIGIERTE_FELDER = ("request>uri", "request>headers>Referer", "resp_headers>Location")


def _redaktionsregel(code: str, feld: str, quelle: str) -> tuple[str, str]:
    """(Muster, Ersatz) der ``regexp``-Filterregel fuer ``feld`` im Log-Block."""
    treffer = re.search(rf'{re.escape(feld)}\s+regexp\s+"([^"]*)"\s+"([^"]*)"', code)
    assert treffer is not None, (
        f"{quelle}: kein 'regexp'-Log-Filter fuer '{feld}' — der Query-String "
        "landet sonst ungefiltert im Protokoll (Refs #1963)."
    )
    return treffer.group(1), treffer.group(2)


def _pruefe_redaktion(code: str, quelle: str) -> None:
    """Jede der drei Regeln gegen alle Sonden fahren — nichts darf uebrig bleiben."""
    for feld in _REDIGIERTE_FELDER:
        muster, ersatz = _redaktionsregel(code, feld, quelle)
        for sonde in _QUERY_SONDEN:
            redigiert = re.sub(muster, ersatz, sonde)
            for verboten in _QUERY_VERBOTEN:
                assert verboten not in redigiert, (
                    f"{quelle}: die Redaktionsregel fuer '{feld}' laesst '{verboten}' "
                    f"stehen ({sonde!r} -> {redigiert!r}) — Refs #1963."
                )


class TestCaddyAccessLogQueryRedaction:
    """Refs #1963 (Sicherheitsreview #1821, Access-Log): Der JSON-Access-Log
    protokollierte ``request>uri`` samt komplettem Query-String. Dort stehen
    Volltext-Suchbegriffe (``q``) und Objekt-IDs (``client``/``case``/``event``/
    ``user``/``assigned_to``) — also Klarnamen und Pseudonym-Referenzen, die
    ueber Wochen ausserhalb von Audit- und Loeschlogik im Log-Volume liegen.

    Bewusst KEINE Denylist einzelner Parameter: die driftet mit jedem neuen
    Filter. Der Query-Teil faellt komplett weg, der Pfad bleibt stehen — damit
    bleiben 4xx/5xx weiterhin einer Route zuzuordnen.

    Mitbehandelt sind die beiden anderen Felder, die dieselbe URL erneut
    transportieren: ``request>headers>Referer`` (bei gleichem Origin schickt
    der Browser trotz ``strict-origin-when-cross-origin`` die volle URL) und
    ``resp_headers>Location`` (Redirects mit ``?next=``).
    """

    _CADDYFILES = (
        Path("Caddyfile"),
        Path("Caddyfile.dev"),
        Path("Caddyfile.demo"),
        Path("Caddyfile.staging"),
        # Fuenfter, live laufender Site-Block: wird per ``import sites.d/*.caddy``
        # aus Caddyfile.dev geladen (ADR-017) und loggt in dasselbe Volume.
        Path("dev-ops/deploy/barbara/barbara.caddy"),
    )

    @staticmethod
    def _code(path: Path) -> str:
        # Der Release-Build (dev-ops/release/build-release.sh) nimmt Stage-,
        # Demo- und dev-ops-Pfade aus dem Public-Snapshot. Ihre Namen stehen
        # literal auf der Gate-Zeile, weil verify_release_test_guard.py den
        # ausgeschlossenen Prefix genau dort erwartet (Refs #1137).
        if not path.exists():  # Caddyfile.staging / Caddyfile.demo / dev-ops/
            pytest.skip(f"{path} nicht im Public-Snapshot (Refs #1047, #1963)")
        return "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_access_log_uses_a_filtering_encoder(self, path: Path) -> None:
        code = self._code(path)
        assert "format json" not in code, (
            f"{path}: 'format json' schreibt request>uri roh ins Access-Log. "
            "Erwartet wird 'format filter { ... wrap json }' (Refs #1963)."
        )
        assert re.search(r"format\s+filter\s*\{", code), (
            f"{path}: Access-Log ohne 'format filter'-Encoder (Refs #1963)."
        )
        assert re.search(r"wrap\s+json\b", code), (
            f"{path}: der Filter-Encoder muss weiterhin JSON ausgeben ('wrap json', Refs #1963)."
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_query_bearing_fields_are_redacted(self, path: Path) -> None:
        _pruefe_redaktion(self._code(path), str(path))

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_path_without_query_survives_untouched(self, path: Path) -> None:
        # Der Pfad ist die Debugging-Information, die erhalten bleiben soll —
        # eine zu gierige Regel wuerde 4xx/5xx unzuordenbar machen.
        code = self._code(path)
        muster, ersatz = _redaktionsregel(code, "request>uri", str(path))
        assert re.sub(muster, ersatz, "/clients/42/documents/") == "/clients/42/documents/", (
            f"{path}: die Redaktionsregel darf den Pfad nicht antasten (Refs #1963)."
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_retention_is_pinned_everywhere(self, path: Path) -> None:
        # Ohne ``roll_keep_for`` haelt Caddy rotierte Logs nach eigenem Default
        # (90 Tage) — laenger als die 720 h, die die Prod-Konfiguration seit
        # jeher vorgibt. Der Wert wird darum in jeder Variante ausgeschrieben.
        code = self._code(path)
        assert re.search(r"roll_keep_for\s+720h\b", code), (
            f"{path}: kein 'roll_keep_for 720h' — rotierte Access-Logs mit "
            "vollstaendigen Client-IPs blieben sonst nach Caddy-Default liegen (Refs #1963)."
        )


class TestCaddyErrorLogRedaction:
    """Refs #1963 (Nachbesserung): Der ``format filter``-Encoder haengt am
    Site-Logger und deckt nur ``http.log.access.*`` ab. Caddys FEHLER-Logger
    ``http.log.error.*`` bleibt davon unberuehrt und laeuft ungefiltert auf
    stderr — im Container also in den Docker-json-file-Log des Hosts: ausserhalb
    des ``caddy_logs``-Volumes, ausserhalb ``roll_keep_for 720h`` und ausserhalb
    jeder Loeschlogik.

    Er traegt dieselbe ``request>uri`` samt Query wie der Access-Log und
    entsteht bei JEDEM Handler-Fehler: Upstream weg (502/504 im Deploy-Fenster,
    Worker-OOM, gunicorn-Restart) oder 413 aus ``request_body max_size``.
    Nachgestellt am gepinnten Image (caddy 2.11.4, Upstream gestoppt, Aufruf
    ``/clients/?q=Suchbegriff&client=PSEUDOID``): im Datei-Log stand
    ``"uri":"/clients/?redigiert"``, auf stderr gleichzeitig
    ``{"logger":"http.log.error.log0",...,"uri":"/clients/?q=Suchbegriff&client=PSEUDOID"}``.

    Ein benannter globaler Logger holt ihn in dasselbe Volume, unter dieselbe
    Aufbewahrung und durch dieselbe Redaktion. Doppelt protokolliert wird
    dadurch nichts: Der Caddyfile-Adapter traegt ``http.log.error`` selbst in
    ``logging.logs.default.exclude`` ein (mit ``caddy adapt`` am selben Image
    geprueft), stderr bleibt fuer alles uebrige zustaendig.

    ``dev-ops/deploy/barbara/barbara.caddy`` steht hier nicht: Es ist ein per
    ``import`` eingebundener Site-Block, der benannte Logger ist eine globale
    Option und deckt aus Caddyfile.dev alle Sites des Servers ab.
    """

    _CADDYFILES = (
        Path("Caddyfile"),
        Path("Caddyfile.dev"),
        Path("Caddyfile.demo"),
        Path("Caddyfile.staging"),
    )

    @staticmethod
    def _code(path: Path) -> str:
        # Gate-Zeile mit vollem Pfad (Caddyfile.staging / Caddyfile.demo),
        # verify_release_test_guard.py erwartet den Prefix genau dort (#1137).
        if not path.exists():
            pytest.skip(f"{path} nicht im Public-Snapshot (Refs #1047, #1963)")
        return "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )

    @classmethod
    def _fehler_block(cls, path: Path) -> str:
        """Rumpf des benannten Logger-Blocks (``log <name> { ... }``).

        Der Site-Log heisst ``log {`` (ohne Namen) — nur der benannte Block ist
        eine globale Option und kann ``include`` fuehren.
        """
        code = cls._code(path)
        start = re.search(r"log\s+[A-Za-z_][\w-]*\s*\{", code)
        assert start is not None, (
            f"{path}: kein benannter Logger-Block ('log <name> {{ … }}') — Caddys "
            "Fehler-Logger laeuft dann ungefiltert auf stderr, also in den "
            "Docker-Log des Hosts, ausserhalb jeder Aufbewahrungsgrenze (Refs #1963)."
        )
        tiefe = 0
        for pos in range(start.end() - 1, len(code)):
            if code[pos] == "{":
                tiefe += 1
            elif code[pos] == "}":
                tiefe -= 1
                if tiefe == 0:
                    return code[start.end() : pos]
        raise AssertionError(f"{path}: benannter Logger-Block nicht geschlossen")

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_error_logger_is_captured(self, path: Path) -> None:
        block = self._fehler_block(path)
        assert re.search(r"include\s+http\.log\.error\b", block), (
            f"{path}: der benannte Logger muss 'include http.log.error' fuehren — "
            "sonst bleibt Caddys Fehler-Logger beim stderr-Default (Refs #1963)."
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_error_log_lands_in_the_retained_volume(self, path: Path) -> None:
        block = self._fehler_block(path)
        assert re.search(r"output\s+file\s+/var/log/caddy/\S+", block), (
            f"{path}: der Fehler-Logger muss nach /var/log/caddy schreiben — nur "
            "dieses Verzeichnis ist als Volume gemountet und damit von Rotation "
            "und Aufbewahrung erfasst (Refs #1963)."
        )
        assert re.search(r"roll_keep_for\s+720h\b", block), (
            f"{path}: dem Fehler-Log fehlt 'roll_keep_for 720h' — es laege sonst "
            "laenger vor als das Zugriffsprotokoll daneben (Refs #1963)."
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_error_log_redacts_the_same_fields(self, path: Path) -> None:
        block = self._fehler_block(path)
        assert re.search(r"wrap\s+json\b", block), (
            f"{path}: der Fehler-Logger muss weiterhin JSON ausgeben ('wrap json', Refs #1963)."
        )
        _pruefe_redaktion(block, f"{path} (Fehler-Logger)")

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_error_log_keeps_the_path_readable(self, path: Path) -> None:
        # Ohne Pfad ist ein 502 keiner Route mehr zuzuordnen — die Redaktion
        # darf nur den Query-Teil treffen.
        muster, ersatz = _redaktionsregel(self._fehler_block(path), "request>uri", f"{path} (Fehler-Logger)")
        assert re.sub(muster, ersatz, "/clients/42/documents/") == "/clients/42/documents/", (
            f"{path}: die Redaktionsregel des Fehler-Logs darf den Pfad nicht antasten (Refs #1963)."
        )


class TestCaddyTrustedProxies:
    """Refs #1963 (Sicherheitsreview #1821, Proxy-Vertrauen): Keine der
    Caddy-Konfigurationen erklaerte, welchen vorgelagerten Proxys sie glauben
    darf. Caddy ersetzt ein eingehendes ``X-Forwarded-For`` genau dann durch
    die Peer-IP, wenn der Peer NICHT in ``trusted_proxies`` steht — die
    dokumentierte CDN-Topologie mit ``TRUSTED_PROXY_HOPS=2`` bekam damit nie
    zwei Eintraege zu sehen und fiel auf ``REMOTE_ADDR`` (die Proxy-IP)
    zurueck: ein gemeinsamer Ratelimit-Eimer, eine Lockout-Achse und eine
    Audit-IP fuer alle.

    Die Bereiche kommen aus derselben Variable, die auch die Settings lesen —
    sonst driften Edge und Anwendung auseinander.
    """

    _ENV_VAR = "TRUSTED_PROXY_CIDRS"

    # barbara.caddy ist ein per ``import`` eingebundener Site-Block; globale
    # Server-Optionen gelten serverweit und werden von Caddyfile.dev gestellt.
    _CADDYFILES = (
        Path("Caddyfile"),
        Path("Caddyfile.dev"),
        Path("Caddyfile.demo"),
        Path("Caddyfile.staging"),
    )

    _COMPOSE = (
        Path("docker-compose.prod.yml"),
        Path("docker-compose.staging.yml"),
        Path("docker-compose.demo.yml"),
        Path("docker-compose.dev.yml"),
    )

    @staticmethod
    def _code(path: Path) -> str:
        if not path.exists():  # Caddyfile.staging / Caddyfile.demo (Refs #1137)
            pytest.skip(f"{path} nicht im Public-Snapshot (Refs #1047, #1963)")
        return "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_global_servers_block_declares_proxy_trust(self, path: Path) -> None:
        code = self._code(path)
        assert re.search(r"servers\s*\{", code), (
            f"{path}: kein globaler 'servers'-Options-Block — ohne ihn gibt es kein Proxy-Vertrauen (Refs #1963)."
        )
        assert re.search(r"trusted_proxies\s+static\s+\S", code), (
            f"{path}: 'trusted_proxies static <bereiche>' fehlt (Refs #1963)."
        )
        assert re.search(r"^\s*trusted_proxies_strict\s*$", code, re.MULTILINE), (
            f"{path}: 'trusted_proxies_strict' fehlt — ohne die Direktive liest "
            "Caddy die Client-IP-Header von links und nimmt damit den vom "
            "Client kontrollierten Eintrag (Refs #1963)."
        )

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_ranges_come_from_the_shared_env_var(self, path: Path) -> None:
        code = self._code(path)
        assert f"{{${self._ENV_VAR}" in code, (
            f"{path}: die vertrauten Bereiche muessen aus '{{${self._ENV_VAR}:...}}' "
            "stammen — dieselbe Variable liest settings.TRUSTED_PROXY_CIDRS, "
            "sonst driften Edge und Anwendung auseinander (Refs #1963)."
        )

    def test_setting_and_caddy_share_the_variable_name(self) -> None:
        import anlaufstelle.settings.base as base_settings

        assert hasattr(base_settings, self._ENV_VAR), (
            f"settings.base muss '{self._ENV_VAR}' fuehren — der System-Check "
            "koppelt TRUSTED_PROXY_HOPS > 1 daran (Refs #1963)."
        )

    @pytest.mark.parametrize("path", _COMPOSE, ids=str)
    def test_compose_passes_the_variable_to_caddy(self, path: Path) -> None:
        if not path.exists():  # docker-compose.staging.yml / docker-compose.demo.yml (Refs #1137)
            pytest.skip(f"{path} nicht im Public-Snapshot (Refs #1047, #1963)")
        inhalt = path.read_text(encoding="utf-8")
        caddy_block = inhalt.split("\n  caddy:\n", 1)
        assert len(caddy_block) == 2, f"{path}: kein 'caddy'-Service gefunden"
        assert f"- {self._ENV_VAR}" in caddy_block[1], (
            f"{path}: der caddy-Service reicht '{self._ENV_VAR}' nicht durch — "
            "die Caddyfile-Substitution bliebe auf ihrem Default stehen (Refs #1963)."
        )


def _flat_script(path: Path) -> str:
    """Skript ohne Kommentarzeilen, Zeilenfortsetzungen aufgeloest.

    Spiegelt das Muster aus ``TestPublicBackupScriptsRlsRoles._flat`` — damit
    matchen mehrzeilige Aufrufe als ein Statement und Prosa in Kommentaren
    verfaelscht die Guards nicht.
    """
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines).replace("\\\n", " ")


class TestOpsScriptPathResolution:
    """Refs #1336: ``scripts/ops/{backup,restore}.sh`` liegen unter
    ``<repo>/scripts/ops/``. PROJECT_DIR muss den Repo-Root treffen (dort liegt
    ``docker-compose.prod.yml``). Ein einfaches ``dirname "$SCRIPT_DIR"`` loeste
    nur auf ``<repo>/scripts`` auf -> COMPOSE_FILE zeigte ins Leere und jedes
    Backup/Restore scheiterte sofort. ``scripts/ops/`` ist im Public-Snapshot,
    also kein Skip.
    """

    SCRIPTS = ("scripts/ops/backup.sh", "scripts/ops/restore.sh")

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_no_single_dirname_bug(self, script: str) -> None:
        flat = _flat_script(Path(script))
        assert 'PROJECT_DIR="$(dirname "$SCRIPT_DIR")"' not in flat, (
            f"{script} berechnet PROJECT_DIR noch mit einfachem dirname -> zeigt "
            "auf <repo>/scripts statt Repo-Root (Refs #1336)."
        )

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_project_dir_resolves_two_levels_up(self, script: str) -> None:
        flat = _flat_script(Path(script))
        assert "${SCRIPT_DIR}/../.." in flat, (
            f"{script} muss PROJECT_DIR zwei Ebenen ueber scripts/ops/ aufloesen "
            "(Repo-Root mit docker-compose.prod.yml, Refs #1336)."
        )

    @pytest.mark.parametrize("script", SCRIPTS)
    def test_guards_missing_compose_file(self, script: str) -> None:
        flat = _flat_script(Path(script))
        assert '! -f "$COMPOSE_FILE"' in flat, (
            f"{script} sollte fruehzeitig abbrechen, wenn COMPOSE_FILE fehlt — "
            "statt einen kryptischen docker-Fehler zu werfen (Refs #1336)."
        )


class TestBackupVerifyCompletenessGuard:
    """Refs #1336 (DOC-P1): der ``--verify``-Pfad muss den stillen
    unvollstaendigen Dump (Dump unter NOBYPASSRLS-Rolle -> 0 Zeilen der
    FORCE-RLS-Tabellen) erkennbar machen: Zeilenzahl-Abgleich MEHRERER
    kritischer RLS-Tabellen zwischen Restore und Live-DB.
    """

    _BACKUP = Path("scripts/ops/backup.sh")

    def test_verify_compares_multiple_rls_tables(self) -> None:
        flat = _flat_script(self._BACKUP)
        for tbl in ("core_client", "core_event", "core_workitem", "core_auditlog"):
            assert tbl in flat, (
                f"--verify muss auch {tbl} gegen die Live-DB abgleichen — eine "
                "einzelne Tabelle laesst RLS-Luecken unentdeckt (Refs #1336)."
            )

    def test_verify_compares_restore_against_live(self) -> None:
        flat = _flat_script(self._BACKUP)
        assert '-d "$VERIFY_DB"' in flat and '-d "$POSTGRES_DB"' in flat, (
            "Der Vollstaendigkeits-Guard muss Restore-DB (VERIFY_DB) und Live-DB "
            "(POSTGRES_DB) vergleichen (Refs #1336)."
        )
        assert 'RESTORED_COUNT" -eq 0' in flat, (
            "Der Guard muss auf 'Live>0 aber Restore=0' (RLS-leerer Dump) fail-closed reagieren (Refs #1336)."
        )


class TestRestoreDrillProdFormat:
    """Refs #1336 (OPS-05): der Restore-Drill deckte nur das dev-Format
    (``dump-*.pgc.enc``, pg_restore) ab. Der an Self-Hoster ausgelieferte
    kanonische Backup-Pfad ist aber ``scripts/ops/backup.sh`` ->
    ``backups/daily/anlaufstelle_*.sql.gz.enc`` (plain SQL, psql,
    docker-compose.prod.yml). Der Drill muss dieses Prod-Format ebenso pruefen.
    """

    _DRILL = Path("scripts/ops/restore-drill.sh")

    def test_supports_prod_sql_gz_enc_backups(self) -> None:
        flat = _flat_script(self._DRILL)
        assert "anlaufstelle_*.sql.gz.enc" in flat, (
            "restore-drill.sh muss das Prod-Backup-Format (anlaufstelle_*.sql.gz.enc) kennen (OPS-05, Refs #1336)."
        )

    def test_supports_prod_compose_stack(self) -> None:
        flat = _flat_script(self._DRILL)
        assert "docker-compose.prod.yml" in flat, (
            "restore-drill.sh muss den Prod-Stack (docker-compose.prod.yml) ansprechen koennen (OPS-05, Refs #1336)."
        )

    def test_prod_uses_psql_restore_path(self) -> None:
        flat = _flat_script(self._DRILL)
        assert "gunzip" in flat and 'psql -U "$SU_USER" -d "$DRILL_DB"' in flat, (
            "Das Prod-Format ist plain SQL + gzip und wird via 'gunzip | psql' "
            "eingespielt (nicht pg_restore, OPS-05, Refs #1336)."
        )

    def test_prod_verifies_hmac_sidecar(self) -> None:
        flat = _flat_script(self._DRILL)
        assert "backup_verify_hmac" in flat, (
            "Prod-Backups tragen einen A4.3-HMAC-Sidecar — der Drill muss ihn VOR "
            "dem Entschluesseln pruefen (Refs #1336)."
        )

    def test_format_flag_selectable(self) -> None:
        flat = _flat_script(self._DRILL)
        assert "--prod" in flat and "--dev" in flat, (
            "restore-drill.sh muss das Format per --dev/--prod waehlbar machen (plus Auto-Erkennung, Refs #1336)."
        )

    def test_prod_completeness_guard_before_mark_verified(self) -> None:
        """Refs #1336 (Cleanup): der --prod-Zweig spielt via ``psql`` bewusst
        OHNE ON_ERROR_STOP ein (benigner MV-Refresh unter FORCE-RLS), wodurch der
        psql-Exit-Code auch bei ECHTEN Fehlern 0 bleibt. Ein leerer/teilweiser
        Restore darf den Compliance-Marker NICHT gruen faerben — daher muss ein
        Vollstaendigkeits-Abgleich (Wegwerf-DB vs. Live-DB, fail-closed bei
        Live>0 aber Restore=0, wie ``backup.sh --verify``) VOR
        ``mark_restore_verified`` laufen.
        """
        flat = _flat_script(self._DRILL)
        # Zeilenzahl-Abgleich Restore-DB (DRILL_DB) gegen Quelle/Live-DB (POSTGRES_DB).
        assert '-d "$DRILL_DB"' in flat and '-d "$POSTGRES_DB"' in flat, (
            "Der Drill muss die Wegwerf-DB (DRILL_DB) gegen die Live-DB "
            "(POSTGRES_DB) abgleichen — sonst bleibt ein leerer Restore unsichtbar (Refs #1336)."
        )
        assert 'RESTORED_COUNT" -eq 0' in flat, (
            "Der Vollstaendigkeits-Guard muss auf 'Live>0 aber Restore=0' "
            "(leerer/teilweiser Restore) fail-closed reagieren (Refs #1336)."
        )
        # Der Nachweis muss VOR mark_restore_verified stehen, sonst dokumentiert
        # der Drill einen unvollstaendigen Restore als verifiziert.
        guard_pos = flat.find('RESTORED_COUNT" -eq 0')
        mark_pos = flat.find("mark_restore_verified")
        assert guard_pos != -1 and mark_pos != -1, (
            "Vollstaendigkeits-Guard und mark_restore_verified muessen beide im Drill vorkommen (Refs #1336)."
        )
        assert guard_pos < mark_pos, (
            "Der Vollstaendigkeits-Check muss VOR mark_restore_verified stehen — "
            "sonst faerbt ein unvollstaendiger Restore den Compliance-Check gruen (Refs #1336)."
        )

    def test_requires_live_db_name_for_comparison(self) -> None:
        """Der Abgleich braucht den Live-DB-Namen. Fehlt POSTGRES_DB, koennte ein
        leerer Restore nicht erkannt werden — deshalb fail-closed abbrechen statt
        stumm zu vergleichen (Refs #1336)."""
        flat = _flat_script(self._DRILL)
        assert '"${POSTGRES_DB:?' in flat, (
            "restore-drill.sh muss POSTGRES_DB fail-closed einfordern (:?) — der "
            "Vollstaendigkeits-Abgleich gegen die Live-DB braucht den Namen (Refs #1336)."
        )


class TestOpsRunbookRetentionCrontabAdminRole:
    """Refs #1335/#1962: ``enforce_retention`` bricht ohne
    BYPASSRLS-Admin-Kontext genauso fail-loud ab wie ``verify_audit_chain``
    (``enforce_retention.py:73`` — identischer ``_has_rls_bypass_context()``-Guard).
    Jede Host-Crontab-Zeile im Runbook, die ``enforce_retention`` via
    ``docker compose exec`` startet, muss deshalb den
    den dedizierten ``maintenance``-Service laufen — sonst faehrt ein
    Self-Hoster, der den Block woertlich uebernimmt, die
    Aufbewahrungsfrist-Durchsetzung als RLS-gefilterte App-Rolle und sie bricht
    jede Nacht fail-loud ab: die exakte DSGVO-Retention-Luecke, die #1335
    schliessen soll.

    Refs #794: Seit dem ``cron``-Sidecar enthaelt das Runbook MEHRERE
    ```cron-Bloecke (Host-Cron in §3.3, Legacy-Vollblock im Details-Aufklapper).
    Der Guard prueft daher alle Bloecke statt nur den ersten — die
    Sidecar-Variante selbst bekommt ihre Admin-Rolle nicht per Zeile, sondern
    aus der ``environment:``-Sektion des Service (eigener Guard in
    ``test_ops_cron_sidecar.py``)."""

    _RUNBOOK = Path("docs/ops-runbook.md")

    def _crontab_blocks(self) -> list[str]:
        text = self._RUNBOOK.read_text(encoding="utf-8")
        blocks = re.findall(r"```cron\n(.*?)\n```", text, re.DOTALL)
        assert blocks, "kein ```cron-Block in ops-runbook.md § 3 gefunden"
        return blocks

    def test_enforce_retention_line_uses_maintenance_service(self) -> None:
        retention_lines = [
            line
            for block in self._crontab_blocks()
            for line in block.splitlines()
            if "enforce_retention" in line and not line.lstrip().startswith("#")
        ]
        assert retention_lines, "keine enforce_retention-Crontab-Zeile in ops-runbook.md § 3 gefunden"
        for line in retention_lines:
            assert "maintenance enforce_retention" in line, (
                "enforce_retention bricht ohne BYPASSRLS-Admin-Kontext fail-loud ab "
                "(enforce_retention.py:73) — die Beispiel-Crontab-Zeile muss den "
                "dedizierten Maintenance-Service nutzen, der allein die Admin-Secrets sieht "
                f"(Refs #1335). Betroffen: {line!r}"
            )


class TestProdBackupDirVisibility:
    """Refs #1872: der Prod-Stack muss die Backup-Ablage im ``web``-Container sehen.

    ``scripts/ops/backup.sh`` legt die verschluesselten Dumps unter
    ``<repo>/backups/{daily,weekly,monthly}`` auf dem **Host** ab. Im
    ``web``-Container zeigt ``settings.BACKUP_DIR`` per Default auf
    ``BASE_DIR.parent/backups`` — einen Pfad, den es im ``read_only``-Image
    gar nicht gibt. Ohne Mount + passende Variable melden die Backup-Zeile
    der ``/system/``-Health-Card und ``last_backup_age_hours`` in ``/health/``
    deshalb dauerhaft „Kein Backup gefunden", egal wie zuverlaessig der
    Host-Cron laeuft. ``docker-compose.dev.yml``/``.demo.yml`` haben den
    Mount seit Refs #871, der Prod-Stack fiel durchs Raster.
    """

    _COMPOSE = Path("docker-compose.prod.yml")
    _ENV_EXAMPLE = Path(".env.example")

    @staticmethod
    def _uncommented(path: Path) -> list[str]:
        return [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#")
        ]

    def test_prod_compose_mounts_backup_dir_read_only(self) -> None:
        lines = self._uncommented(self._COMPOSE)
        assert any(line.startswith("- ${BACKUP_HOST_DIR") and line.endswith(":ro") for line in lines), (
            "docker-compose.prod.yml muss die Host-Backup-Ablage read-only in den "
            "web-Container mounten (Refs #1872) — sonst zeigt /system/ dauerhaft "
            "'Kein Backup gefunden'."
        )

    def test_prod_compose_pins_backup_dir_env(self) -> None:
        lines = self._uncommented(self._COMPOSE)
        assert any(line.startswith("BACKUP_DIR: ${BACKUP_DIR") for line in lines), (
            "docker-compose.prod.yml muss BACKUP_DIR auf denselben Container-Pfad "
            "setzen wie das Mount-Ziel (Refs #1872)."
        )

    def test_env_example_documents_both_backup_paths(self) -> None:
        text = self._ENV_EXAMPLE.read_text(encoding="utf-8")
        assert "BACKUP_HOST_DIR=" in text, ".env.example muss BACKUP_HOST_DIR dokumentieren (Refs #1872)."
        assert "BACKUP_DIR=" in text, ".env.example muss BACKUP_DIR dokumentieren (Refs #1872)."


class TestDemoDeployTreeSync:
    """Refs #1971: ``deploy-demo.sh`` bringt den Serverbaum in einen definierten Zustand.

    Bis #1971 lief der Sync per ``tar``-over-ssh. ``tar x`` überschreibt
    vorhandene Dateien, **löscht aber nie welche**: alles, was eine frühere
    Version hatte und die neue nicht mehr, blieb auf dem Server liegen. Beim
    Rückbau der Demo auf ``v0.80.0`` überlebte so das in ``v0.81.0`` neu
    angelegte Paket ``src/core/urls/`` neben dem wiederhergestellten Modul
    ``src/core/urls.py`` — Python bevorzugt Pakete gegenüber gleichnamigen
    Modulen, also lief die v0.81-URL-Konfiguration gegen v0.80-Views und der
    Deploy brach mit ``ImportError`` ab. Dieselbe Fehlklasse trifft verwaiste
    Migrationen (anderer Migrationsgraph als lokal und in der CI), Templates,
    Static-Dateien und Management-Commands — beliebig lange unsichtbar, bis ein
    gelöschter Dateiname wieder relevant wird.

    Der Skriptkopf begründete den tar-Weg mit „Der Server hat KEIN rsync". Das
    ist widerlegt (``command -v rsync`` auf dem Demo-Host -> ``/usr/bin/rsync``),
    damit fällt die Sonderrolle weg: der Sync läuft wie in ``deploy-dev.sh``
    über ``rsync -a --delete``.

    ``--delete`` macht den Zielpfad sicherheitsrelevant — es räumt auf dem
    Server alles weg, was nicht im Build-Kontext steht. Ein leerer, relativer,
    zu flacher oder auf ein Systemverzeichnis zeigender ``REMOTE_DIR`` würde
    damit fremde Bäume ausräumen. Deshalb der Zielpfad-Guard, und deshalb ist
    ein abweichender ``REMOTE_DIR`` nur zusammen mit dem reinen Sync-Lauf
    erlaubt (der Stack-Teil ist über den Compose-Projektnamen und die
    systemd-Unit fest an ``/opt/anlaufstelle`` gebunden).
    """

    _SCRIPT = Path("dev-ops/deploy/deploy-demo.sh")

    def _flat(self) -> str:
        # ``dev-ops/`` ist im Public-/Stage-Snapshot ausgeschlossen
        # (build-release.sh) — voller Pfad in der Skip-Zeile, weil
        # verify_release_test_guard den ausgeschlossenen Prefix
        # (dev-ops/deploy/deploy-demo.sh) auf der Gate-Zeile verlangt (Refs #1137).
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/deploy-demo.sh nicht im Public-Snapshot (Refs #1971)")
        return _flat_script(self._SCRIPT)

    def _rsync_call(self) -> str:
        flat = self._flat()
        # Zeilenanfang-verankert: die Fehlermeldung des Zielpfad-Guards nennt
        # "rsync --delete" in Prosa — gemeint ist hier der echte Aufruf.
        call = re.search(r"^rsync\s[^\n]*", flat, re.MULTILINE)
        assert call is not None, (
            "deploy-demo.sh muss den Build-Kontext per rsync syncen (Refs #1971) — "
            "der tar-Weg loescht auf dem Server nie etwas."
        )
        return call.group(0)

    def test_sync_deletes_orphans_on_the_server(self) -> None:
        assert "--delete" in self._rsync_call(), (
            "Der Build-Kontext-Sync muss '--delete' setzen (Refs #1971) — sonst "
            "ueberleben geloeschte Dateien den Deploy und beschatten den neuen "
            "Stand (Vorfall: src/core/urls/ vs. src/core/urls.py)."
        )

    def test_no_tar_over_ssh_sync_left(self) -> None:
        flat = self._flat()
        assert "tar czf" not in flat and "tar xzf" not in flat, (
            "Der tar-over-ssh-Sync muss vollstaendig ersetzt sein (Refs #1971) — "
            "'tar x' ueberschreibt zwar, loescht aber nie."
        )

    def test_sync_preserves_server_side_env_file(self) -> None:
        call = self._rsync_call()
        assert re.search(r"--exclude=/?\.env\.demo(\s|$)", call), (
            "Der Sync muss .env.demo ausschliessen (Refs #1971) — die Datei liegt "
            "nur auf dem Server; ohne Exclude wuerde '--delete' sie entfernen."
        )

    def test_server_side_env_file_is_only_rewritten_on_a_version_change(self) -> None:
        """Akzeptanzkriterium 2 zu #1971 verlangt eine unveraenderte Pruefsumme der
        server-seitigen Env-Datei ueber den Deploy hinweg. Der Sync selbst fasst sie
        nicht an (Exclude), der Stack-Teil patcht aber ``SOURCE_CODE_VERSION``
        (Refs #1812) und schrieb sie damit bei JEDEM Lauf neu — auch wenn der Wert
        schon stimmte. Steht der Zielwert bereits drin, darf die Datei nicht
        angefasst werden; dann ist das AK fuer den Regelfall (denselben Stand erneut
        deployen) woertlich pruefbar und nur der Versionswechsel bleibt als bewusste,
        einzeilige Abweichung uebrig.
        """
        guard = re.search(r"grep[^\n]*SOURCE_CODE_VERSION=\$\{APP_VERSION\}[^\n]*", self._flat())
        assert guard is not None, (
            "Der SOURCE_CODE_VERSION-Patch muss kurzschliessen, wenn der Zielwert "
            "schon in der Env-Datei steht (Refs #1971 AK2) — sonst aendert jeder "
            "Deploy die Pruefsumme, obwohl sich inhaltlich nichts aendert."
        )

    def test_remote_dir_is_overridable(self) -> None:
        assert 'REMOTE_DIR="${REMOTE_DIR:-' in self._flat(), (
            "REMOTE_DIR muss ueberschreibbar sein (Refs #1971), damit der Sync "
            "gegen ein Scratch-Verzeichnis geprueft werden kann, ohne die "
            "laufende Demo anzufassen (Policy #1955)."
        )

    # --- Verhaltens-Guards: Skript real starten, ssh/rsync gestubbt ---------

    @staticmethod
    def _stub_env(tmp_path: Path, remote_dir: str | None, sync_only: str | None) -> tuple[dict[str, str], Path]:
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir(exist_ok=True)
        argv_log = tmp_path / "rsync.argv"
        (stub_dir / "rsync").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$RSYNC_ARGV_LOG"\n', encoding="utf-8"
        )
        # ssh protokolliert seine Argumente mit: nur so laesst sich pruefen, ob
        # ein Lauf beim reinen Sync stehen geblieben ist oder den Stack-Teil
        # (``bash -se`` mit dem Build/Migrate/Up-Heredoc) angefasst hat.
        (stub_dir / "ssh").write_text(
            '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$SSH_ARGV_LOG"\nexit 0\n', encoding="utf-8"
        )
        # curl gestubbt, damit der Voll-Deploy-Pfad im Test keinen Netzzugriff
        # versucht (und ein Fehlschlag dort nicht als Guard-Wirkung durchgeht).
        (stub_dir / "curl").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for stub in ("rsync", "ssh", "curl"):
            (stub_dir / stub).chmod(0o700)
        env = {
            "PATH": f"{stub_dir}:{os.environ.get('PATH', '')}",
            "HOME": str(tmp_path),
            # Fake-Host + gestubbtes ssh/rsync: der Test darf unter keinen
            # Umstaenden einen echten Deploy ausloesen.
            "DEMO_HOST": "stub@invalid.test",
            "APP_VERSION": "v0.0.0-guardtest",
            "RSYNC_ARGV_LOG": str(argv_log),
            "SSH_ARGV_LOG": str(tmp_path / "ssh.argv"),
        }
        if remote_dir is not None:
            env["REMOTE_DIR"] = remote_dir
        if sync_only is not None:
            env["DEPLOY_SYNC_ONLY"] = sync_only
        return env, argv_log

    def _run_script(
        self,
        tmp_path: Path,
        remote_dir: str | None,
        *,
        sync_only: str | None = "1",
        cwd: Path | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        script = self._SCRIPT.resolve() if self._SCRIPT.exists() else None
        if script is None:
            pytest.skip("dev-ops/deploy/deploy-demo.sh nicht im Public-Snapshot (Refs #1971)")
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash nicht verfuegbar — Verhaltens-Guard nicht ausfuehrbar")
        env, argv_log = self._stub_env(tmp_path, remote_dir, sync_only)
        result = subprocess.run(  # noqa: S603 — fester bash-Pfad, Repo-eigenes Skript, ssh/rsync gestubbt
            [bash, str(script)],
            env=env,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result, argv_log

    @pytest.mark.parametrize(
        "unsafe",
        [
            "relativ/pfad",
            "/",
            "/opt",
            "/opt/anlaufstelle/",
            "/opt/../etc/anlaufstelle",
            "/etc/anlaufstelle",
            "/var/lib/anlaufstelle",
            # Kein Systemverzeichnis, trotzdem toedlich: siehe
            # test_guard_rejects_the_service_users_home_itself.
            "/home",
            "/home/anlaufstelle",
            # Versteckte Verzeichnisse sind nie ein Deploy-Baum, aber oft ein
            # Zustandsspeicher (SSH-Keys, Credentials, Caches).
            "/home/anlaufstelle/.ssh",
            # Ausserhalb der Allowlist: der Guard laesst nur den Deploy-Baum
            # und Scratch-Verzeichnisse unterhalb eines Home-Verzeichnisses zu.
            "/srv/anlaufstelle",
            "/mnt/backups/anlaufstelle",
        ],
    )
    def test_guard_rejects_unsafe_remote_dir(self, tmp_path: Path, unsafe: str) -> None:
        result, argv_log = self._run_script(tmp_path, unsafe)
        assert result.returncode != 0, (
            f"REMOTE_DIR={unsafe!r} muss abgewiesen werden (Refs #1971) — "
            "'rsync --delete' auf diesen Pfad raeumt fremde Verzeichnisse aus."
        )
        assert "REMOTE_DIR" in result.stderr, f"Fehlermeldung muss REMOTE_DIR nennen, war: {result.stderr!r}"
        assert not argv_log.exists(), f"rsync lief trotz unzulaessigem REMOTE_DIR={unsafe!r}"

    def test_guard_rejects_the_service_users_home_itself(self, tmp_path: Path) -> None:
        """Das Home des Service-Users ist absolut, zweisegmentig, sonderzeichenfrei
        und kein Systemverzeichnis — eine Denylist bekannter Tabu-Pfade laesst es
        also durch. Ein ``rsync --delete`` dorthin loescht aber alles im Home, was
        nicht im Build-Kontext steht, darunter ``.ssh/authorized_keys``: genau der
        Schluessel, den das Server-Bootstrap dort ablegt. Ein einziger Lauf mit
        vergessenem letzten Pfadsegment nimmt damit den Deploy-Zugang zum Server
        mit. Der Guard muss deshalb als Allowlist arbeiten (fail-closed), nicht
        als Aufzaehlung der Faelle, an die jemand gedacht hat.
        """
        result, argv_log = self._run_script(tmp_path, "/home/anlaufstelle")
        assert result.returncode != 0, (
            "REMOTE_DIR='/home/anlaufstelle' muss abgewiesen werden (Refs #1971) — "
            "der Sync wuerde das Home des Service-Users ausraeumen."
        )
        assert not argv_log.exists(), "rsync lief mit dem Home-Verzeichnis als Ziel"

    def test_sync_targets_the_overridden_remote_dir(self, tmp_path: Path) -> None:
        result, argv_log = self._run_script(tmp_path, "/home/anlaufstelle/deploy-selftest")
        assert result.returncode == 0, f"Sync-Lauf gescheitert: {result.stderr!r}"
        argv = argv_log.read_text(encoding="utf-8").splitlines()
        assert "--delete" in argv, f"rsync ohne --delete aufgerufen: {argv!r}"
        assert argv[-1] == "stub@invalid.test:/home/anlaufstelle/deploy-selftest/", (
            f"rsync-Ziel folgt REMOTE_DIR nicht: {argv!r}"
        )
        assert any(arg.startswith("--exclude=") and arg.endswith(".env.demo") for arg in argv), (
            f".env.demo fehlt in den rsync-Excludes: {argv!r}"
        )

    def test_non_default_remote_dir_does_not_touch_the_stack(self, tmp_path: Path) -> None:
        """Ein abweichender REMOTE_DIR ohne reinen Sync-Lauf muss abbrechen: der
        Stack-Teil (Build/Migrate/Up/Reset-Timer) ist ueber den aus dem
        Verzeichnis-Basename abgeleiteten Compose-Projektnamen und die
        systemd-Unit fest an ``/opt/anlaufstelle`` gebunden — ein Lauf aus einem
        anderen Verzeichnis erzeugt einen zweiten Stack mit neuen Volumes."""
        result, argv_log = self._run_script(tmp_path, "/home/anlaufstelle/deploy-selftest", sync_only=None)
        assert result.returncode != 0, "abweichender REMOTE_DIR ohne Sync-Only-Lauf muss abbrechen (Refs #1971)"
        assert "DEPLOY_SYNC_ONLY" in result.stderr, (
            f"Die Meldung muss den erlaubten Sync-Only-Lauf nennen, war: {result.stderr!r}"
        )
        assert not argv_log.exists(), "rsync lief, obwohl der Lauf abgewiesen werden musste"

    @staticmethod
    def _ssh_calls(tmp_path: Path) -> list[str]:
        log = tmp_path / "ssh.argv"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    @pytest.mark.parametrize("spelling", ["1", "true", "TRUE", "yes", "on"])
    def test_sync_only_honours_common_boolean_spellings(self, tmp_path: Path, spelling: str) -> None:
        """Der Schalter darf nicht fail-open sein: verglich er hart gegen ``1``,
        fiel jede andere uebliche Schreibweise still in den vollen Deploy-Pfad —
        wer ``DEPLOY_SYNC_ONLY=true`` setzt, um nur den Sync zu pruefen, baute
        stattdessen das Image, migrierte die Demo-DB und erstellte den Stack neu.
        Alle anderen Guards hier sind fail-closed; dieser muss es auch sein.
        """
        result, argv_log = self._run_script(tmp_path, None, sync_only=spelling)
        assert result.returncode == 0, f"Sync-Lauf mit DEPLOY_SYNC_ONLY={spelling!r} gescheitert: {result.stderr!r}"
        assert argv_log.exists(), f"rsync lief nicht (DEPLOY_SYNC_ONLY={spelling!r})"
        assert not any("bash -se" in call for call in self._ssh_calls(tmp_path)), (
            f"DEPLOY_SYNC_ONLY={spelling!r} hat den Stack-Teil ausgeloest — der Schalter "
            "darf nicht nur das Literal '1' kennen (Refs #1971)."
        )

    def test_sync_only_rejects_an_unrecognised_value(self, tmp_path: Path) -> None:
        """Ein Wert, den das Skript nicht als Boolean deuten kann, ist ein Tippfehler
        oder ein Missverstaendnis — beides darf nicht in einen vollen Deploy kippen."""
        result, argv_log = self._run_script(tmp_path, None, sync_only="vielleicht")
        assert result.returncode != 0, "unklarer DEPLOY_SYNC_ONLY-Wert muss abbrechen (Refs #1971)"
        assert "DEPLOY_SYNC_ONLY" in result.stderr, (
            f"Fehlermeldung muss DEPLOY_SYNC_ONLY nennen, war: {result.stderr!r}"
        )
        assert not argv_log.exists(), "rsync lief trotz unklarem DEPLOY_SYNC_ONLY-Wert"

    def test_refuses_to_sync_from_a_foreign_working_directory(self, tmp_path: Path) -> None:
        """Quelle wie Ziel sind bei ``--delete`` sicherheitsrelevant: aus einem
        fremden Arbeitsverzeichnis gestartet wuerde der Serverbaum durch dessen
        Inhalt ERSETZT statt aktualisiert."""
        foreign = tmp_path / "fremdes-verzeichnis"
        foreign.mkdir()
        result, argv_log = self._run_script(tmp_path, "/home/anlaufstelle/deploy-selftest", cwd=foreign)
        assert result.returncode != 0, "Start ausserhalb des Repo-Roots muss abbrechen (Refs #1971)"
        # Bewusst ohne den Namen der Compose-Datei in der Assertion: der
        # Release-Snapshot strippt sie, und verify_release_test_guard verbietet
        # ungegatete Literale ausgeschlossener Pfade in ausgelieferten Tests.
        assert "Repo-Root" in result.stderr, f"Die Meldung muss auf den Repo-Root verweisen, war: {result.stderr!r}"
        assert not argv_log.exists(), "rsync lief aus einem fremden Arbeitsverzeichnis"


class TestBootstrapInstallsSyncDependency:
    """Refs #1971: Der Build-Kontext geht per ``rsync`` an dev und demo — ohne
    Fallback. Auf einem Debian-Minimal-Image ist ``rsync`` nicht vorinstalliert;
    fehlt es, bricht der erste Deploy nach einem Server-Neuaufbau mit
    ``rsync: command not found`` ab (Exit 127 unter ``set -e``), bevor
    irgendetwas uebertragen ist. Der tar-Weg, der bis #1971 ohne Zusatzpaket
    auskam, ist bewusst weg — also muss das Server-Bootstrap das Paket
    mitbringen. Der laufende Demo-Host hat rsync zufaellig schon (haendisch
    nachinstalliert), der naechste Neuaufbau haette es nicht.
    """

    _SCRIPT = Path("dev-ops/deploy/bootstrap.sh")

    def test_apt_package_list_contains_rsync(self) -> None:
        # ``dev-ops/`` ist im Public-/Stage-Snapshot ausgeschlossen — voller Pfad
        # in der Skip-Zeile, weil verify_release_test_guard den ausgeschlossenen
        # Prefix (dev-ops/deploy/bootstrap.sh) auf der Gate-Zeile verlangt
        # (Refs #1137).
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/bootstrap.sh nicht im Public-Snapshot (Refs #1971)")
        flat = _flat_script(self._SCRIPT)
        installs = re.findall(r"apt-get install[^\n]*", flat)
        assert installs, "kein 'apt-get install' im Server-Bootstrap gefunden"
        assert any(re.search(r"(?<![\w-])rsync(?![\w-])", call) for call in installs), (
            "Das Server-Bootstrap muss 'rsync' mitinstallieren (Refs #1971) — "
            "die Deploy-Skripte syncen den Build-Kontext ausschliesslich damit, "
            "und ein frisch aufgesetzter Server hat es nicht."
        )


class TestReleaseWorkflowSupplyChain:
    """Refs #2061: Release-Images bleiben bis zum Digest nachweisbar."""

    _WORKFLOW = Path(".github/workflows/release.yml")
    _PROD_COMPOSE = Path("docker-compose.prod.yml")
    _RUNBOOKS = (Path("docs/ops-runbook.md"), Path("docs/en/ops-runbook.md"))

    @classmethod
    def _steps(cls) -> list[dict[str, object]]:
        workflow = yaml.safe_load(cls._WORKFLOW.read_text(encoding="utf-8"))
        return workflow["jobs"]["build-and-push"]["steps"]

    def test_keyless_signature_uses_oidc_and_build_digest(self) -> None:
        workflow = yaml.safe_load(self._WORKFLOW.read_text(encoding="utf-8"))
        job = workflow["jobs"]["build-and-push"]
        assert job["permissions"].get("id-token") == "write", (
            "Der Release-Job braucht 'id-token: write' — ohne OIDC-Token schlaegt Cosign-Keyless fehl (Refs #2061)."
        )

        build = next(
            (step for step in job["steps"] if str(step.get("uses", "")).startswith("docker/build-push-action@")),
            None,
        )
        assert build is not None, "docker/build-push-action fehlt im Release-Job (Refs #2061)"
        assert build.get("id") == "build", (
            "Der Build-Step braucht die ID 'build'; sonst fehlt Cosign der Digest-Output (Refs #2061)."
        )
        assert "max" in str(build["with"].get("provenance", "")), (
            "Der Image-Build muss maximale Provenance attestieren (Refs #2061)."
        )
        assert build["with"].get("sbom") is True, "Der Image-Build muss eine SBOM-Attestation erzeugen (Refs #2061)."

        sign = next((step for step in job["steps"] if "cosign sign" in str(step.get("run", ""))), None)
        assert sign is not None, "Dem Release-Job fehlt die Cosign-Signatur (Refs #2061)"
        sign_call = next(line for line in str(sign["run"]).splitlines() if "cosign sign" in line)
        assert "steps.build.outputs.digest" in str(sign), (
            "Cosign muss den Digest-Output des Build-Steps signieren (Refs #2061)."
        )
        assert ":${{ github.ref_name }}" not in sign_call and ":latest" not in sign_call, (
            "Cosign muss den unveraenderlichen Digest statt eines beweglichen Tags signieren (Refs #2061)."
        )

    def test_all_actions_are_commit_sha_pinned(self) -> None:
        for step in self._steps():
            uses = step.get("uses")
            if uses is not None:
                assert re.search(r"@[0-9a-f]{40}$", str(uses)), (
                    f"Action {uses!r} ist nicht auf einen 40-stelligen Commit-SHA gepinnt (Refs #2061)."
                )

    def test_all_app_services_support_the_same_optional_digest_pin(self) -> None:
        image_lines = [
            line.strip()
            for line in self._PROD_COMPOSE.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("image: ghcr.io/anlaufstelle/app:")
        ]
        assert len(image_lines) == 3, (
            "Prod-Compose muss genau web, maintenance und cron auf das Release-Image setzen (Refs #2061)."
        )
        assert len(set(image_lines)) == 1, (
            "web, maintenance und cron muessen wortgleich auf dasselbe Image zeigen — sonst migriert maintenance "
            "gegen anderen Code, als web ausliefert (Refs #2061)."
        )
        assert "${APP_VERSION" in image_lines[0] and "${APP_IMAGE_DIGEST" in image_lines[0], (
            "Das App-Image muss APP_VERSION und das optionale APP_IMAGE_DIGEST kombinieren (Refs #2061)."
        )

    def test_digest_variable_is_documented_for_operators_in_both_languages(self) -> None:
        for path in self._RUNBOOKS:
            assert "APP_IMAGE_DIGEST" in path.read_text(encoding="utf-8"), (
                f"{path} muss APP_IMAGE_DIGEST erklaeren — eine undokumentierte Compose-Variable ist fuer Betreiber "
                "nicht nutzbar (Refs #2061)."
            )


class TestDemoDeployPullsTaggedRelease:
    """Refs #2064: Demo-Deploys verwenden ausschliesslich Release-Images."""

    _WORKFLOW = Path(".github/workflows/release.yml")
    _SCRIPT = Path("dev-ops/deploy/deploy-demo.sh")

    def _flat(self) -> str:
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/deploy-demo.sh nicht im Public-Snapshot (Refs #2064)")
        return _flat_script(self._SCRIPT)

    def _run_stubbed(
        self,
        tmp_path: Path,
        remote_dir: str | None,
        *,
        sync_only: str | None,
        without_app_version: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/deploy-demo.sh nicht im Public-Snapshot (Refs #2064)")
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash nicht verfuegbar — Verhaltens-Guard nicht ausfuehrbar")
        env, argv_log = TestDemoDeployTreeSync._stub_env(tmp_path, remote_dir, sync_only)
        if without_app_version:
            env.pop("APP_VERSION")
        if extra_env is not None:
            env.update(extra_env)
        result = subprocess.run(  # noqa: S603 — fester bash-Pfad, Repo-Skript, Netzwerk-Kommandos gestubbt
            [bash, str(self._SCRIPT.resolve())],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return result, argv_log

    def test_release_workflow_rejects_non_semver_before_checkout(self) -> None:
        workflow = yaml.safe_load(self._WORKFLOW.read_text(encoding="utf-8"))
        first = workflow["jobs"]["build-and-push"]["steps"][0]
        assert first.get("name") == "Validate semver tag", (
            "Der Semver-Guard muss vor Checkout/Build laufen; spaeter waere er wirkungslos (Refs #2064)."
        )
        assert "github.ref_name" in str(first), "Der Semver-Guard muss den ausloesenden Tag pruefen (Refs #2064)."
        assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in str(first.get("run", "")), (
            "Der Release-Workflow muss ausschliesslich exakte vX.Y.Z-Tags akzeptieren (Refs #2064)."
        )

    def test_demo_has_no_ad_hoc_version_default_or_server_build(self) -> None:
        flat = self._flat()
        assert "demo-$(git rev-parse" not in flat, (
            "Der Demo-Deploy darf nicht still auf einen demo-<sha>-Stand ausweichen (Refs #2064)."
        )
        assert "docker build" not in flat, (
            "Die Demo muss das publizierte Image ziehen statt es auf dem Server zu bauen (Refs #2064)."
        )

    def test_demo_pulls_release_and_updates_the_compose_local_tag(self) -> None:
        flat = self._flat()
        assert 'docker pull "${DEMO_IMAGE}:${APP_VERSION}"' in flat, (
            "Der Demo-Deploy muss das durch APP_VERSION bestimmte Release-Image ziehen (Refs #2064)."
        )
        assert re.search(
            r'docker tag\s+"\$\{DEMO_IMAGE\}:\$\{APP_VERSION\}"\s+anlaufstelle:demo',
            flat,
        ), "Ohne lokales Tag anlaufstelle:demo startet Compose den alten Stand (Refs #2064)."

    def test_demo_semver_guard_runs_before_rsync(self) -> None:
        flat = self._flat()
        semver = flat.find(r"^v[0-9]+\.[0-9]+\.[0-9]+")
        rsync = flat.find("rsync -a --delete")
        assert semver != -1, "Dem Demo-Deploy fehlt der APP_VERSION-Format-Guard (Refs #2064)"
        assert rsync != -1 and semver < rsync, (
            "Der APP_VERSION-Guard muss vor rsync abbrechen; sonst wird ungueltiger Stand synchronisiert (Refs #2064)."
        )

    def test_sync_only_does_not_require_app_version(self, tmp_path: Path) -> None:
        result, argv_log = self._run_stubbed(
            tmp_path,
            "/home/anlaufstelle/deploy-selftest",
            sync_only="1",
            without_app_version=True,
        )
        assert result.returncode == 0, (
            "Ein reiner Sync braucht kein Release-Image und darf deshalb APP_VERSION nicht verlangen (Refs #2064): "
            f"{result.stderr!r}"
        )
        assert argv_log.exists(), "Der dokumentierte Sync-Only-Selbsttest muss den Build-Kontext synchronisieren"
        assert not any("bash -se" in call for call in TestDemoDeployTreeSync._ssh_calls(tmp_path)), (
            "Sync-Only ohne APP_VERSION darf den Stack-Teil nicht ausloesen"
        )

    def test_ghcr_login_rejects_shell_unsafe_username(self, tmp_path: Path) -> None:
        result, _ = self._run_stubbed(
            tmp_path,
            None,
            sync_only=None,
            extra_env={"GHCR_USER": "bad'user", "GHCR_TOKEN": "guard-secret"},
        )
        assert result.returncode != 0, (
            "Ein shell-unsauberer GHCR_USER muss vor docker login abgewiesen werden (Refs #2064)"
        )
        assert "GHCR_USER" in result.stderr, f"Die Fehlermeldung muss GHCR_USER nennen, war: {result.stderr!r}"

    def test_ghcr_login_uses_stdin_without_ssh_argument_assignment(self) -> None:
        flat = self._flat()
        login = next(
            (line for line in flat.splitlines() if "docker login ghcr.io" in line and "--password-stdin" in line),
            None,
        )
        assert login is not None and "GHCR_TOKEN" in login and "| ssh" in login, (
            "Der optionale GHCR-Login muss das Token ausschliesslich per stdin uebertragen (Refs #2064)."
        )
        assert re.search(r"\bssh\b[^\n]*\bGHCR_(?:TOKEN|USER)=", flat) is None, (
            "GHCR-Zugangsdaten duerfen nie als ssh-Zuweisung in der Server-Prozessliste erscheinen (Refs #2064)."
        )


class TestUfwDockerPinning:
    """Refs #2062: Das Bootstrap fuehrt ufw-docker als root aus; der Download
    muss deshalb unbeweglich gepinnt und vor der Installation verifiziert sein.
    """

    _SCRIPT = Path("dev-ops/deploy/bootstrap.sh")

    def _flat(self) -> str:
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/bootstrap.sh nicht im Public-Snapshot (Refs #2062)")
        return _flat_script(self._SCRIPT)

    def test_no_moving_ref_in_ufw_docker_source(self) -> None:
        flat = self._flat()
        for moving_ref in ("/raw/master/", "/master/ufw-docker", "refs/heads/"):
            assert moving_ref not in flat, f"ufw-docker nutzt beweglichen Ref {moving_ref!r} (Refs #2062)"

    def test_pin_constants_are_wellformed(self) -> None:
        flat = self._flat()
        commit_match = re.search(r'^UFW_DOCKER_COMMIT="([^"]+)"$', flat, re.MULTILINE)
        checksum_match = re.search(r'^UFW_DOCKER_SHA256="([^"]+)"$', flat, re.MULTILINE)
        assert commit_match is not None, "UFW_DOCKER_COMMIT fehlt im Bootstrap (Refs #2062)"
        assert checksum_match is not None, "UFW_DOCKER_SHA256 fehlt im Bootstrap (Refs #2062)"
        assert re.fullmatch(r"[0-9a-f]{40}", commit_match.group(1)), "Commit-Pin muss 40 Hex-Zeichen haben"
        assert re.fullmatch(r"[0-9a-f]{64}", checksum_match.group(1)), "sha256-Pin muss 64 Hex-Zeichen haben"

    def test_checksum_is_verified_before_install(self) -> None:
        flat = self._flat()
        checksum_index = flat.index('sha256sum "$ufw_docker_tmp"')
        install_index = flat.index('install -m 0755 "$ufw_docker_tmp" /usr/local/bin/ufw-docker')
        assert checksum_index < install_index, "ufw-docker muss vor der Installation verifiziert werden (Refs #2062)"

    def test_mismatch_aborts_and_never_writes_the_target_directly(self) -> None:
        flat = self._flat()
        mismatch = re.search(
            r'if \[ "\$ufw_docker_actual" != "\$UFW_DOCKER_SHA256" \]; then(?P<body>.*?)\nfi',
            flat,
            re.DOTALL,
        )
        assert mismatch is not None, "expliziter sha256-Mismatch-Zweig fehlt (Refs #2062)"
        assert "exit 1" in mismatch.group("body"), "sha256-Mismatch muss fail-loud abbrechen (Refs #2062)"
        assert not re.search(r"curl\b[^\n]*-o\s+/usr/local/bin/ufw-docker", flat), (
            "curl darf nie direkt in den root-Ausfuehrungspfad schreiben (Refs #2062)"
        )

    def test_existing_binary_is_revalidated_against_the_pin(self) -> None:
        flat = self._flat()
        assert "if ! command -v ufw-docker" not in flat, (
            "eine vorhandene ungepinnte Kopie darf nicht blind uebersprungen werden (Refs #2062)"
        )
        assert 'sha256sum "$ufw_docker_target"' in flat, (
            "eine vorhandene ufw-docker-Datei muss gegen den Pin geprueft werden (Refs #2062)"
        )
        assert '"$ufw_docker_installed_sha256" = "$UFW_DOCKER_SHA256"' in flat, (
            "die Bestandspruefung muss den gemessenen Hash mit dem Pin vergleichen (Refs #2062)"
        )

    def test_bootstrap_parses_with_bash_n(self) -> None:
        if not self._SCRIPT.exists():
            pytest.skip("dev-ops/deploy/bootstrap.sh nicht im Public-Snapshot (Refs #2062)")
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("bash nicht verfuegbar — Syntax-Guard nicht ausfuehrbar")
        result = subprocess.run(  # noqa: S603 — fester bash-Pfad, Repo-eigene Datei
            [bash, "-n", str(self._SCRIPT)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"Shell-Syntaxfehler in {self._SCRIPT}:\n{result.stderr.strip()}"


class TestStagingStackRoleSeparation:
    """Refs #2086: Staging fuhr den Webprozess als Postgres-Bootstrap-Superuser.

    ``docker-compose.staging.yml`` war der einzige Deployment-Stack, der das
    Drei-Rollen-Modell (ADR-005/017/020, Refs #902/#1026) nie nachgezogen hat:
    kein ``deploy/postgres-init``-Mount, ``db`` und ``web`` beide auf rohem
    ``env_file``. Damit verband die Runtime als SUPERUSER mit BYPASSRLS —
    ``FORCE ROW LEVEL SECURITY`` war auf Staging faktisch wirkungslos, und der
    Stack validierte genau die Fehlerklasse nicht, fuer die es ihn gibt.

    Der Nachweis kann nur statisch sein: es gibt keinen Laufzeit-Test, der die
    echte Postgres-Rolle des Webprozesses prueft (die Unit-Tests laufen selbst
    als Superuser). Dieser Guard plus der Hand-Check auf dem Server
    (``check_db_roles``) sind der gesamte Beweis.
    """

    _STAGING_COMPOSE = Path("docker-compose.staging.yml")
    _INITIAL_SETUP = Path("scripts/initial-setup.sh")
    # Die drei Variablen, die ``settings/prod.py`` (Refs #1962) im
    # Runtime-Prozess fail-closed abweist.
    _PRIVILEGED = {
        "POSTGRES_ADMIN_USER",
        "POSTGRES_ADMIN_PASSWORD",
        "POSTGRES_BOOTSTRAP_PASSWORD",
    }
    # Hardening-Stanzas, die beim Kopieren aus prod still verloren gehen
    # koennen — ein halb kopierter Service laeuft einwandfrei (Refs #1374).
    _HARDENING = ("security_opt", "cap_drop", "read_only", "pids_limit", "mem_limit")

    @classmethod
    def _services(cls) -> dict:
        if not cls._STAGING_COMPOSE.exists():
            pytest.skip(
                "docker-compose.staging.yml / .env.staging.example nicht im Public-Snapshot (Refs #1137, #2086)"
            )
        return yaml.safe_load(cls._STAGING_COMPOSE.read_text(encoding="utf-8"))["services"]

    def test_db_bootstraps_via_postgres_init_not_via_env_file(self) -> None:
        db = self._services()["db"]
        assert "env_file" not in db, (
            "staging-db darf .env.staging nicht roh laden — POSTGRES_USER waere "
            "sonst die App-Rolle und damit der Bootstrap-Superuser (Refs #2086)."
        )
        environment = db.get("environment", {})
        assert environment.get("POSTGRES_USER") == "postgres"
        assert environment.get("REQUIRE_ADMIN_DB_ROLE") == "true", (
            "Ohne REQUIRE_ADMIN_DB_ROLE faellt 01-app-role.sh auf den unsicheren "
            "Legacy-Pfad zurueck (App-Rolle wird selbst Owner)."
        )
        assert environment.get("APP_DB_USER") == "${POSTGRES_USER}"
        assert environment.get("ADMIN_DB_USER") == "${POSTGRES_ADMIN_USER}"
        assert "./deploy/postgres-init:/docker-entrypoint-initdb.d:ro" in db.get("volumes", []), (
            "Ohne den Init-Mount legt der Cluster App-/Admin-Rolle nie an (Refs #2086)."
        )

    def test_db_healthcheck_probes_the_bootstrap_role(self) -> None:
        db = self._services()["db"]
        test = " ".join(db["healthcheck"]["test"])
        assert "pg_isready -U postgres" in test, (
            f"Der Healthcheck muss die Bootstrap-Rolle literal pruefen, war: {test!r}. "
            "$$POSTGRES_USER zeigte auf die alte, aus .env.staging geladene Rolle."
        )

    def test_web_runs_as_the_unprivileged_app_role(self) -> None:
        web = self._services()["web"]
        assert "env_file" not in web, (
            "staging-web darf keine vollstaendige Env-Datei laden — sie fuehrt jetzt "
            "Admin-/Bootstrap-Secrets, die prod.py im Runtime-Prozess fail-closed "
            "abweist (Refs #1962/#2086)."
        )
        environment = web.get("environment", {})
        leaked = self._PRIVILEGED.intersection(environment)
        assert not leaked, f"privilegierte DB-Variablen in staging-web: {sorted(leaked)}"
        assert environment.get("ANLAUFSTELLE_PROCESS_ROLE") == "runtime"
        assert environment.get("POSTGRES_APP_USER") == "${POSTGRES_USER}", (
            "check_db_roles und der konditionale Funktions-GRANT lesen den stabilen "
            "App-Rollennamen aus POSTGRES_APP_USER (Refs #1017) — fehlt er, wird die "
            "Pruefung bzw. der GRANT still uebersprungen."
        )

    def test_maintenance_service_exists_and_uses_the_admin_role(self) -> None:
        maintenance = self._services().get("maintenance")
        assert maintenance is not None, (
            "Ohne maintenance-Service gibt es auf Staging keinen Migrate-/Operator-Pfad "
            "— scripts/initial-setup.sh bricht mit 'service maintenance is not defined' ab."
        )
        assert "maintenance" in maintenance.get("profiles", [])
        environment = maintenance.get("environment", {})
        assert environment.get("ANLAUFSTELLE_PROCESS_ROLE") == "maintenance"
        assert environment.get("POSTGRES_USER") == "${POSTGRES_ADMIN_USER}"
        assert environment.get("POSTGRES_PASSWORD") == "${POSTGRES_ADMIN_PASSWORD}"
        assert environment.get("POSTGRES_BOOTSTRAP_PASSWORD") == "${POSTGRES_BOOTSTRAP_PASSWORD}"
        assert environment.get("POSTGRES_APP_USER") == "${POSTGRES_USER}"

    def test_cron_sidecar_runs_jobs_as_the_admin_role(self) -> None:
        cron = self._services().get("cron")
        assert cron is not None, (
            "Ohne cron-Sidecar laufen Retention/Breach/Audit-Verifikation auf Staging nie "
            "— der Stack waere nicht prod-repraesentativ (Refs #794/#2086)."
        )
        environment = cron.get("environment", {})
        assert environment.get("POSTGRES_USER") == "${POSTGRES_ADMIN_USER}"
        assert environment.get("POSTGRES_PASSWORD") == "${POSTGRES_ADMIN_PASSWORD}"
        assert environment.get("POSTGRES_APP_USER") == "${POSTGRES_USER}"
        assert "./deploy/crontab:/etc/anlaufstelle/crontab:ro" in cron.get("volumes", [])

    @pytest.mark.parametrize("service_name", ("maintenance", "cron"))
    def test_new_services_carry_the_full_container_hardening(self, service_name: str) -> None:
        service = self._services().get(service_name, {})
        missing = [key for key in self._HARDENING if key not in service]
        assert not missing, (
            f"docker-compose.staging.yml: {service_name} fehlt Hardening {missing} — "
            "ein halb aus prod kopierter Service laeuft, nur ungehaertet (Refs #1374)."
        )
        assert service.get("cap_drop") == ["ALL"]
        assert service.get("read_only") is True

    def test_initial_setup_passes_the_env_file_through(self) -> None:
        body = self._INITIAL_SETUP.read_text(encoding="utf-8")
        wrapper = re.search(r"^compose\(\)[^\n]*$", body, re.MULTILINE)
        assert wrapper is not None, "compose()-Wrapper in scripts/initial-setup.sh nicht gefunden."
        assert "--env-file" in wrapper.group(0), (
            "Auf Staging kommen alle ${…}-Werte — inklusive der DB-Rollen-Secrets — ueber "
            "Compose-Interpolation; ohne --env-file bricht das DB-Init an fehlendem "
            "APP_DB_USER ab (Refs #1370/#2086)."
        )


class TestStagingStackFailClosedRolePosture:
    """Refs #2086 (Fix-Runde 1): Die Rollentrennung muss fail-closed sein.

    Der erste Wurf verdrahtete den Soll-Pfad korrekt, liess aber jede
    Fehlkonfiguration durch: ``APP_DB_USER=postgres`` oder App-Rolle ==
    Admin-Rolle wurden vom Init-Hook widerspruchslos akzeptiert (die
    ``WHERE NOT EXISTS``-Klausel uebersprang dann einfach das ``CREATE ROLE``
    und der bestehende Bootstrap-Superuser blieb), und der dokumentierte
    Direktstart ``docker compose ... up -d`` fuehrte ueberhaupt kein
    Rollen-Gate aus — das lebte nur im optionalen ``initial-setup.sh`` und im
    Migrate-One-Shot.

    Verschaerfend: der Postgres-Entrypoint legt ``PG_VERSION`` per ``initdb``
    an, BEVOR er ``/docker-entrypoint-initdb.d/*`` abarbeitet. Scheitert der
    Hook, bleibt ein initialisiertes PGDATA zurueck, ``restart:
    unless-stopped`` startet den Bootstrap-only-Cluster neu, und die
    Init-Skripte werden uebersprungen. Ein Gate, das nur beim ersten Init
    greift, ist damit kein Gate.

    Deshalb zwei unabhaengige Sicherungen, die dieser Guard pinnt:
    Namensinvarianten im Init-Preflight, und eine Laufzeit-Posture-Probe im
    ``web``-Healthcheck, die bei JEDEM Start (und danach dauerhaft) laeuft.
    """

    _STAGING_COMPOSE = Path("docker-compose.staging.yml")
    _INIT_SCRIPT = Path("deploy/postgres-init/01-app-role.sh")
    _INITIAL_SETUP = Path("scripts/initial-setup.sh")
    _LONG = "x" * 40

    @classmethod
    def _services(cls) -> dict:
        if not cls._STAGING_COMPOSE.exists():
            pytest.skip(
                "docker-compose.staging.yml / .env.staging.example nicht im Public-Snapshot (Refs #1137, #2086)"
            )
        return yaml.safe_load(cls._STAGING_COMPOSE.read_text(encoding="utf-8"))["services"]

    def _run_init(self, **overrides) -> subprocess.CompletedProcess:
        """Init-Hook mit gueltiger Basiskonfiguration + gezielten Abweichungen.

        ``PATH`` bleibt leer: erreicht der Lauf ``psql``, ist das ein
        Preflight-Durchlasser (Exit 127 statt 1) — genau die Unterscheidung,
        um die es hier geht.
        """
        env = {
            "PATH": "",
            "REQUIRE_ADMIN_DB_ROLE": "true",
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": self._LONG,
            "POSTGRES_DB": "anlaufstelle",
            "APP_DB_USER": "anlaufstelle",
            "APP_DB_PASSWORD": self._LONG,
            "ADMIN_DB_USER": "anlaufstelle_admin",
            "ADMIN_DB_PASSWORD": self._LONG,
        }
        env.update(overrides)
        return subprocess.run(
            ["/bin/sh", str(self._INIT_SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
        )

    def test_init_rejects_app_role_named_like_the_bootstrap_superuser(self) -> None:
        result = self._run_init(APP_DB_USER="postgres")
        assert result.returncode == 1, (
            "APP_DB_USER=postgres muss im Preflight fail-closed abbrechen. Exit "
            f"{result.returncode} heisst: der Hook lief bis psql weiter, die "
            "'WHERE NOT EXISTS'-Klausel haette das CREATE ROLE uebersprungen und "
            f"der Bootstrap-Superuser waere die Runtime-Rolle geblieben.\n{result.stderr}"
        )
        assert "APP_DB_USER" in result.stderr

    def test_init_rejects_app_role_equal_to_the_configured_bootstrap_role(self) -> None:
        """Auch bei einem anderen Bootstrap-Namen als ``postgres``."""
        result = self._run_init(POSTGRES_USER="bootstrap", APP_DB_USER="bootstrap")
        assert result.returncode == 1, result.stderr

    def test_init_rejects_identical_app_and_admin_roles(self) -> None:
        result = self._run_init(APP_DB_USER="anlaufstelle", ADMIN_DB_USER="anlaufstelle")
        assert result.returncode == 1, (
            "App-Rolle == Admin-Rolle hebt die Runtime-/Owner-Trennung auf und muss "
            f"abbrechen (Exit war {result.returncode}).\n{result.stderr}"
        )
        assert "ADMIN_DB_USER" in result.stderr

    def test_init_rejects_admin_role_named_like_the_bootstrap_superuser(self) -> None:
        result = self._run_init(ADMIN_DB_USER="postgres")
        assert result.returncode == 1, result.stderr

    def test_init_still_accepts_the_documented_configuration(self) -> None:
        """Gegenprobe: die Soll-Konfiguration darf der Preflight nicht abweisen.

        Ohne ``psql`` im leeren PATH endet sie bei 127 — also NACH dem
        Preflight. Alles andere waere ein falsch-positiver Guard.
        """
        result = self._run_init()
        assert result.returncode == 127, (
            "Die dokumentierte Konfiguration darf den Preflight passieren, war Exit "
            f"{result.returncode}: {result.stderr}"
        )

    def test_web_healthcheck_proves_the_runtime_role_posture(self) -> None:
        """Der HTTP-Check allein ist blind fuer einen RLS-Bypass.

        ``/health/`` meldet bei bypassender Rolle zwar ``degraded``, aber
        weiterhin HTTP 200 — ``urlopen()`` sieht keinen Fehler. Das Detailfeld
        ``db_role_posture`` steht nur im Token-/Session-Payload und taugt
        deshalb nicht als Healthcheck-Kriterium.
        """
        healthcheck = self._services()["web"]["healthcheck"]
        probe = " ".join(healthcheck["test"])
        for fragment in ("rolsuper", "rolbypassrls", "current_user"):
            assert fragment in probe, f"web-Healthcheck prueft die Laufzeit-Rolle nicht ({fragment!r} fehlt): {probe!r}"

    def test_web_healthcheck_keeps_the_http_liveness_probe(self) -> None:
        probe = " ".join(self._services()["web"]["healthcheck"]["test"])
        assert "/health/" in probe, "Die HTTP-Liveness darf durch die Rollenprobe nicht ersetzt werden."


class TestStagingEnvFileIsParametrisable:
    """Refs #2086 (Fix-Runde 1): ``ENV_FILE`` war nur halb verdrahtet.

    ``scripts/initial-setup.sh`` bewarb eine frei waehlbare Env-Datei und
    reichte sie als ``--env-file`` durch — das steuert bei Compose aber nur
    die **Interpolation**. ``maintenance`` und ``cron`` luden zusaetzlich per
    service-level ``env_file:`` den harten Repo-Pfad ``.env.staging``. Mit
    einer Env-Datei ausserhalb des Projektverzeichnisses brach ``docker
    compose`` daher mit Exit 14 ab ("env file ... not found") — der alte
    Guard sah nur das CLI-Flag und bemerkte die gebrochene Semantik nicht.
    """

    _STAGING_COMPOSE = Path("docker-compose.staging.yml")
    _ENV_TEMPLATE = Path(".env.staging.example")
    _INITIAL_SETUP = Path("scripts/initial-setup.sh")
    _COMPOSE_VAR = "STAGING_ENV_FILE"

    @classmethod
    def _raw(cls) -> str:
        if not cls._STAGING_COMPOSE.exists():
            pytest.skip(
                "docker-compose.staging.yml / .env.staging.example nicht im Public-Snapshot (Refs #1137, #2086)"
            )
        return cls._STAGING_COMPOSE.read_text(encoding="utf-8")

    @pytest.mark.parametrize("service_name", ("maintenance", "cron"))
    def test_service_env_file_is_interpolated_with_a_default(self, service_name: str) -> None:
        services = yaml.safe_load(self._raw())["services"]
        env_file = services[service_name].get("env_file")
        assert isinstance(env_file, str), f"{service_name}: env_file fehlt oder ist keine einzelne Datei."
        assert env_file.startswith(f"${{{self._COMPOSE_VAR}"), (
            f"{service_name}: env_file ist der harte Repo-Pfad {env_file!r} — "
            f"'--env-file' steuert nur die Interpolation und ersetzt ihn NICHT."
        )
        assert ".env.staging" in env_file, f"{service_name}: der Default muss .env.staging bleiben, war {env_file!r}."

    def test_initial_setup_exports_the_compose_variable(self) -> None:
        body = self._INITIAL_SETUP.read_text(encoding="utf-8")
        assert re.search(rf"^export {self._COMPOSE_VAR}=", body, re.MULTILINE), (
            f"scripts/initial-setup.sh muss {self._COMPOSE_VAR} exportieren — sonst laedt der "
            "service-level env_file weiter den Repo-Pfad, egal was ENV_FILE sagt."
        )

    def test_compose_resolves_an_absolute_env_file_outside_the_project(self, tmp_path: Path) -> None:
        """Der echte Beweis: ``docker compose config`` mit Env-Datei in /tmp.

        Genau dieser Aufruf brach vorher mit Exit 14 ab.
        """
        raw = self._raw()  # Skip-Gate
        if not self._ENV_TEMPLATE.exists():
            pytest.skip(".env.staging.example nicht im Public-Snapshot (Refs #1137, #2086)")
        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("docker nicht verfuegbar — Compose-Aufloesung nicht pruefbar")
        version = subprocess.run([docker, "compose", "version"], capture_output=True, text=True)
        if version.returncode != 0:
            pytest.skip("'docker compose' nicht nutzbar — Compose-Aufloesung nicht pruefbar")

        env_file = tmp_path / ".env.staging"
        env_file.write_text(self._ENV_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        assert "services:" in raw

        result = subprocess.run(
            [
                docker,
                "compose",
                "-f",
                str(self._STAGING_COMPOSE.resolve()),
                "--env-file",
                str(env_file),
                "--profile",
                "maintenance",
                "config",
                "-q",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, self._COMPOSE_VAR: str(env_file)},
        )
        assert result.returncode == 0, (
            "Compose kann eine Env-Datei ausserhalb des Projektverzeichnisses nicht aufloesen — "
            f"Exit {result.returncode}: {result.stderr.strip()}"
        )


class TestStagingExposureIsGatedOnRolePosture:
    """Refs #2086 (Fix-Runde 2): ``unhealthy`` allein sperrt keinen laufenden Caddy.

    Die Runtime-Probe im ``web``-Healthcheck schliesst den **kalten** Start:
    Caddy wartet dort ueber ``depends_on.condition: service_healthy``. Beim
    Upgrade eines **laufenden** Stacks greift sie nicht — ``depends_on`` ist
    Startreihenfolge, und ohne ``restart: true`` startet Compose den
    Abhaengigen nach einem Dependency-Update nicht neu; Docker-Restart-Policies
    reagieren auf Container-Exit, nicht auf den Healthstatus. Ein als
    ``unhealthy`` markierter Bootstrap-Superuser-Webprozess bliebe also hinter
    einem weiterlaufenden Caddy erreichbar.

    Zwei Sperren, die dieser Guard pinnt:
    1. ``restart: true`` an Caddys Abhaengigkeit — Compose startet Caddy nach
       einem ``web``-Update neu und haelt ihn dabei erneut an
       ``service_healthy`` auf.
    2. Ein **aktiver** Upstream-Healthcheck in ``Caddyfile.staging``, der die
       Rollen-Posture selbst auswertet. Ein blosser HTTP-Statuscheck taugt
       dafuer nicht: ``/health/`` antwortet bei bypassender Rolle weiterhin
       mit 200. Der anonyme Gesamtstatus taugt ebenfalls nicht — er ist auf
       diesem Stack dauerhaft ``degraded``, weil bewusst kein clamav-Service
       laeuft. Bleibt das Detailfeld ``db_role_posture`` hinter dem
       Health-Token.
    """

    _STAGING_COMPOSE = Path("docker-compose.staging.yml")
    _CADDYFILE = Path("Caddyfile.staging")
    _TOKEN = "DJANGO_HEALTH_DETAIL_TOKEN"

    @classmethod
    def _services(cls) -> dict:
        if not cls._STAGING_COMPOSE.exists():
            pytest.skip(
                "docker-compose.staging.yml / .env.staging.example nicht im Public-Snapshot (Refs #1137, #2086)"
            )
        return yaml.safe_load(cls._STAGING_COMPOSE.read_text(encoding="utf-8"))["services"]

    def test_caddy_is_restarted_when_web_is_updated(self) -> None:
        caddy = self._services()["caddy"]
        dependency = caddy.get("depends_on", {}).get("web", {})
        assert dependency.get("condition") == "service_healthy"
        assert dependency.get("restart") is True, (
            "Ohne 'restart: true' laesst Compose einen bereits laufenden Caddy nach einem "
            "web-Update unberuehrt weiterrouten — der Healthstatus waere dann nur "
            "Beobachtung, keine Sperre."
        )

    def test_health_token_reaches_web_and_caddy(self) -> None:
        services = self._services()
        assert self._TOKEN in services["web"].get("environment", {}), (
            f"{self._TOKEN} fehlt in der web-Allowlist — ohne gesetztes Token liefert "
            "/health/ das Feld db_role_posture ueberhaupt nicht aus."
        )
        assert self._TOKEN in services["caddy"].get("environment", []), (
            f"{self._TOKEN} muss als Passthrough am caddy-Service stehen, sonst bleibt "
            "der Platzhalter in Caddyfile.staging leer."
        )

    def test_caddy_actively_probes_the_role_posture(self) -> None:
        if not self._CADDYFILE.exists():
            pytest.skip("Caddyfile.staging nicht im Public-Snapshot (Refs #1047, #2086)")
        code = "\n".join(
            line
            for line in self._CADDYFILE.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "health_uri" in code, "Caddy proxied blind — kein aktiver Upstream-Healthcheck."
        assert "db_role_posture" in code, (
            "Der aktive Healthcheck muss die Rollen-Posture auswerten. Ein reiner "
            "Statuscheck sieht den Bypass nicht: /health/ bleibt dabei HTTP 200."
        )
        assert "health_headers" in code and self._TOKEN in code, (
            "db_role_posture steht nur im Token-/Session-Payload — ohne einen "
            f"health_headers-Block mit X-Health-Token {{${self._TOKEN}}} bekommt Caddy es nie zu sehen."
        )
        assert "health_request_header" not in code, (
            "'health_request_header' gibt es in Caddy 2 nicht. Die Direktive laesst Caddy schon "
            "beim Adaptieren der Konfiguration abbrechen — der Stack startet dann GAR NICHT."
        )
        # Die Probe entscheidet ueber die Exposition; sie muss den JSON-Key und den
        # vollstaendigen Wert treffen. Eine lose Regex (db_role_posture[^a-z]+ok)
        # matchte auch auf 'not_db_role_posture: "ok"' neben einem echten
        # 'bypasses_rls' — und auf ein fremdes Feld, dessen INHALT so aussieht.
        assert re.search(r"health_body\s+\S*\\x22db_role_posture\\x22", code), (
            "health_body muss den JSON-Key feldgenau verankern (Anfuehrungszeichen als \\x22, "
            f"damit die Caddyfile-Quotierung nicht zur Fehlerquelle wird): {code!r}"
        )
        assert "db_role_posture[^a-z]+ok" not in code, (
            "Die lose Posture-Regex kann fail-open matchen und darf nicht zurueckkommen."
        )


class TestCaddyReverseProxySubdirectivesExist:
    """Refs #2086 (Fix-Runde 3): eine erfundene ``reverse_proxy``-Subdirektive
    verhindert den Caddy-Start, und kein Test der Suite sah das.

    ``health_request_header`` klingt plausibel, existiert in Caddy 2 aber nicht.
    Caddy bricht damit bereits beim **Adaptieren** der Caddyfile ab
    (``unrecognized subdirective``) — der Container kommt gar nicht hoch. Das
    ist die unangenehmste Fehlerklasse: nicht still falsch, sondern
    startverhindernd, und weder Ruff noch ein YAML-Parser noch ein
    Python-Test fassen sie an.

    Dieser Guard ist bewusst **kein** Ersatz fuer ``caddy validate`` (das
    braucht das Caddy-Binary bzw. den Container und gehoert ins Release-Gate).
    Er prueft nur, dass jede Subdirektive innerhalb eines
    ``reverse_proxy``-Blocks aus der dokumentierten Menge stammt — genau der
    Fehler, der hier passiert ist.
    """

    # Subdirektiven, die der Parser des gepinnten Caddy 2.11.4 akzeptiert
    # (modules/caddyhttp/reverseproxy/caddyfile.go). Kommt eine echte Direktive
    # dazu, gehoert sie hier ergaenzt — der Fehlertext sagt das.
    #
    # Refs #2086, Fix-Runde 4: Die erste Fassung dieser Liste war in beide
    # Richtungen falsch. Sie liess `buffer_requests`/`buffer_responses`/
    # `max_buffer_size` durch (vom 2.11.4-Parser NICHT akzeptiert, in Caddy 2
    # durch `request_buffers`/`response_buffers` ersetzt) und haette
    # `health_path`, die drei `stream_*`-Direktiven und `verbose_logs`
    # faelschlich als unbekannt gemeldet. Ein Guard, der gueltige Namen
    # blockiert und ungueltige durchlaesst, ist schlimmer als keiner.
    _KNOWN = frozenset(
        {
            "to",
            "dynamic",
            "lb_policy",
            "lb_retries",
            "lb_try_duration",
            "lb_try_interval",
            "lb_retry_match",
            "health_uri",
            "health_port",
            "health_interval",
            "health_passes",
            "health_fails",
            "health_timeout",
            "health_status",
            "health_body",
            "health_method",
            "health_request_body",
            "health_headers",
            "health_follow_redirects",
            "health_upstream",
            "fail_duration",
            "max_fails",
            "unhealthy_status",
            "unhealthy_latency",
            "unhealthy_request_count",
            "health_path",
            "flush_interval",
            "request_buffers",
            "response_buffers",
            "stream_buffer_size",
            "stream_timeout",
            "stream_close_delay",
            "verbose_logs",
            "header_up",
            "header_down",
            "method",
            "rewrite",
            "trusted_proxies",
            "transport",
            "handle_response",
            "replace_status",
        }
    )

    _CADDYFILES = (
        Path("Caddyfile"),
        Path("Caddyfile.dev"),
        Path("Caddyfile.demo"),
        Path("Caddyfile.staging"),
    )

    @staticmethod
    def _reverse_proxy_subdirectives(text: str) -> list[str]:
        """Erste Ebene innerhalb jedes ``reverse_proxy { … }``-Blocks.

        Verschachtelte Bloecke (z.B. ``health_headers { … }``) enthalten
        Header-Namen, keine Subdirektiven — die bleiben aussen vor.
        """
        found: list[str] = []
        depth = 0
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if depth == 0:
                if re.match(r"^reverse_proxy\b.*\{$", line):
                    depth = 1
                continue
            if depth == 1 and line == "}":
                depth = 0
                continue
            if depth == 1:
                token = line.split()[0]
                # Inline definierte Response-Matcher (@name ...) sind keine
                # Subdirektiven, sondern Matcher-Definitionen.
                if not token.startswith("@"):
                    found.append(token)
            depth += line.count("{") - line.count("}")
        return found

    @pytest.mark.parametrize("path", _CADDYFILES, ids=str)
    def test_only_documented_subdirectives_are_used(self, path: Path) -> None:
        if not path.exists():  # Caddyfile.staging / Caddyfile.demo (Refs #1137)
            pytest.skip(f"{path} nicht im Public-Snapshot (Refs #1047, #2086)")
        unknown = sorted(set(self._reverse_proxy_subdirectives(path.read_text(encoding="utf-8"))) - self._KNOWN)
        assert not unknown, (
            f"{path}: unbekannte reverse_proxy-Subdirektive(n) {unknown}. Caddy bricht dann "
            "schon beim Adaptieren der Konfiguration ab und der Container startet nicht. "
            "Ist die Direktive echt, gehoert sie in _KNOWN — sonst korrigieren."
        )

    def test_guard_detects_an_invented_subdirective(self) -> None:
        """Zaehne-Nachweis am genau hier passierten Fehler."""
        probe = (
            "site {\n    reverse_proxy web:8000 {\n        health_uri /health/\n"
            "        health_request_header X-Y z\n    }\n}\n"
        )
        assert set(self._reverse_proxy_subdirectives(probe)) - self._KNOWN == {"health_request_header"}

    def test_guard_ignores_nested_block_contents(self) -> None:
        """Header-Namen in ``health_headers { … }`` sind keine Subdirektiven."""
        probe = (
            "site {\n    reverse_proxy web:8000 {\n        health_headers {\n"
            '            X-Health-Token ["abc"]\n        }\n    }\n}\n'
        )
        assert set(self._reverse_proxy_subdirectives(probe)) - self._KNOWN == set()

    def test_whitelist_matches_the_pinned_parser(self) -> None:
        """Refs #2086, Fix-Runde 4: die Liste war in beide Richtungen falsch.

        Die drei Caddy-1-Namen duerfen nicht mehr durchgehen, die fuenf
        gueltigen 2.11.4-Namen nicht mehr als unbekannt gelten.
        """
        for veraltet in ("buffer_requests", "buffer_responses", "max_buffer_size"):
            assert veraltet not in self._KNOWN, (
                f"{veraltet!r} akzeptiert der 2.11.4-Parser nicht — der Guard darf es nicht freigeben."
            )
        for gueltig in ("health_path", "stream_buffer_size", "stream_timeout", "stream_close_delay", "verbose_logs"):
            assert gueltig in self._KNOWN, f"{gueltig!r} ist gueltig und darf keinen Fehlalarm ausloesen."

    def test_guard_ignores_inline_response_matchers(self) -> None:
        probe = "site {\n    reverse_proxy web:8000 {\n        @err status 500\n        health_uri /health/\n    }\n}\n"
        assert set(self._reverse_proxy_subdirectives(probe)) - self._KNOWN == set()


class TestInitialSetupGuardsTheHealthToken:
    """Refs #2086 (Fix-Runde 5): der Erstsetup faengt die eigene neue Falle ab.

    Diese Lane hat einen Fehlermodus neu eingefuehrt: Caddy ersetzt
    ``{$DJANGO_HEALTH_DETAIL_TOKEN}`` beim Adaptieren der Caddyfile. Ist der
    Wert leer, bricht die Adaption ab und der Reverse-Proxy startet gar nicht
    erst. Ohne Preflight sieht eine Operatorin davon nur einen Stack, der nicht
    hochkommt — die Ursache steht dann ausschliesslich in ``docker compose logs
    caddy``.

    Der Preflight haengt bewusst nicht am Dateinamen des Stacks, sondern daran,
    ob die Compose-Datei die Variable ueberhaupt verdrahtet. Fuer prod ist er
    damit ein No-op, und ein spaeterer Stack mit demselben Muster ist
    automatisch mitgeschuetzt.
    """

    _SCRIPT = Path("scripts/initial-setup.sh")
    _TOKEN = "DJANGO_HEALTH_DETAIL_TOKEN"

    def test_preflight_is_conditional_on_the_compose_file(self) -> None:
        body = self._SCRIPT.read_text(encoding="utf-8")
        assert self._TOKEN in body, f"scripts/initial-setup.sh prueft {self._TOKEN} nicht."
        assert re.search(rf'grep -q [\'"]{self._TOKEN}[\'"] "\$COMPOSE_FILE"', body), (
            "Der Preflight muss an der Compose-Datei haengen, nicht am Stack-Namen — "
            "sonst schlaegt er auf prod falsch an."
        )

    @staticmethod
    def _stub_docker(tmp_path: Path) -> tuple[Path, Path]:
        """``docker``-Attrappe: protokolliert argv, startet nie etwas."""
        bindir = tmp_path / "bin"
        bindir.mkdir()
        log = tmp_path / "docker-argv.log"
        stub = bindir / "docker"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
        return bindir, log

    def _run(self, tmp_path: Path, token_line: str) -> tuple[subprocess.CompletedProcess, Path]:
        bindir, log = self._stub_docker(tmp_path)
        compose = tmp_path / "docker-compose.probe.yml"
        compose.write_text(f"services:\n  caddy:\n    environment:\n      - {self._TOKEN}\n", encoding="utf-8")
        env_file = tmp_path / ".env.probe"
        env_file.write_text(f"POSTGRES_USER=anlaufstelle\n{token_line}", encoding="utf-8")
        result = subprocess.run(
            ["/bin/bash", str(self._SCRIPT.resolve())],
            capture_output=True,
            text=True,
            env={
                "PATH": f"{bindir}:/usr/bin:/bin",
                "COMPOSE_FILE": str(compose),
                "ENV_FILE": str(env_file),
                "SKIP_SUPER_ADMIN": "1",
                # Ohne das wartet ``wait_for_service`` gegen die docker-Attrappe
                # bis zum Default-Timeout (180 s) — der Test haenge sonst.
                "WAIT_TIMEOUT": "0",
            },
            timeout=60,
        )
        return result, log

    def test_empty_token_aborts_before_any_container_is_touched(self, tmp_path: Path) -> None:
        result, log = self._run(tmp_path, f"{self._TOKEN}=\n")
        assert result.returncode != 0, f"Leeres Token muss fail-loud abbrechen.\n{result.stdout}"
        assert self._TOKEN in result.stderr
        assert "adaptiert" in result.stderr, (
            f"Die Meldung muss die Ursache benennen (Caddy adaptiert die Konfiguration nicht): {result.stderr!r}"
        )
        gestartet = log.read_text(encoding="utf-8") if log.exists() else ""
        assert " up " not in f" {gestartet} ", (
            f"Der Abbruch kam zu spaet — docker wurde schon zum Starten gerufen: {gestartet!r}"
        )

    def test_missing_token_line_aborts_as_well(self, tmp_path: Path) -> None:
        result, _ = self._run(tmp_path, "")
        assert result.returncode != 0
        assert self._TOKEN in result.stderr

    # Refs #2086, Fix-Runde 6: Die Schreibweisen sind NICHT aus dem Kopf
    # gegriffen, sondern mit `docker compose config --format json` gegen eine
    # Wegwerf-Compose gemessen. Links steht, was Compose dem caddy-Service
    # tatsaechlich uebergibt.
    #
    # Compose liefert hier einen leeren (bzw. reinen Whitespace-) Wert — der
    # Platzhalter in Caddyfile.staging bleibt damit ohne brauchbaren Inhalt und
    # die Konfigurationsadaption scheitert. Der Preflight MUSS abbrechen.
    _COMPOSE_LEER = (
        pytest.param(f"{_TOKEN}=\n", id="kein-wert"),
        pytest.param(f'{_TOKEN}=""\n', id="doppelte-quotes"),
        pytest.param(f"{_TOKEN}=''\n", id="einfache-quotes"),
        pytest.param(f"{_TOKEN}=   \n", id="nur-whitespace"),
        pytest.param(f"{_TOKEN}=\t\n", id="tab"),
        pytest.param(f"#{_TOKEN}=abc123\n{_TOKEN}=\n", id="kommentiert-dann-leer"),
        pytest.param(f"export {_TOKEN}=\n", id="export-leer"),
        pytest.param(f"{_TOKEN}=abc123\n{_TOKEN}=\n", id="gesetzt-dann-leer"),
        pytest.param(f'{_TOKEN}="   "\n', id="quotes-mit-whitespace"),
    )

    # Compose liefert hier einen echten Wert. Der Preflight darf NICHT
    # abbrechen — ein Fehlalarm blockiert eine gueltige Erstinbetriebnahme.
    _COMPOSE_GESETZT = (
        pytest.param(f"{_TOKEN}=abc123\n", id="schlicht"),
        pytest.param(f"export {_TOKEN}=abc123\n", id="export-praefix"),
        pytest.param(f'{_TOKEN}="abc123"\n', id="quotes-mit-wert"),
        pytest.param(f"  {_TOKEN}=abc123\n", id="fuehrende-leerzeichen"),
        pytest.param(f"{_TOKEN}=\n{_TOKEN}=abc123\n", id="leer-dann-gesetzt"),
    )

    @pytest.mark.parametrize("token_line", _COMPOSE_LEER)
    def test_every_form_compose_reads_as_empty_is_rejected(self, tmp_path: Path, token_line: str) -> None:
        result, log = self._run(tmp_path, token_line)
        assert result.returncode != 0, (
            f"Compose liefert fuer diese Schreibweise einen leeren Wert — der Preflight muss "
            f"abbrechen, sonst startet caddy spaeter nicht.\n{result.stdout}"
        )
        assert self._TOKEN in result.stderr
        gestartet = log.read_text(encoding="utf-8") if log.exists() else ""
        assert " up " not in f" {gestartet} ", f"Abbruch kam zu spaet: {gestartet!r}"

    @pytest.mark.parametrize("token_line", _COMPOSE_GESETZT)
    def test_every_form_compose_reads_as_set_passes(self, tmp_path: Path, token_line: str) -> None:
        result, log = self._run(tmp_path, token_line)
        assert self._TOKEN not in result.stderr, (
            f"Fehlalarm: Compose liefert hier einen echten Wert, der Preflight blockiert "
            f"aber die Erstinbetriebnahme.\n{result.stderr}"
        )
        assert log.exists(), "Der Lauf haette den Preflight passieren muessen."

    def test_set_token_passes_the_preflight(self, tmp_path: Path) -> None:
        """Gegenprobe: ein gesetzter Wert darf den Preflight nicht aufhalten."""
        result, log = self._run(tmp_path, f"{self._TOKEN}=abc123\n")
        assert self._TOKEN not in result.stderr, f"Gesetztes Token darf keinen Abbruch ausloesen: {result.stderr!r}"
        assert log.exists(), "Der Lauf haette den Preflight passieren und docker aufrufen muessen."
