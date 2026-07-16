"""Feld-Diff gegen den DB-Stand für die Audit-Erfassung (Refs #1613).

Warum nicht gegen die übergebene Instanz diffen? Ein ``ModelForm(instance=…)``
mutiert seine Instanz bereits in ``_post_clean()`` (``construct_instance``) —
also **bevor** der Service-Layer den Diff bildet. Ein Vergleich
``getattr(instance, feld) != neuer_wert`` sieht dann ``neu == neu`` und meldet
nie eine Änderung: ``changed_fields`` bleibt leer, ``*_UPDATE`` wird nie
geschrieben (Threat-Model TB6, Repudiation). ``log_activity`` läuft trotzdem —
daher zeigt der Zeitstrahl die Änderung, das Audit-Log aber nicht.

Der Fix liest den **alten** Stand direkt aus der Datenbank (die Zeile ist bis
zum ``save()`` des Callers noch unverändert) und vergleicht ihn gegen die neu
zu setzenden Werte. Das ist zugleich die SSOT-Variante: sie deckt auch
Nicht-Formular-Aufrufer (z.B. Offline-Replay) ab, während ein durchgereichtes
``form.changed_data`` die Audit-Wahrheit in die View verlagern würde.
"""

from __future__ import annotations


def changed_fields_vs_db(instance, fields, *, lock=True) -> list[str]:
    """Liste der Felder aus ``fields``, die vom aktuellen DB-Stand abweichen.

    ``instance`` liefert nur Modellklasse und PK — die Vergleichswerte werden
    **frisch aus der Datenbank** gelesen, nicht von der (evtl. bereits durch
    ``_post_clean`` mutierten) Instanz. ``fields`` ist die ``{name: wert}``-Map,
    die der Caller gleich setzen und speichern wird.

    - **FK-Vergleich über ``attname``** (``assigned_to`` → ``assigned_to_id``,
      ``client`` → ``client_id``): zwingend, sonst entstehen False-Positives/
      -Negatives, wenn ``fields`` ein Modell-Objekt und die DB eine ID hält.
      Ein als Objekt übergebener FK-Wert wird auf ``.pk`` reduziert, ein roher
      PK (Offline-Replay) bleibt, wie er ist.
    - ``values(*attnames)`` statt ``.get(pk=…)`` → kein N+1, kein Nachladen
      verwandter Objekte (analog ``check_version_conflict``).
    - ``_base_manager`` statt ``objects`` → kein Custom-Manager (Facility-
      Scoping) verfälscht den Low-Level-Lookup (analog
      ``check_version_conflict``).
    - ``lock=True`` (Default) liest unter ``select_for_update()`` — analog
      ``update_workitem_status``; alle Aufrufer sind ``@transaction.atomic``,
      die Zeile bleibt so bis zum Commit gesperrt (kein TOCTOU zwischen Read
      und späterem ``save()``).

    Ist die Zeile (noch) nicht in der DB, gelten alle ``fields`` als geändert.
    """
    model = type(instance)
    # Feldname → (attname, is_relation) auflösen; der attname ist die
    # DB-Spalte, gegen die verglichen wird (bei FKs die ``*_id``-Spalte).
    resolved = {}
    for name in fields:
        field = model._meta.get_field(name)
        resolved[name] = (field.attname, field.is_relation)

    qs = model._base_manager.filter(pk=instance.pk)
    if lock:
        qs = qs.select_for_update()
    row = qs.values(*[attname for attname, _ in resolved.values()]).first()

    if row is None:
        return list(fields.keys())

    changed = []
    for name, value in fields.items():
        attname, is_relation = resolved[name]
        new_value = value.pk if is_relation and value is not None and hasattr(value, "pk") else value
        if row[attname] != new_value:
            changed.append(name)
    return changed
