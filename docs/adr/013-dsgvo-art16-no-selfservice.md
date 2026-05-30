# ADR-013: DSGVO Art. 16 ohne App-Self-Service

- **Status:** Accepted
- **Date:** 2026-04-15
- **Deciders:** Tobias Nix

## Context

DSGVO Art. 16 räumt betroffenen Personen das Recht auf **Berichtigung** unrichtiger personenbezogener Daten ein. Ein technischer Self-Service-Workflow („Klientel sieht ihre Daten und korrigiert direkt im Portal") wäre denkbar — wäre aber im Kontext niedrigschwelliger Einrichtungen fachlich falsch:

- **Identitätsprüfung**: Im niedrigschwelligen Bereich werden Klientel oft nur über Kontaktstufen geführt, teilweise pseudonymisiert. Ein Online-Self-Service müsste eine starke Identitätsbindung erzwingen, die der Niedrigschwelligkeit zuwiderläuft.
- **Fachliche Kontextualisierung**: „Falsche" Einträge sind häufig keine Faktenfehler, sondern abweichende Wahrnehmungen oder spätere Klärungen. Eine Berichtigung ohne Rücksprache mit der dokumentierenden Fachkraft entwertet die fachliche Verlässlichkeit der Akte.
- **Aufsichtspflicht**: Mitarbeiter und Leitung haben eine fachliche Verantwortung für die geführten Akten. Diese kann nicht an einen Self-Service-Klick delegiert werden.

Gleichzeitig ist Art. 16 ein verbindliches Recht. Es muss organisatorisch erfüllbar sein.

## Decision

Berichtigung nach DSGVO Art. 16 läuft **organisatorisch** über Mitarbeiter und Leitung der Einrichtung — nicht als App-Self-Service.

- Kein Klientel-Login, kein Self-Service-Formular für Datenkorrekturen.
- Berichtigungs­anfragen werden offline an die Einrichtung gestellt, fachlich geprüft und durch berechtigtes Personal in der App umgesetzt.
- Jede Berichtigung erzeugt einen AuditLog-Eintrag (ADR-007).
- Die Datenauskunft nach Art. 15 (Auskunft, nicht Berichtigung) ist unabhängig davon möglich — sie wird auf Anfrage von Mitarbeitern als PDF erzeugt (ADR-010).

## Consequences

- **+** Niedrigschwelligkeit bleibt erhalten — keine erzwungene Identitätsprüfung gegenüber Klientel.
- **+** Fachliche Verantwortung bleibt bei Mitarbeitern und Leitung.
- **+** AuditLog dokumentiert *wer* berichtigt hat, mit welcher fachlichen Begründung.
- **−** Keine technische Skalierung — bei großen Trägern könnte ein Self-Service Personal entlasten. Für die Zielgruppe (kleine Einrichtungen) nicht relevant.
- **−** Einrichtungen müssen den organisatorischen Prozess dokumentieren (Vorlagen in [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/)).

## Alternatives considered

- **Self-Service-Portal mit starker Authentifizierung:** Verworfen — siehe Context (Niedrigschwelligkeit, fachlicher Kontext).
- **Self-Service ohne Authentifizierung:** Nicht ernsthaft erwogen — Datenschutz-Anti-Pattern.
- **Hybrid: Self-Service-Anfrage, Mitarbeiter genehmigt:** Verworfen für die initiale Version — Komplexität ohne klaren Mehrwert gegenüber dem rein organisatorischen Weg.

## References

- [`src/core/dsgvo_templates/`](../../src/core/dsgvo_templates/)
- [`docs/admin-guide.md`](../admin-guide.md)
