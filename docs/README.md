# Dokumentation — Übersicht

> **[English Documentation](en/README.md)**

Dieses Verzeichnis enthält die gesamte Projektdokumentation.

---

## Konzept

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [fachkonzept-anlaufstelle.md](fachkonzept-anlaufstelle.md) | Domänenkonzept, Produktvision, Architekturentscheidungen, nicht-funktionale Anforderungen, DSGVO | Stakeholder, Architekten, Entwickler |

---

## Betrieb & Nutzung

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [admin-guide.md](admin-guide.md) | Betriebshandbuch: Installation, Backup, Monitoring, DSGVO | IT-Admins |
| [user-guide.md](user-guide.md) | Benutzerhandbuch: Zeitstrom, Klientel, Events, Suche, Export, Rollen | Endanwender |

---

## Entwicklung

| Dokument | Beschreibung | Zielgruppe |
|----------|-------------|------------|
| [e2e-architecture.md](e2e-architecture.md) | E2E-Test-Infrastruktur: gunicorn, Playwright, Wait-Strategien, Fixtures | Entwickler, QA |
| [e2e-runbook.md](e2e-runbook.md) | E2E-Test-Ausführung: Server starten, Tests laufen lassen, Debugging | Entwickler, QA |
| [async-pdf-evaluation.md](async-pdf-evaluation.md) | Evaluierung asynchroner Task-Queues für WeasyPrint-PDF-Generierung. | Entwickler, Architekten |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Dev-Setup, Make-Targets, Coding Conventions, Architektur, PR-Prozess | Entwickler |

---

## DSGVO-Vorlagen

Vorlagen für den Datenschutz in [`dsgvo-templates/`](dsgvo-templates/). Werden von der App aktiv genutzt.

| Dokument | Beschreibung |
|----------|-------------|
| [av-vertrag.md](dsgvo-templates/av-vertrag.md) | Auftragsverarbeitungsvertrag |
| [dsfa.md](dsgvo-templates/dsfa.md) | Datenschutz-Folgenabschätzung |
| [informationspflichten.md](dsgvo-templates/informationspflichten.md) | Informationspflichten nach Art. 13/14 DSGVO |
| [toms.md](dsgvo-templates/toms.md) | Technische und organisatorische Maßnahmen |
| [verarbeitungsverzeichnis.md](dsgvo-templates/verarbeitungsverzeichnis.md) | Verzeichnis von Verarbeitungstätigkeiten |

---

