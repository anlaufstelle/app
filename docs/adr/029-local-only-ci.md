# ADR-029: Quality-Gates lokal-only durchgesetzt (Dev-CI bewusst deaktiviert)

- **Status:** Accepted — **erneut wirksam ab 2026-07-13** (die Reaktivierung vom 2026-07-11 ist zurückgenommen, alle Dev-Workflows wieder `disabled_manually`; siehe [Update 2026-07-13](#update-2026-07-13--dev-ci-wieder-deaktiviert) und [Update 2026-07-11](#update-2026-07-11--dev-ci-reaktiviert))
- **Date:** 2026-06-28
- **Deciders:** Tobias Nix
- **Refs:** #1150 (Härtung gegen „falsch grün"), #860 (CI-Kosten-Postmortem), #1309 (Reaktivierung), #1514 (Runner-Nichtdeterminismus Release-Build), [ADR-011](011-three-repo-release-pipeline.md) (3-Repo-Release-Pipeline)

## Update 2026-07-13 — Dev-CI wieder deaktiviert

`gh workflow list --all --repo anlaufstelle/app` zeigt am **2026-07-13** wieder **alle** Dev-Workflows auf `disabled_manually` — einzig aktiv ist `Dependabot Updates`. Die Reaktivierung vom 2026-07-11 ([Update unten](#update-2026-07-11--dev-ci-reaktiviert)) ist damit **nicht mehr wirksam**; der in dieser ADR beschriebene lokal-only-Zustand gilt wieder unverändert. Die Aktivierung bleibt reversibel ([docs/dev/ci-workflows.md § Reaktivierung](../dev/ci-workflows.md)).

Der Release-Reproduzierbarkeits-Test `test_two_cold_builds_are_byte_identical` war während der 07-11-Reaktivierung auf dem Runner nichtdeterministisch (#1514, Signaturverdacht). Der Test ist inzwischen **gehärtet**: die harte Determinismus-Garantie läuft über den *strukturellen* Snapshot (Tree + vollständige Metadaten je Boundary) statt über die signaturhaltige HEAD-SHA; die SSH-Signatur wird separat und diagnostisch geprüft. Die **Root-Cause bleibt bis zu einem echten Runner-Lauf unbestätigt** — bei allen `disabled_manually` derzeit nicht verifizierbar, daher bleibt #1514 **offen**. Bis dahin ist `make ci` lokal das verbindliche Gate.

## Update 2026-07-11 — Dev-CI reaktiviert

> **Überholt (2026-07-13):** Dieses Update beschreibt einen zwischenzeitlichen Zustand; die Workflows sind seit 2026-07-13 wieder deaktiviert (siehe [Update 2026-07-13](#update-2026-07-13--dev-ci-wieder-deaktiviert)).

Der in dieser ADR als Follow-up vorgesehene Fall ist eingetreten: Das Actions-Spending-Limit wurde zurückgesetzt und die Dev-Workflows wurden als eigener Vorgang wieder aktiviert (#1309). Reaktiviert sind **Test**, **Lint**, **E2E**, **CodeQL** (PR-/Push-Gate), **Dev-Image** (baut `ghcr:main` bei jedem `main`-Push, #1247) sowie **Mutation-Nightly** und **Perf-Nightly**; der **Release**-Workflow bleibt bewusst deaktiviert (auf dem Dev-Repo unnötig, Releases laufen über die Stage/App-Pipeline).

**Verifiziert** an PR [#1513](https://github.com/anlaufstelle/app/pull/1513): Lint und E2E grün, Test/CodeQL liefen real gegen den PR — die Gates blocken/laufen also wieder serverseitig, nicht nur lokal. Damit ist der Dev-PR nicht länger nur disziplingetragen abgesichert (adressiert den `−`-Punkt „per `--no-verify` umgehbar" unten).

Die **lokale** Durchsetzung (`make ci`, Pre-Push-Hook, `make release-gates`) bleibt unverändert die erste Verteidigungslinie und schnellste Feedback-Schleife — die reaktivierte Dev-CI ist ein zusätzliches serverseitiges Netz, kein Ersatz. Bekannte Einschränkung: der Release-Reproduzierbarkeits-Test `test_two_cold_builds_are_byte_identical` ist auf dem Runner nichtdeterministisch (getrackt in #1514); bis zu dessen Härtung bleibt `make ci` lokal das verbindliche Gate.

## Context

Die Quality-Gates des Projekts (Lint, Typecheck, Unit-/Integration-Tests, Coverage-Floor 96 %, E2E, Mutation-Testing, CodeQL) sind als GitHub-Workflows definiert. Auf dem Dev-Repo `anlaufstelle/app` sind jedoch **alle** Test-/Lint-/E2E-/Mutation-/CodeQL-Workflows `disabled_manually` — `gh workflow list --all` zeigt nur Dependabot aktiv. Grund ist das **Actions-Spending-Limit** (privates Repo, kostenpflichtige Minuten); das Postmortem dazu ist #860.

Damit blocken Coverage-Floor, Mutation und CodeQL im Dev-Repo **nicht** bei Push/PR. Das ist kein Versehen, sondern eine Kostenentscheidung — aber bisher nur verteilt in Doku-Fußnoten festgehalten (`docs/dev/release-checklist.md` § 1.1, `docs/dev/ci-workflows.md`). #1150 verlangt, die Nicht-Durchsetzung als **bewusste, dokumentierte Entscheidung** festzuschreiben, damit sie kein blinder Fleck ist (sonst entsteht „falsch grün" unbemerkt).

Die 3-Repo-Pipeline ([ADR-011](011-three-repo-release-pipeline.md)) hat zudem ein nachgelagertes Netz: Auf `anlaufstelle/stage` (privat) und `anlaufstelle/app` (public) laufen `test.yml`/`lint.yml`/`e2e.yml`/`codeql.yml` **aktiv** — der eigentliche CI-Gate liegt also vor dem App-Push, nicht im Dev-Repo.

## Decision

**Die Quality-Gates werden im Dev-Repo bewusst lokal-only durchgesetzt** — nicht über GitHub-Actions auf `anlaufstelle/app`. Die Durchsetzungskette ist:

1. **Lokal blockierend:** `make ci` (`lint` · `check` · `deps-check` · `verify-matrix-drift` · `typecheck` · `test-parallel`) vor jedem Commit/Push; volle E2E (`make test-e2e-parallel`, sandbox seriell) vor jedem Release-Tag.
2. **Pre-Push-Hook:** `pre-commit install --hook-type pre-push` aktiviert `make lint && make deps-check && make check` automatisch vor jedem `git push` (umgehbar nur mit `--no-verify` — verboten ohne ausdrücklichen Wunsch).
3. **Release-Gate:** `make release-gates` repliziert die Stage-only-Gates lokal (pip-audit, `check --deploy`, Coverage-Floor, Lizenz-/Translation-Gates); zusätzlich sind **Mutation-Testing + Coverage** als verbindlicher Release-Schritt festgeschrieben (#1150 M3, Schwellen `core.forms` ≥ 85 %, `core.services` ≥ 75 % nach Triage).
4. **Stage-CI:** Standard-Gate vor jedem App-Push — `test.yml`/`lint.yml`/`e2e.yml`/`codeql.yml` laufen aktiv auf `stage`/`app` ([ADR-011](011-three-repo-release-pipeline.md)).

**Reaktivierung ist eine bewusste Folgeentscheidung**, kein Default: Sobald das Spending-Limit zurückgesetzt bzw. ein Budget bereitsteht, wird die Wieder-Aktivierung der Dev-Workflows als eigener Vorgang abgewogen (Kosten/Nutzen). Bis dahin gilt diese ADR; Dev-Workflows werden **nicht** ohne Maintainer-Rücksprache reaktiviert.

## Consequences

- **+** Die Nicht-Durchsetzung im Dev-Repo ist eine dokumentierte, auffindbare Entscheidung statt eines blinden Flecks — direkt adressiert das „falsch grün"-Risiko aus #1150.
- **+** Keine laufenden Actions-Kosten auf dem privaten Dev-Repo; der eigentliche Gate-Schutz liegt auf Stage/App, wo CodeQL im Public-Repo kostenfrei läuft.
- **+** Mutation + Coverage sind als Release-Gate verbindlich — eine abgeschwächte/bug-konforme Assertion fällt spätestens hier auf.
- **−** Die lokale Durchsetzung ist disziplingetragen und per `--no-verify` technisch umgehbar; sie hängt am Entwickler, nicht an einer serverseitigen Schranke.
- **−** Drift wird im Dev-Repo erst lokal bzw. spätestens auf der Stage-CI sichtbar, nicht schon bei einem Dev-PR — ein bewusst akzeptierter Latenz-Trade-off.
- **−** Die Entscheidung muss revidiert werden, sobald das Budget zurückkehrt (Reaktivierung als Follow-up).

## Alternatives considered

- **Dev-CI (Test/Lint/E2E/Mutation) reaktivieren.** Verworfen (vorerst): kostet Actions-Minuten auf dem privaten Repo ohne Zusatzschutz gegenüber der Stage-CI (#860). Bleibt der bevorzugte Zustand nach Budget-Reset — dann als eigener Vorgang.
- **Gar keine Gate-Durchsetzung bis Stage.** Verworfen: zu spätes Feedback; lokale `make ci` + Pre-Push fangen den Großteil sofort.
- **Nur Coverage als Gate, Mutation weiter rein nightly/manuell.** Verworfen für die Release-Grenze: 96 % Coverage misst ausgeführte, nicht geprüfte Zeilen — Mutation ist genau das Maß gegen „falsch grün" und gehört daher verbindlich an die Release-Schwelle.

## References

- #1150 — Quality-Gates gegen „falsch grün" härten (M2: diese ADR; M3: Mutation/Coverage-Release-Gate)
- #860 — Postmortem CI-Kosten / Dev-Workflows deaktiviert
- [ADR-011](011-three-repo-release-pipeline.md) — 3-Repo-Release-Pipeline (Stage/App tragen die aktive CI)
- `docs/dev/release-checklist.md` § 1.1 — Workflow-Sichtbarkeit dev/stage/app, Schutzkette
- `docs/dev/ci-workflows.md` — Status der deaktivierten Dev-Workflows + Reaktivierungs-Routine
- `docs/testing/mutation-testing.md` — Mutation-Schwellen, Triage-Kategorien (inkl. `bug` = „falsch grün")
