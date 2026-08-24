"""Architecture-Guards — dev-ops-Wartungsskripte (Refs #1047)."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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


class TestReadmeQuickStart:
    """Refs #1894/#1977: Der Compose-Quick-Start muss wirklich funktionieren.

    Die Dev-Compose mountet den Quellbaum nach ``/app``. Deshalb muss sie den
    dort verdeckten Image-Entrypoint neutralisieren, den versprochenen
    ``runserver`` selbst starten und das im Image gebaute CSS aus einem nicht
    verdeckten Pfad in den Bind-Mount kopieren. Migration und Seed bleiben
    ausdrueckliche Quick-Start-Schritte; kein Startpfad fuehrt sie implizit aus.
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

    def test_no_startup_path_migrates_implicitly(self) -> None:
        """Praemisse der folgenden Guards — kippt sie (etwa mit dem
        One-Command-Startup, Refs #1062), gehoert der README-Text mit umgestellt."""
        entrypoint = Path("docker-entrypoint.sh").read_text(encoding="utf-8")
        code = "\n".join(line for line in entrypoint.splitlines() if not line.lstrip().startswith("#"))
        assert not self._MIGRATE_CALL.search(code), (
            "docker-entrypoint.sh migriert wieder — dann darf der README-Quick-Start "
            "den expliziten Migrate-Schritt verlieren (Refs #802/#1062)."
        )
        assert re.search(r"^exec gunicorn\b", code, re.MULTILINE), (
            "docker-entrypoint.sh startet nicht mehr gunicorn — Startpfad und README-Quick-Start pruefen (Refs #802)."
        )
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        assert not self._MIGRATE_CALL.search(compose), (
            "docker-compose.yml migriert wieder — README-Quick-Start pruefen (Refs #1062)."
        )

    def test_quickstart_names_an_explicit_migrate_step(self) -> None:
        for readme in self._READMES:
            quickstart = self._quickstart(readme)
            assert self._MIGRATE_STEP.search(quickstart), (
                f"{readme}: Der Quick-Start muss den Migrate-Schritt ausdruecklich "
                "nennen — kein Startpfad fuehrt ihn aus, und ohne Schema laeuft die "
                "Anwendung in einen Datenbankfehler statt hochzukommen (Refs #1894)."
            )
            assert self._SEED_STEP.search(quickstart), (
                f"{readme}: Der Compose-Quick-Start muss den Seed-Schritt ausdruecklich "
                "nennen — er laeuft nicht implizit (Refs #1977)."
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

    def test_dev_compose_passes_the_encryption_key_without_a_default(self) -> None:
        value = self._compose()["services"]["web"]["environment"]["ENCRYPTION_KEY"]
        assert value.startswith("${ENCRYPTION_KEY:?") and value.endswith("}"), (
            "Dev-web muss ENCRYPTION_KEY aus der Host-Umgebung fail-loud "
            "durchreichen — kein ausgelieferter Festschluessel (Refs #1977)."
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
