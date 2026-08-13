"""Env-Var-Parsing-Helfer fuer die Settings-Module (Refs #1827).

``base.py``/``prod.py`` wiederholten drei Muster knapp 40x von Hand: 29x
``int(os.environ.get(name, "N"))``, 5x den Bool-Truthy-Check
(``.lower() in ("true", "1", "yes")``), 4x das Komma-Listen-Splitting
(``[x.strip() for x in os.environ.get(...).split(",") if x.strip()]``).

Bewusst ein eigenes, reines stdlib-Modul (wie ``_mfa_roles.py``, Refs #1639):
Settings-Module laufen vor dem App-Registry-Load und duerfen kein
``core``-Modell importieren; ein Django-freies Modul bleibt zirkularitaets-
frei und laesst sich isoliert (ohne Settings-Reimport) testen.
"""

import os

# Dieselbe Werteliste wie ueberall im Projekt bereits verwendet — case-
# insensitiv, damit ".env"-Werte wie "True"/"TRUE"/"1"/"yes" gleichermassen
# als wahr gelten.
_TRUE_VALUES = ("true", "1", "yes")


def env_int(name: str, default: int) -> int:
    """``int(os.environ.get(name, default))`` — Default bleibt ein Python-``int``,
    keine erneute String-Umwandlung an der Aufrufstelle noetig."""
    return int(os.environ.get(name, str(default)))


def env_bool(name: str, *, default: bool) -> bool:
    """Truthy-Wert einer Env-Var gegen die projektweite Werteliste
    (``"true"``/``"1"``/``"yes"``, case-insensitiv)."""
    raw = os.environ.get(name, "true" if default else "false")
    return raw.lower() in _TRUE_VALUES


def env_csv(name: str, default: str = "") -> list[str]:
    """Kommaliste -> getrimmte, nicht-leere Eintraege (Reihenfolge erhalten,
    Duplikate NICHT entfernt — wie das bisherige Hand-Parsing)."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]
