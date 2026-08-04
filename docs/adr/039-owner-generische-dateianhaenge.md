# ADR-039: Owner-generische Dateianhänge (Attachment an Ereignis, Person, Fall)

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Tobias Nix
- **Refs:** #1738 (Milestone 24, Planung), #1732 (Herkunft/Produktentscheid), #1733 (Typwechsel-Confirm), [M4/#1060](https://github.com/anlaufstelle/app/issues/1060) (Export = Metadaten), [ADR-014](014-encrypted-file-vault.md) (File-Vault), [ADR-020](020-three-role-postgres-model.md) (Drei-Rollen-Postgres-Modell), [ADR-037](037-konfigurationspakete-dynamische-felder.md) §9 (E8-Sperre, hiermit abgelöst)

## Context

Die Attachment-Pipeline hing bis Milestone 24 vollständig am Ereignis: `EventAttachment` hatte einen NOT-NULL-FK auf `Event`, **keine eigene `facility_id`**, und seine RLS-Policy lief als Join über `core_event`. Datei-Felder an Personen- und Fall-Typen waren deshalb per Mehrschicht-Sperre verboten ([ADR-037](037-konfigurationspakete-dynamische-felder.md) §9 „E8") — ein `file`-Feld dort wäre eine Sackgasse gewesen, der Upload hätte keinen Speicherort gehabt.

Der Produktentscheid vom 2026-08-03 (#1732, Option c) verlangt Dateianhänge auch an Personen und Fällen, als voller Umbau statt Anbau. Geklärte Produktfragen:

| Frage | Entscheid |
|---|---|
| Retention Personen-/Fall-Dateien | **Owner-Lebenszyklus**: Löschung mit der Klienten-Anonymisierung (Papierkorb-Frist `client_trash_days`); keine eigene Zeitfrist in v1; `DocumentType.retention_days` bleibt event-only (expliziter No-Op + Negativtest) |
| Art.-15-Auskunft | **Attachment-Metadaten in JSON+PDF** für alle drei Owner — schließt auch die bestehende Event-Lücke. Keine Binärinhalte (konsistent mit M4/#1060) |

Zwänge: RLS-Umbau ist nach Prod-Deploy praktisch irreversibel (DDL + Datenbestand); die Versionsketten-Semantik (`entry_id`, `is_current`, `superseded_by`, `deleted_at`) und die Speicherpfade müssen bitgleich überleben; Audit-Alt-Zeilen sind HMAC-verkettet und append-only (nie migrierbar); keine neuen Abhängigkeiten.

## Decision

`EventAttachment` wird zu einem generischen **`Attachment`** mit eigener `facility_id` und **genau einem** Owner (Ereignis, Person oder Fall); die RLS-Policy wechselt von der Join- auf die direkte facility-Klausel; die E8-Sperre fällt ersatzlos. Im Einzelnen:

### 1. Ein Modell, genau ein Owner (Option A)

`EventAttachment` → `Attachment` (RenameModel, Tabelle `core_eventattachment` → `core_attachment`). Drei nullable Owner-FKs `event`/`client`/`case` (alle CASCADE, alle `related_name="attachments"` — fünffach auf fünf verschiedene Zielmodelle ist kollisionfrei, da `related_name` je Zielmodell aufgelöst wird) + `facility`-FK NOT NULL (Backfill aus `event.facility`) + `CheckConstraint attachment_exactly_one_owner` (genau ein Owner-FK gesetzt). Die Versionskette bleibt owner-agnostisch unverändert. Owner-Helfer im Modell: `OWNER_FIELDS`, Properties `owner_type`/`owner_id`/`owner` (Branch auf `*_id` vor Attribut-Zugriff — kein N+1), Modul-Helfer `owner_field_name(owner)`.

### 2. RLS direkt statt Join

Direkte Policy nach der Vorlage `0127_rls_offline_client_revocation.py` (inkl. `is_super_admin`-Bypass-Branch). Defense-in-Depth-Gewinn: unter der Join-Policy wäre eine Zeile ohne Ereignis policy-los gewesen. Neue Leak-Verantwortung des Service-Layers: `facility` wird IMMER aus `owner.facility` abgeleitet, nie als freier Parameter entgegengenommen (Unit-Test pinnt `attachment.facility_id == owner.facility_id`). Composite-FK-Härtung `(event_id, facility_id)` ist bewusst NICHT Teil dieses Umbaus → eigenes Härtungs-Issue.

### 3. Manager

`AttachmentQuerySet(FacilityScopedQuerySet)` + `AttachmentManager(FacilityScopedManager)` mit `active()`/`deleted()`-Override auf `deleted_at` — der nackte `FacilityScopedManager` vererbt `is_deleted`-basierte Methoden auf den Reverse-Manager und liefe auf einen `FieldError`. Keine `AlterModelManagers`-Migration nötig (vorher Default-Manager).

### 4. Index

Nur `(facility, -created_at)` (`attachment_facility_ca_idx`) für die Übersichts-Query; FK-Indexe legt Django automatisch an; partielle Owner-Indexe sind YAGNI, bis eine Query sie verlangt.

### 5. Audit-Schema der Anhang-Domäne

Alt-Zeilen mit `target_type="EventAttachment"` werden NIE migriert (HMAC-Kette + Immutability-Trigger); künftige Writes tragen `"Attachment"`. Einheitliches Detail-Schema: `owner_type`/`owner_id` statt `event_id` — auch im Download-Audit. Konsequenz für die Art.-15-Auskunft: die Audit-Zuordnung kennt beide Schemata (`detail.owner_id` neu, `detail.event_id` für Alt-Zeilen). Betreiberhinweis: Auswertungen müssen beide `target_type`-Strings kennen.

### 6. Kein Fall-Löschworkflow, keine Case-Legal-Hold-Regel in v1

Es gibt heute keinen Fall-Löschpfad; fall-eigene Dateien fallen mit der Klienten-Anonymisierung, der Client-Legal-Hold schützt transitiv.

### 7. Typwechsel an Person/Fall mit Dateien + Marker-Invariante I-M1

Betroffene Versionsketten werden beim Typwechsel **soft-deletet** (`discard_type_switch_file_chains`), nicht physisch gelöscht; der Confirm-Dialog (#1733) nennt die Datei-Anzahl. **Invariante I-M1:** jeder Pfad, der Attachment-Zeilen physisch löscht, bereinigt im selben `transaction.atomic` die `__files__`-Marker des Owners (oder leert dessen `data_json`). Prüftabelle je Löschpfad:

| Löschpfad | physisch? | Marker-Bereinigung |
|---|---|---|
| `anonymize_client` → `_delete_owned_attachments_for_client` | ja (Event-, Client-, Case-Anhänge) | Redaktion leert/ersetzt die `data_json`-Träger im selben atomic; Marker-Rest wäre I-M1-Verstoß → Test |
| K-Anon-Bridge `_k_anonymize_with_cascade` | ja (identischer Helfer, Paritäts-Guard) | wie oben |
| `soft_delete_event` → `delete_attachments_for(event)` | ja (Bestandsverhalten seit ADR-014) | Event ist danach soft-deleted und nicht wiederherstellbar; Marker im verwaisten `data_json` sind Bestandsverhalten, dokumentiert |
| `enforce_retention`/`enforce_document_type_retention` → `_soft_delete_events` | ja | wie `soft_delete_event` |
| `discard_type_switch_file_chains` (neu) | **nein** (soft-delete) | Typwechsel verwirft `data_json` ohnehin (E11-Rebuild) — kein physischer Löschpfad, I-M1 nicht berührt |
| `purge_orphan_field_data` | nein | skippt FILE-Waisen explizit (Docstring + Skip-Pin-Test) |
| `cleanup_orphan_storage_files` | nur Disk | kennt keine Marker; verschont alle `storage_filename` bekannter Zeilen |

### 8. Offline

Client-/Fall-Formulare bleiben nicht queuebar. Zukunftssicherung: inertes `data-offline-enctype-downgrade` + `event-form-enctype.js`-Include an beiden Formularen + Warnkommentar an `QUEUE_PATTERNS`; `ATTACHMENT_DOWNLOAD`-Regex um `clients|cases` erweitert (sonst zeigte der Offline-Fallback eine Ersatzseite statt eines Fehlers, Refs #751).

### 9. E8-Ablösung

Alle vier Schichten fallen ersatzlos (Paket-Schema, Config-Service, Formular-Skip `exclude_file_fields`, Typwechsel-Guard `FieldTemplateForm.clean_field_type`); das Flag `exclude_file_fields` wird komplett entfernt (an allen Ausprägungen gleich = tote Konfiguration; `DynamicClientDataForm`/`DynamicCaseDataForm` bleiben als leere Klassen für stabile Importpfade/Lognamen). **Kein `schema_version`-Bump** — eine Restriktions-Lockerung ist abwärtskompatibel: jedes bisher gültige Paket bleibt gültig. `src/core/packages/default.yaml` bleibt unangetastet (FILE an Personen-Typ = Träger-Entscheidung, kein Auslieferungs-Default); ein auskommentiertes Beispiel steht in `docs/packages.md`.

### 10. ADR-037 §9

Kein Status-Wechsel an ADR-037; §9 erhält einen „Update"-Block (Konvention wie §10): abgelöst durch dieses ADR ab Migration 0129.

### 11. Migrationskette (3 Migrationen, lückenlos fail-closed)

- **`0129_rename_eventattachment_to_attachment`** — nur RenameModel. Policies/RLS-Flags/GRANTs hängen an der Tabellen-OID und **überleben das Rename**; kein Policy-Handling hier. Self-FK `superseded_by` wird repointet (Test-Assertion).
- **`0130_attachment_owner_facility`** (atomic PFLICHT) — AddField `facility` nullable → RunPython-Backfill aus `event.facility` unter der **BYPASSRLS-Migrationsrolle** (ADR-020; `assert_migration_role_bypasses_rls` als erste Zeile jeder RunPython-Funktion, Konvention seit 0123/#1746 — die Session-GUC `app.is_super_admin` ist bewusst NICHT der Migrationspfad; Rest-NULL-Verifikation fail-loud) → NOT NULL → `event` nullable + `client`/`case` + CheckConstraint + Index. **Reverse-Guard als LETZTE Operation** — Operationen laufen rückwärts in umgekehrter Reihenfolge, der Guard läuft beim Rollback damit als ERSTE (Position per Test gepinnt): `RuntimeError`, sobald client-/case-Zeilen existieren.
- **`0131_rls_attachment`** (RunSQL) — idempotent ENABLE+FORCE, `DROP POLICY IF EXISTS` + `CREATE POLICY` **auf `core_attachment`** (die Policy folgt der OID, nicht dem Altnamen). Reverse stellt die Join-Policy in der **0085-Variante inkl. `is_super_admin`-OR-Branch** wieder her (0047 hatte den Branch noch nicht) und lässt RLS bewusst AKTIV — anders als die Vorlage 0111, die RLS neu einführte. Zusätzlich trägt 0131 einen eigenen Rollback-Guard als letzte Operation: auch der legale Einzelschritt-Rollback `migrate core 0130` ist blockiert, solange client-/case-eigene Zeilen existieren — unter der wiederhergestellten Join-Policy wären sie policy-verdeckt.

Lückenlosigkeit: das Rename lässt die Join-Policy aktiv; nach 0130 sind alle Zeilen event-owned; 0131 läuft in einer Transaktion (ACCESS EXCLUSIVE; „RLS an ohne Policy" = default-deny); schlägt 0131 fehl, lehnt die USING-Klausel (implizites WITH CHECK) INSERTs mit `event_id IS NULL` ab. **0130+0131 dürfen nie in getrennten Releases landen.** Rollback (`migrate core 0130` wie `migrate core 0128`) ist nur zulässig, solange keine client-/case-eigenen Anhänge existieren (Rollback-Guards in 0131 und 0130).

### 12. Sichtbarkeits-Gate für Downloads (5 Stufen) + Rollen-Gefälle

(1) Owner-Lookup facility-gescopt + `is_deleted=False` → 404; (2) Doc-Type-Sensitivity `user_can_see_document_type` → **404** (einzige nicht-leckende Antwort — die Zusatzfelder-Sektion ist für Unterberechtigte komplett abwesend; `document_type is None` ⇒ fail-closed); (3) Anhang am Owner + `deleted_at IS NULL` → 404; (4) Feld-Sensitivity `user_can_see_field` → **403**; (5) Datei auf Disk → 404 + `logger.error`. Rollen-Gefälle: Fall-Download `StaffRequiredMixin` (Fälle sind für Assistenz unsichtbar — ein ASSISTANT-Download wäre Gate-Umgehung), Client-Download `AssistantOrAboveRequiredMixin`.

## Consequences

- Datei-Felder sind an Personen- und Fall-Typen konfigurierbar; Formulare, Download-Routen, Datei-Übersicht, DSGVO-Lösch- und Auskunftspfade werden owner-generisch.
- Die K-Anonymisierungs-Bridge-Invariante („Anhänge sind beim Bridge-Lauf immer schon weg") gilt nur noch für Event-Anhänge; client-/fall-eigene Anhänge löscht die Bridge über denselben Helfer wie `anonymize_client` (Paritäts-Guard erzwingt Gleichlauf).
- Audit-Auswertungen müssen zwei `target_type`-Strings und zwei Detail-Schemata kennen (Betreiberhinweis in der Admin-Doku).
- Der Service-Layer trägt die Facility-Ableitung als Sicherheitsverantwortung (Pin-Test); eine falsche Owner-Facility-Kombination ist kein DB-Constraint, sondern Konvention + Test (Composite-FK-Härtung folgt separat).
- Rollback der Migrationskette wird mit den ersten client-/case-Anhängen unmöglich (bewusst, Reverse-Guard).
- Folgearbeiten: Composite-FK-Härtung `(event_id, facility_id)`; B4 (superseded-Anzeige der Übersicht) nur notiert; Offline-Queuebarkeit der Client-/Fall-Formulare bleibt aus.

## Alternatives considered

- **Drei Modelle** (`EventAttachment`/`ClientAttachment`/`CaseAttachment`): dreifache Pipeline (Vault, Policy, Admin, Übersicht, Auskunft), dreifache RLS-Migrationen — verworfen.
- **ContentType-FK / GenericForeignKey**: keine DB-Integrität (kein echter FK, kein CASCADE), RLS-Klausel müsste über `content_type_id` dispatchen, ORM-Ergonomie schlecht — verworfen.
- **COALESCE-Join-Policy** (RLS via `COALESCE` über drei Joins statt direkter facility-Spalte): teurer pro Zeile, policy-lose Zeilen bei Owner-Verlust weiter möglich, keine Defense-in-Depth — verworfen.
- **Variante (b) aus #1732** (Anbau: nur zusätzlicher Client-FK am bestehenden Modell, Event-Pfad unangetastet): halbierte Symmetrie (Fälle außen vor), Sperr-Schichten nur teilweise ablösbar, zweite Umbau-Runde absehbar — verworfen zugunsten Option (c).
- **Attachment-`RetentionProposal`** (eigene Fristen für Personen-/Fall-Dateien): Produktentscheid Owner-Lebenszyklus; eigene Fristen erst, wenn ein Träger sie verlangt — verworfen für v1.

## References

- Umsetzungs-Issue: #1738 (Aufgaben AP0–AP6, Gates G1–G5)
- Vorlagen: `src/core/migrations/0127_rls_offline_client_revocation.py` (direkte Policy), `0085_rls_superadmin_bypass.py` (Join-Policy-Variante für den 0131-Reverse)
- Sperre alt: [ADR-037](037-konfigurationspakete-dynamische-felder.md) §9 (E8)
