# ADR-011: 3-Repo-Release-Pipeline (dev → stage → app)

- **Status:** Accepted
- **Date:** 2026-04-12
- **Deciders:** Tobias Nix

## Context

Die Veröffentlichungs­situation hat zwei konkurrierende Anforderungen:

- **Offenheit gegenüber Mitwirkenden und Träger-Stakeholdern:** Der freigegebene Stand soll öffentlich nachvollziehbar sein (Code, Lizenz, Issues).
- **Schutz der laufenden Entwicklung:** Halbfertige Features, Sicherheits­arbeiten und Konzeptentwürfe sollen nicht jeden Bearbeitungsschritt öffentlich preisgeben — bis sie geprüft, getestet und für die Veröffentlichung bereit sind.

Eine Single-Repo-Lösung („einfach `main` öffentlich machen") erzwingt entweder ständigen Selbst-Lektorat-Aufwand bei jedem Commit, oder veröffentlicht Halbfertiges. Beides ist nicht tragfähig.

## Decision

Drei Repositories mit klarer Aufgabentrennung:

| Repo | Sichtbarkeit | Zweck |
|------|--------------|-------|
| `anlaufstelle/app` (**dev**) | privat | Tägliche Entwicklung, Issues, Pläne, Audits, alle Commits. |
| `anlaufstelle/stage` | privat | Release-Kandidaten — vollständig getestet, vor finaler Freigabe. |
| `anlaufstelle/app` | öffentlich | Freigegebene Releases — nur das, was für die Öffentlichkeit bestimmt ist. |

Pipeline (vereinfacht):

1. Entwicklung und CI in **dev**.
2. Release-Kandidat wird auf **stage** gepusht (vom User; das automatisierte Setup hat keinen Push-Zugriff auf stage/app).
3. Nach Stage-Verifikation manuelle Freigabe nach **app**.

## Consequences

- **+** Halbfertige Arbeit bleibt geschützt; veröffentlichter Stand ist immer geprüft.
- **+** Klare Trennung erleichtert Lizenz- und Compliance-Reviews vor jeder öffentlichen Veröffentlichung.
- **+** Issues und Audit-Dokumente können in dev frei diskutiert werden, ohne sofort öffentliche Wirkung.
- **−** Drei Remotes zu synchronisieren — Gefahr der Diskrepanz, wenn Patches direkt in stage/app eingespielt würden (deshalb: Single Source of Truth bleibt dev).
- **−** Cross-Repo-Issue-Verlinkung ist umständlicher (keine `#NNN`-Auto-Resolution über Repo-Grenzen).
- **−** Push-Berechtigungen für `stage`/`app` sind absichtlich eng — automatisierte Agents können den Release nicht selbst auslösen.

## Alternatives considered

- **Einzel-Repo, alles öffentlich von Tag 1:** Verworfen — siehe Context.
- **Einzel-Repo, privat mit Mirror-Branch:** Funktioniert technisch, aber Issue-Trennung und Sichtbarkeits­kontrolle schwächer; Mirror-Konflikte schwer zu debuggen.
- **Monorepo + lange Feature-Branches:** Verworfen — verschiebt das Problem in Branch-Management, ohne die Sichtbarkeits-Trennung zu lösen.

## References

- Release-Runbook (intern, dev-Repo Issue #502)
- [`docs/release-checklist.md`](./release-checklist.md)
