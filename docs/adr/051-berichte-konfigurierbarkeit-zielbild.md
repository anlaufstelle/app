# ADR-051: Zielbild für Berichte-Konfigurierbarkeit und Kern-Konsolidierung

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Tobias Nix
- **Refs:** #2072, #1898, #1905, [ADR-038](038-reporting-plugin-backends.md), [ADR-042](042-report-key-berichtszuordnung.md), [ADR-023](023-k-anonymization-statistik.md)

## Context

[ADR-038](038-reporting-plugin-backends.md) hat die herausgabefähigen
Datensatz-Berichte als Backends mit einem gemeinsamen Vertrag und der Grenze
`build_dataset → prepare_export → Renderer` etabliert.
[ADR-042](042-report-key-berichtszuordnung.md) trennt seitdem die sichtbare
Zuordnung eines Dokumentationstyps oder Felds (`report_key`) von der im Code
reviewten Abbildung eines Schlüssels auf Berichtszeilen.

Damit ist konfigurierbar, **welche fachlichen Typen und Felder einen bekannten
Berichtsschlüssel tragen**. Nicht konfigurierbar ist bislang, wie die neun
ausgelieferten Berichte für eine Einrichtung erscheinen. Auch die Gegenfrage
„Welche Typen und Felder speisen diesen Bericht, und warum zeigt er nur
Nullen?“ ist in der Berichts-Konfiguration nicht beantwortet.

Unterhalb der gemeinsamen Statistik-Oberfläche bestehen außerdem getrennte
Rechenwelten:

1. die interne, rollenbeschränkte und bewusst unsuppressierte
   Dashboard-Statistik;
2. ältere herausgabefähige Berichte wie Externer Bericht und Halbjahres-PDF
   mit eigener Suppressions- und Snapshot-Logik;
3. die Datensatz-Backends aus ADR-038 mit der zentralen
   `prepare_export`-Grenze.

Die Doppelungen sollen langfristig sinken, ohne die Trennung zwischen interner
Rohsicht und extern zirkulierenden, kleinstzellengeschützten Artefakten
aufzuweichen. Der Maintainer-Entscheid in
#1905 § 5.3 begrenzt den
1.0-Umfang auf das Festschreiben dieses Zielbilds. Die Umsetzung von D1–D4 und
K1–K4 verbleibt am Strang #1898
für post-1.0.

## Decision

### 1. Konfigurierbar wird die Darstellung, nicht die Rechnung

Die Einrichtung darf künftig sichtbar wählen, **wie ihre Berichte erscheinen**.
Sie darf nicht wählen, **was gerechnet, welchem Schlüssel eine Berichtszeile
zugeordnet oder wie Kleinzellenschutz angewendet wird**.

Darstellungs-Konfiguration wird ausschließlich auf das bereits durch
`prepare_export` geschützte Ergebnis angewendet. Sie darf Informationen nur
weglassen, umordnen oder in einer vom Backend freigegebenen Form visualisieren;
sie darf weder Rohdaten noch unterdrückte Werte rekonstruieren. Die
K-Anonymitäts-Schwelle, Randsummen-Doktrin, Vollständigkeitshinweise und
Grundgesamtheits-Ausweise bleiben zentral und nicht wegkonfigurierbar.

Ausgelieferte Berichte bleiben inhaltlich fix und sind nicht editierbar. Eine
spätere Vollersetzung durch Definitionen oder Eigenberichte braucht den in
#1898 beschriebenen
Testballon und einen eigenen Maintainer-Entscheid.

### 2. Zielbild D1–D4

Die vier Darstellungsstufen werden als post-1.0-Ziel akzeptiert:

- **D1 — Berichts-Lesefläche:** `/config/reports/` zeigt je Bericht die
  wirksamen Typ-/Feld-Bindungen, nicht zugeordnete Typen und den Warnzustand
  „0 Bindungen“. Die Auskunft wird aus den bestehenden deklarativen
  Backend-Bindungen abgeleitet, nicht als zweite Mapping-Wahrheit gepflegt.
- **D2 — Sichtbarkeit und Reihenfolge:** Eine facility-gescopte
  Darstellungs-Konfiguration ordnet Berichtskarten und blendet sie in der
  Übersicht aus. Ausblenden löscht keine Fähigkeit: Deep-Links und historische
  Freigaben bleiben erreichbar. Änderungen werden auditiert und mit der
  Einrichtung exportiert.
- **D3 — Spaltenprojektion und Standardzeitraum:** Spalten dürfen aus dem
  geschützten `FieldSpec`-Satz nur weggelassen oder umgeordnet werden. Ein
  einrichtungsspezifischer Standardzeitraum ist ein Startwert; ein explizit
  gewählter Zeitraum gewinnt immer. Panel und alle Exportformate verwenden
  dieselbe wirksame Projektion und protokollieren sie.
- **D4 — Tabelle oder Diagramm:** Das Backend deklariert die erlaubten
  Darstellungen. Diagramme verwenden ausschließlich das suppressierte
  Export-Ergebnis; unterdrückte Zellen bleiben Lücken, und es werden weder
  Restsegmente noch andere ableitbare Ersatzwerte berechnet.

Schreibrecht folgt dem Konfigurationsmuster der Anwendungsbetreuung
(`facility_admin`). Lesen und Exportieren behalten die Rollen aus ADR-038.

### 3. Verhältnis zu ADR-038 und ADR-042

Diese Entscheidung ergänzt ADR-038 und ADR-042; sie supersediert keine von
beiden:

- Der Backend-Vertrag, Lazy-Registry, die generischen Ausgabewege und vor allem
  `prepare_export` aus ADR-038 bleiben die Sicherheits- und
  Erweiterungsgrenze.
- `report_key` aus ADR-042 bleibt die einzige sichtbare Zuordnungsachse an Typ
  und Feld. Eine Einrichtung wählt einen bekannten Schlüssel, aber nicht dessen
  code-reviewte Bedeutung. Slug, `system_type` und frühere Kategorien werden
  keine versteckten Zweitschlüssel.
- Die neue Darstellungs-Schicht liegt logisch **nach** beiden Entscheidungen:
  Backend und Schlüssel bestimmen das geschützte Ergebnis; die
  Facility-Konfiguration bestimmt nur dessen Darstellung.

### 4. Konsolidierungslinie K1–K5

Die Statistik-Altwelt konvergiert schrittweise in die Reporting-Schicht:

- **K1 — Suppressions-Schicht heben:** Die bestehende Mechanik zieht ohne
  Verhaltensänderung aus `core.services.dashboard` nach `core.reporting`.
  Reporting darf danach nicht rückwärts aus Services importieren. K1 geschieht
  vor dem Definitions-Testballon und vor V1.
- **K2 — eine Suppressionskette für `sample-report-1`:** Der Hybrid-PDF-Pfad
  baut ein `DatasetResult` und verwendet genau einmal `prepare_export`; seine
  Hybrid-Datenquelle bleibt dabei zunächst erhalten.
- **K3 — PDF als Backend-Format:** Der Sonderexport geht in den generischen
  Datensatz-Export auf. Ob das PDF weiterhin den retention-festen
  Hybrid-Snapshot oder live berechnete Daten liest, bleibt eine explizite
  Entscheidung der K3-Umsetzung; die datenschutzfreundliche
  „aggregieren, dann löschen“-Eigenschaft des Hybrid-Pfads ist dabei zu
  erhalten oder begründet zu supersedieren.
- **K4 — gemeinsamer Snapshot-Merge-Kernel:** Dashboard- und
  `sample-report-1`-Merge teilen die sicherheitsrelevante Logik, behalten aber
  bis zu einer Engine-Entscheidung dünne Formatadapter.
- **K5 — Zielbild festschreiben:** Diese ADR erfüllt K5. Sie zieht keine der
  übrigen Etappen in release-0.89 oder in den 1.0-Pfad.

Die Abhängigkeiten lauten:

```text
K1 ──► Testballon S ──► separater Entscheid ──► V1 … V5
 │
 ├──► K2 ──► K3 ──► vor V5
 ├──► K4 (unabhängig, vor V1 nützlich)
 └──► K5 (diese ADR)
```

### 5. Grenzen der späteren Kern-Konsolidierung

- Der **Externe Bericht** bleibt vor einem positiven V-Entscheid eine eigene
  Fläche. Danach kann er als Komposition von Berichtsdefinitionen aufgehen.
- Das **Halbjahres-PDF** gehört zur suppressierten Berichtswelt, folgt aber erst
  nach dem V-Entscheid dem Externen Bericht. Sein interner Modus bleibt bis
  dahin unangetastet.
- Der Zeilenexport **Roh-CSV** ist kein Bericht. Er teilt Zeitraumprüfung und
  Export-Audit, bleibt aber rollen- und sensitivitätsgefiltert statt
  kleinstzellensuppressiert.
- **Dashboard-Kennzahlen** bleiben eine eigene interne, unsuppressierte Achse.
  Eine spätere Speisung aus Engines verlangt einen ausdrücklichen
  Maintainer-Entscheid, Golden-Master-Parität und eine deklarierte interne
  Plan-Variante, die nicht in Exportpfade gelangen kann.

## Consequences

**Positiv:**

- Die post-1.0-Umsetzung hat eine feste Sicherheitsgrenze: Darstellung folgt
  der Suppression und kann Rechenlogik nicht verändern.
- ADR-038 und ADR-042 bleiben widerspruchsfrei; Konfiguration erzeugt keine
  zweite Schlüssel- oder Mapping-Wahrheit.
- K1–K4 können einzeln und mit Golden-Master-Parität umgesetzt werden, ohne
  Konsolidierung und Mechanik-Umbau in einem Diff zu vermischen.
- Interne Rohsicht und extern herausgabefähige Statistik bleiben als getrennte
  Objekte verständlich.

**Negativ / Kosten:**

- Bis zur post-1.0-Umsetzung bleiben die getrennten Rechen- und
  Suppressionspfade bestehen.
- D2 benötigt später ein RLS-geschütztes Facility-Modell, Auditierung und
  Facility-Export; D3/D4 benötigen Paritäts- und Disclosure-Guards über Panel
  und alle Ausgabeformate.
- Die Datenquelle des PDF-Formats bleibt bis K3 bewusst offen und muss dort als
  eigener, nachvollziehbarer Entscheid geschlossen werden.

## Nicht-Ziele

- Keine Feature-, Modell-, Migrations- oder UI-Umsetzung in release-0.89.
- Kein Berichtsbaukasten vor dem Testballon und kein Editor für ausgelieferte
  Definitionen oder externe Definitionspakete.
- Keine konfigurierbaren Suppressionsparameter, k-Schwellen, Randsummen,
  Rechenformeln, berechneten Spalten, Zeilenfilter oder Top-N-Listen.
- Keine Live-Vorschau ungespeicherter Definitionen und kein Laufzeit-Umschalter
  zwischen zwei Darstellungswahrheiten für lesende Rollen.
- Kein neuer Endpunkt pro Bericht, keine Slug-Umbenennungen, Backfills oder
  Kompatibilitäts-Migrationen.
- Kein Vorgriff auf KDS-Mapping oder Wirkungsanalyse; beide bleiben eigene
  fachliche Entscheide.

## Alternatives considered

- **Statistik und Berichte sofort vollständig vereinigen.** Verworfen: Das
  vermischt interne Rohsicht und externe Artefakte, bevor Engine-Parität und
  Disclosure-Schutz bewiesen sind.
- **Rechenlogik und Schlüssel-Mapping per Facility konfigurierbar machen.**
  Verworfen: Berichtszahlen würden per Klick verschiebbar; die Grenze aus
  ADR-042 und der code-reviewte Kleinzellenschutz liefen leer.
- **Nur einen frei editierbaren Universal-Bericht bauen.** Verworfen:
  frei kombinierbare Teilmengen und publizierte Randsummen eröffnen
  Differenzangriffe auf unterdrückte Kleinzellen. Ein Cross-Report-Wächter und
  Definitionsstempel sind Vorbedingungen einer späteren Eigenberichtsstufe.
- **Den Bestand unverändert lassen und nur dokumentieren.** Verworfen als
  dauerhaftes Ziel: Die aktuelle Doppelung und fehlende „Warum-Null?“-Auskunft
  bleiben reale Wartungs- und Bedienprobleme. Akzeptiert ist nur der
  zeitliche Schnitt, die Umsetzung post-1.0 zu sequenzieren.
