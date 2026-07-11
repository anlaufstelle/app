"""Architecture-Guards — dev-ops-Wartungsskripte (Refs #1047)."""

import re
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
