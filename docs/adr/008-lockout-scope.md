# ADR-008: Login-Lockout-Scope: Username + IP

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** Tobias Nix

## Context

Brute-Force-Schutz gegen das Login-Endpoint hat zwei Versagens­modi, die sich gegenseitig ausschließen:

- **Nur per Username sperren:** Ein Angreifer kann jeden bekannten Account aus der Ferne aussperren (Denial of Service auf Mitarbeiter-Konten), ohne sein eigenes Verhalten zu ändern.
- **Nur per IP sperren:** Ein Angreifer hinter NAT/CG-NAT sperrt potenziell ein ganzes Netz aus; ein Angreifer mit Botnet umgeht die Sperre über IP-Wechsel.

Für den Anwendungs­kontext (kleine Einrichtungen, oft mit gemeinsamem NAT-Gateway) ist beides relevant: Mitarbeiter sollen nicht remote ausgesperrt werden können, aber gezieltes Raten gegen einen einzelnen Account muss gebremst werden.

## Decision

Lockout greift per **Tupel (Username, IP)** — nicht pro Username allein und nicht pro IP allein. Implementiert in [`src/core/services/security/login_lockout.py`](../../src/core/services/security/login_lockout.py).

- Schwelle und Sperrdauer sind in Settings konfigurierbar.
- Jede Lockout-Aktivierung erzeugt einen AuditLog-Eintrag (Action `security_violation`).
- Sperren werden nicht beim ersten erfolgreichen Login einer anderen IP zurückgesetzt — die Sperre ist auf das Tupel skopiert.

## Consequences

- **+** Ein Angreifer aus einer fremden IP sperrt den legitimen Mitarbeiter aus seinem Büro nicht aus.
- **+** Angreifer aus derselben IP werden nach n Fehlversuchen pro Account gebremst.
- **+** Brute-Force über viele Accounts aus *einer* IP wird sichtbar (Audit-Spur), auch wenn pro Account die Schwelle nicht erreicht wird — Detection-Hook für künftiges SIEM.
- **−** Kein Schutz gegen verteiltes Brute-Forcing mit IP-Wechsel pro Versuch — dafür braucht es Reverse-Proxy-Maßnahmen (Rate-Limiting, CAPTCHA).
- **−** Ops-Tooling muss Lockouts „pro Tupel" anzeigen können, nicht „pro User".

## Alternatives considered

- **`django-axes` mit reinem Username-Lockout:** Verworfen — Remote-DoS-Risiko.
- **Reines IP-Lockout:** Verworfen — NAT-Gateways treffen Unbeteiligte.
- **CAPTCHA nach n Fehlversuchen:** Sinnvoll als ergänzende Maßnahme, ersetzt aber den Lockout nicht. Kann später additiv ergänzt werden.

## References

- [`src/core/services/security/login_lockout.py`](../../src/core/services/security/login_lockout.py)
- [`docs/threat-model.md`](../threat-model.md) (Asset-Tabelle, Lockout-Scope)

## Nachtrag (2026-10-06): Ist-Stand von Schwelle, Achse, Audit und Ausnahmen (Refs #2449)

Die Decision oben beschreibt zwei Punkte anders, als sie umgesetzt sind; die Consequences unterschätzen einen Schutz. Der Ist-Stand:

- **Schwelle und Fenster sind Modulkonstanten, keine Settings.** `LOCKOUT_THRESHOLD = 10` und `LOCKOUT_WINDOW = timedelta(minutes=15)` stehen in [`login_lockout.py`](../../src/core/services/security/login_lockout.py) (Kommentarblock über den Konstanten); unter `src/anlaufstelle/settings/` gibt es keinen `LOCKOUT`-Schalter. Eine Änderung ist eine Codeänderung.
- **Es gibt keine feste Sperrdauer, das Fenster ist selbsterhaltend.** `is_locked` zählt die `LOGIN_FAILED`-Einträge im gleitenden 15-Minuten-Fenster. Ein Versuch gegen eine aktive Sperre wird selbst wieder als `LOGIN_FAILED` (mit `reason: "locked"`) geschrieben und zählt mit. Wer gegen die Sperre weiter anmeldet, hält sie aufrecht; sie endet 15 Minuten nach dem letzten Fehlversuch. Das ist gewollt (Kommentar „self-sustaining“ in `login_lockout.py`).
- **Audit-Aktionen:** Eine Sperre erzeugt keinen `security_violation`-Eintrag. Gezählt und protokolliert wird `LOGIN_FAILED`; ein Login mit korrektem Passwort gegen eine aktive Sperre schreibt `LOGIN_FAILED` mit `reason: "locked"` ([`CustomLoginView.form_valid`](../../src/core/views/auth.py)). Eine Entsperrung schreibt `LOGIN_UNLOCK` (`login_lockout.unlock`); Fehlversuche vor dem letzten `LOGIN_UNLOCK` zählen nicht mehr.
- **Achse (User, Quell-IP).** Mit Request-Kontext zählt `is_locked(user, ip_address=…)` nur die Fehlversuche derselben Quell-IP (#1446). Die IP kommt aus `get_client_ip` (`TRUSTED_PROXY_HOPS`). Aufrufe ohne IP (CLI, Anzeige, `unlock_managed_user`) zählen konservativ alle Fehlversuche des Kontos über alle IPs.
- **`super_admin` ist ausgenommen** (#1375): `is_locked` liefert für die Rolle immer `False`, `unlock` ist für sie ein No-op. Grund: Ein Angreifer soll das Break-Glass-Konto nicht gezielt aussperren können. Den Deckel gegen Raten bildet für diese Rolle allein das Username-Ratelimit (nächster Punkt); siehe auch [ADR-018 Nachtrag (2026-10-06)](018-rollenmodell-superadmin.md#nachtrag-2026-10-06-lockout-ausnahme-und-entsperrpfade-refs-2449).
- **Verteiltes Raten ist gedeckelt.** Der Consequence-Punkt „Kein Schutz gegen verteiltes Brute-Forcing mit IP-Wechsel“ trifft nicht mehr zu: `CustomLoginView.post` trägt neben dem IP-Limit (`5/m`) ein Username-Limit (`10/h`), das unabhängig von der Quell-IP je Benutzername zählt (#598, Decorator `ratelimit(key=_login_username_key, rate="10/h")` in [`auth.py`](../../src/core/views/auth.py)). Rotierende IPs umgehen es nicht. Es ist eine Drosselung (HTTP 429), kein Account-Lockout. In E2E ist es abgeschaltet (`RATELIMIT_ENABLE = False`, siehe [ADR-009](009-settings-inheritance.md)).
- **Entsperrwege:** `/system/lockouts/unlock/` (`super_admin`, mit Sudo), `/verwaltung/zugaenge/<pk>/entsperren/` (Einrichtungsverwaltung, mit Sudo), Self-Service per Mail-Token oder MFA-Backup-Code (`/account/recovery/…`, #869), Passwort-Reset und Einladungs-Setup (über `revoke_credentials`) sowie `manage.py unlock <username>`.
