# Datenschutz / DSGVO — Wegweiser

> **Wegweiser, kein Inhalts-Silo.** Diese Seite verlinkt je DSGVO-Thema die maßgebliche Quelle (ADR, Vorlage, Code, FAQ) — sie erklärt die Mechanik nicht erneut. **Bei Widerspruch gilt der Code** (bzw. ADR/Vorlage), nicht diese Übersicht.
>
> **Pflege:** Kommt eine neue datenschutzrelevante ADR hinzu, hier eine Zeile ergänzen.

**Audience-Tags:** **[Auditor]** Nachweis/Entscheidung · **[Dev]** Code/Architektur · **[Träger]** Bedienung/Betrieb. Tags stehen nur, wo sie real zutreffen.

## A. Grundsätze der Verarbeitung (Art. 5 DSGVO)

| Grundsatz | Verweis | Für |
|---|---|---|
| Art. 5(1)(c) Datenminimierung | [ADR-023](adr/023-k-anonymization-statistik.md) · [`external_report.py`](../src/core/services/dashboard/external_report.py) | [Auditor] [Dev] |
| Art. 5(1)(e) Speicherbegrenzung | [ADR-021](adr/021-retention-modell.md) | [Auditor] [Dev] [Träger] |
| Art. 5(1)(f) Integrität & Vertraulichkeit | [ADR-006](adr/006-fernet-field-encryption.md) · [ADR-007](adr/007-auditlog-append-only.md) · [ADR-014](adr/014-encrypted-file-vault.md) · [security-notes.md](security-notes.md) | [Auditor] [Dev] |
| Art. 5(2) Rechenschaftspflicht | [ADR-007](adr/007-auditlog-append-only.md) · [VVT](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Auditor] |

## B. Betroffenenrechte (Art. 12–22 DSGVO)

| Recht | Verweis | Für |
|---|---|---|
| Art. 13/14 Informationspflichten | [Informationspflichten](../src/core/dsgvo_templates/informationspflichten.md) | [Träger] [Auditor] |
| Art. 15 Auskunft | Personen-Datenexport JSON/PDF ([`ClientDataExportJSONView`/`ClientDataExportPDFView`](../src/core/views/clients.py)) · [FAQ §C](faq.md#c-rollen--datenschutz) | [Träger] [Dev] |
| Art. 16 Berichtigung | [ADR-013](adr/013-dsgvo-art16-no-selfservice.md) — bewusst kein Self-Service | [Auditor] [Dev] |
| Art. 17 Löschung | [ADR-021](adr/021-retention-modell.md) (Soft-Delete-Strategien, k-Anon-Retention inkl. Freitext-Kaskade #1094) · [`core/retention/`](../src/core/retention/) | [Auditor] [Dev] [Träger] |
| Art. 18 Einschränkung | Legal Hold — [ADR-021](adr/021-retention-modell.md) · [`legal_holds.py`](../src/core/retention/legal_holds.py) | [Auditor] [Dev] |
| Art. 20 Datenübertragbarkeit | teilweise über den maschinenlesbaren Personen-JSON-Export; kein dedizierter Übertragbarkeits-Workflow | [Träger] |

## C. Technische & organisatorische Maßnahmen (Art. 25, 32 DSGVO)

| Maßnahme | Verweis | Für |
|---|---|---|
| Art. 25 Privacy by Design/Default | [ADR-005](adr/005-facility-scoping-and-rls.md) — Facility-Scoping + RLS | [Auditor] [Dev] |
| Art. 32 Verschlüsselung at rest | [ADR-006](adr/006-fernet-field-encryption.md) (Felder) · [ADR-014](adr/014-encrypted-file-vault.md) (Datei-Vault, Fernet/AES-128) | [Auditor] [Dev] |
| Art. 32 Zugriffskontrolle | [ADR-005](adr/005-facility-scoping-and-rls.md) (RLS) · [ADR-018](adr/018-rollenmodell-superadmin.md)/[ADR-020](adr/020-three-role-postgres-model.md) (Rollen) · [ADR-015](adr/015-mfa-totp.md) (MFA) | [Auditor] [Dev] |
| Art. 32 Integrität/Nachvollziehbarkeit | [ADR-007](adr/007-auditlog-append-only.md) — AuditLog append-only | [Auditor] [Dev] |
| Art. 32 Offline-Daten | [ADR-022](adr/022-offline-snapshot-keys.md) — Offline-Snapshot & -Keys, verschlüsselte IndexedDB; [DSFA-/TOM-Einordnung des Offline-Pfads](adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## D. Auftragsverarbeitung & Nachweisdokumente (Art. 28, 30, 35 DSGVO)

| Dokument | Verweis | Für |
|---|---|---|
| Art. 28 Auftragsverarbeitungsvertrag | [AV-Vertrag](../src/core/dsgvo_templates/av-vertrag.md) | [Träger] |
| Art. 30 Verzeichnis von Verarbeitungstätigkeiten | [VVT](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Träger] [Auditor] |
| Art. 35 Datenschutz-Folgenabschätzung | [DSFA](../src/core/dsgvo_templates/dsfa.md) | [Träger] [Auditor] |
| TOM (Gesamtübersicht) | [TOM](../src/core/dsgvo_templates/toms.md) | [Träger] [Auditor] |
| App-Feature „DSGVO-Paket" (Bündel-Download) | [`DSGVOPackageView`](../src/core/views/dsgvo.py) · [`generate_dsgvo_package`](../src/core/management/commands/generate_dsgvo_package.py) | [Träger] [Dev] |

## E. Begriffe

Definitionen (PII, K-Anonymität, k-Anon-Retention, Pseudonymisierung, Legal Hold, Speicherbegrenzung, Soft-Delete-Strategien) → [glossar.md](glossar.md).
