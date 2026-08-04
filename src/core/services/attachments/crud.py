"""Schreibpfade der Anhang-Domaene — owner-generisch (M24/AP2, Refs #1738).

Umgezogen aus :mod:`core.services.events.crud`: die Attachment-Mutationen
waren nie event-spezifisch, sie brauchten nur einen Owner mit
``data_json``-Feld und dem Reverse-Accessor ``attachments``. Seit ADR-039
sind das Ereignis, Person und Fall.

``events/crud.py`` behaelt duenne, delegierende Aliasse (``finalize_staged_files``,
``apply_attachment_changes``) — Bestands-Importe (``core.services.events``) und
der Event-View-Pfad bleiben dadurch unveraendert gueltig.

**Patch-Vertrag:** ``commit_staged_upload``/``store_encrypted_file``/
``soft_delete_attachment_chain`` werden weiterhin **funktionslokal aus dem
Paket** ``core.services.file_vault`` geholt (nicht aus dem Submodul). Tests
patchen genau diesen Paket-Alias (z. B.
``patch("core.services.file_vault.commit_staged_upload")``); ein Modul-Level-
Import oder ein Submodul-Import wuerde den Mock zur Aufrufzeit umgehen.
"""

from __future__ import annotations

from core.models import FieldTemplate
from core.services.dynamic_data import build_field_template_lookup, is_file_marker, normalize_file_marker


def finalize_staged_files(owner, user, staged_by_slug, document_type):
    """Persistiere vor-verschluesselte Uploads an einen frisch erzeugten Owner.

    Refs #1345: Der teure ClamAV-Scan + Fernet-Encrypt laeuft bereits VOR der
    Transaktion (``prepare_encrypted_upload``); hier wird pro Slug nur noch der
    schnelle DB-Insert (``commit_staged_upload``) plus der Stufe-B-Marker
    (``__files__``) geschrieben. Dadurch haelt der AuditLog-Hashchain-Advisory-
    Lock (aus dem Owner-Create) nicht mehr ueber den Scan. Der Aufrufer ist fuer
    den umliegenden ``transaction.atomic``-Block zustaendig (Refs #584).

    ``staged_by_slug`` ist ein ``{slug: [StagedUpload, ...]}``-Dict in
    Upload-Reihenfolge (siehe ``EventCreateView.post``). ``owner`` ist ein
    ``Event``, ``Client`` oder ``Case`` (Refs #1738).
    """
    from core.services.file_vault import commit_staged_upload

    if not staged_by_slug:
        return
    field_templates = build_field_template_lookup(document_type)
    for slug, staged_list in staged_by_slug.items():
        ft = field_templates.get(slug)
        if not ft or not staged_list:
            continue
        entries = []
        for idx, staged in enumerate(staged_list):
            attachment = commit_staged_upload(owner, ft, staged, user, sort_order=idx)
            entries.append({"id": str(attachment.pk), "sort": idx})
        owner.data_json[slug] = {"__files__": True, "entries": entries}
    owner.save(update_fields=["data_json"])


def _apply_remove(slug, current_entries, attachments_by_pk, request_post, owner, user):
    """REMOVE-Schritt: hidden CSV ``<slug>__remove`` listet zu loeschende
    ``entry_id``-Werte -> ``soft_delete_attachment_chain``. Liefert die um die
    geloeschten Entries bereinigte Entry-Liste."""
    from core.services.file_vault import soft_delete_attachment_chain

    remove_raw = request_post.get(f"{slug}__remove", "")
    remove_ids = {x.strip() for x in remove_raw.split(",") if x.strip()}
    if not remove_ids:
        return current_entries
    filtered = []
    for entry in current_entries:
        att = attachments_by_pk.get(str(entry["id"]))
        if att and str(att.entry_id) in remove_ids:
            soft_delete_attachment_chain(owner, att.entry_id, user)
            continue
        filtered.append(entry)
    return filtered


def _apply_replace(slug, current_entries, attachments_by_pk, request_files, facility, ft, owner, user):
    """REPLACE-Schritt: pro bestehendem Entry ein File-Input
    ``<slug>__replace__<entry_id>`` -> ``store_encrypted_file(supersedes=...)``.
    Liefert die (ggf. ersetzte) Entry-Liste."""
    from core.services.file_vault import store_encrypted_file

    updated_entries = []
    for entry in current_entries:
        att = attachments_by_pk.get(str(entry["id"]))
        if not att:
            continue
        replace_file = request_files.get(f"{slug}__replace__{att.entry_id}")
        if replace_file is not None:
            new_att = store_encrypted_file(facility, replace_file, ft, owner, user, supersedes=att)
            updated_entries.append({"id": str(new_att.pk), "sort": att.sort_order})
        else:
            updated_entries.append(entry)
    return updated_entries


def _apply_add(slug, updated_entries, file_fields, facility, ft, owner, user):
    """ADD-Schritt: alle Files aus dem Multi-Upload-Feld werden mit frischen
    ``entry_id`` angehaengt. Liefert die um die neuen Entries erweiterte Liste."""
    from core.services.file_vault import store_encrypted_file

    add_files = file_fields.get(slug) or []
    base_sort = (max((e.get("sort", 0) for e in updated_entries), default=-1)) + 1
    for idx, uploaded_file in enumerate(add_files):
        new_att = store_encrypted_file(facility, uploaded_file, ft, owner, user, sort_order=base_sort + idx)
        updated_entries.append({"id": str(new_att.pk), "sort": base_sort + idx})
    return updated_entries


def _write_file_marker(owner, slug, updated_entries):
    """Schreibt bzw. entfernt den Stufe-B-Marker im ``owner.data_json``."""
    if updated_entries:
        owner.data_json[slug] = {"__files__": True, "entries": updated_entries}
    elif slug in owner.data_json:
        del owner.data_json[slug]


def apply_attachment_changes(owner, user, request_post, request_files, file_fields, document_type):
    """Applizieren von REMOVE/REPLACE/ADD auf die FILE-Felder eines Owners.

    - **REMOVE**: hidden CSV ``<slug>__remove`` listet zu loeschende
      ``entry_id``-Werte -> ``soft_delete_attachment_chain``.
    - **REPLACE**: pro bestehendem Entry ein File-Input
      ``<slug>__replace__<entry_id>`` -> ``store_encrypted_file(supersedes=...)``.
    - **ADD**: alle Files aus dem Multi-Upload-Feld werden mit frischen
      ``entry_id`` angehaengt.

    Aktualisiert anschliessend den Marker im ``owner.data_json``. ``owner`` ist
    ein ``Event``, ``Client`` oder ``Case`` (Refs #1738).
    """
    from core.services.compliance import user_can_see_field

    field_templates = build_field_template_lookup(document_type)
    # ADR-039 §2: die Facility kommt aus dem Owner, nie aus dem Request.
    facility = owner.facility
    doc_sensitivity = document_type.sensitivity
    for slug, ft in field_templates.items():
        if ft.field_type != FieldTemplate.FieldType.FILE:
            continue

        # R11 (Refs #1476/#1666): Sensitivity-Gate auch auf Anhang-Mutation.
        # Der Update-Pfad entfernt Text-Felder oberhalb der Rollen-Sicht
        # per ``remove_restricted_fields`` aus dem Form; die Anhang-Mutation
        # liest REMOVE/REPLACE/ADD aber direkt aus ``request.POST``/``FILES``
        # und umging dieses Gate. Ein User, der den Owner sieht (Doc-Type-Gate),
        # aber dieses FILE-Feld wegen eines feldbezogenen Sensitivity-Overrides
        # NICHT sehen darf, darf dessen Anhaenge nicht loeschen/ersetzen/
        # ergaenzen — der Marker bleibt dann unveraendert. (``user is None`` =
        # System-/Service-Kontext ohne Rolle → kein Gate.)
        if user is not None and not user_can_see_field(user, doc_sensitivity, ft.sensitivity):
            continue

        existing_marker = (owner.data_json or {}).get(slug)
        current_entries = normalize_file_marker(existing_marker)

        # Refs #782 (C-17): Bulk-Load aller Attachments des aktuellen Slugs
        # in einer Query — vorher lud der REMOVE/REPLACE-Pfad jeden Entry
        # einzeln (``filter(pk=entry["id"]).first()``) und produzierte N+1.
        entry_ids = [entry["id"] for entry in current_entries]
        attachments_by_pk = (
            {str(att.pk): att for att in owner.attachments.filter(pk__in=entry_ids)} if entry_ids else {}
        )

        current_entries = _apply_remove(slug, current_entries, attachments_by_pk, request_post, owner, user)
        updated_entries = _apply_replace(
            slug, current_entries, attachments_by_pk, request_files, facility, ft, owner, user
        )
        updated_entries = _apply_add(slug, updated_entries, file_fields, facility, ft, owner, user)

        _write_file_marker(owner, slug, updated_entries)

    owner.save(update_fields=["data_json"])


def preserve_file_markers(owner, merged_data, previous_data, file_field_slugs):
    """Bestehende FILE-Marker in eine frisch gemergte ``data_json`` zurueckholen.

    Owner-generisches Pendant zum FILE-Zweig von
    :func:`core.services.events.crud.merge_update_payload` (Refs #1160).

    Warum das noetig ist: der Update-Pfad baut ``data_json`` komplett aus dem
    **Formular** des aktuellen Typs neu auf (E11-Merge-Policy,
    :func:`~core.services.dynamic_data.merge_dynamic_data`). Ein FILE-Feld
    traegt aber gar keinen Formularwert — sein Wert IST der Marker auf die
    ``Attachment``-Zeilen. Ohne diese Re-Injektion wuerde bereits der erste
    reine Text-Edit still **alle** Bestandsanhaenge des Owners aus dem
    ``data_json`` werfen (die Zeilen blieben als UI-unsichtbare Waisen liegen).
    :func:`apply_attachment_changes` passt den Marker danach an.

    Mutiert ``merged_data`` in-place und gibt es zurueck (Signatur-Muster wie
    ``merge_update_payload``).

    :param owner: Ereignis/Person/Fall — dokumentiert die Zugehoerigkeit; der
        Helfer selbst liest den Owner nicht (die Marker liegen in
        ``previous_data``). Bewusst im Signatur-Kopf, damit Aufrufstellen nicht
        versehentlich die ``data_json`` einer anderen Entitaet mergen.
    :param merged_data: Ergebnis des Form-Merges (wird ergaenzt).
    :param previous_data: bisheriges ``data_json`` des Owners.
    :param file_field_slugs: Slugs der FILE-Felder des **aktuellen** Typs. Nur
        sie werden nachgezogen — bei einem Typwechsel gehoert ein Alt-Slug nicht
        mehr zum Feldset und darf nicht wieder auftauchen.
    """
    previous = previous_data or {}
    for slug in file_field_slugs:
        marker = previous.get(slug)
        # Dieselbe Marker-Erkennung wie ``validate_data_json``: was dort
        # durchfaellt, wuerde ohnehin verworfen — kein Grund, es einzusetzen.
        if is_file_marker(marker):
            merged_data[slug] = marker
    return merged_data


def discard_type_switch_file_chains(owner, user):
    """Alle Versionsketten eines Owners soft-loeschen (Typwechsel, ADR-039 §7).

    Wechselt der Dokumentationstyp einer Person/eines Falls, verwirft die
    E11-Merge-Policy das komplette ``data_json`` des alten Typs — samt der
    FILE-Marker. Die ``Attachment``-Zeilen blieben ohne diesen Aufruf als
    UI-unsichtbare Waisen zurueck (weder in der Datei-Uebersicht noch in der
    Auskunft auffindbar, aber weiterhin auf Disk).

    Bewusst **soft-delete** (``deleted_at``), nicht physisch: die Ketten sollen
    fuer Auskunft/Forensik nachvollziehbar bleiben und faellig erst mit dem
    Owner-Lebenszyklus (Klienten-Anonymisierung). Damit ist dieser Pfad **kein**
    physischer Loeschpfad — Invariante I-M1 (Marker-Bereinigung im selben
    ``atomic``) ist nicht beruehrt; der Typwechsel verwirft das ``data_json``
    ohnehin.

    Idempotent: bereits soft-geloeschte Ketten werden nicht erneut angefasst.

    Returns: Anzahl der betroffenen **Ketten** (nicht der Einzelversionen) —
    genau die Zahl, die der Bestaetigungsdialog nennt (Refs #1733).
    """
    from core.services.file_vault import soft_delete_attachment_chain

    entry_ids = list(
        owner.attachments.filter(deleted_at__isnull=True)
        # ``Attachment.Meta.ordering`` sortiert nach ``-created_at``; ohne
        # ``order_by()`` zoege PostgreSQL diese Spalte ins DISTINCT und die
        # Ketten kaemen mehrfach zurueck (falsche Zaehlung).
        .order_by()
        .values_list("entry_id", flat=True)
        .distinct()
    )
    for entry_id in entry_ids:
        soft_delete_attachment_chain(owner, entry_id, user)
    return len(entry_ids)
