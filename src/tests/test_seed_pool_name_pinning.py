"""Pin der Seed-Namenskopplung an die default.yaml (E4, Refs #1850).

Seit der SystemType-Reduktion sind die Schluessel von ``EVENT_DATA_POOLS``
und ``EVENT_TYPE_WEIGHTS`` fuer die sieben ehemaligen Berichts-Systemtypen
**freie deutsche Namen** aus der ``default.yaml`` (nur ``crisis``/``ban``
bleiben Enum-Werte). Beide Zugriffe fallen still zurueck (generische
Zufallsdaten bzw. Default-Gewicht 5) — ein Rename im Standardpaket wuerde
die Seed-Qualitaet also lautlos degradieren statt zu brechen. Dieser Test
macht die Kopplung byte-genau falsifizierbar.
"""

from pathlib import Path

import yaml

from core.models import DocumentType
from core.seed.constants import EVENT_DATA_POOLS, EVENT_TYPE_WEIGHTS

_DEFAULT_YAML = Path("src/core/packages/default.yaml")

_ENUM_KEYS = frozenset(DocumentType.SystemType.values)


def _paket_namen() -> frozenset[str]:
    data = yaml.safe_load(_DEFAULT_YAML.read_text())
    namen = frozenset(eintrag["name"] for eintrag in data["document_types"])
    assert namen, "default.yaml ohne document_types — Pfad-/Format-Drift?"
    return namen


def test_pool_schluessel_entsprechen_paketnamen_oder_enum():
    namen = _paket_namen()
    fremd = {key for key in EVENT_DATA_POOLS if key not in namen and key not in _ENUM_KEYS}
    assert not fremd, (
        "EVENT_DATA_POOLS-Schluessel ohne byte-genauen Namens-Treffer in der "
        f"default.yaml (und kein SystemType-Wert): {sorted(fremd)} — Rename im "
        "Standardpaket? Dann degradiert der Seed still auf Zufallsdaten."
    )


def test_gewichts_schluessel_entsprechen_paketnamen_oder_enum():
    namen = _paket_namen()
    fremd = {key for key in EVENT_TYPE_WEIGHTS if key not in namen and key not in _ENUM_KEYS}
    assert not fremd, (
        "EVENT_TYPE_WEIGHTS-Schluessel ohne byte-genauen Namens-Treffer in der "
        f"default.yaml (und kein SystemType-Wert): {sorted(fremd)} — Rename im "
        "Standardpaket? Dann fallen die Typen still auf Gewicht 5 zurueck."
    )


def test_sonderzweige_der_ereignis_erzeugung_treffen_paketnamen():
    """Die zwei namensbasierten Sonderzweige in ``seed/events.py`` (Begleitung,
    Beratungsgespraech) muessen auf existierende Paketnamen zeigen."""
    namen = _paket_namen()
    for sonderfall in ("Begleitung", "Beratungsgespräch"):
        assert sonderfall in namen, (
            f"Sonderzweig-Name {sonderfall!r} aus seed/events.py fehlt in der "
            "default.yaml — Zweig wuerde nie mehr greifen."
        )
