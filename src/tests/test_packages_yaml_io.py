"""Tests für den Duplikat-Key-strikten YAML-Loader (Härtung Refs #1727).

``yaml.SafeLoader`` akzeptiert Duplikat-Keys still (Last-Write-Wins). Für ein
Konfigurationspaket ist das ein Fehler-Vektor — ``core.packages.yaml_io``
lehnt sie mit Key-Name und Zeile ab. Rein in-memory (kein ``django_db``).
"""

from __future__ import annotations

import pytest
import yaml

from core.packages.yaml_io import DuplicateKeyError, safe_load_unique


def test_unique_keys_load_normally() -> None:
    assert safe_load_unique("a: 1\nb: 2\n") == {"a": 1, "b": 2}


def test_nested_unique_keys_load_normally() -> None:
    data = safe_load_unique("outer:\n  k1: 1\n  k2: 2\nliste:\n  - {slug: x, name: X}\n")
    assert data == {"outer": {"k1": 1, "k2": 2}, "liste": [{"slug": "x", "name": "X"}]}


def test_duplicate_key_raises_naming_the_key() -> None:
    with pytest.raises(DuplicateKeyError) as excinfo:
        safe_load_unique("a: 1\na: 2\n")
    assert "'a'" in str(excinfo.value)


def test_duplicate_key_is_a_yaml_error() -> None:
    # Subklasse von ``yaml.YAMLError`` — bestehende ``except yaml.YAMLError``-
    # Klauseln (load_package-Command) fangen sie ohne Anpassung.
    with pytest.raises(yaml.YAMLError):
        safe_load_unique("x: 1\nx: 2\n")


def test_duplicate_key_in_nested_mapping_raises() -> None:
    with pytest.raises(DuplicateKeyError):
        safe_load_unique("outer:\n  k: 1\n  k: 2\n")


def test_error_reports_line_number() -> None:
    with pytest.raises(DuplicateKeyError) as excinfo:
        safe_load_unique("a: 1\nb: 2\na: 3\n")
    assert "Zeile 3" in str(excinfo.value)
