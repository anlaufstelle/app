# Data Protection / GDPR — Guide

> **A guide, not a content silo.** This page links, per GDPR topic, to the authoritative source (ADR, template, code, FAQ) — it does not re-explain the mechanics. **In case of conflict, the code** (or the ADR/template) **wins**, not this overview. ADRs and templates are German-language; see the [English docs index](README.md).
>
> **Maintenance:** When a new data-protection-relevant ADR is added, add a line here.

**Audience tags:** **[Auditor]** proof/decision · **[Dev]** code/architecture · **[Operator]** operation/usage. Tags appear only where they genuinely apply.

## A. Principles of processing (Art. 5 GDPR)

| Principle | Reference | For |
|---|---|---|
| Art. 5(1)(c) Data minimization | [ADR-023](../adr/023-k-anonymization-statistik.md) · [`external_report.py`](../../src/core/services/dashboard/external_report.py) | [Auditor] [Dev] |
| Art. 5(1)(e) Storage limitation | [ADR-021](../adr/021-retention-modell.md) | [Auditor] [Dev] [Operator] |
| Art. 5(1)(f) Integrity & confidentiality | [ADR-006](../adr/006-fernet-field-encryption.md) · [ADR-007](../adr/007-auditlog-append-only.md) · [ADR-014](../adr/014-encrypted-file-vault.md) · [security-notes.md](../security-notes.md) | [Auditor] [Dev] |
| Art. 5(2) Accountability | [ADR-007](../adr/007-auditlog-append-only.md) · [Processing register](../../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Auditor] |

## B. Data subject rights (Art. 12–22 GDPR)

| Right | Reference | For |
|---|---|---|
| Art. 13/14 Information duties | [Information duties](../../src/core/dsgvo_templates/informationspflichten.md) | [Operator] [Auditor] |
| Art. 15 Access | Per-person data export JSON/PDF ([`ClientDataExportJSONView`/`ClientDataExportPDFView`](../../src/core/views/clients.py)) · [FAQ §C](../faq.md#c-rollen--datenschutz) | [Operator] [Dev] |
| Art. 16 Rectification | [ADR-013](../adr/013-dsgvo-art16-no-selfservice.md) — deliberately no self-service | [Auditor] [Dev] |
| Art. 17 Erasure | [ADR-021](../adr/021-retention-modell.md) (soft-delete strategies, k-anon retention incl. free-text cascade #1094) · [`core/retention/`](../../src/core/retention/) | [Auditor] [Dev] [Operator] |
| Art. 18 Restriction | Legal Hold — [ADR-021](../adr/021-retention-modell.md) · [`legal_holds.py`](../../src/core/retention/legal_holds.py) | [Auditor] [Dev] |
| Art. 20 Portability | partially via the machine-readable per-person JSON export; no dedicated portability workflow | [Operator] |

## C. Technical & organizational measures (Art. 25, 32 GDPR)

| Measure | Reference | For |
|---|---|---|
| Art. 25 Privacy by design/default | [ADR-005](../adr/005-facility-scoping-and-rls.md) — facility scoping + RLS | [Auditor] [Dev] |
| Art. 32 Encryption at rest | [ADR-006](../adr/006-fernet-field-encryption.md) (fields) · [ADR-014](../adr/014-encrypted-file-vault.md) (file vault, Fernet/AES-128) | [Auditor] [Dev] |
| Art. 32 Access control | [ADR-005](../adr/005-facility-scoping-and-rls.md) (RLS) · [ADR-018](../adr/018-rollenmodell-superadmin.md)/[ADR-020](../adr/020-three-role-postgres-model.md) (roles) · [ADR-015](../adr/015-mfa-totp.md) (MFA) | [Auditor] [Dev] |
| Art. 32 Integrity/traceability | [ADR-007](../adr/007-auditlog-append-only.md) — append-only audit log | [Auditor] [Dev] |
| Art. 32 Offline data | [ADR-022](../adr/022-offline-snapshot-keys.md) — offline snapshot & keys, encrypted IndexedDB; [DPIA/TOM classification of the offline path](../adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## D. Processing agreements & evidence documents (Art. 28, 30, 35 GDPR)

| Document | Reference | For |
|---|---|---|
| Art. 28 Data processing agreement | [Data processing agreement](../../src/core/dsgvo_templates/av-vertrag.md) | [Operator] |
| Art. 30 Record of processing activities | [Processing register](../../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | [Operator] [Auditor] |
| Art. 35 Data protection impact assessment | [DPIA](../../src/core/dsgvo_templates/dsfa.md) | [Operator] [Auditor] |
| TOMs (overview) | [TOMs](../../src/core/dsgvo_templates/toms.md) | [Operator] [Auditor] |
| App feature "GDPR package" (bundle download) | [`DSGVOPackageView`](../../src/core/views/dsgvo.py) · [`generate_dsgvo_package`](../../src/core/management/commands/generate_dsgvo_package.py) | [Operator] [Dev] |

## D2. Public demo instance — web analytics & session recording (Refs #1604)

> Applies **exclusively** to the public demo `demo.anlaufstelle.app`, **not** to the self-hosted product. Self-hosted operator instances run no Umami/tracking at all.

| Aspect | Rule | For |
|---|---|---|
| Legal basis | Art. 6(1)(a) — **opt-in consent**: tracking/recording/heatmap only start technically **after** consent is given (no silent tracking); declining is possible | [Auditor] [Operator] |
| Scope | Umami web analytics **including session recording + heatmap** (self-hosted, `umami.example.org`); **only after login**, **not** on the sign-in page | [Auditor] [Dev] |
| Data | exclusively **synthetic seed data** (hourly reset) — no real data subjects | [Auditor] |
| Legal texts | Imprint + privacy policy of the [website](https://anlaufstelle.app) (linked from the demo footer + consent banner) | [Operator] |
| Technology / CSP scoping | consent gate [`umami-consent.js`](../../src/static/js/umami-consent.js) · narrowly scoped CSP allowance [`DemoUmamiCSPRelaxMiddleware`](../../src/core/middleware/demo_umami_csp_relax.py) · [security-notes.md](../security-notes.md#eng-gescopte-ausnahme--umami-analytics-auf-der-demo-issue-1604) · [ADR-022 § (c)](../adr/022-offline-snapshot-keys.md#dsfa-tom-einordnung-des-offline-pfads-1343) | [Dev] [Auditor] |

## E. Terms

Definitions (PII, k-anonymity, k-anon retention, pseudonymization, legal hold, storage limitation, soft-delete strategies) → [glossary.md](glossary.md).

<!-- translation-source: docs/datenschutz.md -->
<!-- translation-version: v0.22.0 -->
<!-- translation-date: 2026-08-01 -->
<!-- source-hash: 3729c4ae2d5cfc4290bc7cc3442bfcf9b5bfc82c -->
