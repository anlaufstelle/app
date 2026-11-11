# Facility export — schema reference (v2)

Versioned JSON export of **one** facility (GDPR Article 20, data portability; #1060). The [`export_facility` command](../../src/core/management/commands/export_facility.py) calls [`build_facility_export`](../../src/core/services/system/facility_export.py). See [the operations runbook, section 3.5](ops-runbook.md) for the database administrator role and handling of the plaintext file.

## Usage

```bash
python manage.py export_facility --facility <uuid-or-name> --output <path> [--indent N] [--no-file-hashes]
```

- `--facility` accepts a UUID or an unambiguous facility name; unknown or ambiguous names raise `CommandError`.
- The command fails explicitly unless its database connection bypasses RLS. An administrator database role is required to avoid incomplete exports.
- Each run writes an audit entry (`Action.EXPORT`, `target_type="FacilityExport"`) containing entity counts without field values.

## Versioning

`schema_version` is an integer incremented for **every incompatible change**: removal or renaming of a field, changed meaning or a moved structure. Additive fields do not increment the version; consumers must ignore unknown keys. The field list below is the reference, updated together with the corresponding issue.

Person notes and case, episode and task descriptions are stored exclusively in typed `data_json` with `document_type_id`; `clients[].notes` and separate `description` keys for cases, episodes and tasks are not schema fields. See the [operator actions in the changelog](../../CHANGELOG.md#unreleased) for standard types and backups.

## Principles

- **Plaintext is intentional** in the Article 20 export: encrypted `data_json` values and attachment filenames are decrypted. Handle the file like a database backup. Unreadable individual values use the `safe_decrypt` placeholder; missing encryption keys abort the export.
- **The person determines the trash boundary:** people with `is_deleted=True` and all their child records (cases, events, episodes, goals, work items and attachments) are excluded. Events also follow their own soft deletion and retention filters.
- **Cases form a second trash boundary** (#1818, [ADR-044](../adr/044-fall-loeschpfad.md)): deleted cases, their episodes, goals and milestones are excluded. Their events remain because they belong to the person; `case_id` and `episode_id` are `null` when those targets are absent.
- **Anonymized people remain included and marked:** both k-anonymized (`k_anonymized=true`) and Article 17 anonymized people (`is_anonymized=true`, derived from `is_active=false`) are exported along with their redacted records. Excluding them would create dangling references.
- **References:** foreign keys are UUID strings named `<name>_id`. Person, case and episode references resolve within the export, or are `null` where the underlying foreign key is nullable.
- **Configuration remains unfiltered:** `is_active` on document types and field templates is exported as metadata rather than used as an export filter.
- Datetimes use ISO 8601 UTC. The export uses ordinary JSON values without a custom encoder.

## Schema v2 — fields

```text
schema_version: 2
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
  clients[]:   {id, pseudonym, contact_stage, age_cluster, document_type_id,
                data_json, is_active, k_anonymized, is_anonymized, created_at,
                updated_at, created_by_id, attachments[]}
  events[]:    {id, client_id, case_id, episode_id, document_type_id, occurred_at,
                data_json, is_anonymous, created_by_id, created_at, updated_at, attachments[]}
  cases[]:     {id, client_id, document_type_id, title, status, data_json,
                lead_user_id, created_by_id, created_at, updated_at, closed_at, attachments[]}
  episodes[]:  {id, case_id, title, document_type_id, data_json, started_at, ended_at, created_by_id, created_at}
  goals[]:     {id, case_id, title, description, is_achieved, achieved_at, created_by_id,
                created_at, milestones[]: {id, title, is_completed, completed_at, sort_order, created_at}}
  work_items[]:{id, client_id, created_by_id, assigned_to_id, item_type, status,
                priority, title, document_type_id, data_json, created_at, updated_at, completed_at,
                due_date, remind_at, recurrence}

attachments[] (embedded under client/event/case):
  {id, owner, original_filename, size, content_type, created_at, sha256}
```

## Attachments

Only metadata is exported, without file contents. `owner` identifies `client`, `event` or `case`; `original_filename` is decrypted. `sha256` is calculated by streaming the **decrypted** file:

- A missing, corrupt or truncated file yields `sha256: null`; the export continues and logs the attachment identifier.
- `--no-file-hashes` sets `sha256: null` for every attachment. The key remains present in both modes.
- Missing encryption keys (`EncryptionKeyMissing`) abort the export rather than producing a masked null value.

User references (`created_by_id`, `lead_user_id`, `assigned_to_id`) are UUID strings without a separate `users` section. Employee master data is outside this facility export.

<!-- translation-source: docs/facility-export-schema.md -->
<!-- translation-version: v0.99.10 -->
<!-- source-hash: d4111994967cda932cd756ef231ccf4315b32667 -->
<!-- translation-date: 2026-10-26 -->
