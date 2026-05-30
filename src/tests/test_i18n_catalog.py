"""Guard gegen unvollständige/fuzzy englische Übersetzungen (Refs #974).

Die EN-Oberfläche fiel reihenweise auf deutschen Text zurück, weil der
Katalog 121 ``#, fuzzy``- und 122 leere ``msgstr``-Einträge hatte (Folge der
Klientel→Person-Umbenennung ohne nachgezogene Übersetzung) — und es keinen
CI-Guard gab. Dieser Test schlägt fehl, sobald der EN-Katalog wieder fuzzy
oder unübersetzt wird. Bewusst **ohne** externe Abhängigkeit (kein polib):
ein kleiner stdlib-Parser für PO-Blöcke genügt.
"""

from pathlib import Path

import pytest

PO_PATH = (
    Path(__file__).resolve().parent.parent
    / "locale"
    / "en"
    / "LC_MESSAGES"
    / "django.po"
)


def _parse_active_entries(text: str):
    """Yield (msgid, fuzzy, msgstr_values) für aktive (nicht-obsolete) Einträge.

    ``msgstr_values`` ist die Liste aller msgstr/msgstr[n]-Werte des Eintrags
    (bei Plural mehrere). Obsolete Einträge (``#~``) werden übersprungen.
    """
    blocks = text.split("\n\n")
    for block in blocks:
        lines = block.splitlines()
        if not lines:
            continue
        # Obsolete Einträge komplett ignorieren.
        if any(ln.startswith("#~") for ln in lines):
            continue
        fuzzy = any(ln.startswith("#,") and "fuzzy" in ln for ln in lines)
        msgid_parts: list[str] = []
        msgstr_values: list[str] = []
        current = None  # "id" | "str"
        for ln in lines:
            if ln.startswith("msgid_plural"):
                current = None
            elif ln.startswith("msgid "):
                current = "id"
                msgid_parts.append(_unquote(ln[len("msgid ") :]))
            elif ln.startswith("msgstr"):
                current = "str"
                # msgstr "..."  oder  msgstr[0] "..."
                _, _, rest = ln.partition(" ")
                msgstr_values.append(_unquote(rest))
            elif ln.startswith('"'):
                if current == "id":
                    msgid_parts.append(_unquote(ln))
                elif current == "str" and msgstr_values:
                    msgstr_values[-1] += _unquote(ln)
            else:
                current = None
        msgid = "".join(msgid_parts)
        if not msgid:  # Header-Block
            continue
        yield msgid, fuzzy, msgstr_values


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


@pytest.mark.skipif(not PO_PATH.exists(), reason="EN-Katalog nicht vorhanden")
class TestEnglishCatalogComplete:
    """Der englische Katalog muss vollständig und ohne fuzzy-Einträge sein."""

    def test_no_fuzzy_entries(self):
        fuzzy = [mid for mid, fz, _ in _parse_active_entries(PO_PATH.read_text(encoding="utf-8")) if fz]
        assert not fuzzy, (
            f"{len(fuzzy)} fuzzy EN-Einträge — fuzzy wird beim Kompilieren ignoriert "
            f"→ deutscher Fallback (Refs #974). Erste: {fuzzy[:5]}"
        )

    def test_no_untranslated_entries(self):
        untranslated = [
            mid
            for mid, _, values in _parse_active_entries(PO_PATH.read_text(encoding="utf-8"))
            if any(v == "" for v in values)
        ]
        assert not untranslated, (
            f"{len(untranslated)} unübersetzte EN-Einträge (leeres msgstr) "
            f"→ deutscher Fallback (Refs #974). Erste: {untranslated[:5]}"
        )
