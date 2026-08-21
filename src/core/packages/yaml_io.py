"""Duplikat-Key-strikte YAML-Deserialisierung fürs Paketformat (Härtung Refs #1727).

``yaml.SafeLoader`` akzeptiert denselben Schlüssel mehrfach in EINEM Mapping
stillschweigend — der letzte gewinnt (Last-Write-Wins). Für ein
Konfigurationspaket ist das ein Fehler-Vektor: ein versehentliches zweites
``name:``/``sort_order:`` verpuffte lautlos, obwohl es fast sicher ein Copy-
Paste-Fehler ist. :func:`safe_load_unique` verhält sich wie
``yaml.safe_load``, wirft aber bei Duplikat-Keys einen
:class:`DuplicateKeyError` (Key-Name + Zeile).

Bewusst als eigenes Modul (nicht im Command): der Standardkatalog wird über
denselben strikten Pfad gelesen
(:func:`core.packages.default_catalog.load_default_package_data`), damit ein
Katalog-Copy-Paste-Fehler im Repo genauso auffällt wie in einem fremden Paket.
"""

from __future__ import annotations

from typing import Any, cast

import yaml


class DuplicateKeyError(yaml.YAMLError):
    """Ein YAML-Mapping enthält denselben Schlüssel mehrfach.

    Subklasse von :class:`yaml.YAMLError`, damit bestehende
    ``except yaml.YAMLError``-Klauseln (Command) sie ohne Anpassung fangen.
    """


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """``SafeLoader``, der Duplikat-Keys je Mapping ablehnt."""


def _construct_mapping(loader: _UniqueKeySafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    # Reihenfolge wie im Standard-Constructor, nur mit vorgeschalteter
    # Duplikat-Erkennung: Paket-Keys sind ausnahmslos Skalare (Strings), daher
    # ist ``construct_object`` auf dem Key-Node hier immer hashbar.
    seen: set[Any] = set()
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            mark = key_node.start_mark
            raise DuplicateKeyError(f"Doppelter Schlüssel {key!r} in Zeile {mark.line + 1}, Spalte {mark.column + 1}.")
        seen.add(key)
    # Paket-Keys sind ausnahmslos Skalare (Strings, s. Docstring) — der
    # Stub von ``construct_mapping`` liefert das generischere
    # ``dict[Hashable, Any]``, das die Signatur hier nicht widerspiegelt.
    return cast("dict[str, Any]", yaml.SafeLoader.construct_mapping(loader, node, deep=deep))


_UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def safe_load_unique(text: str) -> Any:
    """Wie ``yaml.safe_load(text)``, aber Duplikat-Keys sind ein Fehler.

    :raises DuplicateKeyError: sobald ein Mapping denselben Schlüssel zweimal
        trägt (mit Key-Name und Zeile). Andere Syntaxfehler bleiben ein
        gewöhnlicher ``yaml.YAMLError``.
    """
    # Sicher: ``_UniqueKeySafeLoader`` erbt von ``yaml.SafeLoader`` und
    # registriert KEINE ``!!python/object``-Konstruktoren — es fügt nur die
    # Duplikat-Key-Prüfung über dem Safe-Mapping-Constructor hinzu. Das ist
    # PyYAMLs dokumentiertes Muster zum Anpassen des sicheren Ladens; es ist
    # NICHT ``yaml.unsafe_load``/``FullLoader`` (kein Code-Execution-Vektor).
    return yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506 — SafeLoader-Subklasse, keine python/object-Tags
