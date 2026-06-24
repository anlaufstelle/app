# ADR-028: Demo-Instanz läuft nur auf getaggtem Release

- **Status:** Accepted
- **Date:** 2026-06-23
- **Deciders:** Tobias Nix
- **Refs:** #1062 (Deployment, Demo-Instanz & Doku), #971 (Entkopplung dev/demo), [ADR-017](017-deployment-topology.md) (Topologie), [ADR-011](011-three-repo-release-pipeline.md) (Release-Pipeline)

## Context

[ADR-017](017-deployment-topology.md) (Decision, Punkt 4) hält fest: Die Live-Demo läuft als isolierter Single-Stack auf einer eigenen Maschine und ist **die operative Self-Host-Referenz** für das `docker compose up`-Versprechen aus Fachkonzept §1009 — „Bugs am Self-Host-Pfad sehen wir vor Trägern." Seit M6 ist diese Instanz öffentlich unter `demo.anlaufstelle.app` live (#1062), entkoppelt von dev (#971).

Offen blieb, **welchen Stand** die Demo fährt. Der Status quo widerspricht der Referenz-Idee: Die Demo-Deploy-Automatisierung (`deploy-demo.sh`) baut das Web-Image per Default **auf dem Server aus dem hochgeladenen Working-Tree** (`APP_VERSION=demo-<git-short-sha>`, `docker-compose.demo.yml` mit `pull_policy: never`). Der Footer zeigt entsprechend `0.15.0 (demo-<sha>)`. Das ist faktisch ein dev-naher Ad-hoc-Build:

- **nicht reproduzierbar** — der SHA bezieht sich auf einen server-lokalen Build eines synchronisierten Working-Tree, der nicht einmal einem gepushten Commit entsprechen muss;
- **mehrdeutig** — „auf der Demo ist X kaputt" lässt sich keinem definierten Stand zuordnen;
- **gegenläufig zur Referenz** — Self-Hoster bekommen ein getaggtes Release (`ghcr.io/anlaufstelle/app:vX.Y.Z`, vgl. [`docker-compose.prod.yml`](../../docker-compose.prod.yml)), nicht den `main`-Stand. Die Demo zeigt damit etwas anderes, als sie demonstrieren soll.

## Decision

**Die Demo-Instanz (`demo.anlaufstelle.app`) läuft ausschließlich auf einer signierten, getaggten Release-Version** (`APP_VERSION=vX.Y.Z`, Tag nach [ADR-011](011-three-repo-release-pipeline.md)). Kein `main`-/`dev`-Build, kein On-Server-Ad-hoc-Build aus dem Working-Tree.

- **dev bleibt die Bleeding-Edge-Umgebung.** Unreleased-/in-Arbeit-Stände werden auf `dev.anlaufstelle.app` (`:main`) gezeigt — nie auf der Demo.
- **Aktualisierung nur im Release-Zug.** Die Demo wird nach einem Release auf den frischen Tag gehoben (Post-Release-Schritt der Release-Checkliste). Zwischen Releases ist sie auf dem letzten Tag eingefroren.
- **Eindeutige Versionsanzeige.** Mit `APP_VERSION=vX.Y.Z` unterdrückt [`src/core/context_processors.py`](../../src/core/context_processors.py) den Build-Suffix; der Footer zeigt das saubere `vX.Y.Z` — wie in Produktion.

**Umsetzungs-Hinweis (noch ausstehend, #1062):** Ziel-Mechanik ist, dass die Demo das **publizierte Release-Image zieht** (`ghcr.io/anlaufstelle/app:${APP_VERSION}`, wie Produktion), statt on-server zu bauen — ein fixer Tag hebt den heutigen Lokal-Build-Grund („`:main` driftet/ist stale") ohnehin auf. Bis die Mechanik darauf umgestellt ist, gilt die Policy **disziplinär**: bei jedem Demo-Deploy `APP_VERSION` explizit auf den Release-Tag setzen und aus dem getaggten Stand deployen.

## Consequences

- **+** Die Demo ist, was Self-Hoster bekommen — die Self-Host-Referenz aus [ADR-017](017-deployment-topology.md) wird auch versionsseitig eingelöst.
- **+** Reproduzierbar und eindeutig: ein Tag statt eines server-lokalen SHA; Demo-Befunde sind einem definierten Release zuordenbar.
- **+** Klare Trennung dev/demo — die beiden Umgebungen sind nicht mehr quasi-redundant „aktueller Code".
- **+** Das Release-Gate (Akzeptanztest, CHANGELOG, Sanitizing, signierter Tag) schützt öffentliche Besucher vor halbfertigem Stand.
- **−** Die Demo hinkt `main` um bis zu ein Release hinterher — gewollt, aber neue Features sind erst nach Release sichtbar.
- **−** Ein demo-spezifischer Fix erfordert ein (Patch-)Release statt eines Hot-Patch auf dem Host.
- **−** Solange die Mechanik-Umstellung aussteht, ist die Policy nicht durch Code erzwungen, sondern Disziplin (siehe Umsetzungs-Hinweis) — das Risiko eines versehentlichen Ad-hoc-Deploys bleibt bis dahin.

## Alternatives considered

- **Demo folgt `:main` (wie dev).** Verworfen: macht die Demo quasi-redundant zu dev, instabil und am Release-Gate vorbei — öffentliche Besucher sähen ungetesteten Stand.
- **On-Server-Build aus dem Working-Tree (Status quo).** Verworfen: `demo-<sha>` ist nicht reproduzierbar und mehrdeutig; widerspricht der Self-Host-Referenz.
- **Aus dem Release-Tag *bauen* statt das publizierte Image *ziehen*.** Möglich und ein kleinerer Eingriff, aber schwächer: ein lokaler Rebuild ist nicht bit-identisch zum Artefakt, das Träger einsetzen. Die Ziel-Mechanik bleibt der Image-Pull wie in Produktion; Build-aus-Tag ist allenfalls Übergangslösung.

## References

- [ADR-017](017-deployment-topology.md) (Decision, Punkt 4) — Demo als Live-Self-Host-Referenz (§1009)
- [ADR-011](011-three-repo-release-pipeline.md) — 3-Repo-Release-Pipeline, signierte `v*`-Tags
- [`docs/fachkonzept-anlaufstelle.md`](../fachkonzept-anlaufstelle.md) §1009 (Self-Host-Versprechen, `docker compose up` als harte Anforderung)
- Versionsanzeige-Logik: [`src/core/context_processors.py`](../../src/core/context_processors.py) (Build-Suffix-Unterdrückung), [`src/core/services/system/health.py`](../../src/core/services/system/health.py)
- Produktions-Pinning als Vorbild: [`docker-compose.prod.yml`](../../docker-compose.prod.yml) (`ghcr.io/anlaufstelle/app:${APP_VERSION:-vX.Y.Z}`)
- Demo-Deploy (dev-intern): `dev-ops/deploy/deploy-demo.sh`, `docker-compose.demo.yml`
- Issues: #1062, #971; Roadmap #1054
