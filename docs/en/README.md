# Documentation — English

> This is a partial English translation of the Anlaufstelle documentation.
> The [German documentation](../README.md) is the authoritative source.
> Internal artifacts (`docs/archive/`, `docs/superpowers/`, `docs/ai/`, `docs/dev/`, and `docs/testing/` except the manual test matrix and the assertion conventions) are dev-only.


---

## Conventions

The docs describe the **current state**. History belongs in [CHANGELOG.md](../../CHANGELOG.md) and the [ADRs](../adr/) — v1.0.0 is the first public release, so there are no pre-1.0 existing installations that would need a migration guide. Accordingly: no "since vX.Y" markers in running text, no migration boxes for pre-1.0 states (Refs #2205).

---

## Available in English

| Document | Description | Audience |
|----------|-------------|----------|
| [admin-guide.md](admin-guide.md) | Operations manual: installation, backup/restore, monitoring, MFA, retention, GDPR | IT Admins |
| [ops-runbook.md](ops-runbook.md) | Operations runbook: deployment, rollback, cron jobs, log analysis, troubleshooting, RLS, backup/restore, restore drill (Refs #1062) | IT Admins/DevOps |
| [user-guide.md](user-guide.md) | User handbook: work center, timeline, persons, events, search, export, statistics, roles | End users |
| [../screenshots.en.md](../screenshots.en.md) | Illustrated tour of the interface (demo data, pseudonymized) | Stakeholders, End users |
| [domain-concept-summary.md](domain-concept-summary.md) | Summary of the domain concept (not a 1:1 translation) | Stakeholders, Developers |
| [glossary.md](glossary.md) | Bilingual glossary of domain terms (DE↔EN) | Everyone |
| [data-protection.md](data-protection.md) | GDPR guide: article/right → ADR/template/code/FAQ (companion to German [datenschutz.md](../datenschutz.md)) | Everyone (Auditor/Dev/Operator) |
| [reporting-plugins.md](reporting-plugins.md) | Report backends as a plugin system: API reference (`DatasetBackend`, `DatasetResult`), "your own backend in five steps", discovery, snapshot contract, k-anonymity, export formats, test checklist (Refs #1061, [ADR-038](../adr/038-reporting-plugin-backends.md)) | Developers |
| [../../README.en.md](../../README.en.md) | Project README | Everyone |
| [../../CONTRIBUTING.en.md](../../CONTRIBUTING.en.md) | Contributing guidelines | Developers |

---

## German-only Documentation

The documents listed below are **intentionally German-only** — they are not awaiting translation. Each is either deeply rooted in German legal/regulatory frameworks (DSGVO, BDSG, SGB X) or primarily relevant for internal development and operations, so an English version would add little value. Where the underlying concepts matter to an English-speaking audience, a companion document already exists — for example the [domain concept summary](domain-concept-summary.md) and the bilingual [glossary](glossary.md).

### Domain Concept (Fachkonzept)

The full domain concept ([fachkonzept-anlaufstelle.md](../fachkonzept-anlaufstelle.md)) is a 1,300+ line document covering product vision, architecture decisions, non-functional requirements, and GDPR compliance strategy. A condensed English summary is available in [domain-concept-summary.md](domain-concept-summary.md). The [glossary](glossary.md) maps all domain-specific German terms to their English equivalents.

### Data Protection Templates (DSGVO)

Anlaufstelle includes German-language data protection templates in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/). These implement requirements specific to **German law** (DSGVO + BDSG + SGB X). They are **not** generic GDPR templates. For GDPR compliance in other EU jurisdictions, consult your local Data Protection Authority (DPA). The templates live in the app package so the Docker image ships them (#784).

Templates included:
- **AV-Vertrag** — Data Processing Agreement (Art. 28 DSGVO)
- **TOMs** — Technical and Organizational Measures (Art. 32 DSGVO)
- **DSFA** — Data Protection Impact Assessment (Art. 35 DSGVO)
- **Informationspflichten** — Data subject information (Arts. 13–14 DSGVO)
- **Löschkonzept** — Retention and deletion concept for active data and backups
- **Mitarbeiter-Informationspflichten** — Employee information (Arts. 13–14 DSGVO and § 26 BDSG)
- **Verarbeitungsverzeichnis** — Processing register (Art. 30 DSGVO)

### Other German-only Documents

| Document | Description |
|----------|-------------|
| [architecture.md](../architecture.md) | Architecture overview as a signpost: layers (views → services → models), security architecture in brief, no-code configuration core, offline PWA, test landscape — with pointers into the in-depth documents (Refs #1062) |
| [packages.md](../packages.md) | Configuration packages (YAML, schema v1): format reference, loader semantics, `manage.py load_package`, maintenance rules (Refs #1058, [ADR-037](../adr/037-konfigurationspakete-dynamische-felder.md)) |
| [packages-beratungsstelle.md](../packages-beratungsstelle.md) | “Beratungsstelle” package: eight documentation types, sensitivity matrix, reports, and operator recommendations (Refs #2145) |
| [packages-awareness.md](../packages-awareness.md) | “Awareness” package: seven documentation types, sensitivity matrix, reports, and operator recommendations (Refs #2137) |
| [packages-frauenhaus.md](../packages-frauenhaus.md) | “Frauenhaus” package: eight documentation types, effective sensitivity matrix, reports, and recommendations for 24/7 operation (Refs #2138) |
| [packages-jugendarbeit.md](../packages-jugendarbeit.md) | “Jugendarbeit” package: eight documentation types, sensitivity matrix, reports, and operator recommendations (Refs #2139) |
| [facility-export-schema.md](../facility-export-schema.md) | Full facility export (JSON, schema v1): field list, versioning rule, trash/anonymization boundaries, attachment hashes, `manage.py export_facility` (Refs #1060) |
| [monitoring-guide.md](../monitoring-guide.md) | `/health/` endpoint: fields, HTTP status codes, liveness vs. detail, hooking up uptime/monitoring tools (Refs #1071) |
| (DE: [disaster-recovery.md](../disaster-recovery.md)) | Total-loss recovery: obtaining an off-site backup, key escrow, restore on a fresh host, RTO/RPO (Refs #1071) |
| [faq.md](../faq.md) ([English offline excerpt](faq.md)) | Authoritative source for frequently asked questions about operations, troubleshooting, and organizational workflows |
| [accessibility-audit.md](../accessibility-audit.md) | Accessibility audit evidence for WCAG 2.1 Level AA, including automated findings, remediation, and the screen-reader test protocol |
| (DE: [security-notes.md](../security-notes.md)) | Deliberate security design decisions (2FA, Fernet, RLS, audit, lockout) |
| (DE: [threat-model.md](../threat-model.md)) | Security model, attack surface, known open gaps |
| `audit-inventar.md` (dev-only) | Index of audit classification codes (FND-\*, S-\*, phases, sprints) |
| `dev-deployment.md` (dev-only) | Dev/test environment deployment |
| `release-checklist.md` (dev-only) | Release process, sanitize steps, hard-exclusion list |
| [performance-budgets.md](../performance-budgets.md) | Performance targets and budgets |
| [sprachleitlinie.md](../sprachleitlinie.md) | Language guideline for UI and handbook — _Klientel → Person_, terminology matrix, refactor priorities (Refs #604) |
| [adr/](../adr/) | Architecture Decision Records — why the architecture looks the way it does |
| [testing/](../testing/) | Manual test matrix and assertion conventions |
| [testing/manual-test-matrix.md](../testing/manual-test-matrix.md) | Manual test matrix (German): hub with front matter and appendices, sections a–d |
| [e2e-architecture.md](../e2e-architecture.md) | E2E testing infrastructure (developer-facing) |
| [e2e-runbook.md](../e2e-runbook.md) | E2E test execution, debugging, checklists |
| `async-pdf-evaluation.md` (dev-only) | _Historical_ — async PDF queue evaluation, superseded by [ADR-010](../adr/010-sync-pdf-generation.md) |

<!-- translation-source: docs/README.md -->
<!-- translation-version: v0.99.8 -->
<!-- translation-date: 2026-10-06 -->
<!-- source-hash: d497f883a851f57579488f54abecc401d93f70af -->
