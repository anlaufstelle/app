# ADR-009: Settings-Vererbung — E2E erbt von Dev, nicht Test

- **Status:** Accepted
- **Date:** 2026-03-20
- **Deciders:** Tobias Nix

## Context

Django-Projekte haben typischerweise drei Settings-Profile: `dev`, `test`, `prod`. Bei Anlaufstelle kam ein viertes hinzu — `e2e` für Playwright-getriebene End-to-End-Tests.

Naheliegend wäre, dass `e2e` von `test` erbt: beide sind „Test"-Kontexte. Genau das führte aber zu einer subtilen Falle:

- `test.py` setzt `PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]` für schnelle Test-Setups.
- Seed-Daten werden mit dem konfigurierten Hasher erzeugt.
- Wenn der E2E-Server mit `test`-Settings läuft, sind die Seed-Passwörter MD5-gehasht.
- In Produktion (`prod`) sind sie PBKDF2 — der Login-Flow im E2E-Test verhält sich anders als in Produktion.

Das Ziel von E2E ist explizit, **Produktions-nahe** zu testen. Eine Abweichung in Auth-Primitiven untergräbt die Aussagekraft.

## Decision

`anlaufstelle.settings.e2e` erbt von `anlaufstelle.settings.dev`, nicht von `test`. Damit:

- PBKDF2 als Hasher (wie in Produktion).
- Datenbank `anlaufstelle_e2e` (separate Instanz, kein Conflict mit Dev).
- E2E-Server läuft auf Port **8844**, klar abgegrenzt von Dev (8000).

Die Konsequenz im Test-Setup ist explizit dokumentiert in [`docs/e2e-architecture.md`](../e2e-architecture.md) und [`docs/e2e-runbook.md`](../e2e-runbook.md).

## Consequences

- **+** Login-, Session-, Lockout- und MFA-Flows verhalten sich in E2E exakt wie in Produktion.
- **+** Seed-Logins sind deterministisch und produktions-äquivalent.
- **−** Test-Setup für E2E ist langsamer (PBKDF2-Hashing beim Seed), als es theoretisch sein könnte. Akzeptabel — Setup läuft einmal pro Suite, nicht pro Test.
- **−** Entwickler müssen bei neuen Settings-Werten überlegen, in welche Schicht (`base` / `dev` / `e2e` / `test` / `prod`) sie gehören.

## Alternatives considered

- **`e2e` erbt von `test`:** Verworfen — siehe Context.
- **Seed-Passwörter explizit auf PBKDF2 zwingen, unabhängig vom Settings-Profil:** Funktioniert, aber kaschiert das eigentliche Problem (Test-Settings sind für E2E nicht passend) und macht die Seed-Logik komplexer.

## References

- [`src/anlaufstelle/settings/e2e.py`](../../src/anlaufstelle/settings/e2e.py)
- [`docs/e2e-architecture.md`](../e2e-architecture.md)

## Nachtrag (2026-10-06): E2E weicht bei Ratelimit und MFA-Pflicht ab (Refs #2499)

Die Consequences sagen, Lockout- und MFA-Flows verhielten sich in E2E „exakt wie in Produktion“. Das gilt für Passwort-Hashing, Session-Verhalten und den Account-Lockout selbst, aber nicht für zwei Schutzschichten. [`settings/e2e.py`](../../src/anlaufstelle/settings/e2e.py) schaltet sie bewusst ab:

- **`RATELIMIT_ENABLE = False`**: Alle `django-ratelimit`-Grenzen sind in E2E aus, auch die Login-Drosselung (`5/m` je IP, `10/h` je Benutzername, [ADR-026](026-rate-limiting.md)). E2E-Läufe melden viele Logins von einer IP an. Ob ein Ratelimit greift, belegen die Unit-Tests, nicht die E2E-Suite.
- **`MFA_ENFORCE_PRIVILEGED_ROLES`** ist in E2E standardmäßig **aus** (nur mit `E2E_MFA_ENFORCE_ROLES=1` an). In Produktion ist es an (`settings/base.py`), und `super_admin`/`facility_admin` müssen MFA sofort einrichten. Die Standard-Logins der E2E-Suite sind MFA-frei. Den Rollen-Redirect prüft ein eigener Server mit gesetztem Schalter (#1019).

Die Vererbung „E2E erbt von Dev, nicht von Test“ bleibt richtig. Für Lockout- und MFA-Flows ist E2E aber kein vollständiges Abbild der Produktion.
