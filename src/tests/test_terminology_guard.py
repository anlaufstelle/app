"""Terminologie-Guards (Refs #1805).

Der Sprach-Sweep haelt nur, wenn die Regeln danach maschinell nachgehalten
werden — sonst driftet die naechste Aenderung lautlos zurueck. Zwei Klassen
von Guards leben hier:

1. **Ein-Quellen-Garantie fuer die Kontaktstufen-Labels.** Die Labels stehen
   in DREI Baeumen: ``Client.ContactStage`` (Modell), ``core.constants
   .CONTACT_STAGE_CHOICES`` (speist ``DocumentType.min_contact_stage`` und
   ``packages.schema``) und ``static/js/statistics-charts.js`` (hartcodierte
   Chart-Legende ohne i18n). Die beiden Python-Quellen waren bisher nur per
   Kommentar-Disziplin gekoppelt, das JS gar nicht — wer eine Datei vergisst,
   bekommt zwei Vokabeln nebeneinander auf einem Bildschirm.

2. **Denylist-Scan ueber die uebersetzbaren Strings.** Kein Markup-Dialekt
   (T3), kein Code-Identifier (T2), keine aufgeloeste Begriffs-Drift (T1).

``pytest.mark.architecture`` nach dem Muster von
``test_architecture_guards_templates.py`` (Mutmut-Konvention).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core import constants
from core.models.client import Client

pytestmark = pytest.mark.architecture

SRC = Path(__file__).resolve().parent.parent
CORE = SRC / "core"
TEMPLATES = SRC / "templates"
STATIC_JS = SRC / "static" / "js"


# --------------------------------------------------------------------------
# 1. Kontaktstufen-Labels: eine Aussage, drei Baeume
# --------------------------------------------------------------------------

# Entscheid des Maintainers zu #1805 §6 Frage 4: die Stufen heissen
# lebensweltlich, nicht mehr "Identifiziert"/"Qualifiziert". Die Werte
# ("identified"/"qualified") und damit API, Export und CSV-Datenvertrag
# bleiben unveraendert — nur das sichtbare Label wechselt.
EXPECTED_CONTACT_STAGE_LABELS = {
    "identified": "Nur Pseudonym",
    "qualified": "Mit Zusatzangaben",
}


def test_contact_stage_labels_are_lifeworld_wording() -> None:
    """Die Choice-Labels tragen die entschiedene Wortwahl (und die Werte nicht)."""
    assert {stage.value: str(stage.label) for stage in Client.ContactStage} == EXPECTED_CONTACT_STAGE_LABELS


def test_contact_stage_labels_single_source() -> None:
    """``core.constants`` darf nicht von ``Client.ContactStage`` abweichen.

    Die Konstante speist ``DocumentType.min_contact_stage`` — driftet sie,
    stehen im Typ-Formular andere Stufennamen als in der Personenakte.
    """
    assert {value: str(label) for value, label in constants.CONTACT_STAGE_CHOICES} == {
        stage.value: str(stage.label) for stage in Client.ContactStage
    }


def test_chart_js_labels_match_contact_stage() -> None:
    """Die hartcodierte Chart-Legende traegt dieselben Labels wie das Modell.

    ``statistics-charts.js`` liegt ausserhalb des i18n-Katalogs und ausserhalb
    des Denylist-Scans unten — ohne diesen Guard driftet die Legende des
    Kontaktverlaufs lautlos gegen den Rest der Statistik.
    """
    js = (STATIC_JS / "statistics-charts.js").read_text(encoding="utf-8")
    for stage in Client.ContactStage:
        assert f'label: "{stage.label}"' in js, (
            f"statistics-charts.js kennt das Label {stage.label!r} nicht — "
            "Chart-Legende und Kontaktstufen sind auseinandergelaufen."
        )


def test_no_retired_contact_stage_labels_in_static_js() -> None:
    """Kein JS traegt noch die abgeloesten Stufennamen.

    ``src/static/js`` liegt ausserhalb des i18n-Katalogs; der Denylist-Scan
    unten sieht nur ``_()``/``{% trans %}``. Ohne diese Runde bliebe eine
    zweite hartcodierte Legende unbemerkt.
    """
    offenders = [
        f"{path.relative_to(SRC.parent)}: {word}"
        for path in sorted(STATIC_JS.rglob("*.js"))
        if "dexie" not in path.name
        for word in ("Identifiziert", "Qualifiziert")
        if word in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "Abgeloeste Kontaktstufen-Labels in JS: " + ", ".join(offenders)


# --------------------------------------------------------------------------
# 2. Denylist-Scan ueber die uebersetzbaren Strings
# --------------------------------------------------------------------------

# Nur ``_()``-artige Aufrufe: alles andere ist Code, kein Text an Menschen.
_GETTEXT_FUNCS = frozenset(
    {
        "_",
        "gettext",
        "gettext_lazy",
        "ngettext",
        "ngettext_lazy",
        "pgettext",
        "pgettext_lazy",
        "format_lazy",
    }
)

DENYLIST: list[tuple[str, re.Pattern[str], str]] = [
    (
        # T3 — help_text landet ueber components/_form_field.html ohne ``|safe``
        # im HTML. ``**fett**`` erzeugt literale Sternchen im Formular.
        "Markup-Dialekt",
        re.compile(r"\*\*"),
        "Betonung gehoert ins Template, nicht in den Python-String.",
    ),
    (
        # T2 — Ausgaben an Menschen, die diese Namen nirgends sehen koennen.
        "Code-Identifier",
        re.compile(
            r"FieldTemplate|DocumentType|Sensitivity\s*=|is_encrypted|encrypt_field\("
            r"|\bELEVATED\b|\bHIGH\b|AgeCluster|ContactStage|Art-9"
        ),
        "Das sichtbare UI-Label nehmen (z. B. „Zusatzfeld“, „Sensibilität ‚Hoch‘“, „Art. 9 DSGVO“).",
    ),
    (
        # T1 — je Ebene genau eine Form. Wortgrenze, damit
        # „Dokumentationstyp“ nicht mittrifft.
        "Aufgeloeste Begriffs-Drift",
        re.compile(r"Klient|\bDokumenttyp|Alterscluster"),
        "Kanonisch sind „Person“, „Dokumentationstyp“ und „Altersgruppe“ (docs/sprachleitlinie.md).",
    ),
]

# Knappe, begruendete Ausnahmen. Leer — bislang braucht kein
# Datenschutz-String einen Code-Identifier, die VVT nennt die UI-Labels.
ALLOWED: frozenset[str] = frozenset()

_TRANS_RE = re.compile(r"{%\s*(?:trans|translate)\s+([\"'])(.*?)\1", re.S)
_BLOCKTRANS_RE = re.compile(r"{%\s*blocktrans(?:late)?[^%]*%}(.*?){%\s*endblocktrans(?:late)?\s*%}", re.S)


def _python_translatable_strings():
    """``(datei, zeile, text)`` je Literal in einem gettext-Aufruf unter ``src/core``.

    ``ast`` statt Regex, weil implizite String-Konkatenation ueber mehrere
    Zeilen (das Hausmuster fuer lange ``help_text``) sonst zerfaellt.
    Migrationen sind ausgenommen: sie frieren historische Labels ein und
    werden nie neu formuliert.
    """
    import ast

    for path in sorted(CORE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name not in _GETTEXT_FUNCS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield path, node.lineno, arg.value


def _template_translatable_strings():
    """``(datei, zeile, text)`` je ``{% trans %}``/``{% blocktrans %}``-Text."""
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in _TRANS_RE.finditer(text):
            yield path, text[: match.start()].count("\n") + 1, match.group(2)
        for match in _BLOCKTRANS_RE.finditer(text):
            yield path, text[: match.start()].count("\n") + 1, match.group(1)


def test_denylist_scan_finds_something_to_scan() -> None:
    """Selbsttest: ein kaputter Extraktor darf nicht als „0 Befunde“ durchgehen."""
    assert sum(1 for _ in _python_translatable_strings()) > 500
    assert sum(1 for _ in _template_translatable_strings()) > 500


@pytest.mark.parametrize("label", [entry[0] for entry in DENYLIST])
def test_no_denylisted_terms_in_translatable_strings(label: str) -> None:
    """Kein uebersetzbarer String traegt Markup, Code-Identifier oder Alt-Vokabel."""
    _, pattern, hint = next(entry for entry in DENYLIST if entry[0] == label)
    offenders = [
        f"{path.relative_to(SRC.parent)}:{lineno} — {text[:120]!r}"
        for path, lineno, text in (
            *_python_translatable_strings(),
            *_template_translatable_strings(),
        )
        if text not in ALLOWED and pattern.search(text)
    ]
    assert not offenders, f"{label} in uebersetzbaren Strings ({hint}):\n" + "\n".join(offenders)
