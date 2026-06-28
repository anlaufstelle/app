"""Drift-Guard: Seed-Code ↔ CONTRIBUTING-Doku (Refs #1096).

Die Seed-Konfiguration (``SCALE_CONFIG``/``USER_TEMPLATES`` + das Seed-Passwort)
und die Tabelle „Seed-Daten laden" in ``CONTRIBUTING.md`` werden heute von Hand
synchron gehalten — die Regel steht in ``CLAUDE.md``, aber nichts erzwingt sie.
Dieser Guard erzwingt die **Vollstaendigkeit** (Profile, Metrik-Felder,
Credentials), bewusst NICHT die Zahlenwerte (die Doku zeigt menschenfreundliche
Werte wie „3 Jahre"/„50 %").

Skeleton-Vorbild: ``scripts/verify_test_matrix_drift.py`` + ``test_matrix_drift``
— reine Funktionen, mit synthetischem Input unit-getestet. Kein ``django_db``:
der Test importiert nur Seed-Module (Registry, kein DB-Zugriff) und liest Dateien.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.seed.constants import USER_TEMPLATES
from core.seed.scale import SCALE_CONFIG

# Meta-Test, der auf das Repo-Filesystem zugreift (``CONTRIBUTING.md`` +
# Seed-Quellen). Im Mutmut-Subprozess laeuft pytest aus ``mutants/`` und diese
# Pfade fehlen, daher als ``architecture`` markiert (analog test_matrix_drift).
pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
SEED_USERS_SRC = REPO_ROOT / "src" / "core" / "seed" / "users.py"

# Profile, die bewusst NICHT in der oeffentlichen CONTRIBUTING-Tabelle stehen
# (``solo`` ist ein internes Profil; die Tabelle zeigt nur small/medium/large).
UNDOCUMENTED_PROFILES = frozenset({"solo"})

# Jeder Metrik-Key aus ``SCALE_CONFIG`` wird einer Zeilenbeschriftung der
# CONTRIBUTING-Tabelle zugeordnet. Fehlt ein Mapping fuer einen neuen Key, failt
# ``test_every_scale_key_is_documented`` laut — genau die ``CLAUDE.md``-Regel
# „neue Seed-Felder mitdokumentieren". Die Map ist der eine Pflegepunkt, aber
# guarded.
KEY_TO_DOC_LABEL = {
    "facilities": "Einrichtungen",
    "users_per_facility": "Users / Einrichtung",
    "clients_per_facility": "Clients / Einrichtung",
    "events_per_facility": "Events / Einrichtung",
    "cases": "Cases",
    "episodes": "Episoden",
    "goals": "Wirkungsziele",
    "milestones_per_goal": "Meilensteine / Ziel",
    "work_items": "WorkItems",
    "deletion_requests": "DeletionRequests",
    "retention_proposals": "RetentionProposals",
    "attachment_ratio": "Dateianhänge (ca.)",
    "zeitraum_days": "Zeitraum",
}


# ---------------------------------------------------------------------------
# Reine Funktionen (synthetisch unit-testbar)
# ---------------------------------------------------------------------------


def _scale_table_rows(md: str) -> list[str]:
    """Markdown-Zeilen der „Scale-Profile im Überblick"-Tabelle (ohne Separator).

    ``rows[0]`` ist der Header, ``rows[1:]`` sind die Datenzeilen.
    """
    lines = md.splitlines()
    start = next((i for i, ln in enumerate(lines) if "Scale-Profile im Überblick" in ln), None)
    if start is None:
        return []
    rows: list[str] = []
    for ln in lines[start:]:
        stripped = ln.strip()
        if stripped.startswith("|") and set(stripped) <= set("|-: "):
            continue  # Separatorzeile |---|---|
        if stripped.startswith("|"):
            rows.append(stripped)
        elif rows:
            break  # erste Nicht-Tabellenzeile nach Tabellenbeginn -> Ende
    return rows


def documented_profiles(md: str) -> set[str]:
    """Profil-Spalten der Scale-Tabelle (in Backticks im Header)."""
    rows = _scale_table_rows(md)
    if not rows:
        return set()
    return set(re.findall(r"`(\w+)`", rows[0]))


def documented_metric_labels(md: str) -> set[str]:
    """Zeilenbeschriftungen (erste Spalte) der Scale-Datenzeilen."""
    labels: set[str] = set()
    for row in _scale_table_rows(md)[1:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if cells and cells[0]:
            labels.add(cells[0])
    return labels


def parse_seed_credentials(md: str) -> tuple[dict[str, str], str]:
    """``({username: role}, password)`` aus der „Seed-Zugangsdaten"-Zeile."""
    line = next((ln for ln in md.splitlines() if "Seed-Zugangsdaten" in ln), "")
    pw_match = re.search(r"Passwort\s+`([^`]+)`", line)
    password = pw_match.group(1) if pw_match else ""
    creds = dict(re.findall(r"`(\w+)`\s*→\s*`(\w+)`", line))
    return creds, password


def seed_password() -> str:
    """Das in ``core/seed/users.py`` geseedete Passwort-Literal (SSOT).

    Bewusst aus der Quelle gelesen statt im Test dupliziert — sonst entstaende
    eine dritte, ungeprüfte Kopie. Mehrere ``set_password``-Aufrufe muessen
    denselben Wert tragen, sonst ist der Seed selbst inkonsistent.
    """
    src = SEED_USERS_SRC.read_text(encoding="utf-8")
    literals = set(re.findall(r"""set_password\(\s*["']([^"']+)["']\s*\)""", src))
    assert len(literals) == 1, f"Erwartete genau ein Seed-Passwort-Literal in users.py, fand: {sorted(literals)}"
    return literals.pop()


# ---------------------------------------------------------------------------
# Echte-Daten-Guards (heute MUSS Code == Doku gelten)
# ---------------------------------------------------------------------------


def _contributing() -> str:
    return CONTRIBUTING.read_text(encoding="utf-8")


def test_documented_profiles_match_scale_config() -> None:
    documented = documented_profiles(_contributing())
    assert set(SCALE_CONFIG) - UNDOCUMENTED_PROFILES == documented, (
        f"Scale-Profile (Code) != CONTRIBUTING-Tabelle. Code: {sorted(set(SCALE_CONFIG) - UNDOCUMENTED_PROFILES)}; "
        f"Doku: {sorted(documented)}. Neues Profil in die Tabelle aufnehmen — oder, falls bewusst intern, "
        "in UNDOCUMENTED_PROFILES ergaenzen."
    )


def test_every_scale_key_is_documented() -> None:
    labels = documented_metric_labels(_contributing())
    all_keys = {key for cfg in SCALE_CONFIG.values() for key in cfg}
    for key in sorted(all_keys):
        assert key in KEY_TO_DOC_LABEL, (
            f"Neues Seed-Feld '{key}': CONTRIBUTING-Tabelle + KEY_TO_DOC_LABEL ergaenzen (CLAUDE.md-Regel)."
        )
        assert KEY_TO_DOC_LABEL[key] in labels, (
            f"Seed-Feld '{key}' erwartet die Tabellenzeile '{KEY_TO_DOC_LABEL[key]}' in CONTRIBUTING — fehlt."
        )


def test_documented_credentials_match_seed_code() -> None:
    creds, password = parse_seed_credentials(_contributing())
    expected = {"superadmin": "super_admin"}
    expected.update({base: role.value for base, _first, _last, role in USER_TEMPLATES})
    assert creds == expected, f"Seed-Logins (Doku) != Code. Doku: {creds}; erwartet: {expected}."
    assert password == seed_password(), f"Dokumentiertes Seed-Passwort '{password}' != Seed-Quelle '{seed_password()}'."


# ---------------------------------------------------------------------------
# Synthetische Unit-Tests (Muster test_matrix_drift) — beweisen, dass die
# Funktionen Drift erkennen, ohne von der echten Doku abzuhaengen.
# ---------------------------------------------------------------------------

_SYNTHETIC = """\
**Scale-Profile im Überblick:**

| Daten | `small` (Default) | `medium` | `large` |
|---|---|---|---|
| Einrichtungen | 1 | 2 | 5 |
| Cases | 3 | 12 | 50 |

> Hinweis ausserhalb der Tabelle.

Seed-Zugangsdaten: Passwort `geheim123`, Logins (Username → Rolle): \
`superadmin` → `super_admin` (keine `facility`-Zuordnung), `admin` → `facility_admin`.
"""


def test_synthetic_documented_profiles() -> None:
    assert documented_profiles(_SYNTHETIC) == {"small", "medium", "large"}


def test_synthetic_metric_labels() -> None:
    assert documented_metric_labels(_SYNTHETIC) == {"Einrichtungen", "Cases"}


def test_synthetic_missing_profile_is_detected() -> None:
    snippet = _SYNTHETIC.replace(" `large` |", " |", 1)
    assert "large" not in documented_profiles(snippet)


def test_synthetic_credentials_and_password() -> None:
    creds, password = parse_seed_credentials(_SYNTHETIC)
    assert creds == {"superadmin": "super_admin", "admin": "facility_admin"}
    assert password == "geheim123"


def test_synthetic_wrong_password_is_detected() -> None:
    snippet = _SYNTHETIC.replace("geheim123", "falsch999")
    _creds, password = parse_seed_credentials(snippet)
    assert password == "falsch999"  # Parser folgt der Doku -> Guard-Vergleich wuerde rot


def test_synthetic_missing_row_is_detected() -> None:
    snippet = _SYNTHETIC.replace("| Cases | 3 | 12 | 50 |\n", "")
    assert "Cases" not in documented_metric_labels(snippet)
