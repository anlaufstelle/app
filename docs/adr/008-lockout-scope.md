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

Lockout greift per **Tupel (Username, IP)** — nicht pro Username allein und nicht pro IP allein. Implementiert in [`src/core/services/login_lockout.py`](././src/core/services/login_lockout.py).

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

- [`src/core/services/login_lockout.py`](././src/core/services/login_lockout.py)
- [`docs/threat-model.md`](./threat-model.md) (Asset-Tabelle, Lockout-Scope)
