# Security Policy

> **[English version below](#english)**

Anlaufstelle verarbeitet sensible Sozialdaten gemäß Art. 9 DSGVO. Sicherheitsmeldungen werden ernst genommen und mit hoher Priorität bearbeitet.

> **Sicherheitsmodell:** Ein expliziter Threat Model (STRIDE-Lite) liegt unter [`docs/threat-model.md`](docs/threat-model.md) — Assets, Akteure, Vertrauensgrenzen, Bedrohungen je Boundary mit Mitigation und offenen Lücken.

## Unterstützte Versionen

Sicherheitsupdates werden für die folgende Version bereitgestellt:

| Version | Status |
|-----------|------------------------------|
| `0.81.x` | Unterstützt (Pre-Release) |
| `< 0.81` | Nicht unterstützt |

### Support- und End-of-Life-Regel

Vor `1.0` gilt eine **regelbasierte** Zusage **ohne Kalenderfristen**:

1. **Gepflegt wird ausschließlich die jeweils aktuelle Minor-Reihe** (`X.Y.x`). Sicherheitsfixes erscheinen als Patch-Release dieser Reihe.
2. **Eine Minor-Reihe erreicht ihr End of Life, sobald die nächste erscheint.** Es gibt keine Backports in ältere Reihen — der Sicherheitsfix ist das Update auf die aktuelle Reihe.
3. **Ältere Reihen** (`< X.Y`) erhalten keine Sicherheitsupdates, auch nicht auf Anfrage.
4. **Kein zugesicherter Mindest-Support-Zeitraum.** Die Release-Kadenz ist vor `1.0` bewusst hoch und nicht planbar; ein festes End-of-Support-Datum je Minor wäre eine Zusage, die ein Vor-1.0-Projekt mit einem Maintainer nicht verlässlich halten kann. Lieber eine ehrliche Regel als eine Frist, die reißt.

**Umstellung mit `1.0`:** Ab dem `1.0`-Release wird diese Regel durch **Datumsfristen** ersetzt — mindestens die aktuelle und die vorherige Minor-Version werden mit Sicherheitsupdates versorgt, und die Versionstabelle oben nennt je Reihe ein konkretes End-of-Support-Datum.

> **Konsequenz für Betreiber:** Dem aktuellen Minor folgen. Ein Stand, der zwei Reihen zurückliegt, ist ohne weitere Ankündigung ungepatcht. Verbindliche Auskunft ist die Versionstabelle oben, nicht das Release-Datum.

Einordnung dieser Regel gegenüber Art. 13(8) der EU-Cyberresilienz-Verordnung: [`docs/compliance/cra-einordnung.md`](docs/compliance/cra-einordnung.md).

## Schwachstellen melden

**Sicherheitsmeldungen bitte NICHT als öffentliches GitHub-Issue eröffnen.** Öffentlich gemeldete Schwachstellen gefährden Nutzerdaten anderer Einrichtungen, bevor wir eine Korrektur ausliefern können.

Meldewege:

1. **E-Mail** an [kontakt@anlaufstelle.app](mailto:kontakt@anlaufstelle.app) — dieser Weg trägt immer. Auf Wunsch stellen wir vor dem Austausch sensibler Details einen verschlüsselten Kanal bereit (PGP-Key auf Anfrage).
2. **GitHub Security Advisory** (privat): [Neue Meldung erstellen](https://github.com/anlaufstelle/app/security/advisories/new) — setzt voraus, dass „Private Vulnerability Reporting" auf `anlaufstelle/app` aktiviert ist. Führt der Link ins Leere oder zeigt kein Formular, nimm bitte den E-Mail-Weg; es geht dadurch nichts verloren.

Bitte gib in deiner Meldung folgende Informationen an:

- **Beschreibung** der Schwachstelle und des potenziellen Risikos
- **Reproduktion**: möglichst detaillierte Schritte, betroffene Datei(en), Commit-SHA
- **Auswirkung**: welche Daten oder Funktionen sind betroffen
- **Vorschlag** zur Behebung, sofern bekannt
- **Kontaktdaten** für Rückfragen

Bei Bedarf unterstützen wir bei einer koordinierten Offenlegung (Coordinated Disclosure).

## Bearbeitungs-SLA (Best Effort, Pre-Release)

Anlaufstelle wird derzeit von einem Solo-Maintainer gepflegt (Verantwortliche, Bus-Faktor und die noch offene Vertretungsfrage: [MAINTAINERS.md](MAINTAINERS.md); Entscheidungs-/Eskalationsmodell: [GOVERNANCE.md](GOVERNANCE.md)). Die folgenden Zeiten sind **Richtwerte als Best-Effort-Ziel**, keine vertraglich zugesicherten Reaktionszeiten — sobald `1.0` erreicht ist und ein Maintainer-Team etabliert ist, werden sie verbindlicher gefasst:

| Schritt | Zielwert |
|-------------------------------|--------------------------------|
| Eingangsbestätigung | ≤ 5 Werktage |
| Erste Einschätzung | ≤ 10 Werktage |
| Korrektur (kritisch / hoch) | priorisiert, in der Regel 14–30 Tage |
| Korrektur (mittel / niedrig) | im nächsten regulären Release |
| Veröffentlichung des Advisory | nach Patch + 7 Tage Schonfrist |

Wenn ein Zielwert nicht eingehalten werden kann, kommunizieren wir das transparent — bei kritischen Funden zuerst.

## Veröffentlichung von Advisories

Behobene Schwachstellen werden **koordiniert veröffentlicht**, nicht stillschweigend gepatcht:

1. **Bewertung** durch den Maintainer (bei Ausfall: die benannte Vertretung, [MAINTAINERS.md](MAINTAINERS.md)) mit CVSS-v3.1-Basis-Score und Einstufung kritisch/hoch/mittel/niedrig — Ergebnis geht an die meldende Person.
2. **Entwurf** eines GitHub Security Advisory (GHSA) im Repository [`anlaufstelle/app`](https://github.com/anlaufstelle/app/security/advisories) mit betroffenen und gepatchten Versionen; bei kritisch/hoch wird über GitHub eine CVE-ID angefordert.
3. **Fix** als Patch-Release der aktuellen Minor-Reihe (siehe Support-Regel oben).
4. **Veröffentlichung** des Advisory nach Auslieferung des Patches plus 7 Tagen Schonfrist (siehe SLA-Tabelle oben), zusammen mit einem `Security`-Eintrag in [CHANGELOG.md](CHANGELOG.md), der die GHSA-/CVE-Nummer nennt.

Der operative Ablauf inklusive Zuständigkeiten und der zusätzlichen behördlichen Meldefristen bei **aktiv ausgenutzten** Schwachstellen steht in [`docs/ops-runbook.md` § 6.5c](docs/ops-runbook.md) und [`docs/compliance/cra-einordnung.md` § 3](docs/compliance/cra-einordnung.md).

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

| Version | Status |
|-----------|------------------------------|
| `0.81.x` | Supported (pre-release) |
| `< 0.81` | Not supported |

### Support and end-of-life rule

Before `1.0` the commitment is **rule-based, without calendar deadlines**:

1. **Only the current minor series** (`X.Y.x`) **is maintained.** Security fixes ship as a patch release of that series.
2. **A minor series reaches end of life as soon as the next one is released.** There are no backports to older series — the security fix *is* the update to the current series.
3. **Older series** (`< X.Y`) receive no security updates, not even on request.
4. **No guaranteed minimum support period.** Before `1.0` the release cadence is deliberately high and not predictable; a fixed end-of-support date per minor would be a promise a pre-1.0 project with a single maintainer cannot reliably keep. An honest rule beats a deadline that breaks.

**Switch at `1.0`:** From the `1.0` release on, this rule is replaced by **date-based deadlines** — at least the current and the previous minor version receive security updates, and the version table above states a concrete end-of-support date per series.

> **Consequence for operators:** follow the current minor. An installation two series behind is unpatched without further notice. The binding statement is the version table above, not the release date.

How this rule relates to Art. 13(8) of the EU Cyber Resilience Act: [`docs/compliance/cra-einordnung.md`](docs/compliance/cra-einordnung.md) (German).

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.** Public disclosure exposes user data in other installations before we can ship a fix.

Reporting channels:

1. **E-mail** to [kontakt@anlaufstelle.app](mailto:kontakt@anlaufstelle.app) — this channel always works. On request we will set up an encrypted channel before any sensitive details are exchanged (PGP key available on request).
2. **GitHub Security Advisory** (private): [Create a new report](https://github.com/anlaufstelle/app/security/advisories/new) — requires "Private vulnerability reporting" to be enabled on `anlaufstelle/app`. If the link leads nowhere or shows no form, please use e-mail instead; nothing is lost that way.

Please include:

- A description of the issue and the potential impact
- Detailed reproduction steps, affected file(s), commit SHA
- Affected data or functionality
- A proposed fix, if known
- Your contact information

We support coordinated disclosure on request.

## Response SLA (best effort, pre-release)

Anlaufstelle is currently maintained by a single person (maintainers, bus factor and the still-open deputy question: [MAINTAINERS.md](MAINTAINERS.md); decision/escalation model: [GOVERNANCE.md](GOVERNANCE.md)). The targets below are **best-effort goals**, not contractually guaranteed response times — they will be tightened once `1.0` ships and a maintainer team is in place:

| Step | Target |
|-------------------------------|-------------------------------------------|
| Acknowledgement | ≤ 5 business days |
| Initial assessment | ≤ 10 business days |
| Fix for critical / high | prioritized, typically 14–30 days |
| Fix for medium / low | in the next regular release |
| Public advisory | patch + 7 days |

If a target cannot be met we will communicate transparently — critical findings first.

## Publishing advisories

Fixed vulnerabilities are **disclosed in a coordinated way**, not patched silently:

1. **Assessment** by the maintainer (or, if unavailable, the designated deputy, [MAINTAINERS.md](MAINTAINERS.md)) with a CVSS v3.1 base score and a critical/high/medium/low rating — the result is sent to the reporter.
2. **Draft** of a GitHub Security Advisory (GHSA) in the [`anlaufstelle/app`](https://github.com/anlaufstelle/app/security/advisories) repository listing affected and patched versions; for critical/high we request a CVE ID through GitHub.
3. **Fix** as a patch release of the current minor series (see the support rule above).
4. **Publication** of the advisory after the patch ships plus a 7-day grace period (see the SLA table above), together with a `Security` entry in [CHANGELOG.md](CHANGELOG.md) naming the GHSA/CVE ID.

The operational procedure including responsibilities and the additional regulatory reporting deadlines for **actively exploited** vulnerabilities lives in [`docs/ops-runbook.md` § 6.5c](docs/ops-runbook.md) and [`docs/compliance/cra-einordnung.md` § 3](docs/compliance/cra-einordnung.md) (both German).

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
