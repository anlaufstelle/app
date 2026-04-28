# Security Policy

> **[English version below](#english)**

Anlaufstelle verarbeitet sensible Sozialdaten gemäß Art. 9 DSGVO. Sicherheitsmeldungen werden ernst genommen und mit hoher Priorität bearbeitet.

## Unterstützte Versionen

Sicherheitsupdates werden für die folgende Version bereitgestellt:

| Version  | Status                |
|----------|-----------------------|
| `0.9.x`  | Unterstützt (Pre-Release) |
| `< 0.9`  | Nicht unterstützt    |

Das Projekt befindet sich derzeit in einer Pre-Release-Phase. Sobald `1.0` veröffentlicht ist, werden mindestens die jeweils aktuelle und die vorherige Minor-Version mit Sicherheitsupdates versorgt.

## Schwachstellen melden

**Sicherheitsmeldungen bitte NICHT als öffentliches GitHub-Issue eröffnen.** Öffentlich gemeldete Schwachstellen gefährden Nutzerdaten anderer Einrichtungen, bevor wir eine Korrektur ausliefern können.

Bevorzugter Weg:

1. **GitHub Security Advisory** (privat): [Neue Meldung erstellen](https://github.com/tobiasnix/anlaufstelle/security/advisories/new)
2. **E-Mail** mit verschlüsselter Übertragung an die Maintainer (Kontakt siehe [README.md](README.md)).

Bitte gib in deiner Meldung folgende Informationen an:

- **Beschreibung** der Schwachstelle und des potenziellen Risikos
- **Reproduktion**: möglichst detaillierte Schritte, betroffene Datei(en), Commit-SHA
- **Auswirkung**: welche Daten oder Funktionen sind betroffen
- **Vorschlag** zur Behebung, sofern bekannt
- **Kontaktdaten** für Rückfragen

Bei Bedarf unterstützen wir bei einer koordinierten Offenlegung (Coordinated Disclosure).

## Bearbeitungs-SLA

Wir bemühen uns um folgende Reaktionszeiten:

| Schritt                       | Zeit          |
|-------------------------------|---------------|
| Eingangsbestätigung           | 3 Werktage    |
| Erste Einschätzung            | 7 Werktage    |
| Korrektur (kritisch / hoch)   | 14 Tage       |
| Korrektur (mittel / niedrig)  | 30 Tage       |
| Veröffentlichung des Advisory | nach Patch + 7 Tage Schonfrist |

Bei Pre-Release-Status kann die tatsächliche Bearbeitungszeit von der angegebenen abweichen — wir werden in dem Fall transparent kommunizieren.

## Anerkennung (Hall of Fame)

Wer eine valide Schwachstelle verantwortungsvoll meldet, wird auf Wunsch im Veröffentlichungs-Advisory und in einer kurzen Hall-of-Fame-Sektion in [CHANGELOG.md](CHANGELOG.md) genannt. Bitte gib an, ob und wie du genannt werden möchtest.

## Out of Scope

Folgende Punkte werden ohne Rücksprache nicht als Sicherheitslücke akzeptiert:

- Reine Best-Practice-Empfehlungen ohne nachgewiesene Auswirkung
- Self-XSS, der nur den eigenen Browser betrifft
- Denial-of-Service-Angriffe gegen den eigenen lokalen Dev-Server
- Brute-Force-Angriffe auf das Login (Rate-Limit ist aktiv und erwartet)
- Fehlende Header in Response-Pfaden, die ohnehin durch Caddy gesetzt werden
- Findings aus automatischen Scannern ohne ausnutzbaren Pfad

---

<a id="english"></a>

# Security Policy (English)

Anlaufstelle processes sensitive social data under GDPR Art. 9. Security reports are taken seriously and handled with high priority.

## Supported Versions

| Version  | Status                       |
|----------|------------------------------|
| `0.9.x`  | Supported (pre-release)      |
| `< 0.9`  | Not supported                |

After the `1.0` release we will support at least the current and the previous minor version with security updates.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.** Public disclosure exposes user data in other installations before we can ship a fix.

Preferred channels:

1. **GitHub Security Advisory** (private): [Create a new report](https://github.com/tobiasnix/anlaufstelle/security/advisories/new)
2. **Encrypted e-mail** to the maintainers (contact details in [README.md](README.md))

Please include:

- A description of the issue and the potential impact
- Detailed reproduction steps, affected file(s), commit SHA
- Affected data or functionality
- A proposed fix, if known
- Your contact information

We support coordinated disclosure on request.

## Response SLA

| Step                          | Target            |
|-------------------------------|-------------------|
| Acknowledgement               | 3 business days   |
| Initial assessment            | 7 business days   |
| Fix for critical / high       | 14 days           |
| Fix for medium / low          | 30 days           |
| Public advisory               | patch + 7 days    |

While the project is in pre-release status, real response times may differ — we will communicate transparently in that case.

## Hall of Fame

Reporters of valid issues will, on request, be credited in the published advisory and in a short hall-of-fame section of [CHANGELOG.md](CHANGELOG.md). Please tell us how you would like to be credited.

## Out of Scope

The following are typically not accepted as security issues without further context:

- Best-practice recommendations without demonstrated impact
- Self-XSS that only affects the reporter's own browser
- Denial-of-service against your own local development server
- Brute-force attacks on login (rate-limiting is active and expected)
- Missing headers on response paths that are added by Caddy in production
- Automatic scanner findings without an exploitable path
