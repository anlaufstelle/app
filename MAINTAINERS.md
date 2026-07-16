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

## Vertretung: TBD — vor 2026-09-11 vom Maintainer zu benennen

**Aktuell ist keine Vertretung benannt.** Es wird hier bewusst **keine** Person
erfunden oder in Aussicht gestellt.

Die eigene CRA-Einordnung fordert, dass **vor dem 11.09.2026** eine Vertretung
benannt wird, damit die behördliche 24-Stunden-Frühwarnfrist auch bei Ausfall der
Hauptperson eingehalten werden kann
([docs/compliance/cra-einordnung.md § 3/§ 4](docs/compliance/cra-einordnung.md)).
Das ist eine **organisatorische Personalentscheidung** des Maintainers, keine rein
dokumentarische Aufgabe — dieser Abschnitt kann sie nur sichtbar halten, nicht
ersetzen.

Zur Vertretung gehören mindestens:

- **Meldefähigkeit:** Eine benannte Person kann die Frühwarnung/Meldung an das
 zuständige CSIRT und die Aufsichtsbehörde auslösen, wenn die Hauptperson
 ausfällt (Bezug: [docs/ops-runbook.md § 6.5b](docs/ops-runbook.md)).
- **Signing-Key-/Secret-Escrow-Trennung (offene organisatorische Aufgabe):** Der
 Release-Signing-Key und die Betriebs-/Deploy-Geheimnisse liegen heute
 ausschließlich bei der Hauptperson. Ein tragfähiger Vertretungsweg erfordert
 eine **Trennung** — etwa einen hinterlegten (Escrow-)Zugang oder einen
 separaten Signing-Pfad für die Zweitperson, sodass die Vertretung im Ernstfall
 handlungsfähig ist, **ohne** dass beide Rollen dieselbe Person mit demselben
 Schlüsselbund sind. Wie genau (Escrow-Verwahrung, getrennte Schlüssel,
 Vier-Augen-Prinzip) ist noch zu entscheiden und hier absichtlich offen gelassen.

Solange keine Vertretung eingerichtet ist, bleibt dieser Punkt der wichtigste
offene Governance-Posten des Projekts.

## Verantwortungsbereiche (Code-Ownership)

Die maschinenlesbare Zuordnung von Pfaden zu Reviewern steht in
[.github/CODEOWNERS](.github/CODEOWNERS). Bei nur einer aktiven Person ist der
Owner überall dieselbe Person; **Self-Review ist damit die dokumentierte Realität**
und keine Politik-Wahl (siehe [GOVERNANCE.md § Review & Self-Review](GOVERNANCE.md#review--self-review)).

## Kontakt

- **Sicherheitsmeldungen:** ausschließlich über die Wege in [SECURITY.md](SECURITY.md)
 (kein öffentliches Issue).
- **Alles andere:** [GitHub Issues](https://github.com/anlaufstelle/app/issues).
