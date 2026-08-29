"""Der Art.-18-Ausstieg bleibt modul-privat (Cross-Review-Befund 4).

Die erste Fassung reichte ``allow_restricted=True`` als **oeffentlichen
Parameter** durch ``update_client``/``assert_processing_allowed``. Damit konnte
jeder interne Aufrufer das Gate vollstaendig abschalten; dass faktisch nur
``_apply_rectification`` es tat, war eine Konvention, kein Vertrag.

Der Ausstieg ist jetzt ein modul-privater Kontext. Dieser Guard haelt den
Vertrag **strukturell**: Nur die beiden Module, die ihn definieren und
verwenden duerfen, duerfen ihn ueberhaupt nennen. Ein dritter Aufrufer reisst
den Test — genau der Moment, in dem aus der Ausnahme wieder ein
Generalschluessel wuerde.

Der Scanner ist bewusst ueber ``root=`` parametrisiert und wird gegen einen
``tmp_path``-Baum selbst mitgeprueft (Standardblock-Regel: Scanner-Tests legen
NIE Proben in den echten Quellbaum).
"""

from pathlib import Path

import pytest

#: ALLE Symbole, ueber die sich das Gate abschalten laesst. Die erste Fassung
#: kannte nur den Kontextmanager — ein Modul konnte stattdessen die ContextVar
#: direkt setzen (``_ACTIVE_WORKFLOW.set(...)``) und denselben globalen Bypass
#: erreichen, ohne den Guard auszuloesen (Cross-Review-Runde 2). Erfasst werden
#: deshalb der generische Kontext, seine benannten Fassungen und die Variable.
BYPASS_SYMBOLS = (
    "_workflow_write",
    "_rectification_write",
    "_deletion_workflow_write",
    "_ACTIVE_WORKFLOW",
)

#: Einzelname fuer den Scanner-Selbsttest gegen ``tmp_path``.
BYPASS_SYMBOL = "_rectification_write"

#: Die einzigen Module, die eines dieser Symbole nennen duerfen — mit Begruendung.
ALLOWED_MODULES = {
    # Definiert sie.
    "core/services/client/restriction.py": "definiert die Kontexte",
    # Art. 16 ist der Weg, auf dem die bestrittene Richtigkeit geklaert wird
    # (Art. 18 Abs. 1 lit. a).
    "core/services/client/rectification.py": "wendet die genehmigte Berichtigung an",
    # Art. 17 gilt waehrend der Einschraenkung weiter: der GEPRUEFTE
    # Vier-Augen-Loeschweg ruft denselben destruktiven Service wie der
    # (blockierte) Direktweg und braucht dafuer den benannten Ausstieg.
    "core/services/events/deletion.py": "genehmigt den Vier-Augen-Loeschantrag",
}

_SRC_ROOT = Path(__file__).resolve().parent.parent


def modules_referencing_bypass(root: Path, *, symbols: tuple[str, ...] = BYPASS_SYMBOLS) -> set[str]:
    """Alle Python-Module unter *root*, die eines der *symbols* nennen.

    ``root=`` ist Pflicht und nicht Kosmetik: der Test gegen den echten Baum
    und der Selbsttest gegen ``tmp_path`` teilen sich dieselbe Funktion, und
    der Selbsttest darf keine Probe im Quellbaum hinterlassen.
    """
    hits: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts or "/tests/" in path.as_posix():
            continue
        text = path.read_text(encoding="utf-8")
        if any(symbol in text for symbol in symbols):
            hits.add(path.relative_to(root).as_posix())
    return hits


class TestBypassStaysPrivate:
    def test_only_the_sanctioned_modules_reference_it(self):
        referencing = modules_referencing_bypass(_SRC_ROOT)
        unexpected = referencing - set(ALLOWED_MODULES)
        assert not unexpected, (
            f"Eines der Ausstiegssymbole {BYPASS_SYMBOLS} wird ausserhalb der "
            f"vorgesehenen Module verwendet — damit waere der Ausstieg wieder "
            f"ein Generalschluessel: {sorted(unexpected)}"
        )

    def test_the_context_variable_itself_is_covered(self):
        """Der Direktweg am Kontextmanager vorbei muss miterfasst sein —
        ``_ACTIVE_WORKFLOW.set(True)`` waere sonst ein unsichtbarer Bypass."""
        assert "_ACTIVE_WORKFLOW" in BYPASS_SYMBOLS

    def test_both_sanctioned_modules_still_use_it(self):
        """Gegenrichtung: verschwindet der Kontext aus einem der beiden Module,
        ist entweder das Gate tot oder die Berichtigung blockiert."""
        referencing = modules_referencing_bypass(_SRC_ROOT)
        missing = set(ALLOWED_MODULES) - referencing
        assert not missing, f"Erwartete Nutzer des Ausstiegs fehlen: {sorted(missing)}"

    def test_update_client_has_no_bypass_parameter_in_its_signature(self):
        """Der Parameter selbst darf nicht zurueckkehren."""
        import inspect

        from core.services.client.main import update_client

        assert "allow_restricted" not in inspect.signature(update_client).parameters


class TestScannerItself:
    """Selbsttest gegen ``tmp_path`` — nie gegen den echten Quellbaum."""

    @pytest.fixture
    def sample_tree(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "clean.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "pkg" / "user.py").write_text(
            f"from a import {BYPASS_SYMBOL}\n",
            encoding="utf-8",
        )
        (tmp_path / "pkg" / "__pycache__").mkdir()
        (tmp_path / "pkg" / "__pycache__" / "cached.py").write_text(BYPASS_SYMBOL, encoding="utf-8")
        return tmp_path

    def test_finds_the_referencing_module(self, sample_tree):
        assert modules_referencing_bypass(sample_tree, symbols=(BYPASS_SYMBOL,)) == {"pkg/user.py"}

    def test_ignores_pycache(self, sample_tree):
        assert "pkg/__pycache__/cached.py" not in modules_referencing_bypass(sample_tree, symbols=(BYPASS_SYMBOL,))

    def test_reports_nothing_for_a_clean_tree(self, tmp_path):
        (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
        assert modules_referencing_bypass(tmp_path, symbols=(BYPASS_SYMBOL,)) == set()
