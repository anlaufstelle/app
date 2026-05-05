# Security Policy

> **[English version below](#english)**

Anlaufstelle verarbeitet sensible Sozialdaten gemäß Art. 9 DSGVO. Sicherheitsmeldungen werden ernst genommen und mit hoher Priorität bearbeitet.

> **Sicherheitsmodell:** Ein expliziter Threat Model (STRIDE-Lite) liegt unter [`docs/threat-model.md`](docs/threat-model.md) — Assets, Akteure, Vertrauensgrenzen, Bedrohungen je Boundary mit Mitigation und offenen Lücken.

## Unterstützte Versionen

Sicherheitsupdates werden für die folgende Version bereitgestellt:

| Version   | Status                       |
|-----------|------------------------------|
| `0.10.x`  | Unterstützt (Pre-Release)    |
| `< 0.10`  | Nicht unterstützt            |

Das Projekt befindet sich derzeit in einer Pre-Release-Phase. Sobald `1.0` veröffentlicht ist, werden mindestens die jeweils aktuelle und die vorherige Minor-Version mit Sicherheitsupdates versorgt.

## Schwachstellen melden

**Sicherheitsmeldungen bitte NICHT als öffentliches GitHub-Issue eröffnen.** Öffentlich gemeldete Schwachstellen gefährden Nutzerdaten anderer Einrichtungen, bevor wir eine Korrektur ausliefern können.

Bevorzugter Weg:

1. **GitHub Security Advisory** (privat): [Neue Meldung erstellen](https://github.com/anlaufstelle/app/security/advisories/new)
2. **E-Mail** an [kontakt@anlaufstelle.app](mailto:kontakt@anlaufstelle.app). Auf Wunsch stellen wir vor dem Austausch sensibler Details einen verschlüsselten Kanal bereit (PGP-Key auf Anfrage).

Bitte gib in deiner Meldung folgende Informationen an:

- **Beschreibung** der Schwachstelle und des potenziellen Risikos
- **Reproduktion**: möglichst detaillierte Schritte, betroffene Datei(en), Commit-SHA
- **Auswirkung**: welche Daten oder Funktionen sind betroffen
- **Vorschlag** zur Behebung, sofern bekannt
- **Kontaktdaten** für Rückfragen

Bei Bedarf unterstützen wir bei einer koordinierten Offenlegung (Coordinated Disclosure).

## Bearbeitungs-SLA (Best Effort, Pre-Release)

Anlaufstelle wird derzeit von einem Solo-Maintainer gepflegt. Die folgenden Zeiten sind **Richtwerte als Best-Effort-Ziel**, keine vertraglich zugesicherten Reaktionszeiten — sobald `1.0` erreicht ist und ein Maintainer-Team etabliert ist, werden sie verbindlicher gefasst:

| Schritt                       | Zielwert                       |
|-------------------------------|--------------------------------|
| Eingangsbestätigung           | ≤ 5 Werktage                   |
| Erste Einschätzung            | ≤ 10 Werktage                  |
| Korrektur (kritisch / hoch)   | priorisiert, in der Regel 14–30 Tage |
| Korrektur (mittel / niedrig)  | im nächsten regulären Release  |
| Veröffentlichung des Advisory | nach Patch + 7 Tage Schonfrist |

Wenn ein Zielwert nicht eingehalten werden kann, kommunizieren wir das transparent — bei kritischen Funden zuerst.

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

> **Threat model:** An explicit STRIDE-Lite threat model lives at [`docs/threat-model.md`](docs/threat-model.md) (German) — assets, actors, trust boundaries, and per-boundary STRIDE tables with mitigation and open gaps.

## Supported Versions

| Version   | Status                       |
|-----------|------------------------------|
| `0.10.x`  | Supported (pre-release)      |
| `< 0.10`  | Not supported                |

After the `1.0` release we will support at least the current and the previous minor version with security updates.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.** Public disclosure exposes user data in other installations before we can ship a fix.

Preferred channels:

1. **GitHub Security Advisory** (private): [Create a new report](https://github.com/anlaufstelle/app/security/advisories/new)
2. **E-mail** to [kontakt@anlaufstelle.app](mailto:kontakt@anlaufstelle.app). On request we will set up an encrypted channel before any sensitive details are exchanged (PGP key available on request).

Please include:

- A description of the issue and the potential impact
- Detailed reproduction steps, affected file(s), commit SHA
- Affected data or functionality
- A proposed fix, if known
- Your contact information

We support coordinated disclosure on request.

## Response SLA (best effort, pre-release)

Anlaufstelle is currently maintained by a single person. The targets below are **best-effort goals**, not contractually guaranteed response times — they will be tightened once `1.0` ships and a maintainer team is in place:

| Step                          | Target                                    |
|-------------------------------|-------------------------------------------|
| Acknowledgement               | ≤ 5 business days                         |
| Initial assessment            | ≤ 10 business days                        |
| Fix for critical / high       | prioritized, typically 14–30 days         |
| Fix for medium / low          | in the next regular release               |
| Public advisory               | patch + 7 days                            |

If a target cannot be met we will communicate transparently — critical findings first.

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
