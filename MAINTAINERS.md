# Maintainer

Diese Datei benennt die für Anlaufstelle verantwortlichen Personen und macht den
aktuellen **Bus-Faktor** transparent. Das zugehörige Entscheidungs- und
Aufnahmemodell steht in [GOVERNANCE.md](GOVERNANCE.md); der Beitragsprozess in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Aktive Maintainer

| Name | GitHub | Rolle | Bereiche |
|------|--------|-------|----------|
| Tobias Nix | [@tobiasnix](https://github.com/tobiasnix) | Lead-Maintainer (Solo) | alle — Architektur, Code, Release, Sicherheit, Betrieb, Doku |

Stand heute wird das Projekt von **einer** aktiven Person gepflegt. Diese Person
trifft und verantwortet sämtliche Entscheidungen (siehe
[GOVERNANCE.md § Entscheidungsmodell](GOVERNANCE.md#entscheidungsmodell)),
ist alleinige Ansprechperson für Sicherheitsmeldungen
([SECURITY.md](SECURITY.md)) und hält die Release-Signing- und Deploy-Geheimnisse.

Das ist ein bewusst offen ausgewiesenes **Bus-Faktor-1-Risiko**: Fällt diese
Person aus, steht das Projekt ohne handlungsfähige Zweitperson da.

## Vertretung: derzeit keine (Stand 2026-08-27)

> **Stand 2026-08-27 (Maintainer-Entscheid, präzisiert 2026-09-10):** Es ist
> derzeit **keine Vertretung benannt**, weil es keine zweite aktive Person gibt.
> Das Solo-Maintainer-Modell ist der **Ist-Stand, nicht das Zielbild**: Sobald
> jemand substanziell mitarbeitet, wird das Maintainer-Team erweitert
> ([GOVERNANCE.md § Aufnahme weiterer Maintainer](GOVERNANCE.md#aufnahme-weiterer-maintainer));
> Ziel ist eine Community um das Projekt. Bis dahin ist das
> **Bus-Faktor-1-Risiko** offen ausgewiesen und in Kauf genommen.

Begründung und Reichweite:

- **Konsequenz im Ausfall der Hauptperson:** Es gibt keine handlungsfähige
  Zweitperson. Sicherheitsmeldungen ([SECURITY.md](SECURITY.md)), Releases und
  die Deploy-Geheimnisse ruhen so lange. Das ist Teil des akzeptierten Risikos.
- **Trigger:** Erfüllt eine zweite Person die Aufnahmekriterien
  ([GOVERNANCE.md § Aufnahme weiterer Maintainer](GOVERNANCE.md#aufnahme-weiterer-maintainer)),
  werden E-1 (Vertretung), E-2 (Escrow-Trennung) und E-3 (Vier-Augen-Review)
  gemeinsam ausgeführt. Dafür gibt es weder einen Zeitplan noch eine
  Suchkampagne — die Aufnahme erfolgt anlassbezogen.
- **Vorlagen bleiben erhalten:** die vorbereitete Checkliste
  (`docs/dev/deputy-escrow-runbook.md`, dev-only) und die auskommentierten
  Vier-Augen-Zeilen in [.github/CODEOWNERS](.github/CODEOWNERS).

## Verantwortungsbereiche (Code-Ownership)

Die maschinenlesbare Zuordnung von Pfaden zu Reviewern steht in
[.github/CODEOWNERS](.github/CODEOWNERS). Bei nur einer aktiven Person ist der
Owner überall dieselbe Person; **Self-Review ist damit die dokumentierte Realität**
und keine Politik-Wahl (siehe [GOVERNANCE.md § Review & Self-Review](GOVERNANCE.md#review--self-review)).

## Offene Entscheidungspunkte

Punkte, die **nur der Maintainer** entscheiden kann — hier gesammelt, damit sie
nicht in der Prosa untergehen. Sie werden gestrichen, sobald sie entschieden sind.

| # | Offener Punkt | Frist / Anlass | Stand und Folgearbeit im Repository |
|---|---------------|----------------|--------------------------------------|
| ~~**E-1**~~ | ~~Vertretung benennen~~ — **Stand 2026-08-27: derzeit keine Vertretung** (siehe [§ Vertretung](#vertretung-derzeit-keine-stand-2026-08-27)) | anlassbezogen: zweite Person erfüllt die Aufnahmekriterien | Bus-Faktor-1-Risiko offen ausgewiesen und in Kauf genommen; [SECURITY.md](SECURITY.md) entsprechend formuliert |
| **E-2** | **Escrow-Trennung wählen** — **ruht, bis E-1 eintritt**: solange keine Zweitperson benannt ist, gibt es niemanden, für den getrennte Zugänge einzurichten wären | gemeinsam mit E-1 | Checkliste `docs/dev/deputy-escrow-runbook.md` (dev-only) bleibt als Vorlage erhalten |
| **E-3** | **Vier-Augen-Review für sicherheitskritische Pfade** aktivieren | sobald eine zweite Person Maintainer ist | vorbereitete, auskommentierte Zeilen in [.github/CODEOWNERS](.github/CODEOWNERS) einkommentieren; [GOVERNANCE.md § Review & Self-Review](GOVERNANCE.md#review--self-review) nachziehen |
| **E-4** | **Eigene `AUTHORS`-/Copyright-Datei** — ja oder nein | ohne Frist | Heute bewusst **nicht** angelegt: Urheberschaft wird über Git-Historie, `Co-authored-by`-Trailer und die namentliche Nennung im Release-CHANGELOG geführt ([CONTRIBUTING.md § Namensnennung](CONTRIBUTING.md#namensnennung)). Eine eigene Datei setzt eine Copyright-/SPDX-Konvention voraus, die das Projekt bisher nicht hat — sie wäre sonst nur eine zweite, driftende Kopie der Historie. |

## Kontakt

- **Sicherheitsmeldungen:** ausschließlich über die Wege in [SECURITY.md](SECURITY.md)
  (kein öffentliches Issue).
- **Alles andere:** [GitHub Issues](https://github.com/anlaufstelle/app/issues).
