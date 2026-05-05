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
