# ADR-026: Rate-Limiting-Strategie

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Tobias Nix
- **Refs:** #1024, #1016 (Shared-Cache), [ADR-008](008-lockout-scope.md) (Login-Lockout)
- **Verschriftlicht:** 2026-06-20 aus dem Code als ADR (Refs #1071 §E, #1101).

## Context

Anlaufstelle drosselt Endpunkte mit [`django-ratelimit`](https://github.com/jsocol/django-ratelimit). Die Schwellen und die Politik dahinter (welcher Schlüssel, welche HTTP-Methode, welche Schicht) sind im Code verstreut — drei benannte Tarife in [`src/core/constants.py`](../../src/core/constants.py) plus endpunktspezifische Limits an Login, Suche, Health und CSP-Report — aber die **Rationale** war nirgends festgehalten. Diese ADR schreibt die bestehende Entscheidung fest, ohne das Verhalten zu ändern.

## Decision

Rate-Limiting liegt in der **Anwendung**, nicht am Edge, und folgt zwei Achsen — **Schlüssel** (wer wird gezählt) und **Methode** (was wird gezählt):

**1. Schlüssel.** Authentifizierte fachliche Endpunkte zählen pro `user` (`key="user"`) — eine einzelne Person/ein Account kann andere nicht aushungern, und hinter geteiltem Büro-NAT wird nicht das ganze Team bestraft. Vor-/peri-authentifizierte und unauthentifizierte Endpunkte zählen pro `ip`: Login `5/m`/IP **plus** `10/h` pro Benutzername ([`auth.py`](../../src/core/views/auth.py)), CSP-Report `10/m`/IP ([`csp_report.py`](../../src/core/views/csp_report.py)), Health `120/m`/IP ([`health.py`](../../src/core/views/health.py)).

**2. Methode.** Limits zählen nur die teure Methode: Mutationen auf `method="POST"`, Lesen/Suche auf `method="GET"`. Normale Seitennavigation (GET) verbraucht kein Limit-Budget. Der Bulk-Tarif deckt beides ab — POST-Massenaktionen **und** wenige schwere GET-Exporte (DSGVO-Auskunft, Offline-Snapshot-Download).

**Drei benannte Pro-User-Stundentarife** (Sliding Window, [`constants.py`](../../src/core/constants.py)):

| Konstante | Rate | Klasse | Beispiel-Endpunkte |
|-----------|------|--------|--------------------|
| `RATELIMIT_BULK_ACTION` | `30/h` | Bulk-/teure Aktionen (fächert über viele Zeilen oder erzeugt einen großen Export) | POST: [`workitem_bulk.py`](../../src/core/views/workitem_bulk.py), Retention-Bulk ([`retention.py`](../../src/core/views/retention.py)); GET: DSGVO-Auskunft ([`dsgvo.py`](../../src/core/views/dsgvo.py)), Offline-Snapshot ([`offline.py`](../../src/core/views/offline.py)) |
| `RATELIMIT_MUTATION` | `60/h` | Einzelobjekt-Schreibzugriffe | [`cases.py`](../../src/core/views/cases.py), [`case_episodes.py`](../../src/core/views/case_episodes.py), [`event_deletion.py`](../../src/core/views/event_deletion.py) |
| `RATELIMIT_FREQUENT` | `120/h` | hochfrequente legitime Aktionen | [`workitem_actions.py`](../../src/core/views/workitem_actions.py), [`case_goals.py`](../../src/core/views/case_goals.py), Anhang-Download ([`attachments.py`](../../src/core/views/attachments.py)) |

Bulk ist am niedrigsten, weil ein Aufruf am meisten Arbeit auslöst; „frequent" am höchsten, weil es legitime kleinteilige UI-Interaktionen sind. Alle mit `block=True` → Überschreitung liefert **HTTP 429**, kein stilles Verwerfen.

**Shared Cache ist in Produktion Pflicht.** Die Zähler liegen im Django-Cache; Prod nutzt `DatabaseCache` mit `RATELIMIT_USE_CACHE="default"` ([`prod.py`](../../src/anlaufstelle/settings/prod.py)), damit die Zählung **prozessübergreifend** geteilt wird. Ein per-Worker-`LocMemCache` würde jedes Limit ver-N-fachen (ein Limit pro Gunicorn-Worker). Cache-Tabelle via Migration `0092` (Refs #1024/#1016, A5.1). In Test/E2E ist Rate-Limiting deterministisch abgeschaltet (`RATELIMIT_ENABLE=False`).

## Consequences

- **+** Pro-`user`-Keying ist fair unter geteiltem NAT; ein lauter Account trifft nur sich selbst.
- **+** POST-only-Messung koppelt Limits an tatsächliche Kosten — Lese-Navigation löst nie ein Limit aus.
- **+** Drei benannte Tarife machen die Absicht lesbar (bulk < mutation < frequent) statt magischer Zahlen pro View.
- **+** Shared `DatabaseCache` liefert korrekte Limits in Multi-Worker-Prod **ohne** neue Dependency (Redis-Vollausbau bleibt #795).
- **−** `DatabaseCache` kostet pro gedrosseltem Request einen DB-Roundtrip (akzeptiert ggü. Redis-Betriebsaufwand; bei Skalierung re-evaluieren → #795).
- **−** Pro-User-Limits schützen unauthentifizierte Endpunkte nicht; dort greifen IP-Limits (Login/Health/CSP), die gröber und NAT-empfindlich sind. Login ist zusätzlich durch [ADR-008](008-lockout-scope.md) (Lockout) abgesichert.
- **−** Schwellen sind globale Konstanten, nicht pro Facility tunebar (YAGNI; erst bei echtem Bedarf).

## Alternatives considered

- **Edge-/Caddy-Rate-Limit als primäre Schicht.** Verworfen: Caddy kann nur auf IP keyen — keine Pro-User-Granularität, bestraft Teams hinter geteiltem NAT. Das `caddy-ratelimit`-Modul ist im [`Caddyfile`](../../Caddyfile) als optionaler grober IP-Backstop dokumentiert, aber **nicht aktiviert**; der App-Layer ist autoritativ.
- **In-Process-`LocMemCache` für die Zähler.** Verworfen: per-Worker-Isolation ver-N-facht die Limits in Multi-Worker-Prod — genau der Bug, den der Shared-Cache behebt.
- **Redis für die Zähler.** Vertagt (#795): zusätzliches bewegliches Teil; `DatabaseCache` ist bei aktueller Last „gut genug".
- **Ad-hoc-Rate pro View überall.** Verworfen zugunsten dreier benannter Tarife für die häufigen Fälle; bespoke Raten bleiben nur, wo das Zugriffsmuster wirklich anders ist (Login, Suche, Health, CSP).

## References

- [`src/core/constants.py`](../../src/core/constants.py) — die drei Tarife (`RATELIMIT_BULK_ACTION`/`_MUTATION`/`_FREQUENT`)
- Decorators: [`workitem_bulk.py`](../../src/core/views/workitem_bulk.py), [`retention.py`](../../src/core/views/retention.py), [`dsgvo.py`](../../src/core/views/dsgvo.py), [`offline.py`](../../src/core/views/offline.py) (Bulk-Tarif); [`cases.py`](../../src/core/views/cases.py), [`case_episodes.py`](../../src/core/views/case_episodes.py), [`event_deletion.py`](../../src/core/views/event_deletion.py) (Mutation); [`workitem_actions.py`](../../src/core/views/workitem_actions.py), [`case_goals.py`](../../src/core/views/case_goals.py), [`attachments.py`](../../src/core/views/attachments.py) (Frequent); [`auth.py`](../../src/core/views/auth.py), [`search.py`](../../src/core/views/search.py), [`health.py`](../../src/core/views/health.py), [`csp_report.py`](../../src/core/views/csp_report.py) (endpunktspezifisch)
- [`src/anlaufstelle/settings/prod.py`](../../src/anlaufstelle/settings/prod.py) — `CACHES` (`DatabaseCache`) + `RATELIMIT_USE_CACHE`
- [`Caddyfile`](../../Caddyfile) — optionales `caddy-ratelimit`-Modul (auskommentiert)
- [ADR-008](008-lockout-scope.md) — Login-Lockout (Username + IP), komplementär zum Login-Rate-Limit
- Issues #1071 §E, #1101; Shared-Cache #1024/#1016
