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
| Entwicklungs-Repository (**dev**) | privat | Tägliche Entwicklung, Issues, Pläne, Audits, alle Commits. |
| `anlaufstelle/stage` | privat | Release-Kandidaten — vollständig getestet, vor finaler Freigabe. |
| `anlaufstelle/app` | öffentlich | Freigegebene Releases — nur das, was für die Öffentlichkeit bestimmt ist. |

Pipeline (vereinfacht):

1. Entwicklung und CI in **dev**.
2. Release-Kandidat wird auf **stage** gepusht (vom User; das automatisierte Setup hat keinen Push-Zugriff auf stage/app).
3. Nach Stage-Verifikation manuelle Freigabe nach **app**.

> **Release-Tag auf allen drei Repos:** Der signierte Tag `vX.Y.Z` wird vom Release-Build (`dev-ops/release/build-release.sh`, dev-only) auf **allen drei** Repos gesetzt — im **dev**-Repo auf dem Quell-Commit, in **stage**/**app** auf dem daraus gebauten, sanitierten Snapshot (andere SHA, dasselbe Release). So bleibt der Release-Stand auch aus dem dev-Repo allein eindeutig ablesbar; `dev-ops/release/verify-leak.sh` (dev-only) erzwingt den dev-Tag als Teil des Release-Gates (Refs #1237).

## Nachtrag 2026-08-20: Stufe 3 ruht bis v1.0 (Refs #1955)

Die Pipeline bleibt dreistufig; bis einschließlich v0.x endet jeder Release-Cut aber bei **stage**. Der Push nach `anlaufstelle/app`, `release-verify-public.sh` und das GitHub-Release entfallen bis dahin — `anlaufstelle/app` bleibt auf `v0.80.0` stehen.

Unberührt bleibt alles, was die Trennung überhaupt trägt: Der sanitierte Snapshot wird bei jedem Cut gebaut, `verify-leak.sh` läuft, der signierte Tag wird in Dev **und** Stage gesetzt, Stage-CI und Demo-Gate bleiben Pflicht. Es ruht ausschließlich die Veröffentlichungsstufe, nicht die Prüfsubstanz — der Stand bleibt jederzeit publizierbar.

Begründung: Der App-Zweig kostet pro Cut eine eigene Maintainer-Handoff-Kette für ein Publikum, das es vor 1.0 noch nicht gibt (Zeitmodell in #1953). `release-verify-tags.sh` leitet den Modus aus der Major-Version des Tags ab, damit die Kette zum 1.0-Cut ohne Code-Änderung zurückkommt.

Offen zu diesem Nachtrag: AGPL §13 für `demo.anlaufstelle.app` — läuft die öffentliche Demo auf einer Version, deren Corresponding Source nicht öffentlich abrufbar ist, muss die Demo entweder auf dem letzten veröffentlichten Stand bleiben oder die betreffende Version doch publiziert werden.

## Nachtrag 2026-10-08: Stufe 3 wieder aktiv ab v0.99.6 (Refs #1955)

Maintainer-Entscheid 2026-10-08: Ab v0.99.6 endet jeder Release-Cut wieder auf **app**, nicht erst ab 1.0. Der Nachtrag vom 2026-08-20 ist damit für alle folgenden Cuts aufgehoben und bleibt als Begründung der Cuts bis v0.99.5 stehen. Zu jedem Cut gehören wieder der Fast-Forward-Push des Public-Snapshots nach `anlaufstelle/app`, der Tag auf allen drei Repos, `release-verify-public.sh` und das GitHub-Release mit Release-Notes und SBOM. Die Pushes bleiben Schritte des Maintainers; das automatisierte Setup hat weiterhin keinen Push-Zugriff auf stage/app.

`release-verify-tags.sh` (dev-only) leitet den Modus nicht mehr aus der Major-Version des Tags ab: Der App-Zweig ist für jeden Tag Pflicht, `RELEASE_APP_PUSH=0` überspringt ihn nur als ausdrücklicher Ausweg. Die offene AGPL-§13-Frage des Nachtrags vom 2026-08-20 entfällt für veröffentlichte Releases; die Demo bleibt nach [ADR-028](028-demo-release-versioning.md) auf veröffentlichte Stände beschränkt.

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
- `docs/dev/release-checklist.md` (dev-intern)

## Nachtrag 2026-10-06: Veröffentlichungs-Ausnahme und Demo-Quelle (Refs #2450)

Der Nachtrag vom 2026-08-20 ist für die Veröffentlichung von v0.99.5 eingeschränkt: Public-Snapshot und Tag werden für das Demo-Image veröffentlicht; die GitHub-Release-Seite bleibt ausgesetzt. Die damalige Aussage „app bleibt auf v0.80.0“ ist daher keine aktuelle Bestandsangabe. Die Demo darf nur einen veröffentlichten Release verwenden, dessen zugehöriger Quellstand zugänglich ist; die damalige offene Quellenfrage wird durch diese Veröffentlichung adressiert. Beleg: `docs/dev/release-checklist.md` (dev-only), Abschnitt „Stand“/Ausnahme v0.99.5; `dev-ops/release/release-verify-tags.sh` (dev-only), `RELEASE_APP_PUSH`; [ADR-028](028-demo-release-versioning.md), „Decision“. Ein tatsächlich erreichbarer Remote-Tag ist zusätzlich im Release-Gate zu prüfen.
