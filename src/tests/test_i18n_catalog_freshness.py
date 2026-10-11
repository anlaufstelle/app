"""Frische-Guard: das ausgelieferte ``.mo`` passt inhaltlich zur ``.po``-Quelle.

Refs #1775. Die Fehlerklasse trat im Sammel-Lauf real auf: eine ``.po``
korrigiert, ``compilemessages`` vergessen. Django liest zur Laufzeit
**ausschliesslich** das kompilierte ``.mo`` — die Korrektur ist damit in
Produktion wirkungslos, waehrend der Diff im Review korrekt aussieht.

Bisher deckte das nur ``test_i18n_audit_action_labels`` ab, und dort nur fuer
eine Handvoll Audit-Labels; jede andere msgid blieb ungeschuetzt.

Der Vergleich ist bewusst **mtime-unabhaengig**: die ``.po`` wird per
``msgfmt`` (dasselbe Werkzeug, das ``manage.py compilemessages`` aufruft) in
ein Tempdir neu kompiliert, und der resultierende Katalog wird mit dem
ausgelieferten ``.mo`` verglichen. Ein Zeitstempel-Vergleich waere wertlos —
ein ``touch``, ein frischer Checkout oder ein Merge in beliebiger Reihenfolge
kippt ihn in beide Richtungen.

Verglichen wird der geladene Katalog (msgid -> msgstr, inklusive
Plural-Tupeln und Header), nicht das Byte-Bild: die Bytes haengen an der
msgfmt-Version und ihren Optionen, der Katalog nicht.
"""

from __future__ import annotations

import gettext
import shutil
import subprocess
from pathlib import Path

import pytest

# Meta-Test auf dem Repo-Filesystem (msgfmt-Subprozess) — wie die uebrigen
# Datei-Guards vom Mutmut-Lauf deselektiert (Refs #930).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALE_DIR = REPO_ROOT / "src" / "locale"
SPRACHEN = ("de", "en")


def _katalog(mo_pfad: Path) -> dict:
    """msgid -> msgstr des kompilierten Katalogs (inkl. Header und Pluralen)."""
    with mo_pfad.open("rb") as fh:
        return gettext.GNUTranslations(fh)._catalog


def _neu_kompiliert(po_pfad: Path, ziel: Path) -> dict:
    ergebnis = subprocess.run(
        ["msgfmt", "--check-format", "-o", str(ziel), str(po_pfad)],
        capture_output=True,
        text=True,
    )
    assert ergebnis.returncode == 0, f"msgfmt scheitert an {po_pfad}:\n{ergebnis.stderr}"
    return _katalog(ziel)


def _abweichungen(erwartet: dict, geliefert: dict) -> list[str]:
    befunde = []
    for key in sorted(set(erwartet) | set(geliefert), key=repr):
        if erwartet.get(key) != geliefert.get(key):
            befunde.append(f"  {key!r}: .po -> {erwartet.get(key)!r} | .mo -> {geliefert.get(key)!r}")
    return befunde


@pytest.mark.skipif(shutil.which("msgfmt") is None, reason="gettext-tools (msgfmt) nicht installiert")
@pytest.mark.parametrize("sprache", SPRACHEN)
class TestKatalogFrische:
    def test_mo_entspricht_der_po_quelle(self, sprache, tmp_path):
        po = LOCALE_DIR / sprache / "LC_MESSAGES" / "django.po"
        mo = LOCALE_DIR / sprache / "LC_MESSAGES" / "django.mo"
        if not (po.exists() and mo.exists()):  # pragma: no cover — nur im mutants/-Tree
            pytest.skip(f"Katalog fuer {sprache} nicht im Baum")

        erwartet = _neu_kompiliert(po, tmp_path / f"{sprache}.mo")
        geliefert = _katalog(mo)

        abweichungen = _abweichungen(erwartet, geliefert)
        assert not abweichungen, (
            f"Das ausgelieferte {mo.relative_to(REPO_ROOT)} passt nicht zu seiner .po-Quelle — "
            "``python src/manage.py compilemessages`` nach der Katalogaenderung vergessen? "
            "Django liest nur die .mo, die Korrektur waere in Produktion wirkungslos "
            f"(CONTRIBUTING.md: die .mo liegen im Repo, das Deployment baut sie nicht).\n"
            f"{chr(10).join(abweichungen[:20])}"
        )

    def test_der_vergleich_erkennt_eine_drift(self, sprache, tmp_path):
        """Positiv-Kontrolle: eine geaenderte ``.po`` MUSS auffallen.

        Ohne diesen Anker koennte der Vergleich stillschweigend leerlaufen
        (z. B. wenn ``_katalog`` beide Seiten aus derselben Datei laedt) und
        der Guard waere dauerhaft gruen, ohne je etwas zu pruefen.
        """
        po = LOCALE_DIR / sprache / "LC_MESSAGES" / "django.po"
        mo = LOCALE_DIR / sprache / "LC_MESSAGES" / "django.mo"
        if not (po.exists() and mo.exists()):  # pragma: no cover — nur im mutants/-Tree
            pytest.skip(f"Katalog fuer {sprache} nicht im Baum")

        # Eine msgid mit nicht-leerer Uebersetzung verbiegen — dieselbe
        # Situation wie ".po geaendert, .mo vergessen". Gesucht wird der erste
        # Eintrag, der in der .po als einzeiliges ``msgid``/``msgstr``-Paar
        # steht (mehrzeilige Eintraege liessen sich nicht per Textersetzung
        # verbiegen); welcher es ist, spielt fuer die Kontrolle keine Rolle.
        geliefert = _katalog(mo)
        quelle = po.read_text(encoding="utf-8")
        ziel_msgid = ziel_paar = None
        for key, wert in sorted(geliefert.items(), key=repr):
            if not (isinstance(key, str) and key and wert):
                continue
            if "%" in key or "{" in key:
                # ``msgfmt --check-format`` rejects a placeholder msgid paired with
                # the control translation; the probe needs an entry without placeholders.
                continue
            paar = f'msgid "{key}"\nmsgstr "{wert}"'
            if paar in quelle:
                ziel_msgid, ziel_paar = key, paar
                break
        assert ziel_msgid, f"Kein einzeiliger Katalogeintrag in {po.relative_to(REPO_ROOT)} gefunden"

        verbogen = tmp_path / "verbogen.po"
        verbogen.write_text(
            quelle.replace(ziel_paar, f'msgid "{ziel_msgid}"\nmsgstr "Drift-Kontrolle"', 1),
            encoding="utf-8",
        )

        erwartet = _neu_kompiliert(verbogen, tmp_path / "verbogen.mo")

        abweichungen = _abweichungen(erwartet, geliefert)
        assert abweichungen, f"Der Vergleich haette die verbogene Uebersetzung von {ziel_msgid!r} melden muessen"
