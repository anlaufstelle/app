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


#: Sentinel für „Slug steht auf dieser Seite gar nicht". Ein Zugang bzw. Abgang
#: eines Slugs ist eine Inhaltsänderung (Feld erstmals gefüllt, Feld aus dem
#: Dokumentationstyp gefallen) und muss auditiert werden. ``None``/``""`` als
#: Ersatzwert würde ihn mit dem häufigsten echten Fall verwechseln — einem
#: geleerten Feld.
_ABWESEND: object = object()


def _vergleichswert(value):
    """Fernet-Token auf den Klartext ziehen, alles andere unverändert lassen.

    Ohne diesen Schritt gälte **jedes** verschlüsselte Feld bei jeder
    Bearbeitung als geändert: ``_encrypt_sensitive_fields`` (``models/mixins``)
    erzeugt bei jedem ``save()`` ein frisches Token für denselben Klartext, und
    der DB-Stand liegt als Token vor, während die neue Payload aus dem Formular
    im Klartext kommt.

    Ein **nicht** entschlüsselbares Token (korrupter Wert, aus der Rotation
    gefallener Schlüssel) fällt bewusst auf den Rohwert zurück statt auf einen
    gemeinsamen Platzhalter: zwei verschiedene kaputte Token wären sonst
    „gleich" und die Änderung verschwände aus dem Audit. Ein unveränderter
    Rohwert bleibt dabei weiterhin gleich (der Vergleich ist byteweise).
    """
    from core.services.file_vault import safe_decrypt

    return safe_decrypt(value, default=value)


def changed_data_json_slugs(instance, new_data_json, *, lock=True) -> list[str]:
    """Sortierte Liste der ``data_json``-Slugs, die vom DB-Stand abweichen.

    Vertrag analog :func:`changed_fields_vs_db`, nur für die dynamischen
    Formularfelder eines ``document_type``-Trägers (Ereignis/Person/Fall).

    Warum gegen die **Datenbank** und nicht gegen ``instance.data_json``? Nicht
    wegen ``ModelForm._post_clean`` — die dynamischen Formulare erben von
    ``forms.Form`` (``DynamicDataForm``) und mutieren gar keine Instanz. Der
    Grund ist derselbe wie bei ``check_version_conflict`` und
    ``update_client``: ``lock=True`` (Default) liest unter
    ``select_for_update()``, die Zeile bleibt damit bis zum Commit gesperrt und
    zwischen Diff und ``save()`` passt kein fremder Schreibvorgang (TOCTOU).
    Zugleich ist die DB die einzige Quelle, die auch für Nicht-Formular-Aufrufer
    (Offline-Replay) stimmt — die In-Memory-Instanz kann dort bereits die neue
    Payload tragen.

    - **Verschlüsselte Slugs** werden auf beiden Seiten über ``safe_decrypt``
      verglichen (siehe :func:`_vergleichswert`). Das trifft zwei Lagen: den
      DB-Stand (Token) gegen den frisch eingegebenen Klartext **und** die von
      ``merge_update_payload`` unverändert re-injizierten Token der für die
      Rolle unsichtbaren Felder.
    - **Datei-Marker** (``is_file_marker``, beide Formate) bleiben außen vor —
      steht auf einer der beiden Seiten ein Marker, wird der Slug übersprungen.
      Anhangs-Änderungen haben ihren eigenen Audit-Pfad; ein Marker-Wechsel
      würde hier sonst als Feldänderung erscheinen.
    - **Zu- und Abgänge** eines Slugs (Key nur auf einer Seite) zählen als
      Änderung. Im Ticket nicht spezifiziert, hier bewusst in die
      konservative Richtung entschieden: ein Rechenschaftsnachweis darf eher
      zu viel als zu wenig melden, und ein Abgang (Feld aus dem
      Dokumentationstyp gefallen) ist ein echter Inhaltsverlust, der sonst
      spurlos verschwände. Bekannte Folge des Gegenstücks: wird eine
      Feldvorlage nachträglich an einen Dokumentationstyp gehängt, führt die
      erste Bearbeitung jedes Alt-Ereignisses diesen Slug einmal mit, weil
      sein Key neu in ``data_json`` auftaucht.
    - Fehlt die Zeile in der DB (Offline-Replay vor dem Insert), gelten alle
      übergebenen Slugs als geändert — wie bei ``changed_fields_vs_db``.

    Zurück kommen **nur Slugs, nie Werte**: der Rückgabewert landet direkt im
    append-only ``detail`` (Datenminimierung, Refs #1944).
    """
    from core.services.dynamic_data import is_file_marker

    model = type(instance)
    qs = model._base_manager.filter(pk=instance.pk)
    if lock:
        qs = qs.select_for_update()
    row = qs.values("data_json").first()

    alt = (row or {}).get("data_json") or {}
    neu = new_data_json or {}

    changed = []
    for slug in sorted(set(alt) | set(neu)):
        alt_wert = alt.get(slug, _ABWESEND)
        neu_wert = neu.get(slug, _ABWESEND)
        if is_file_marker(alt_wert) or is_file_marker(neu_wert):
            continue
        if _vergleichswert(alt_wert) != _vergleichswert(neu_wert):
            changed.append(slug)
    return changed
