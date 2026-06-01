# Documentation — English

> This is a partial English translation of the Anlaufstelle documentation.
> The [German documentation](../README.md) is the authoritative source.

---

## Available in English

| Document | Description | Audience |
|----------|-------------|----------|
| [admin-guide.md](admin-guide.md) | Operations manual: installation, backup, monitoring, MFA, compliance dashboard, GDPR | IT Admins |
| [user-guide.md](user-guide.md) | User handbook: work center, timeline, persons, events, search, export, statistics, roles | End users |
| [domain-concept-summary.md](domain-concept-summary.md) | Summary of the domain concept (not a 1:1 translation) | Stakeholders, Developers |
| [glossary.md](glossary.md) | Bilingual glossary of domain terms (DE↔EN) | Everyone |
| [../../README.en.md](../../README.en.md) | Project README | Everyone |
| [../../CONTRIBUTING.en.md](../../CONTRIBUTING.en.md) | Contributing guidelines | Developers |

---

## German-only Documentation

The following documents are available only in German. They are either deeply rooted in German legal/regulatory frameworks or primarily relevant for internal development.

### Domain Concept (Fachkonzept)

The full domain concept ([fachkonzept-anlaufstelle.md](../fachkonzept-anlaufstelle.md)) is a 1,300+ line document covering product vision, architecture decisions, non-functional requirements, and GDPR compliance strategy. A condensed English summary is available in [domain-concept-summary.md](domain-concept-summary.md). The [glossary](glossary.md) maps all domain-specific German terms to their English equivalents.

### Data Protection Templates (DSGVO)

Anlaufstelle includes German-language data protection templates in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/). These implement requirements specific to **German law** (DSGVO + BDSG + SGB X). They are **not** generic GDPR templates. For GDPR compliance in other EU jurisdictions, consult your local Data Protection Authority (DPA). Templates moved into the app package in #784 so the Docker image ships them.

Templates included:
- **AV-Vertrag** — Data Processing Agreement (Art. 28 DSGVO)
- **TOMs** — Technical and Organizational Measures (Art. 32 DSGVO)
- **DSFA** — Data Protection Impact Assessment (Art. 35 DSGVO)
- **Informationspflichten** — Data subject information (Arts. 13–14 DSGVO)
- **Verarbeitungsverzeichnis** — Processing register (Art. 30 DSGVO)

### Other German-only Documents

| Document | Description |
|----------|-------------|
| [ops-runbook.md](../ops-runbook.md) | Operations runbook: monitoring, alerts, cron jobs, RLS, backup/restore, restore drill |
| [faq.md](../faq.md) | Frequently asked questions — operations & troubleshooting |
| [security-notes.md](../security-notes.md) | Deliberate security design decisions (2FA, Fernet, RLS, audit, lockout) |
| [threat-model.md](../threat-model.md) | Security model, attack surface, known open gaps |
| [audit-inventar.md](../audit-inventar.md) | Index of audit classification codes (FND-\*, S-\*, phases) |
| [dev-deployment.md](../dev-deployment.md) | Dev/test environment deployment |
| [coolify-deployment.md](../coolify-deployment.md) | Coolify deployment (supported alternative; primary is Docker Compose, see [ADR-017](../adr/017-deployment-topology.md)) |
| [release-checklist.md](../release-checklist.md) | Release process, sanitize steps, hard-exclusion list |
| [performance-budgets.md](../performance-budgets.md) | Performance targets and budgets |
| [sprachleitlinie.md](../sprachleitlinie.md) | Language guideline for UI and handbook — _Klientel → Person_, terminology matrix, refactor priorities (Refs #604) |
| [adr/](../adr/) | Architecture Decision Records — why the architecture looks the way it does |
| [testing/](../testing/) | Test matrix, mutation testing, release test profiles |
| [e2e-architecture.md](../e2e-architecture.md) | E2E testing infrastructure (developer-facing) |
| [e2e-runbook.md](../e2e-runbook.md) | E2E test execution, debugging, checklists |
| [async-pdf-evaluation.md](../async-pdf-evaluation.md) | _Historical_ — async PDF queue evaluation, superseded by [ADR-010](../adr/010-sync-pdf-generation.md) |

<!-- translation-source: docs/README.md -->
<!-- translation-version: v0.12.0 -->
<!-- translation-date: 2026-05-26 -->
<!-- source-hash: 4fe0c79 -->
