# Dokumentation — Übersicht

> **[English Documentation](en/README.md)**

Dieses Verzeichnis enthält die gesamte Projektdokumentation. Interne Artefakte
(`docs/archive/`, `docs/superpowers/`, `docs/ai/`, `docs/dev/` und `docs/testing/` außer der
manuellen Testmatrix und den Assertion-Konventionen) sind dev-only.


---

## Konventionen

Die Doku beschreibt den **Ist-Stand**. Historie gehört ins [CHANGELOG.md](../CHANGELOG.md) und in die [ADRs](adr/) — v1.0.0 ist die erste öffentliche Version, es gibt keine Installationen auf Vor-1.0-Ständen, für die eine Migrationsanleitung nötig wäre. Entsprechend: keine „seit vX.Y"-Marker in Fließtext, keine Migrationskästen für Stände vor 1.0 (Refs #2205).

---

## Konzept & Architektur

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [architecture.md](architecture.md) | Architektur-Überblick als Wegweiser: Schichten (Views → Services → Models), Sicherheitsarchitektur in Kurzform, No-Code-Konfigurationskern, Offline-PWA, Test-Landschaft — mit Verweisen in die vertiefenden Dokumente (Refs #1062) | Neue Mitwirkende, Entwickler |
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
| [faq.md](faq.md) | Maßgebliche Quelle für häufige Fragen zu Betrieb, Troubleshooting und organisatorischen Abläufen | Admins, Endanwender |
| [packages.md](packages.md) | Konfigurationspakete (YAML, Schema v1): Formatreferenz, Loader-Semantik, `manage.py load_package`, Pflegeregeln (Refs #1058, [ADR-037](adr/037-konfigurationspakete-dynamische-felder.md)) | IT-Admins, Konfigurations-Autor:innen |
| [packages-beratungsstelle.md](packages-beratungsstelle.md) | Beratungsstelle-Paket: acht Typen, Sensitivitätsmatrix, Berichte und Betreiber-Empfehlungen (Refs #2145) | IT-Admins, Einrichtungsleitung |
| [packages-awareness.md](packages-awareness.md) | Awareness-Paket: sieben Typen, Sensitivitätsmatrix, Berichte und Betreiber-Empfehlungen (Refs #2137) | IT-Admins, Awareness-Koordination |
| [packages-frauenhaus.md](packages-frauenhaus.md) | Frauenhaus-Paket: acht Typen, Sensitivitätsmatrix, Berichte und Betreiber-Empfehlungen für den 24/7-Betrieb (Refs #2138) | IT-Admins, Einrichtungsleitung |
| [packages-jugendarbeit.md](packages-jugendarbeit.md) | Jugendarbeit-Paket: acht Typen, Sensitivitätsmatrix, Berichte und Betreiber-Empfehlungen (Refs #2139) | IT-Admins, Anwendungsbetreuung |
| [ops-runbook.md](ops-runbook.md) | Betriebs-Runbook: Deployment, Rollback, Cron-Jobs, Log-Analyse, Troubleshooting, RLS, Backup/Restore, Restore-Drill | IT-Admins, DevOps |
| [facility-export-schema.md](facility-export-schema.md) | Facility-Gesamtexport (JSON, Schema v1): Feldliste, Versionierungsregel, Papierkorb-/Anonymisierungs-Grenzen, Anhang-Hashes, `manage.py export_facility` (Refs #1060) | IT-Admins, Entwickler |
| [monitoring-guide.md](monitoring-guide.md) | `/health/`-Endpoint: Felder, HTTP-Status-Codes, Liveness vs. Detail, Anbindung an Uptime-/Monitoring-Tools (Refs #1071) | IT-Admins, DevOps |
| [disaster-recovery.md](disaster-recovery.md) | Totalverlust-Wiederherstellung: Off-Site-Backup beschaffen, Schlüssel-Eskrow, Restore auf frischem Host, RTO/RPO (Refs #1071) | IT-Admins, DevOps |

---

## Deployment & Release

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| `dev-deployment.md` (dev-only) | Deployment der Dev-/Test-Umgebung | DevOps |
| `release-checklist.md` (dev-only) | Release-Prozess, Sanitize-Schritte, Hart-Ausschluss-Liste | Release-Manager |

---

## Sicherheit & Compliance

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [datenschutz.md](datenschutz.md) | DSGVO-Wegweiser: Artikel/Recht → ADR/Vorlage/Code/FAQ | alle (Auditor/Dev/Träger) |
| [glossar.md](glossar.md) | Datenschutz-/Compliance-Glossar — Vertiefung der Datenschutz-Begriffe (K-Anonymität, Retention, Pseudonymisierung …); allgemeines Domänen-Glossar in [Fachkonzept §14](fachkonzept-anlaufstelle.md#14-glossar), bilingual [en/glossary.md](en/glossary.md) | alle (Auditor/Dev/Träger) |
| [accessibility-audit.md](accessibility-audit.md) | Nachweis der Barrierefreiheitsprüfung nach WCAG 2.1 Stufe AA mit automatisierten Befunden, Korrekturen und Screenreader-Prüfprotokoll | Auditoren, Entwickler, QA |
| [security-notes.md](security-notes.md) | Bewusste Security-Design-Entscheidungen (2FA, Fernet, RLS, Audit, Lockout) | Security-Officer, Entwickler |
| [threat-model.md](threat-model.md) | Sicherheitsmodell, Angriffsfläche, bekannte offene Lücken | Security-Officer, Auditoren |
| `audit-inventar.md` (dev-only) | Index aller Audit-Klassifizierungs-Codes (FND-\*, S-\*, Phasen, Sprints); die zugrunde liegenden Multi-AI-Audits liegen archiviert unter `docs/archive/` (dev-only) | Entwickler, Auditoren |

---

## Entwicklung & Test

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Dev-Setup, Make-Targets, Coding Conventions, Architektur, PR-Prozess | Entwickler |
| [reporting-plugins.md](reporting-plugins.md) | Berichts-Backends als Plugin-System: API-Referenz (`DatasetBackend`, `DatasetResult`), „Eigenes Backend in fünf Schritten", Discovery, Snapshot-Vertrag, k-Anonymität, Export-Formate, Test-Checkliste (Refs #1061, [ADR-038](adr/038-reporting-plugin-backends.md)); EN: [en/reporting-plugins.md](en/reporting-plugins.md) | Entwickler |
| [e2e-architecture.md](e2e-architecture.md) | E2E-Test-Infrastruktur: gunicorn, Playwright, Wait-Strategien, Fixtures | Entwickler, QA |
| [e2e-runbook.md](e2e-runbook.md) | E2E-Test-Ausführung: Server starten, Tests laufen lassen, Debugging | Entwickler, QA |
| [testing/assertion-conventions.md](testing/assertion-conventions.md) | Konventionen für präzise Assertions zu Exceptions, Logs, eingeschränkten Saves und Query-Budgets | Entwickler, QA |
| [testing/manual-test-matrix.md](testing/manual-test-matrix.md) | Manuelle Testmatrix: Hub (Front-matter + Anhänge) mit den Sektions-Dateien [`manual-test-matrix-a.md`](testing/manual-test-matrix-a.md), [`-b.md`](testing/manual-test-matrix-b.md), [`-c.md`](testing/manual-test-matrix-c.md) und [`-d.md`](testing/manual-test-matrix-d.md) | QA, Entwickler |
| `testing/test-matrix-index.md` (dev-only) | Test-Matrix-Übersicht (manuelle Testfälle + E2E-Mapping), Mutation-Testing in `testing/mutation-testing.md` (dev-only) | QA, Entwickler |
| `testing/release-test-profiles.md` (dev-only) | Release-Testprofile — manueller Rest neben dem automatisierten Gate (Refs #1081); Vorlage `run-template.md` → `testing/runs/` | QA, Release-Manager |
| `testing/abnahme-0.98.md` (dev-only) | Gerüst der konsolidierten Abnahmematrix für release-0.98 — Quellenabbildung, Spaltenschema, neue Prüfbereiche (Refs #2170) | QA, Release-Manager |
| `testing/mutation-survivors-baseline.md` (dev-only) | Baseline überlebender Mutanten (Mutation-Testing) | QA, Entwickler |
| [performance-budgets.md](performance-budgets.md) | Performance-Ziele und Budget-Grenzen | Entwickler, QA |
| `async-pdf-evaluation.md` (dev-only) | _Historisch_ — Evaluierung asynchroner PDF-Generierung, abgelöst durch [ADR-010](adr/010-sync-pdf-generation.md) | Entwickler, Architekten |

---

## DSGVO-Vorlagen

Vorlagen für den Datenschutz in [`src/core/dsgvo_templates/`](../src/core/dsgvo_templates/). Werden von der App aktiv genutzt und liegen im App-Paket, damit das Docker-Image sie enthält (#784).

| Dokument | Beschreibung |
|----------|-------------|
| [av-vertrag.md](../src/core/dsgvo_templates/av-vertrag.md) | Auftragsverarbeitungsvertrag |
| [dsfa.md](../src/core/dsgvo_templates/dsfa.md) | Datenschutz-Folgenabschätzung |
| [informationspflichten.md](../src/core/dsgvo_templates/informationspflichten.md) | Informationspflichten nach Art. 13/14 DSGVO |
| [loeschkonzept.md](../src/core/dsgvo_templates/loeschkonzept.md) | Löschkonzept mit Fristen für aktive Daten und Sicherungsstände |
| [mitarbeiter_informationspflichten.md](../src/core/dsgvo_templates/mitarbeiter_informationspflichten.md) | Informationspflichten für Beschäftigte nach Art. 13/14 DSGVO und § 26 BDSG |
| [toms.md](../src/core/dsgvo_templates/toms.md) | Technische und organisatorische Maßnahmen |
| [verarbeitungsverzeichnis.md](../src/core/dsgvo_templates/verarbeitungsverzeichnis.md) | Verzeichnis von Verarbeitungstätigkeiten |
