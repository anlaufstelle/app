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

## Vertretung: keine — bewusster Entscheid (2026-08-27)

> **ENTSCHIEDEN (Maintainer, 2026-08-27): Es wird keine Vertretung benannt.**
> Das Solo-Maintainer-Modell ist der gewollte Dauerzustand, nicht ein
> Übergangszustand. Das damit verbundene **Bus-Faktor-1-Risiko ist bewusst
> akzeptiert** und bleibt oben offen ausgewiesen.

Begründung und Reichweite:

- Nach der eigenen Scope-Einschätzung liegt das Projekt im heutigen
 Betriebsmodell (quelloffen, kostenlos, kein Hosting-/SaaS-Angebot)
 **außerhalb des CRA-Anwendungsbereichs**; einschlägig wären allenfalls die
 anteiligen Meldepflichten des Verwalter-Light-Regimes (Art. 24)
 ([docs/compliance/cra-einordnung.md § 1.4](docs/compliance/cra-einordnung.md)).
 Die früher hier geführte Frist 2026-09-11 war eine selbst gesetzte
 Vorsichtsmarke, keine feststehende Rechtspflicht.
- **Konsequenz im Ausfall der Hauptperson:** Es gibt keine meldefähige
 Zweitperson; die 24-Stunden-Frühwarnfähigkeit ([docs/ops-runbook.md
 § 6.5b](docs/ops-runbook.md)) besteht in diesem Fall nicht. Das ist Teil des
 akzeptierten Risikos.
- **Wiedervorlage-Trigger bleibt:** Wechselt das Betriebsmodell in den
 CRA-Scope (kostenpflichtiges Hosting/SaaS, Produktverkauf, kommerzialisierte
 Demo), ist dieser Entscheid **neu zu bewerten**
 ([docs/compliance/cra-einordnung.md § 4](docs/compliance/cra-einordnung.md)).
- Die Signing-Key-/Secret-Escrow-Trennung entfällt als eigenständige Aufgabe
 sie war Voraussetzung eines Vertretungswegs. Die vorbereitete Checkliste
 (`docs/dev/deputy-escrow-runbook.md`, dev-only) bleibt als Vorlage erhalten,
 falls der Entscheid später revidiert wird; dasselbe gilt für die
 auskommentierten Vier-Augen-Zeilen in [.github/CODEOWNERS](.github/CODEOWNERS).

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
| ~~**E-1**~~ | ~~Vertretung benennen~~ — **entschieden 2026-08-27: keine Vertretung** (siehe [§ Vertretung](#vertretung-keine--bewusster-entscheid-2026-08-27)) | Wiedervorlage nur bei Wechsel des Betriebsmodells in den CRA-Scope | Bus-Faktor-1-Risiko bewusst akzeptiert; [SECURITY.md](SECURITY.md) entsprechend formuliert |
| ~~**E-2**~~ | ~~Escrow-Trennung wählen~~ — **mit E-1 entfallen** (2026-08-27): ohne Vertretung gibt es keine Zweitperson, für die getrennte Zugänge einzurichten wären | Wiedervorlage zusammen mit E-1 | Checkliste `docs/dev/deputy-escrow-runbook.md` (dev-only) bleibt als Vorlage erhalten |
| **E-3** | **Vier-Augen-Review für sicherheitskritische Pfade** aktivieren | sobald eine zweite Person Maintainer ist | vorbereitete, auskommentierte Zeilen in [.github/CODEOWNERS](.github/CODEOWNERS) einkommentieren; [GOVERNANCE.md § Review & Self-Review](GOVERNANCE.md#review--self-review) nachziehen |
| **E-4** | **Eigene `AUTHORS`-/Copyright-Datei** — ja oder nein | ohne Frist | Heute bewusst **nicht** angelegt: Urheberschaft wird über Git-Historie, `Co-authored-by`-Trailer und die namentliche Nennung im Release-CHANGELOG geführt ([CONTRIBUTING.md § Namensnennung](CONTRIBUTING.md#namensnennung)). Eine eigene Datei setzt eine Copyright-/SPDX-Konvention voraus, die das Projekt bisher nicht hat — sie wäre sonst nur eine zweite, driftende Kopie der Historie. |

## Kontakt

- **Sicherheitsmeldungen:** ausschließlich über die Wege in [SECURITY.md](SECURITY.md)
 (kein öffentliches Issue).
- **Alles andere:** [GitHub Issues](https://github.com/anlaufstelle/app/issues).
