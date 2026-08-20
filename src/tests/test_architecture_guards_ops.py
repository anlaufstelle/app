"""Architecture-Guards — dev-ops-Wartungsskripte (Refs #1047)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture


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
    """Refs #1335 (Review-Nachschlag): ``enforce_retention`` bricht ohne
    BYPASSRLS-Admin-Kontext genauso fail-loud ab wie ``verify_audit_chain``
    (``enforce_retention.py:73`` — identischer ``_has_rls_bypass_context()``-Guard).
    Jede Host-Crontab-Zeile im Runbook, die ``enforce_retention`` via
    ``docker compose exec`` startet, muss deshalb den
    ``-e POSTGRES_USER=$POSTGRES_ADMIN_USER``-Override tragen — sonst faehrt ein
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

    def test_enforce_retention_line_uses_admin_role_override(self) -> None:
        retention_lines = [
            line
            for block in self._crontab_blocks()
            for line in block.splitlines()
            if "manage.py enforce_retention" in line
        ]
        assert retention_lines, "keine enforce_retention-Crontab-Zeile in ops-runbook.md § 3 gefunden"
        for line in retention_lines:
            assert "POSTGRES_ADMIN_USER" in line, (
                "enforce_retention bricht ohne BYPASSRLS-Admin-Kontext fail-loud ab "
                "(enforce_retention.py:73) — die Beispiel-Crontab-Zeile muss denselben "
                "-e POSTGRES_USER=$POSTGRES_ADMIN_USER-Override tragen wie verify_audit_chain "
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
