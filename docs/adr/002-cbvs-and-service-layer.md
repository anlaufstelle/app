# ADR-002: Class-Based Views + Service-Layer

- **Status:** Accepted
- **Date:** 2026-03-19
- **Deciders:** Tobias Nix

## Context

Im Vorgänger­prototyp lag Business-Logik verstreut in Views, Templates, Signals und Management-Commands. Das machte Tests teuer (Setup-Heavy), Audit-Hooks unzuverlässig (mehrere Schreibpfade pro Aktion) und Refactorings riskant.

Django bietet zwei View-Stile (FBV/CBV) und keine vorgegebene Stelle für Business-Logik. Für ein Audit- und DSGVO-kritisches System braucht es eine **eindeutige** Stelle, an der Schreibvorgänge stattfinden, damit AuditLog, Verschlüsselung, Retention und Berechtigungen nicht umgangen werden können.

## Decision

- **Views ausschließlich als CBVs** in `src/core/views/`, organisiert nach Feature-Modulen.
- **Business-Logik ausschließlich in `src/core/services/`** — eine Datei pro Feature (z.B. `clients.py`, `cases.py`, `audit.py`, `retention.py`).
- Views orchestrieren: validieren, Service aufrufen, Response rendern. Sie enthalten **keine** ORM-Mutations­logik außer trivialem `Form.save()`.
- Rollen-Mixins liegen zentral in [`src/core/views/mixins.py`](././src/core/views/mixins.py) (`AdminRequiredMixin`, `StaffRequiredMixin`, …) und werden überall statt Decorator-Stapeln verwendet.

## Consequences

- **+** Single-Source-of-Truth für jeden Schreibvorgang — AuditLog, Encryption, Retention werden im Service garantiert ausgelöst.
- **+** Services sind ohne HTTP-Setup testbar; Views brauchen nur Smoke-/Integrationstests.
- **+** Mypy-Strict-Zone (ADR-012) lässt sich gezielt auf `core/services/*` anwenden.
- **−** Mehr Boilerplate für triviale CRUD-Endpunkte (View ruft Service ruft ORM).
- **−** Disziplin nötig: „mal eben im View speichern" wird im Code-Review zurückgewiesen.

## Alternatives considered

- **Fat Models (Active Record):** Verworfen — Cross-Model-Operationen (z.B. „Client anonymisieren" berührt Client + Events + EventHistory + Attachments + DeletionRequests) gehören nicht in *eine* Model-Methode.
- **DDD/Hexagonal mit Repositories:** Verworfen — Overhead ohne Gewinn bei einem System dieser Größe; der ORM-Layer von Django ist gut genug als Persistence-Boundary.
- **FBVs mit Decorators:** Verworfen — Mixin-Komposition ist für unsere Rollenmatrix übersichtlicher als gestapelte Decorators.

## References

- [`src/core/views/`](././src/core/views), [`src/core/services/`](././src/core/services)
- [CLAUDE.md § Projekt & Architektur](././CLAUDE.md)
