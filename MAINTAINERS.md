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

Die eigene CRA-Einordnung sieht **zum 11.09.2026** eine Wiedervorlage vor, zu der
eine Vertretung benannt sein soll, damit die 24-Stunden-Frühwarnfrist auch bei
Ausfall der Hauptperson eingehalten werden kann
([docs/compliance/cra-einordnung.md § 3/§ 4](docs/compliance/cra-einordnung.md)).

**Einordnung dieser Frist:** Nach der eigenen Scope-Einschätzung liegt das Projekt
im heutigen Betriebsmodell (quelloffen, kostenlos, kein Hosting-/SaaS-Angebot)
**außerhalb des CRA-Anwendungsbereichs**; einschlägig wären allenfalls die
anteiligen Meldepflichten des Verwalter-Light-Regimes (Art. 24)
([docs/compliance/cra-einordnung.md § 1.4](docs/compliance/cra-einordnung.md)).
Die Frist ist damit **keine bereits feststehende Rechtspflicht**, sondern eine
selbst gesetzte Vorsichtsmarke — sie wird verbindlich, sobald das Betriebsmodell
wechselt (kostenpflichtiges Hosting/SaaS, Produktverkauf, kommerzialisierte Demo).
Unabhängig von der CRA bleibt die Vertretung schon wegen des Bus-Faktors 1 sinnvoll.

Die Benennung selbst ist eine **organisatorische Personalentscheidung** des
Maintainers, keine rein dokumentarische Aufgabe — dieser Abschnitt kann sie nur
sichtbar halten, nicht ersetzen.

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

### Eintrag bei Benennung (auszufüllen)

Sobald eine Vertretung feststeht, hier eintragen; die übrige Prosa dieses
Abschnitts bleibt bis zur Umsetzung der Escrow-Trennung (siehe unten) gültig:

| Feld | Wert |
|------|------|
| Name | _TBD_ |
| Kontakt | _TBD_ |

Nach dem Ausfüllen die Überschrift dieses Abschnitts von „Vertretung: TBD
vor 2026-09-11 vom Maintainer zu benennen" auf „Vertretung: <Name>" ändern.
Die Signing-Key-/Secret-Escrow-Trennung ist ein zweiter, unabhängiger Schritt
— Checkliste dazu: `docs/dev/deputy-escrow-runbook.md` (dev-only).

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
