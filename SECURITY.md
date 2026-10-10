# Security Policy

> **[English version below](#english)**

Anlaufstelle verarbeitet sensible Sozialdaten gemäß Art. 9 DSGVO. Sicherheitsmeldungen werden ernst genommen und mit hoher Priorität bearbeitet.

> **Sicherheitsmodell:** Ein expliziter Threat Model (STRIDE-Lite) liegt unter [`docs/threat-model.md`](docs/threat-model.md) — Assets, Akteure, Vertrauensgrenzen, Bedrohungen je Boundary mit Mitigation und offenen Lücken.

## Unterstützte Versionen

Sicherheitsupdates werden für die folgende Version bereitgestellt:

| Version   | Status                       |
|-----------|------------------------------|
| `0.99.x`  | Unterstützt (Pre-Release)    |
| `< 0.99`  | Nicht unterstützt            |

### Support- und End-of-Life-Regel

Vor `1.0` gilt eine **regelbasierte** Zusage **ohne Kalenderfristen**:

1. **Gepflegt wird ausschließlich die jeweils aktuelle Minor-Reihe** (`X.Y.x`). Sicherheitsfixes erscheinen als Patch-Release dieser Reihe.
2. **Eine Minor-Reihe erreicht ihr End of Life, sobald die nächste erscheint.** Es gibt keine Backports in ältere Reihen — der Sicherheitsfix ist das Update auf die aktuelle Reihe.
3. **Ältere Reihen** (`< X.Y`) erhalten keine Sicherheitsupdates, auch nicht auf Anfrage.
4. **Kein zugesicherter Mindest-Support-Zeitraum.** Die Release-Kadenz ist vor `1.0` bewusst hoch und nicht planbar; ein festes End-of-Support-Datum je Minor wäre eine Zusage, die ein Vor-1.0-Projekt mit einem Maintainer nicht verlässlich halten kann. Lieber eine ehrliche Regel als eine Frist, die reißt.

**Umstellung mit `1.0`:** Ab dem `1.0`-Release wird diese Regel durch **Datumsfristen** ersetzt — mindestens die aktuelle und die vorherige Minor-Version werden mit Sicherheitsupdates versorgt, und die Versionstabelle oben nennt je Reihe ein konkretes End-of-Support-Datum.

> **Konsequenz für Betreiber:** Dem aktuellen Minor folgen. Ein Stand, der zwei Reihen zurückliegt, ist ohne weitere Ankündigung ungepatcht. Verbindliche Auskunft ist die Versionstabelle oben, nicht das Release-Datum.

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

Anlaufstelle wird von einem Solo-Maintainer gepflegt; eine Vertretung ist derzeit nicht benannt (Verantwortliche, Bus-Faktor und Begründung: [MAINTAINERS.md](MAINTAINERS.md); Entscheidungs-/Eskalationsmodell: [GOVERNANCE.md](GOVERNANCE.md)). Die folgenden Zeiten sind **Richtwerte als Best-Effort-Ziel**, keine vertraglich zugesicherten Reaktionszeiten — sobald `1.0` erreicht ist und ein Maintainer-Team etabliert ist, werden sie verbindlicher gefasst:

| Schritt                       | Zielwert                       |
|-------------------------------|--------------------------------|
| Eingangsbestätigung           | ≤ 5 Werktage                   |
| Erste Einschätzung            | ≤ 10 Werktage                  |
| Korrektur (kritisch / hoch)   | priorisiert, in der Regel 14–30 Tage |
| Korrektur (mittel / niedrig)  | im nächsten regulären Release  |
| Veröffentlichung des Advisory | nach Patch + 7 Tage Schonfrist |

Wenn ein Zielwert nicht eingehalten werden kann, kommunizieren wir das transparent — bei kritischen Funden zuerst.

## Veröffentlichung von Advisories

Behobene Schwachstellen werden **koordiniert veröffentlicht**, nicht stillschweigend gepatcht:

1. **Bewertung** durch den Maintainer (eine Vertretung ist **derzeit nicht benannt** — Stand 2026-08-27, siehe [MAINTAINERS.md](MAINTAINERS.md); bei Ausfall der Hauptperson ruht die Bewertung) mit CVSS-v3.1-Basis-Score und Einstufung kritisch/hoch/mittel/niedrig — Ergebnis geht an die meldende Person.
2. **Entwurf** eines GitHub Security Advisory (GHSA) im Repository [`anlaufstelle/app`](https://github.com/anlaufstelle/app/security/advisories) mit betroffenen und gepatchten Versionen; bei kritisch/hoch wird über GitHub eine CVE-ID angefordert.
3. **Fix** als Patch-Release der aktuellen Minor-Reihe (siehe Support-Regel oben).
4. **Veröffentlichung** des Advisory nach Auslieferung des Patches plus 7 Tagen Schonfrist (siehe SLA-Tabelle oben), zusammen mit einem `Security`-Eintrag in [CHANGELOG.md](CHANGELOG.md), der die GHSA-/CVE-Nummer nennt.

Der operative Ablauf inklusive Zuständigkeiten steht in [`docs/ops-runbook.md` § 6.5c](docs/ops-runbook.md).

## Anerkennung (Hall of Fame)

Wer eine valide Schwachstelle verantwortungsvoll meldet, wird auf Wunsch im Veröffentlichungs-Advisory und in einer kurzen Hall-of-Fame-Sektion in [CHANGELOG.md](CHANGELOG.md) genannt. Bitte gib an, ob und wie du genannt werden möchtest.

## Sicherheits-Header und Middleware-Stack

Die Sicherheits-Header setzt die **Anwendung** selbst, auch hinter einem fremden Reverse-Proxy. Das mitgelieferte [`Caddyfile`](Caddyfile) setzt nur `X-Robots-Tag`. Welche Antwort welchen Header trägt, hängt davon ab, wo im Middleware-Stack (Liste unten) sie entsteht: Eine Antwort, die eine Middleware selbst erzeugt, durchläuft auf dem Rückweg nur die Einträge darüber. Die Spalte „Wann“ nennt die Bedingungen. Zwei Antworten entstehen in `SecurityMiddleware` selbst: die **HTTPS-Umleitung** (301 auf eine Klartext-Anfrage) und die **Host-Ablehnung** (400 auf eine Klartext-Anfrage, deren `Host` nicht in `ALLOWED_HOSTS` steht, außer `/health/`: `SecurityMiddleware` liest beim Bau der Umleitung den Host, Django wandelt den Fehler außerhalb von `SecurityMiddleware` in die 400 um). Die Host-Ablehnung trägt deshalb nur die `Content-Security-Policy`. Über HTTPS oder auf `/health/` entsteht die 400 für einen fremden Host weiter unten im Stack und trägt die Header wie jede andere Antwort. Fehlt ein Header auf einer Antwort, auf der er danach stehen müsste, ist das eine gültige Meldung:

| Header | Quelle | Setting | Wann |
|---|---|---|---|
| `Strict-Transport-Security` | `SecurityMiddleware` | `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD` | nur auf Anfragen über HTTPS (`request.is_secure()`, hinter dem Proxy über `SECURE_PROXY_SSL_HEADER`); nie auf Klartext-HTTP, also weder auf der HTTPS-Umleitung noch auf der Host-Ablehnung noch auf `/health/` über HTTP (`SECURE_REDIRECT_EXEMPT`) |
| `X-Content-Type-Options` | `SecurityMiddleware` | `SECURE_CONTENT_TYPE_NOSNIFF` | jede Antwort, auch die HTTPS-Umleitung; nicht die Host-Ablehnung |
| `Referrer-Policy` | `SecurityMiddleware` | `SECURE_REFERRER_POLICY` | jede Antwort, auch die HTTPS-Umleitung; nicht die Host-Ablehnung |
| `Cross-Origin-Opener-Policy` | `SecurityMiddleware` | `SECURE_CROSS_ORIGIN_OPENER_POLICY` | jede Antwort, auch die HTTPS-Umleitung; nicht die Host-Ablehnung |
| `X-Frame-Options` | `XFrameOptionsMiddleware` | `X_FRAME_OPTIONS` | jede Antwort, die die Middleware durchläuft, auch Wartungs-503 und statische Dateien; nicht die HTTPS-Umleitung und nicht die Host-Ablehnung, die beide in `SecurityMiddleware` darüber entstehen |
| `Content-Security-Policy` | `CSPMiddleware` (django-csp) | `CONTENT_SECURITY_POLICY`; auf `/admin-mgmt/` und auf der Demo eng gelockert, siehe [`docs/threat-model.md`](docs/threat-model.md) TB1 | jede Antwort, auch die HTTPS-Umleitung und die Host-Ablehnung |
| `Cache-Control: no-store, private` | `NoStoreCacheMiddleware` | — | nur für angemeldete Sitzungen und nur, wenn die View keinen eigenen `Cache-Control` setzt; nicht auf Antworten, die weiter oben entstehen (HTTPS-Umleitung, Host-Ablehnung, Wartungs-503, statische Dateien) |

Die Werte stehen in [`src/anlaufstelle/settings/prod.py`](src/anlaufstelle/settings/prod.py) (CSP in [`settings/base.py`](src/anlaufstelle/settings/base.py)). Der Middleware-Stack (`MIDDLEWARE` in [`settings/base.py`](src/anlaufstelle/settings/base.py), in Produktion unverändert übernommen) läuft für Anfragen von oben nach unten, für Antworten in umgekehrter Reihenfolge:

1. `core.middleware.admin_csp_relax.AdminCSPRelaxMiddleware` — lockert die CSP nur unter `/admin-mgmt/` (Admin-Oberfläche)
2. `core.middleware.demo_umami_csp_relax.DemoUmamiCSPRelaxMiddleware` — gibt nur auf der Demo nach dem Login den Analytics-Host in der CSP frei; sonst wirkungslos
3. `csp.middleware.CSPMiddleware` — setzt die `Content-Security-Policy`
4. `django.middleware.security.SecurityMiddleware` — HTTPS-Umleitung, HSTS, `nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`
5. `django.middleware.clickjacking.XFrameOptionsMiddleware` — `X-Frame-Options`
6. `core.middleware.maintenance.MaintenanceModeMiddleware` — antwortet im Wartungsmodus mit 503, vor Session und Anmeldung
7. `whitenoise.middleware.WhiteNoiseMiddleware` — liefert statische Dateien aus
8. `core.middleware.demo_session.DemoSessionMiddleware` — Sitzungen; erweitert Djangos `SessionMiddleware` und lässt auf der Demo das Sitzungscookie unangetastet, wenn eine Anfrage einen bereits ungültigen Sitzungsschlüssel trug und die Sitzung nur gelesen, unverändert und leer war (z. B. nach einem Bereichswechsel oder Ablauf); ausdrückliches Abmelden und alle anderen Installationen bleiben bei Django
9. `django.middleware.locale.LocaleMiddleware` — Sprache der Anfrage
10. `django.middleware.common.CommonMiddleware` — URL-Normalisierung
11. `django.middleware.csrf.CsrfViewMiddleware` — CSRF-Schutz
12. `django.contrib.auth.middleware.AuthenticationMiddleware` — angemeldete Person
13. `core.middleware.no_store_cache.NoStoreCacheMiddleware` — `Cache-Control: no-store` für angemeldete Sitzungen
14. `core.middleware.session_idle.SessionIdleRefreshMiddleware` — Inaktivitäts-Fenster und absolutes Sitzungs-Maximum
15. `django_otp.middleware.OTPMiddleware` — Zustand des zweiten Faktors
16. `core.middleware.htmx_session.HtmxSessionMiddleware` — Sitzungsablauf bei HTMX-Anfragen als `HX-Redirect`
17. `core.middleware.facility_scope.FacilityScopeMiddleware` — Einrichtung der Anfrage und PostgreSQL-Variable für die Row-Level-Security
18. `core.middleware.user_language.UserLanguageMiddleware` — Sprache aus dem Profil
19. `core.middleware.password_change.ForcePasswordChangeMiddleware` — erzwungener Passwortwechsel
20. `core.middleware.mfa.MFAEnforcementMiddleware` — erzwingt Einrichtung bzw. Bestätigung des zweiten Faktors
21. `django.contrib.messages.middleware.MessageMiddleware` — Hinweismeldungen
22. `core.middleware.demo_guard.DemoGuardMiddleware` — sperrt auf der Demo wenige Aktionen, die die Instanz für alle lahmlegen
23. `django_htmx.middleware.HtmxMiddleware` — HTMX-Anfragedaten

## Out of Scope

Folgende Punkte werden ohne Rücksprache nicht als Sicherheitslücke akzeptiert:

- Reine Best-Practice-Empfehlungen ohne nachgewiesene Auswirkung
- Self-XSS, der nur den eigenen Browser betrifft
- Denial-of-Service-Angriffe gegen den eigenen lokalen Dev-Server
- Brute-Force-Angriffe auf das Login (Rate-Limit ist aktiv und erwartet)
- Findings aus automatischen Scannern ohne ausnutzbaren Pfad

---

<a id="english"></a>

# Security Policy (English)

Anlaufstelle processes sensitive social data under GDPR Art. 9. Security reports are taken seriously and handled with high priority.

> **Threat model:** An explicit STRIDE-Lite threat model lives at [`docs/threat-model.md`](docs/threat-model.md) (German) — assets, actors, trust boundaries, and per-boundary STRIDE tables with mitigation and open gaps.

## Supported Versions

| Version   | Status                       |
|-----------|------------------------------|
| `0.99.x`  | Supported (pre-release)      |
| `< 0.99`  | Not supported                |

### Support and end-of-life rule

Before `1.0` the commitment is **rule-based, without calendar deadlines**:

1. **Only the current minor series** (`X.Y.x`) **is maintained.** Security fixes ship as a patch release of that series.
2. **A minor series reaches end of life as soon as the next one is released.** There are no backports to older series — the security fix *is* the update to the current series.
3. **Older series** (`< X.Y`) receive no security updates, not even on request.
4. **No guaranteed minimum support period.** Before `1.0` the release cadence is deliberately high and not predictable; a fixed end-of-support date per minor would be a promise a pre-1.0 project with a single maintainer cannot reliably keep. An honest rule beats a deadline that breaks.

**Switch at `1.0`:** From the `1.0` release on, this rule is replaced by **date-based deadlines** — at least the current and the previous minor version receive security updates, and the version table above states a concrete end-of-support date per series.

> **Consequence for operators:** follow the current minor. An installation two series behind is unpatched without further notice. The binding statement is the version table above, not the release date.

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

Anlaufstelle is maintained by a single person; no deputy is currently designated (maintainers, bus factor and rationale: [MAINTAINERS.md](MAINTAINERS.md); decision/escalation model: [GOVERNANCE.md](GOVERNANCE.md)). The targets below are **best-effort goals**, not contractually guaranteed response times — they will be tightened once `1.0` ships and a maintainer team is in place:

| Step                          | Target                                    |
|-------------------------------|-------------------------------------------|
| Acknowledgement               | ≤ 5 business days                         |
| Initial assessment            | ≤ 10 business days                        |
| Fix for critical / high       | prioritized, typically 14–30 days         |
| Fix for medium / low          | in the next regular release               |
| Public advisory               | patch + 7 days                            |

If a target cannot be met we will communicate transparently — critical findings first.

## Publishing advisories

Fixed vulnerabilities are **disclosed in a coordinated way**, not patched silently:

1. **Assessment** by the maintainer (no deputy is **currently designated** — status as of 2026-08-27, see [MAINTAINERS.md](MAINTAINERS.md); if the maintainer is unavailable, assessment is paused) with a CVSS v3.1 base score and a critical/high/medium/low rating — the result is sent to the reporter.
2. **Draft** of a GitHub Security Advisory (GHSA) in the [`anlaufstelle/app`](https://github.com/anlaufstelle/app/security/advisories) repository listing affected and patched versions; for critical/high we request a CVE ID through GitHub.
3. **Fix** as a patch release of the current minor series (see the support rule above).
4. **Publication** of the advisory after the patch ships plus a 7-day grace period (see the SLA table above), together with a `Security` entry in [CHANGELOG.md](CHANGELOG.md) naming the GHSA/CVE ID.

The operational procedure including responsibilities lives in [`docs/en/ops-runbook.md` § 6.5c](docs/en/ops-runbook.md).

## Hall of Fame

Reporters of valid issues will, on request, be credited in the published advisory and in a short hall-of-fame section of [CHANGELOG.md](CHANGELOG.md). Please tell us how you would like to be credited.

## Security headers and middleware stack

The **application** sets the security headers itself, also behind a third-party reverse proxy. The bundled [`Caddyfile`](Caddyfile) only sets `X-Robots-Tag`. Which response carries which header depends on where in the middleware stack (list below) it is produced: a response that a middleware produces itself passes only the entries above it on the way back. The "When" column names the conditions. Two responses are produced inside `SecurityMiddleware` itself: the **HTTPS redirect** (301 to a plaintext request) and the **host rejection** (400 to a plaintext request whose `Host` is not in `ALLOWED_HOSTS`, except `/health/`: `SecurityMiddleware` reads the host while building the redirect, and Django turns the error into the 400 outside of `SecurityMiddleware`). The host rejection therefore carries only the `Content-Security-Policy`. Over HTTPS or on `/health/`, the 400 for an unknown host is produced further down the stack and carries the headers like any other response. If a header is missing from a response on which it should be present according to that column, that is a valid report:

| Header | Source | Setting | When |
|---|---|---|---|
| `Strict-Transport-Security` | `SecurityMiddleware` | `SECURE_HSTS_SECONDS`, `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD` | only on requests over HTTPS (`request.is_secure()`, behind the proxy via `SECURE_PROXY_SSL_HEADER`); never on plaintext HTTP, so neither on the HTTPS redirect nor on the host rejection nor on `/health/` over HTTP (`SECURE_REDIRECT_EXEMPT`) |
| `X-Content-Type-Options` | `SecurityMiddleware` | `SECURE_CONTENT_TYPE_NOSNIFF` | every response, including the HTTPS redirect; not the host rejection |
| `Referrer-Policy` | `SecurityMiddleware` | `SECURE_REFERRER_POLICY` | every response, including the HTTPS redirect; not the host rejection |
| `Cross-Origin-Opener-Policy` | `SecurityMiddleware` | `SECURE_CROSS_ORIGIN_OPENER_POLICY` | every response, including the HTTPS redirect; not the host rejection |
| `X-Frame-Options` | `XFrameOptionsMiddleware` | `X_FRAME_OPTIONS` | every response that passes the middleware, including the maintenance 503 and static files; not the HTTPS redirect and not the host rejection, both produced inside `SecurityMiddleware` above it |
| `Content-Security-Policy` | `CSPMiddleware` (django-csp) | `CONTENT_SECURITY_POLICY`; narrowly relaxed under `/admin-mgmt/` and on the demo, see [`docs/threat-model.md`](docs/threat-model.md) TB1 | every response, including the HTTPS redirect and the host rejection |
| `Cache-Control: no-store, private` | `NoStoreCacheMiddleware` | — | only for authenticated sessions and only if the view sets no `Cache-Control` of its own; not on responses produced further up (HTTPS redirect, host rejection, maintenance 503, static files) |

The values live in [`src/anlaufstelle/settings/prod.py`](src/anlaufstelle/settings/prod.py) (CSP in [`settings/base.py`](src/anlaufstelle/settings/base.py)). The middleware stack (`MIDDLEWARE` in [`settings/base.py`](src/anlaufstelle/settings/base.py), used unchanged in production) runs top to bottom for requests and in reverse order for responses:

1. `core.middleware.admin_csp_relax.AdminCSPRelaxMiddleware` — relaxes the CSP only under `/admin-mgmt/` (admin interface)
2. `core.middleware.demo_umami_csp_relax.DemoUmamiCSPRelaxMiddleware` — allows the analytics host in the CSP only on the demo after login; no effect elsewhere
3. `csp.middleware.CSPMiddleware` — sets the `Content-Security-Policy`
4. `django.middleware.security.SecurityMiddleware` — HTTPS redirect, HSTS, `nosniff`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`
5. `django.middleware.clickjacking.XFrameOptionsMiddleware` — `X-Frame-Options`
6. `core.middleware.maintenance.MaintenanceModeMiddleware` — answers 503 in maintenance mode, before session and authentication
7. `whitenoise.middleware.WhiteNoiseMiddleware` — serves static files
8. `core.middleware.demo_session.DemoSessionMiddleware` — sessions; extends Django's `SessionMiddleware` and, on the demo, leaves the session cookie alone when a request carried an already invalid session key and the session was only read, unchanged and empty (for example after a scenario switch or expiry); explicit logout and all other installations stay with Django
9. `django.middleware.locale.LocaleMiddleware` — request language
10. `django.middleware.common.CommonMiddleware` — URL normalisation
11. `django.middleware.csrf.CsrfViewMiddleware` — CSRF protection
12. `django.contrib.auth.middleware.AuthenticationMiddleware` — authenticated user
13. `core.middleware.no_store_cache.NoStoreCacheMiddleware` — `Cache-Control: no-store` for authenticated sessions
14. `core.middleware.session_idle.SessionIdleRefreshMiddleware` — idle window and absolute session maximum
15. `django_otp.middleware.OTPMiddleware` — second-factor state
16. `core.middleware.htmx_session.HtmxSessionMiddleware` — session expiry on HTMX requests as `HX-Redirect`
17. `core.middleware.facility_scope.FacilityScopeMiddleware` — facility of the request and PostgreSQL variable for row-level security
18. `core.middleware.user_language.UserLanguageMiddleware` — language from the user profile
19. `core.middleware.password_change.ForcePasswordChangeMiddleware` — enforced password change
20. `core.middleware.mfa.MFAEnforcementMiddleware` — enforces second-factor setup or verification
21. `django.contrib.messages.middleware.MessageMiddleware` — flash messages
22. `core.middleware.demo_guard.DemoGuardMiddleware` — on the demo, blocks a few actions that would break the instance for everyone
23. `django_htmx.middleware.HtmxMiddleware` — HTMX request data

## Out of Scope

The following are typically not accepted as security issues without further context:

- Best-practice recommendations without demonstrated impact
- Self-XSS that only affects the reporter's own browser
- Denial-of-service against your own local development server
- Brute-force attacks on login (rate-limiting is active and expected)
- Automatic scanner findings without an exploitable path
