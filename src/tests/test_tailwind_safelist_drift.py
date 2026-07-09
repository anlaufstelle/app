"""Drift-Guard: dynamische Tailwind-Klassen (Python) ↔ ``@source inline`` (CSS).

Hintergrund (Refs #1480)
========================
Bestimmte Tailwind-Farb-Utilities werden zur **Laufzeit** aus Python emittiert:

* ``core/templatetags/core_tags.py`` — ``_BADGE_COLOR_MAP`` (Badge-Farben wie
  ``bg-indigo-100 text-indigo-800``) **und** das Audit-Detail-``<pre>``
  (``bg-gray-50``),
* ``core/utils/dates.py`` — Fälligkeits-/Wiedervorlage-Status-Farben (z. B.
  ``text-red-600``, ``bg-orange-100 text-orange-800``).

Diese Klassen stehen als **vollständige Literal-Strings** in den ``.py``-Quellen
und werden vom v4-Content-Scan über den ``./src/core/**/*.py``-Glob
(``tailwind.config.js``) bereits erfasst — der Build generiert sie also auch ohne
Safelist. Das ``@source inline(...)`` in ``src/static/css/input.css`` ist daher
**bewusste Absicherung** (defense-in-depth): fällt der ``.py``-Content-Glob je
weg oder werden die Quell-Dateien umbenannt/verschoben, bleiben die Farben
trotzdem im Build.

In Tailwind v3 hielt der ``safelist``-Key in ``tailwind.config.js`` diese Klassen;
v4 unterstützt ``safelist`` in der JS-Config **nicht** mehr (auch nicht via
``@config``) — daher die ``@source inline(...)``-Direktive. Regressierte diese
Absicherung *und* änderte sich zugleich der Content-Glob, verschwänden die Farben
**stillschweigend** (reine Visual-Regression, die weder Build noch ``make ci``
fangen).

Dieser Guard erzwingt die **Vollständigkeit** der Absicherung: jede aus den
Python-Quellen ableitbare Farb-Klasse MUSS in ``input.css`` safelistet sein.
``input.css`` liegt im Public-Release-Snapshot (nicht gestrippt) — der Guard
referenziert keine dev-only Pfade und läuft daher auch auf der Stage-CI.

Skeleton-Vorbild: ``src/tests/test_seed_doc_drift.py`` — reine Funktionen, mit
synthetischem Input unit-getestet, kein ``django_db``, nur Datei-Reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

# Meta-Test, der Source-/Asset-Files des Repos scannt (analog test_seed_doc_drift).
# Im Mutmut-Subprozess läuft pytest aus ``mutants/`` und diese Pfade fehlen — daher
# als ``architecture`` markiert und dort deselektiert.
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_TAGS = REPO_ROOT / "src" / "core" / "templatetags" / "core_tags.py"
DATES = REPO_ROOT / "src" / "core" / "utils" / "dates.py"
INPUT_CSS = REPO_ROOT / "src" / "static" / "css" / "input.css"

# Eine Farb-Utility-Klasse: bg-/text-<farbe>-<schattierung>, z. B. ``bg-indigo-100``,
# ``text-red-600``. Bewusst eng (nur die dynamisch emittierten Farb-Utilities);
# Layout-Utilities wie ``font-semibold`` matchen absichtlich nicht.
COLOR_CLASS_RE = re.compile(r"\b(?:bg|text)-[a-z]+-\d{2,3}\b")


# ---------------------------------------------------------------------------
# Reine Funktionen (synthetisch unit-testbar)
# ---------------------------------------------------------------------------


def badge_color_classes(core_tags_src: str) -> set[str]:
    """Farb-Klassen aus ``core/templatetags/core_tags.py`` (ganze Datei).

    Erfasst die ``_BADGE_COLOR_MAP``-Badge-Farben (``bg-indigo-100`` …) **und**
    weitere aus Python emittierte Farb-Utilities außerhalb des Dict-Literals
    (z. B. ``bg-gray-50`` im Audit-Detail-``<pre>``, ``format_html`` bei
    core_tags.py:``json_pretty``). Wie in ``dates.py`` treten Farb-Utilities hier
    ausschließlich in gerenderten Klassen-Strings auf; ein Scan der ganzen Datei
    ist daher präzise genug und deckt — anders als ein reiner ``_BADGE_COLOR_MAP``-
    Scan — **jede** aus dieser Quelle ableitbare Farb-Klasse ab (kein Widerspruch
    zum erklärten Guard-Ziel).
    """
    return set(COLOR_CLASS_RE.findall(core_tags_src))


def date_status_classes(dates_src: str) -> set[str]:
    """Farb-Klassen aus ``core/utils/dates.py`` (alle ``css_class``-Literale).

    In ``dates.py`` treten Farb-Utilities ausschließlich in den css_class-Strings
    auf (``bg-orange-100 text-orange-800``, ``text-red-600`` …); ein Scan der
    ganzen Datei ist daher präzise genug.
    """
    return set(COLOR_CLASS_RE.findall(dates_src))


def expected_dynamic_classes(core_tags_src: str, dates_src: str) -> set[str]:
    """Vereinigung der aus Python emittierten dynamischen Farb-Klassen."""
    return badge_color_classes(core_tags_src) | date_status_classes(dates_src)


def brace_expand(pattern: str) -> list[str]:
    """Minimale Brace-Expansion: ``bg-{a,b}-{1,2}`` → 4 Klassen.

    Unterstützt mehrere (auch aufeinanderfolgende) ``{...}``-Gruppen via Rekursion.
    Verschachtelte Braces kommen in den ``@source inline``-Zeilen nicht vor.
    """
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    pre, post = pattern[: match.start()], pattern[match.end() :]
    out: list[str] = []
    for option in match.group(1).split(","):
        out.extend(brace_expand(pre + option + post))
    return out


def safelisted_classes(input_css_src: str) -> set[str]:
    """Alle Klassen, die ``input.css`` via ``@source inline("…")`` safelistet.

    Jede ``@source inline("…")``-Direktive kann mehrere space-getrennte Tokens und
    Brace-Expansion enthalten; beides wird hier zu konkreten Klassennamen expandiert.
    """
    classes: set[str] = set()
    for match in re.finditer(r'@source\s+inline\(\s*"([^"]+)"\s*\)', input_css_src):
        for token in match.group(1).split():
            classes.update(brace_expand(token))
    return classes


# ---------------------------------------------------------------------------
# Echte-Daten-Guard (heute MUSS gelten: input.css safelistet jede Python-Klasse)
# ---------------------------------------------------------------------------


def test_input_css_safelists_all_dynamic_python_classes() -> None:
    core_tags_src = CORE_TAGS.read_text(encoding="utf-8")
    dates_src = DATES.read_text(encoding="utf-8")
    input_css_src = INPUT_CSS.read_text(encoding="utf-8")

    expected = expected_dynamic_classes(core_tags_src, dates_src)
    safelisted = safelisted_classes(input_css_src)

    assert expected, "Keine dynamischen Farb-Klassen in den Python-Quellen gefunden — Parser/Regex prüfen."

    missing = expected - safelisted
    assert not missing, (
        "src/static/css/input.css safelistet nicht alle dynamisch aus Python emittierten "
        f"Tailwind-Klassen (Refs #1480). Fehlend: {sorted(missing)}. "
        "Ergänze die entsprechende @source inline(...)-Zeile — sonst verschwinden diese "
        "Badge-/Fälligkeits-Farben unter Tailwind v4 stillschweigend."
    )


def test_badge_and_date_sources_contribute_expected_anchors() -> None:
    """Sanity: beide Python-Quellen liefern die erwarteten Anker-Klassen."""
    badge = badge_color_classes(CORE_TAGS.read_text(encoding="utf-8"))
    dates = date_status_classes(DATES.read_text(encoding="utf-8"))
    # _BADGE_COLOR_MAP: 9 Farben × {bg-100, text-800}
    assert {"bg-indigo-100", "text-indigo-800", "bg-gray-100", "text-gray-800"} <= badge
    # Audit-Detail-<pre> emittiert bg-gray-50 AUSSERHALB von _BADGE_COLOR_MAP —
    # der Voll-Datei-Scan muss auch diese Klasse erfassen (Refs #1480).
    assert "bg-gray-50" in badge
    # dates.py: Fälligkeits- + Wiedervorlage-Farben (inkl. orange, das die
    # frühere JS-safelist NICHT enthielt — Content-Scan-Absicherung).
    assert {"text-red-600", "text-amber-500", "bg-orange-100", "text-orange-800"} <= dates


# ---------------------------------------------------------------------------
# Synthetische Unit-Tests (Muster test_seed_doc_drift) — beweisen, dass die
# Funktionen Drift erkennen, ohne von den echten Quellen abzuhängen.
# ---------------------------------------------------------------------------

_SYNTHETIC_TAGS = """\
_BADGE_COLOR_MAP = {
    "indigo": "bg-indigo-100 text-indigo-800",
    "gray": "bg-gray-100 text-gray-800",
}
_UNRELATED = "font-semibold rounded p-3"  # darf NICHT als Farb-Klasse zählen
# Farb-Utility AUSSERHALB des Dict-Literals (analog bg-gray-50 im Audit-<pre>):
_AUDIT_PRE = '<pre class="text-sm bg-gray-50 rounded p-3">'
"""

_SYNTHETIC_DATES = """\
def f():
    css_class="bg-orange-100 text-orange-800"
    css_class="text-red-600 font-semibold"
"""


def test_synthetic_badge_classes() -> None:
    # Voll-Datei-Scan: Badge-Farben aus dem Dict PLUS die out-of-block-Klasse
    # bg-gray-50 (Audit-<pre>); Nicht-Farb-Utilities (font-semibold/rounded/p-3/
    # text-sm) bleiben ausgeschlossen.
    assert badge_color_classes(_SYNTHETIC_TAGS) == {
        "bg-indigo-100",
        "text-indigo-800",
        "bg-gray-100",
        "text-gray-800",
        "bg-gray-50",
    }


def test_synthetic_date_classes_ignore_non_color_utilities() -> None:
    classes = date_status_classes(_SYNTHETIC_DATES)
    assert classes == {"bg-orange-100", "text-orange-800", "text-red-600"}
    assert "font-semibold" not in classes


def test_synthetic_brace_expand() -> None:
    assert set(brace_expand("bg-{a,b}-{1,2}")) == {"bg-a-1", "bg-a-2", "bg-b-1", "bg-b-2"}
    assert brace_expand("text-red-600") == ["text-red-600"]


def test_synthetic_safelist_extraction() -> None:
    css = '@source inline("bg-{indigo,gray}-100");\n@source inline("text-red-600");'
    assert safelisted_classes(css) == {"bg-indigo-100", "bg-gray-100", "text-red-600"}


def test_synthetic_missing_class_is_detected() -> None:
    expected = expected_dynamic_classes(_SYNTHETIC_TAGS, _SYNTHETIC_DATES)
    # Safelist deckt alles AUSSER text-orange-800 ab → Drift muss auffallen.
    incomplete = (
        '@source inline("bg-{indigo,gray}-100");\n'
        '@source inline("bg-gray-50");\n'  # out-of-block Audit-<pre>-Klasse
        '@source inline("text-{indigo,gray}-800");\n'
        '@source inline("bg-orange-100");\n'
        '@source inline("text-red-600");'
    )
    missing = expected - safelisted_classes(incomplete)
    assert missing == {"text-orange-800"}


def test_collectstatic_laesst_tailwind_quelle_aus(tmp_path) -> None:
    """Regression (Refs #1480): ``css/input.css`` darf NICHT eingesammelt werden.

    Die v4-Quelle beginnt mit ``@import "tailwindcss"`` — würde sie collected,
    bräche das Manifest-Post-Processing (Prod-/Docker-Build) mit
    ``MissingFileError: css/tailwindcss``. ``ProjectStaticFilesConfig``
    (``anlaufstelle/apps.py``) schließt die Quelle via ``ignore_patterns`` aus;
    das kompilierte ``css/styles.css`` wird weiterhin ausgeliefert.
    """
    with override_settings(STATIC_ROOT=str(tmp_path)):
        call_command("collectstatic", "--noinput", verbosity=0)
    assert not (tmp_path / "css" / "input.css").exists()
    assert (tmp_path / "css" / "styles.css").exists()
