# Data Protection / GDPR — Guide

> **A guide, not a content silo.** This page links, per GDPR topic, to the authoritative source (ADR, template, code, FAQ) — it does not re-explain the mechanics. **In case of conflict, the code** (or the ADR/template) **wins**, not this overview. ADRs and templates are German-language; see the [English docs index](README.md).
>
> **Maintenance:** When a new data-protection-relevant ADR is added, add a line here.

**Audience tags:** **[Auditor]** proof/decision · **[Dev]** code/architecture · **[Operator]** operation/usage. Tags appear only where they genuinely apply.

## A. Principles of processing (Art. 5 GDPR)

| Principle | Reference | For |
|---|---|---|
| Art. 5(1)(b) Purpose limitation — search scope | [ADR-043](../adr/043-suchraum-volltextsuche.md) — search queries are deliberately **not** logged (rationale + residual risk), the case frame stays deliberately open to every staff member; five rules for any extension of the search scope | [Auditor] [Dev] |
| Art. 5(1)(c) Data minimization | [ADR-023](../adr/023-k-anonymization-statistik.md) · [`external_report.py`](../../src/core/services/dashboard/external_report.py) | [Auditor] [Dev] |
| Art. 5(1)(e) Storage limitation | [ADR-021](../adr/021-retention-modell.md) · **Attachments:** `DocumentType.retention_days` is **event-only** — client- and case-owned files have no retention period of their own, they follow the owner's lifecycle ([ADR-039](../adr/039-owner-generische-dateianhaenge.md)) | [Auditor] [Dev] [Operator] |
| Art. 5(1)(e) Storage limitation — handover note | **Manual shift note** (#2042): its own, deliberately short period `Settings.retention_handover_notes_days` (default 90 days), **physical** deletion by `enforce_retention` ([`retention/enforcement.py`](../../src/core/retention/enforcement.py)) — no trash, no legal hold. See the "Handover note" section below | [Auditor] [Dev] [Operator] |
| Art. 5(1)(f) Integrity & confidentiality | [ADR-006](../adr/006-fernet-field-encryption.md) · [ADR-007](../adr/007-auditlog-append-only.md) · [ADR-014](../adr/014-encrypted-file-vault.md) · (DE: [security-notes.md](../security-notes.md)) | [Auditor] [Dev] |
| Art. 5(2) Accountability | [ADR-007](../adr/007-auditlog-append-only.md) · [Processing register](../../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) · **what the audit log does not record** (ordinary list, detail and search requests): [ADR-043](../adr/043-suchraum-volltextsuche.md), [Admin Guide § 7.4](admin-guide.md#74-audit-log) | [Auditor] |

## B. Data subject rights (Art. 12–22 GDPR)

| Right | Reference | For |
|---|---|---|
| Art. 13/14 Information duties | [Information duties](../../src/core/dsgvo_templates/informationspflichten.md) | [Operator] [Auditor] |
| Art. 15 Access | Per-person data export JSON/PDF ([`ClientDataExportJSONView`/`ClientDataExportPDFView`](../../src/core/views/clients.py)) · **authoritative for scope and the deliberate exclusions: [Admin Guide § 7.6](admin-guide.md#scope-of-the-access-record-art-15)** · it also contains (#1738) **attachment metadata** for all three owners (filename, size, type, version chain; **no** binary contents) and (#1796) also **rectification requests** (Art. 16, [ADR-041](../adr/041-berichtigungsworkflow-intern.md)) in all states · [FAQ §C](../faq.md#c-rollen--datenschutz) ([English offline excerpt](faq.md)) | [Operator] [Dev] |
| Art. 16 Rectification | [ADR-013](../adr/013-dsgvo-art16-no-selfservice.md) — deliberately no self-service | [Auditor] [Dev] |
| Art. 17 Erasure | [ADR-021](../adr/021-retention-modell.md) (soft-delete strategies, actual scope of the retention periods, k-anon retention incl. free-text cascade #1094) · [`core/retention/`](../../src/core/retention/) · **Attachments:** **four** physical deletion paths — (1) owner lifecycle: client- and case-owned files fall with the client anonymization (trash period `client_trash_days`), including the `.enc` file on **both** anonymization paths (`anonymize_client` and the k-anon bridge, parity guard); (2) purge of an individually deleted case (`anonymize_case`): case-owned files fall once the case is purged from the trash — immediately by the facility administration or after `client_trash_days`; (3) event deletion/retention (`delete_attachments_for`); (4) #1874: the **user-triggered permanent deletion** of a single version chain by managers/administration (`purge_attachment_chain`: rows + markers + `.enc`, blocked by an active legal hold or an Art. 18 restriction on the person) — the only path that removes a single mistakenly uploaded file at once, without taking the whole record with it. [ADR-039](../adr/039-owner-generische-dateianhaenge.md) · **residual risks:** the append-only audit log is not redacted — (DE: [security-notes.md § Audit-Log-Restdaten](../security-notes.md#audit-log-restdaten-überleben-die-art-17-löschung-issue-1753)); media backups survive the permanent deletion — (DE: [security-notes.md § Medien-Backups](../security-notes.md#medien-backups-überleben-das-ersatzlose-löschen-einer-datei-issue-1874)) | [Auditor] [Dev] [Operator] |
| Art. 18 Restriction | **Restriction marker on the client record** (#2045): flag `processing_restricted` + legal ground (Art. 18(1)(a)–(d)) + set by/at · write gate in [`services/client/restriction.py`](../../src/core/services/client/restriction.py), mirroring `resolve_writable_client` — storage remains permitted (Art. 18(2)), substantive use or modification is blocked, **except** the rectification workflow ([ADR-041](../adr/041-berichtigungsworkflow-intern.md)) · lifting requires a second authorised person or a manager and is audited (`PROCESSING_RESTRICTED`/`PROCESSING_RESTRICTION_LIFTED`) · **Not to be confused with the legal hold** ([`legal_holds.py`](../../src/core/retention/legal_holds.py)): that one blocks *erasure* (Art. 17(3)), this marker blocks *use*. The duty to inform before lifting (Art. 18(3)) remains organisational. | [Auditor] [Dev] [Operator] |
| Art. 20 Portability | via the machine-readable per-person JSON export — **same data basis as Art. 15** (same export, same exclusions, [Admin Guide § 7.6](admin-guide.md#scope-of-the-access-record-art-15)); no dedicated portability workflow | [Operator] |

## C. Technical & organizational measures (Art. 25, 32 GDPR)

| Measure | Reference | For |
|---|---|---|
| Art. 25 Privacy by design/default | [ADR-005](../adr/005-facility-scoping-and-rls.md) — facility scoping + RLS | [Auditor] [Dev] |
| Art. 25 Data minimization on upload | **Metadata stripping for image attachments** (#2128): `strip_image_metadata` in [`services/file_vault/policy.py`](../../src/core/services/file_vault/policy.py) re-encodes JPEG/PNG/WebP/TIFF before encryption and removes EXIF (incl. the **GPS capture location**, device identifiers, capture time, embedded thumbnail), PNG text chunks and XMP/IPTC — orientation is preserved as a pixel rotation, the ICC profile is kept. What counts is the type detected via magic bytes, not the extension; content detected as an image under a non-image extension is rejected fail-closed (`mime_mismatch`), as is an undecodable image (`image_undecodable`). Always on, no toggle. **Limits:** (1) PDF/OOXML document metadata (`/Author`, `dc:creator` — staff real names) is **not** cleaned; (2) GIF and BMP stay byte-identical, so GIF89a comment/XMP extensions and BMP v5 ICC survive (a re-encode would change palette and animation; recommendation: do not enable `gif`/`bmp`). | [Auditor] [Dev] [Operator] |
| Art. 32 Encryption at rest | [ADR-006](../adr/006-fernet-field-encryption.md) (fields) · [ADR-014](../adr/014-encrypted-file-vault.md) (file vault, Fernet/AES-128) — **identical per owner**: files on events, clients and cases run through the same pipeline (virus scan → encryption → storage path), [ADR-039](../adr/039-owner-generische-dateianhaenge.md) | [Auditor] [Dev] |
| Art. 32 Access control | [ADR-005](../adr/005-facility-scoping-and-rls.md) (RLS) · [ADR-018](../adr/018-rollenmodell-superadmin.md)/[ADR-020](../adr/020-three-role-postgres-model.md) (roles) · [ADR-015](../adr/015-mfa-totp.md) (MFA) | [Auditor] [Dev] |
| Art. 32 Integrity/traceability | [ADR-007](../adr/007-auditlog-append-only.md) — append-only audit log | [Auditor] [Dev] |
| Art. 32 Offline data | [ADR-022](../adr/022-offline-snapshot-keys.md) — offline snapshot & keys, encrypted IndexedDB; [DPIA/TOM classification of the offline path](../adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |
| Art. 32 Shoulder-surfing protection | **Quick exit + privacy screen** (#2136): a "Quick exit" button on every signed-in page plus `Ctrl`+`Shift`+`X` / `Shift`+`Esc` -- covers the content, ends the session server-side (`POST /logout/` via beacon, `Clear-Site-Data`) and navigates to the facility-wide neutral address (`Settings.quick_exit_url`); a back navigation restored from the bfcache covers and reloads. Opt-in privacy screen on focus/tab change (`Settings.privacy_screen_on_blur`), released by click or key press only. Offline the same path works without a server sign-out: the session key is discarded, backed by the 48-hour revocation and the boot wipe ([ADR-022](../adr/022-offline-snapshot-keys.md)). Both fields are audited (`SETTINGS_CHANGE`); the trigger itself deliberately is **not** (the purpose is protecting the person, not accountability for their behaviour). **Named residual risks** (second opinion on #2136, in full in (DE: [threat-model.md](../threat-model.md))): earlier history entries of the same session remain in the browser's history with path **and page title** (client pages carry the pseudonym in the title) — `location.replace` only replaces the last entry, and browser history cannot be cleared from JavaScript; a second open tab keeps its already rendered content until its next navigation (its local key is only locked along with it when the deletion in the triggering tab is proven to have succeeded); the deletion of the persistent offline key is awaited before leaving, but capped (500 ms) — if it does not succeed, the key remains until the next sign-out, the idle wipe (#1324) or the expiry of the 48-hour offline authorization; if the logout beacon is lost to a dropped connection, the server session only ends via the time limits from #1858/#1868 | [Auditor] [Operator] [Dev] |

## D. Processing agreements & evidence documents (Art. 28, 30, 35 GDPR)

| Document | Reference | For |
|---|---|---|
| Art. 28 Data processing agreement | [Data processing agreement](../../src/core/dsgvo_templates/av-vertrag.md) | [Operator] |
| Art. 30 Record of processing activities | [Processing register](../../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Operator] [Auditor] |
| Art. 35 Data protection impact assessment | [DPIA](../../src/core/dsgvo_templates/dsfa.md) | [Operator] [Auditor] |
| Art. 5(1)(e) / Art. 17 retention and deletion concept | [Deletion concept](../../src/core/dsgvo_templates/loeschkonzept.md) | [Operator] [Auditor] |
| Arts. 13–14 / § 26 BDSG employee information | [Employee information](../../src/core/dsgvo_templates/mitarbeiter_informationspflichten.md) | [Operator] |
| TOMs (overview) | [TOMs](../../src/core/dsgvo_templates/toms.md) | [Operator] [Auditor] |
| App feature "GDPR package" (bundle download) | [`DSGVOPackageView`](../../src/core/views/dsgvo.py) · [`generate_dsgvo_package`](../../src/core/management/commands/generate_dsgvo_package.py) | [Operator] [Dev] |

## D2. Public demo instance — web analytics & session recording (Refs #1604)

> Applies **exclusively** to the public demo `demo.anlaufstelle.app`, **not** to the self-hosted product. Self-hosted operator instances run no Umami/tracking at all.

| Aspect | Rule | For |
|---|---|---|
| Legal basis | Art. 6(1)(a) — **opt-in consent**: tracking/recording/heatmap only start technically **after** consent is given (no silent tracking); declining is possible | [Auditor] [Operator] |
| Scope | Umami web analytics **including session recording + heatmap** (self-hosted; endpoint configured via deployment env, #1701, and named in the demo's privacy policy); **only after login**, **not** on the sign-in page | [Auditor] [Dev] |
| Data | exclusively **synthetic seed data** (hourly reset) — no real data subjects | [Auditor] |
| Legal texts | the demo's own pages `/impressum/` + `/datenschutzerklaerung/` (#1641; contents provided as the operator's deploy-time overlay, linked from the demo footer + consent banner) | [Operator] |
| Technology / CSP scoping | consent gate [`umami-consent.js`](../../src/static/js/umami-consent.js) · narrowly scoped CSP allowance [`DemoUmamiCSPRelaxMiddleware`](../../src/core/middleware/demo_umami_csp_relax.py) · (DE: [security-notes.md](../security-notes.md#eng-gescopte-ausnahme--umami-analytics-auf-der-demo-issue-1604)) · [ADR-022 § (c)](../adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## D3. Handover note (manual shift note, Refs #2042)

The handover shows one free-text field per shift -- what the outgoing worker wants the incoming one to know. The data-protection assessment is given here in full because it deliberately deviates from the rest of the data model in several places.

| Aspect | Rule | For |
|---|---|---|
| Data category | Unstructured free text written by a staff member about the course of a shift. **No client reference in the schema** -- the `ShiftHandoverNote` model carries no client or case foreign key; besides facility, shift date and time window it refers to the staff member who wrote it and the one who last changed it (`author`, `updated_by`; set to empty when the account is deleted). The text *may* name people; the user guide and the field hint therefore require the same pseudonyms as the rest of the documentation | [Auditor] [Operator] |
| Purpose | Handover between two duties (Art. 6(1)(f), or whichever legal basis the operator uses for its professional documentation) -- the note replaces no case record and is no place for diagnoses or assessments | [Operator] [Auditor] |
| Art. 32 encryption at rest | Fernet field encryption, like the reason of a rectification request ([ADR-006](../adr/006-fernet-field-encryption.md)); the column never holds plaintext, and the eighth key-rotation path (`reencrypt_fields`) covers it | [Auditor] [Dev] |
| Art. 32 access control | Reading **and** writing require staff level or above (`staff`/`lead`/`facility_admin`); assistants reach the handover but do not see the note -- not even a placeholder. Row-level security like every facility-owned table ([ADR-005](../adr/005-facility-scoping-and-rls.md)) | [Auditor] [Dev] |
| Art. 5(1)(e) storage limitation | `Settings.retention_handover_notes_days`, default **90 days** from the last change. `enforce_retention` deletes **physically** (no trash, no anonymisation step). There is **no** legal-hold target type for handover notes -- holds protect client, case and event records for ongoing proceedings; the boundary is stated in the hold contract in (DE: [security-notes.md](../security-notes.md)) | [Auditor] [Dev] [Operator] |
| Art. 5(2) accountability -- **manual** create/change/delete | Every action by a staff member writes **one audit row per note** (`HANDOVER_NOTE_CREATED`/`_UPDATED`/`_DELETED`). The `detail` carries only the shift date, the time-window label and the field list `["note"]` -- **never the text** | [Auditor] [Dev] |
| Art. 5(2) accountability -- **expiry** | The nightly retention run deliberately writes **no** per-note row but **one aggregated retention decision** per run and facility (`DELETE`, category `handover_notes`, carrying the count and the applicable period -- the `enforce_activities` pattern). Which individual note expired cannot be reconstructed from it: that is the price of data minimisation in an append-only log, not an oversight | [Auditor] [Dev] |
| Deleting a time filter | A time window that still has notes **cannot be deleted** (`on_delete=PROTECT`). That leaves no third, unaudited path on which handover knowledge disappears -- the configuration can only be tidied up once the notes' retention period has passed | [Auditor] [Operator] [Dev] |
| **Deliberate exclusion:** Art. 15 disclosure | The note is **not** part of the client data export. Without a client foreign key there is no defensible "this note is about this person" mapping; full-text searching encrypted free text in order to compile a disclosure would be a new, broader processing operation. The counterweight is the short period plus the pseudonym rule. Should a note nevertheless be requested for a disclosure in an individual case, that is an **organisational** step for the operator | [Auditor] [Operator] |
| **Deliberate exclusion:** search | The note does not feed `Event.search_text` and cannot be found through the search -- the search space stays limited to `Client.pseudonym` and `Event.search_text` ([ADR-043](../adr/043-suchraum-volltextsuche.md)) | [Auditor] [Dev] |

## E. Terms

Definitions (PII, k-anonymity, k-anon retention, pseudonymization, legal hold, storage limitation, soft-delete strategies) → [glossary.md](glossary.md).

<!-- translation-source: docs/datenschutz.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-07 -->
<!-- source-hash: 5609c903db3a77351627cd73ca0cf469ee2cca55 -->
