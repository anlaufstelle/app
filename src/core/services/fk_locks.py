"""Vorsperre der Fremdschluessel-Ziele einer neuen Zeile (ADR-050 D5.5, Refs #2259/#2337).

Django legt Fremdschluessel auf PostgreSQL ``DEFERRABLE INITIALLY DEFERRED``
an: die Pruefung eines Inserts laeuft erst beim COMMIT und nimmt dort auf jede
referenzierte Zeile eine ``FOR KEY SHARE``-Sperre. Ohne Vorsperre faellt diese
ERSTE Zeilensperre hinter den bereits gehaltenen Ketten-/Kontingent-Lock der
Einrichtung und dreht die einrichtungsweite Ordnung „Zeilen vor Ketten-Lock"
um (Refs #2260).

Dieses Modul ist die EINE Stelle, die die Ziele bestimmt und sperrt — fuer
jeden Pfad, der vor dem Ketten-Lock eine neue Zeile vorbereitet (Import-Lauf,
Anhangs-Batch). Die Ziele werden **aus dem Modell abgeleitet**, nicht
aufgezaehlt: ein spaeter hinzukommender Fremdschluessel ist in jedem Pfad
automatisch dabei, statt in einem von ihnen stillschweigend zu fehlen.

* Staerke ``FOR KEY SHARE`` per Roh-SQL — genau die Sperre, die der COMMIT
  nimmt. ``select_for_update(no_key=True)`` waere ``FOR NO KEY UPDATE`` und
  liesse zwei gleichzeitige Inserts mit demselben Ziel (etwa derselben
  Feldvorlage) aufeinander warten, obwohl keiner die Zeile aendert.
* Feste Reihenfolge Tabelle, dann PK, damit zwei Pfade mit ueberlappenden
  Zielmengen sie nie gegenlaeufig nehmen.
* Auf Nicht-PostgreSQL (SQLite) entfaellt die Sperre.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from django.db import connection
from django.db.models import ForeignKey

#: ``(tabelle, spalte, wert)`` — eine FK-Zielzeile.
LockTarget = tuple[str, str, Any]


def _fk_fields(model) -> list[ForeignKey]:
    return [field for field in model._meta.get_fields() if isinstance(field, ForeignKey)]


def fk_values_of(instance) -> dict[str, Any]:
    """Die Fremdschluesselwerte einer (auch ungespeicherten) Instanz, nach ``attname``."""
    return {field.attname: getattr(instance, field.attname, None) for field in _fk_fields(type(instance))}


def fk_lock_targets(model, values: Mapping[str, Any]) -> list[LockTarget]:
    """Die Zeilen, die ein Insert von ``model`` mit ``values`` beim COMMIT sperrt.

    ``values`` bildet ``attname`` auf den Wert ab. Ein Fremdschluessel, zu dem
    ``values`` nichts sagt, bekommt beim Insert seinen Modell-Default — genau
    den nimmt die Ableitung hier auch. Leere Werte sperren nichts; doppelte
    Ziele einmal. Sortiert nach Tabelle, dann PK.
    """
    targets: set[LockTarget] = set()
    for field in _fk_fields(model):
        value = values[field.attname] if field.attname in values else field.get_default()
        if value is None:
            continue
        # ``target_field`` ist die referenzierte Spalte selbst (der PK des
        # Ziels) — genau die Zeile, die die FK-Pruefung sperrt.
        target = field.target_field
        targets.add((target.model._meta.db_table, cast(str, target.column), value))
    return sorted(targets, key=lambda entry: (entry[0], str(entry[2])))


def lock_fk_targets(targets: Iterable[LockTarget]) -> None:
    """Nimmt ``FOR KEY SHARE`` auf die Ziele — in der uebergebenen Reihenfolge."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        for table, column, value in targets:
            # Tabellen- und Spaltenname stammen aus dem Modell-Meta, nicht aus
            # Eingaben; der Wert bleibt Parameter.
            cur.execute(f'SELECT "{column}" FROM "{table}" WHERE "{column}" = %s FOR KEY SHARE', [value])  # noqa: S608
            cur.fetchall()
