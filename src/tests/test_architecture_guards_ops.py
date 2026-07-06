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
            pytest.skip("dev-ops/ nicht im Public-Snapshot — Guard nur im Dev-Repo relevant (Refs #1047)")
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
