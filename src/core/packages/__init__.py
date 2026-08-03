"""Teilbares Konfigurationspaket-Format (M2, Refs #1058).

Ein *Paket* beschreibt Feldvorlagen, Dokumentationstypen, Feldgruppen,
Feldzuordnungen und Feldbedingungen einer Einrichtung deklarativ — so, dass es
zwischen Instanzen geteilt und wiederholt eingespielt werden kann.

Schichtung (bewusst getrennt):

* :mod:`core.packages.schema` — reine, DB-freie Validierung eines bereits
  geparsten ``dict`` zu einem :class:`~core.packages.schema.PackageV1`
  (frozen Dataclasses). **Kein YAML** — das Parsen liegt beim Aufrufer
  (Management-Command), damit das Format auch aus JSON/API-Input befüllbar ist.
* :mod:`core.packages.loader` — idempotenter Upsert eines ``PackageV1`` gegen
  eine Einrichtung, mit Report, Savepoints je Objekt und Dry-Run.
"""
