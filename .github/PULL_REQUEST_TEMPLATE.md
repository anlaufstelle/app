> **Pull-Request-Vorlage — verbindlich** für jeden Pull Request — siehe [CONTRIBUTING.md § Pull-Request-Prozess](../CONTRIBUTING.md#pull-request-prozess) und § DCO. Bitte ausfüllen und die zutreffenden Checkboxen abhaken.

## Zweck / Refs

Refs #

## Änderungen

-

## Verifikation

- [ ] `make ci` grün (bzw. für reine Doku-Diffs: `make verify-matrix-drift test-matrix-index-check verify-agent-docs-sync verify-translation-versions changelog-check` + `pytest -m architecture`)
- [ ] E2E-Tests (`make test-e2e`), falls die Änderung Laufzeitverhalten betrifft
- [ ] CHANGELOG-Eintrag unter `## [Unreleased]` ergänzt (falls nutzer-/betreiber-/sicherheitsrelevant)
- [ ] Hand-Check-Prüfsatz unter `## [Unreleased]` in `docs/dev/hand-checks.md` ergänzt (falls nutzersichtbar)
- [ ] EN-Spiegel nachgezogen und `source-hash`-Stempel aktualisiert (falls EN-Doku betroffen)

## DCO

- [ ] Alle Commits tragen `Signed-off-by: Name <E-Mail>` — ich stimme dem DCO 1.1 aus [CONTRIBUTING.md](../CONTRIBUTING.md) zu.
