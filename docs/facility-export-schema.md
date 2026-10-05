# Facility-Export — Schema-Referenz (v1)

Versionierter JSON-Gesamtexport **einer** Einrichtung (DSGVO Art. 20 Datenportabilität; Milestone M4, Refs #1060). Erzeugt durch [`manage.py export_facility`](../src/core/management/commands/export_facility.py) über den Service [`build_facility_export`](../src/core/services/system/facility_export.py). Betriebshinweise (Admin-DB-Rolle, Umgang mit der Klartext-Datei): [ops-runbook.md § 3.5](ops-runbook.md#35-manuelle-ausfuehrung).

## Aufruf

```bash
python manage.py export_facility --facility <uuid-oder-name> --output <pfad> [--indent N] [--no-file-hashes]
```

- `--facility` akzeptiert die Facility-UUID oder den eindeutigen Namen; unbekannt/mehrdeutig → `CommandError`.
- Der Command bricht **fail-loud** ab, wenn die DB-Verbindung keinen RLS-Bypass hat (Admin-Rolle nötig) — sonst entstünden stillschweigend leere Listen.
- Jeder Lauf schreibt einen AuditLog-Eintrag (`Action.EXPORT`, `target_type="FacilityExport"`, Detail = Zähler je Entity-Typ, keine Werte).

## Versionierungsregel

`schema_version` ist eine ganze Zahl und wird bei **jeder rückwärts-inkompatiblen** Änderung erhöht (Feld entfernt/umbenannt, Bedeutung geändert, Struktur verschoben). Rein **additive** Felder erhöhen die Version nicht — Konsumenten müssen unbekannte Schlüssel ignorieren. Die jeweils gültige Feldliste dieses Dokuments ist die Referenz; Änderungen laufen über ein Issue + Aktualisierung hier.

## Grundsätze

- **Entschlüsselter Klartext ist gewollt** (Art.-20-Pfad): feldverschlüsselte Werte (`data_json`, Freitexte, Anhang-Dateinamen) erscheinen entschlüsselt. Die Datei ist wie ein DB-Backup zu behandeln. Nicht entschlüsselbare Einzelwerte erscheinen als Platzhaltertext (`safe_decrypt`-Konvention); eine fehlende Schlüsselkonfiguration bricht den Export ab.
- **Papierkorb-Grenze verläuft über die Person:** Personen mit `is_deleted=True` fehlen samt **allen** Kind-Daten (Fälle, Ereignisse, Episoden, Ziele, Arbeitsaufträge, Anhänge). Events tragen zusätzlich einen eigenen Löschpfad (`soft_delete_event`, Retention-Purge) und werden auch darüber gefiltert.
- **Zweite Papierkorb-Grenze: der Fall** (Refs #1818, [ADR-044](adr/044-fall-loeschpfad.md)): ein Fall mit `is_deleted=True` fehlt samt seinen **Episoden und Wirkungszielen** (inkl. Meilensteinen). Seine **Ereignisse bleiben** — sie hängen an der Person, nicht am Fall (ADR-044 § 5) —, tragen aber `case_id: null` und `episode_id: null`, weil beide Ziele im Export fehlen; ein Verweis darauf wäre ein Dangling-FK.
- **Anonymisierte Personen bleiben drin:** k-anonymisierte (`k_anonymized=true`) und Art.-17-anonymisierte Personen (`is_anonymized=true`, abgeleitet aus `is_active=false`) werden **eingeschlossen und markiert** — ihre (bereits redigierten) Fälle/Ereignisse ebenfalls. Ausschluss ergäbe Dangling-FKs ohne Datenschutz-Gewinn.
- **Referenzielle Integrität:** alle FK-Referenzen sind UUID-Strings (`<name>_id`); jede `client_id`/`case_id`/`episode_id` ist innerhalb der Datei auflösbar (bzw. `null` bei den nullable FKs `event.client_id`, `event.case_id`, `event.episode_id`, `work_item.client_id`).
- **Konfiguration unfiltriert:** `document_types`/`field_templates` sind nicht soft-löschbar; `is_active` ist dort ein UI-Schalter und wird als Information exportiert, nicht als Filter verwendet.
- Datetimes ISO-8601 (UTC), reine JSON-Typen, kein Custom-Encoder nötig.

## Schema v1 — Feldliste

```text
schema_version: 1
exported_at: ISO-8601 UTC
facility: {id, name, organization: {id, name}}
configuration:
  document_types[]: {id, name, slug, entity_type, sensitivity, min_contact_stage,
                     system_type, tally_enabled, report_key, retention_days, icon, color, description,
                     is_active, sort_order,
                     fields[]: {id, field_template_id, field_group_id, sort_order}}
  field_templates[]: {id, name, slug, field_type, min_value, max_value, is_required, is_encrypted,
                     sensitivity, options_json, default_value,
                     report_key, help_text, is_active}
  field_groups[]:    {id, document_type_id, name, slug, sort_order, is_collapsible}
  field_conditions[]:{id, target_field_id, depends_on_field_id, operator, value, group_index}
entities:
  clients[]:   {id, pseudonym, contact_stage, age_cluster, notes, document_type_id,
                data_json, is_active, k_anonymized, is_anonymized, created_at,
                updated_at, created_by_id, attachments[]}
  events[]:    {id, client_id, case_id, episode_id, document_type_id, occurred_at,
                data_json, is_anonymous, created_by_id, created_at, updated_at, attachments[]}
  cases[]:     {id, client_id, document_type_id, title, description, status, data_json,
                lead_user_id, created_by_id, created_at, updated_at, closed_at, attachments[]}
  episodes[]:  {id, case_id, title, description, started_at, ended_at, created_by_id, created_at}
  goals[]:     {id, case_id, title, description, is_achieved, achieved_at, created_by_id,
                created_at, milestones[]: {id, title, is_completed, completed_at, sort_order, created_at}}
  work_items[]:{id, client_id, created_by_id, assigned_to_id, item_type, status,
                priority, title, description, created_at, updated_at, completed_at,
                due_date, remind_at, recurrence}

attachments[] (eingebettet unter client/event/case):
  {id, owner, original_filename, size, content_type, created_at, sha256}
```

## Anhänge

Nur **Metadaten**, nie Binärinhalte. `owner` benennt den Owner-Typ (`client`/`event`/`case`). `original_filename` erscheint entschlüsselt. `sha256` ist der Hash des **entschlüsselten** Inhalts, on-the-fly gestreamt:

- Datei fehlt oder ist korrupt/abgeschnitten → `sha256: null` (der Export bricht nicht ab; Warnung im Log mit `attachment_id`).
- `--no-file-hashes` → `sha256: null` bei allen Anhängen (deutlich schneller bei großen Beständen). Der Schlüssel ist in beiden Fällen **immer vorhanden** — ein Vertrag, nicht zwei.
- Fehlende Schlüsselkonfiguration (`EncryptionKeyMissing`) bricht den Export ab (fail-loud, keine Maskierung als `null`).

User-Referenzen (`created_by_id`, `lead_user_id`, `assigned_to_id`) sind UUID-Strings ohne eigenen `users`-Zweig — bewusste v1-Entscheidung (Personal-Stammdaten sind nicht Teil des fachlichen Facility-Datenbestands).
