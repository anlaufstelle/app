"""Guard fuer das JavaScript-Advisory-Gate in ``make ci`` (Refs #1968).

Fuer Python haengt ``pip-audit`` seit jeher im ``audit``-Job von
``.github/workflows/test.yml``; fuer JavaScript existierte gar kein Gate — die
Advisories in ``nanoid``/``postcss``/``undici`` konnten deshalb ueber mehrere
Releases im Lockfile stehen bleiben, ohne dass irgendetwas rot wurde.

Das Gate gehoert in ``make ci`` und nicht nur in den GitHub-Workflow: der
Workflow-Lauf haengt am Actions-Guthaben des Dev-Repos und ruht faktisch, ein
Audit-Schritt dort waere wirkungslos. ``make ci`` ist das reale Tor vor jedem
Commit — genau dort haengt auch ``test-js`` schon.

Der Test parst das Makefile statt ``npm audit`` auszufuehren: der echte
Audit-Lauf braucht die Registry und hat in einer Unit-Suite nichts verloren.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

MAKEFILE = Path("Makefile")
WORKFLOW = Path(".github/workflows/test.yml")


def _prerequisites(makefile_text: str, target: str) -> list[str]:
    """Prerequisite-Liste eines Makefile-Targets (ohne Rezept-Zeilen)."""
    match = re.search(rf"^{re.escape(target)}:(?P<deps>[^\n]*)$", makefile_text, re.MULTILINE)
    assert match is not None, f"Target '{target}:' nicht im Makefile gefunden"
    return match.group("deps").split()


def _recipe(makefile_text: str, target: str) -> str:
    """Rezept-Block (die eingerueckten Zeilen) eines Makefile-Targets."""
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n(?P<body>(?:\t[^\n]*\n)+)",
        makefile_text,
        re.MULTILINE,
    )
    assert match is not None, f"Kein Rezept fuer Target '{target}:' im Makefile gefunden"
    return match.group("body")


class TestJsAuditGate:
    def test_ci_chain_runs_the_js_audit(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        assert "audit-js" in _prerequisites(text, "ci"), (
            "'make ci' muss 'audit-js' aufrufen — ohne das Gate bleibt eine neue "
            "JS-Advisory unbemerkt im Lockfile stehen (Refs #1968)"
        )

    def test_audit_target_is_blocking(self) -> None:
        recipe = _recipe(MAKEFILE.read_text(encoding="utf-8"), "audit-js")
        assert "npm audit" in recipe, "'audit-js' muss 'npm audit' ausfuehren"
        assert "--audit-level" in recipe, (
            "'npm audit' braucht ein explizites --audit-level, sonst haengt die Schwelle am npm-Default"
        )
        assert "|| true" not in recipe and "-npm audit" not in recipe, (
            "'audit-js' darf den Exit-Code nicht maskieren — der Maintainer-Entscheid "
            "ist ein blockierendes Gate (Refs #1968)"
        )

    def test_audit_level_is_overridable_and_defaults_to_moderate(self) -> None:
        """Refs #1968: die Schwelle bleibt blockierend bei ``moderate``, ist aber
        eine ueberschreibbare Variable.

        Eine frisch veroeffentlichte ``moderate``-Advisory OHNE verfuegbaren Fix
        blockiert sonst ab ihrer Veroeffentlichung jeden Commit, ohne dass es
        einen Ausnahme-Pfad gaebe. Als ``?=``-Variable bleibt der Default
        unveraendert, und der Betreiber hat mit ``make ci AUDIT_LEVEL=high``
        eine Notbremse.
        """
        text = MAKEFILE.read_text(encoding="utf-8")
        assert re.search(r"^AUDIT_LEVEL \?= moderate$", text, re.MULTILINE), (
            "'AUDIT_LEVEL ?= moderate' fehlt — die Schwelle steht als Literal im "
            "Rezept und ist ohne Patch nicht ueberschreibbar (Refs #1968)"
        )
        recipe = _recipe(text, "audit-js")
        assert "--audit-level=$(AUDIT_LEVEL)" in recipe, (
            "'audit-js' muss die Schwelle aus $(AUDIT_LEVEL) beziehen, nicht als Literal"
        )

    def test_audit_target_is_phony(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        phony = " ".join(re.findall(r"^\.PHONY:(.*)$", text, re.MULTILINE))
        assert "audit-js" in phony.split(), "'audit-js' fehlt in .PHONY"

    def test_workflow_audit_stage_covers_javascript(self) -> None:
        """Der ``audit``-Job des Workflows kannte nur ``pip-audit``. Er ruht
        zwar (kein Actions-Guthaben), darf aber nicht dauerhaft hinter dem
        lokalen Gate zurueckbleiben — sonst faellt beim Wiederanschalten auf,
        dass JavaScript dort nie geprueft wurde."""
        text = WORKFLOW.read_text(encoding="utf-8")
        audit_stage = text.split("\n  audit:\n", 1)
        assert len(audit_stage) == 2, "audit-Job nicht in .github/workflows/test.yml gefunden"
        # Bis zum Beginn des naechsten Top-Level-Jobs lesen.
        body = re.split(r"\n  \w[\w-]*:\n", audit_stage[1])[0]
        assert "npm audit" in body, "audit-Job prueft keine JavaScript-Advisories (Refs #1968)"
