# Dokumentation — Übersicht

> **[English Documentation](en/README.md)**

Dieses Verzeichnis enthält die gesamte Projektdokumentation. Interne Artefakte
(`docs/archive/`, `docs/superpowers/`, `docs/ai/`) sind dev-only.


---

## Konzept & Architektur

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [fachkonzept-anlaufstelle.md](fachkonzept-anlaufstelle.md) | Domänenkonzept, Produktvision, Architekturentscheidungen, nicht-funktionale Anforderungen, DSGVO | Stakeholder, Architekten, Entwickler |
| [sprachleitlinie.md](sprachleitlinie.md) | Sprachleitlinie für UI und Handbuch — _Klientel → Person_, Terminologie-Matrix, Refactor-Priorisierung (Refs #604) | Entwickler, Doku-Autoren, Designer |
| [adr/](adr/) | Architecture Decision Records — warum die Architektur so aussieht, wie sie aussieht | Architekten, Entwickler |

---

## Betrieb & Nutzung

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [admin-guide.md](admin-guide.md) | Betriebshandbuch: Installation, Backup/Restore, Monitoring, MFA, Retention, DSGVO | IT-Admins |
| [user-guide.md](user-guide.md) | Benutzerhandbuch: Arbeitszentrale, Zeitstrom, Personen, Events, Suche, Export, Statistik, Rollen | Endanwender |
| [screenshots.md](screenshots.md) | Bebilderter Rundgang durch die Oberfläche (Demo-Daten, pseudonymisiert); EN: [screenshots.en.md](screenshots.en.md) | Stakeholder, Endanwender |
| [faq.md](faq.md) | Häufige Fragen — Betrieb, Troubleshooting, organisatorische Abläufe (synchron mit #474) | Admins, Endanwender |
| [ops-runbook.md](ops-runbook.md) | Betriebs-Runbook: Monitoring, Alerts, Cron-Jobs, RLS, Backup/Restore, Restore-Drill | IT-Admins, DevOps |
| [monitoring-guide.md](monitoring-guide.md) | `/health/`-Endpoint: Felder, HTTP-Status-Codes, Liveness vs. Detail, Anbindung an Uptime-/Monitoring-Tools (Refs #1071) | IT-Admins, DevOps |
| [disaster-recovery.md](disaster-recovery.md) | Totalverlust-Wiederherstellung: Off-Site-Backup beschaffen, Schlüssel-Eskrow, Restore auf frischem Host, RTO/RPO (Refs #1071) | IT-Admins, DevOps |

---

## Deployment & Release

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [dev-deployment.md](dev/dev-deployment.md) | Deployment der Dev-/Test-Umgebung | DevOps |
| [coolify-deployment.md](coolify-deployment.md) | Coolify-Deployment (unterstützte Alternative; primär ist plain Docker Compose, siehe [ADR-017](adr/017-deployment-topology.md)) | DevOps |
| `release-checklist.md` (dev-only) | Release-Prozess, Sanitize-Schritte, Hart-Ausschluss-Liste | Release-Manager |

---

## Sicherheit & Compliance

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [datenschutz.md](datenschutz.md) | DSGVO-Wegweiser: Artikel/Recht → ADR/Vorlage/Code/FAQ | alle (Auditor/Dev/Träger) |
| [glossar.md](glossar.md) | Datenschutz-/Compliance-Glossar — Vertiefung der Datenschutz-Begriffe (K-Anonymität, Retention, Pseudonymisierung …); allgemeines Domänen-Glossar in [Fachkonzept §14](fachkonzept-anlaufstelle.md#14-glossar), bilingual [en/glossary.md](en/glossary.md) | alle (Auditor/Dev/Träger) |
| [security-notes.md](security-notes.md) | Bewusste Security-Design-Entscheidungen (2FA, Fernet, RLS, Audit, Lockout) | Security-Officer, Entwickler |
| [threat-model.md](threat-model.md) | Sicherheitsmodell, Angriffsfläche, bekannte offene Lücken | Security-Officer, Auditoren |
| `audit-inventar.md` (dev-only) | Index aller Audit-Klassifizierungs-Codes (FND-\*, S-\*, Phasen, Sprints); die zugrunde liegenden Multi-AI-Audits liegen archiviert unter `docs/archive/` (dev-only) | Entwickler, Auditoren |
| [compliance/cra-einordnung.md](compliance/cra-einordnung.md) | CRA-Einordnung (EU 2024/2847) — FOSS-Scope, Gap-Check gegen Anhang I, Meldeprozess-Skizze, Wiedervorlage-Trigger (Refs #1077) | Maintainer, Auditoren, Träger |

---

## Entwicklung & Test

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Dev-Setup, Make-Targets, Coding Conventions, Architektur, PR-Prozess | Entwickler |
| [e2e-architecture.md](e2e-architecture.md) | E2E-Test-Infrastruktur: gunicorn, Playwright, Wait-Strategien, Fixtures | Entwickler, QA |
| [e2e-runbook.md](e2e-runbook.md) | E2E-Test-Ausführung: Server starten, Tests laufen lassen, Debugging | Entwickler, QA |
| testing/test-matrix-index.md | Test-Matrix-Übersicht (manuelle Testfälle + E2E-Mapping); Detail im Hub [testing/manual-test-matrix.md](testing/manual-test-matrix.md) (Front-matter + Anhänge) mit Sektions-Dateien `manual-test-matrix-a.md`…`-d.md`, Mutation-Testing in testing/mutation-testing.md | QA, Entwickler |
| testing/release-test-profiles.md | Release-Testprofile — manueller Rest neben dem automatisierten Gate (Refs #1081); Vorlage run-template.md → testing/runs/ | QA, Release-Manager |
| testing/mutation-survivors-baseline.md | Baseline überlebender Mutanten (Mutation-Testing,) | QA, Entwickler |
| [performance-budgets.md](performance-budgets.md) | Performance-Ziele und Budget-Grenzen (perf-nightly) | Entwickler, QA |
| `async-pdf-evaluation.md` (dev-only) | _Historisch_ — Evaluierung asynchroner PDF-Generierung, abgelöst durch [ADR-010](adr/010-sync-pdf-generation.md) | Entwickler, Architekten |

---

## DSGVO-Vorlagen

Vorlagen für den Datenschutz in [`src/core/dsgvo_templates/`](../src/core/dsgvo_templates/). Werden von der App aktiv genutzt — seit #784 ins App-Paket verschoben, damit das Docker-Image sie enthält.

| Dokument | Beschreibung |
|----------|-------------|
| [av-vertrag.md](../src/core/dsgvo_templates/av-vertrag.md) | Auftragsverarbeitungsvertrag |
| [dsfa.md](../src/core/dsgvo_templates/dsfa.md) | Datenschutz-Folgenabschätzung |
| [informationspflichten.md](../src/core/dsgvo_templates/informationspflichten.md) | Informationspflichten nach Art. 13/14 DSGVO |
| [toms.md](../src/core/dsgvo_templates/toms.md) | Technische und organisatorische Maßnahmen |
| [verarbeitungsverzeichnis.md](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | Verzeichnis von Verarbeitungstätigkeiten |
