# ADR-042: `report_key` als eigene Achse der Berichtszuordnung

- **Status:** Accepted
- **Date:** 2026-08-12
- **Deciders:** Tobias Nix
- **Refs:** #1847 (Konzept O2″ mit den Entscheiden E-A…E-K), #1849 (Umsetzung E3), #1824 (E1, deklarative Bindung), #1848 (E2, Sichtbarkeit), #1819 (Vollständigkeitshinweis), #1741 / #1785 (Slug-Zweitschlüssel und sein Disclosure-Leck), #1748 (`SystemType`-Freeze), [ADR-038](038-reporting-plugin-backends.md) §4 (**hiermit superseded**), [ADR-040](040-documenttype-category-entfernt.md) (Präzedenz „folgenloses Feld entfernen"), [ADR-037](037-konfigurationspakete-dynamische-felder.md) (Paketformat), [ADR-024](024-slug-based-identifiers.md) (Slug-Rolle)

## Context

`DocumentType.system_type` machte zwei unvereinbare Jobs: Es koppelt App-Verhalten an einen Typ (`ban`, `crisis`) **und** es war der Schlüssel, über den ein Typ in eine Berichtskategorie fällt (`contact`, `counseling`, `medical`, …). Weil beides im selben eingefrorenen Kern-Enum steckte (#1748), erzwang jede neue Berichtskategorie eine Datenmodell-Migration — genau das, was der Freeze verhindern wollte.

Drei Befunde bestimmten den Zuschnitt (#1847):

1. **Der `slug` kann `system_type` fürs Reporting nicht ersetzen.** Er ist je Einrichtung **eindeutig**, Berichtskategorien sind es nicht: „Sozialberatung", „Schuldnerberatung" und „Suchtberatung" brauchen drei Typen in *einer* Berichtszeile. Der Ersatz musste ein eigenes, nicht-eindeutiges Feld sein.
2. **Der Slug lief trotzdem als Zweitschlüssel mit** (#1741) — unsichtbar und deshalb gefährlich: Das Review zu #1785 fand darin ein echtes Disclosure-Leck (ein Hausverbots-Typ mit dem Slug `kontakt` zog seine Ereignisse in den Bericht). Der Fix war eine Reparaturregel („ein gesetzter Erstschlüssel gewinnt inklusive seiner Ausschlüsse"), nicht die Beseitigung der Ursache.
3. **Die Feld-Ebene war schlechter dran als die Typ-Ebene.** `counseling_hours`, `needle_exchange_balance` und `service_distribution` adressierten ihre Feldvorlagen über **hartkodierte Slugs** — ein Zahlenfeld „Gesprächsdauer" statt „Dauer" ergab einen Bericht, der dauerhaft null Minuten auswies, ohne jede Rückmeldung. Kein Dropdown, kein Zweitschlüssel, keine Meldung. Daneben stand `FieldTemplate.statistics_category`: ein in der Konfigurations-UI editierbares Freitextfeld, das **von nichts gelesen** wurde — dasselbe Muster, das bei `category` zur Entfernung führte ([ADR-040](040-documenttype-category-entfernt.md)).

## Decision

**Die Berichtszuordnung bekommt eine eigene Achse: `report_key` an `DocumentType` *und* `FieldTemplate`.** Leitlinie (Konzept #1847):

> Die Einrichtung wählt sichtbar, **welchen Berichtsschlüssel** ihr Typ bzw. ihr Feld trägt. Sie wählt **nicht**, worauf ein Schlüssel abbildet.

Das erste gehört in die UI, das zweite bleibt Fachlogik im Code — der Kern von [ADR-038](038-reporting-plugin-backends.md) §4 („Berichtszahlen nicht per Klick verschiebbar") bleibt damit gewahrt; superseded ist nur die dortige **Schlüsselwahl** („`system_type` zuerst, `slug` als Zweitschlüssel").

Im Einzelnen:

1. **Werteraum aus der Registry, nicht aus dem Datenmodell.** `report_key` trägt bewusst **kein** `choices`: der Wertebereich ist die Vereinigung der Backend-Deklarationen (E1, #1824) plus dem reservierten Sentinel `excluded`. Eine neue Berichtskategorie braucht damit keine Migration mehr, nur einen Deklarationseintrag. Ein `choices` am Modell machte außerdem `full_clean()` bei jedem Fremdwert hart — und damit die Paket- und UI-Toleranz kaputt.
2. **Vier Zustände statt zwei.** `NULL`/leer = „keine Aussage getroffen" (die echte Lücke), `excluded` = „ausdrücklich in keinen Bericht", ein Registry-Schlüssel = zugeordnet, ein **unbekannter** Schlüssel = eigener Meldungscode `unknown_report_keys` (E-H). Damit ist #1819 strukturell gelöst: bewusst ausgeschlossene Typen erzeugen keinen Vollständigkeitshinweis mehr.
3. **Änderbarkeit: leer→gesetzt erlaubt, gesetzt→anders/weg verboten** (E-K), `excluded` zählt als gesetzt. Bewusst **anders als `system_type`**, dessen Konfigurations-UI pauschal jede Bearbeitung sperrt: das Nachtragen ist der Reparaturpfad für die häufigste Überraschung (stille Nicht-Zuordnung) und muss offen bleiben. Die Berichtskontinuität sichert die zweite Hälfte der Regel.
4. **`system_type` und `report_key` dürfen auseinanderlaufen** (E-D/E-I). `crisis` ohne Berichtsschlüssel ist zulässig (Highlight ja, Berichtszeile nein); `ban` mit Zähl-Schlüssel ebenfalls — es wäre eine ausdrückliche, sichtbare Wahl der Einrichtung, kein stiller Nebeneffekt. Als Paritätsschutz belegt das Formular beim Anlegen `crisis`→`crisis` und `ban`→`excluded` **vor** (änderbar, browserseitig, ohne serverseitiges Nachfüllen).
5. **Der Slug-Zweitschlüssel ist ersatzlos entfallen** (E-E). Damit fällt auch die Reparaturregel aus #1785 — sie hat keinen Gegenstand mehr. Voraussetzung war der Backfill (Migration `0145`) und der Nachweis über den Inventar-Command aus E2, dass kein Typ mehr ausschließlich über den Slug gebunden war.
6. **Feld-Kardinalität** (E-J): auf Typ-Ebene ist Mehrfachbelegung der Zweck der Übung, auf Feld-Ebene liest das Backend **Werte je Ereignis**. „Je (Typ, Feld-Schlüssel) höchstens eine aktive Vorlage" wird an Formular, Service und Paket-Loader validiert; entsteht die Doppelbindung trotzdem, zählt das Backend **keines** der Felder und meldet `duplicate_field_binding`. Ein DB-Constraint kann die Regel nicht ausdrücken (M:N über `DocumentTypeField`), ein facility-weiter Unique wäre falsch.
7. **`FieldTemplate.statistics_category` wird ersatzlos entfernt** (E-C, Präzedenz ADR-040) — netto **+1** Feld am Typ, **±0** an der Vorlage.
8. **Paketformat: rein erweiternd, kein `schema_version`-Sprung.** `report_key` kommt an `document_types` und `field_templates` hinzu. Der Wert wird **nicht** gegen eine Werteliste geprüft (E-H); „ehemals gültige Berichtswerte" (`note`/`ban`) werden auf den Sentinel normalisiert, mit sichtbarer Warnung im Load-Report. Ein **fehlender** Schlüssel lässt den Bestand unangetastet — anders als bei `system_type`, wo Weglassen gegen einen gesetzten Bestandswert ein Fehler ist (Begründung unter *Consequences*).

## Consequences

- **+** Eine neue Berichtskategorie ist ein Deklarationseintrag, keine Datenmodell-Migration. Der Freeze aus #1748 erreicht damit erstmals, was er erreichen wollte.
- **+** Die Zuordnung ist **sichtbar und ausdrücklich** — Dropdown mit Registry-Vokabular, „Erscheint in …" an Typ und Feldvorlage (E2), Inventar-Command. Der unsichtbare Zweitschlüssel und mit ihm eine ganze Klasse von Disclosure-Fehlern sind weg.
- **+** Die Feld-Ebene ist erstmals konfigurierbar: „Gesprächsdauer" mit `report_key=dauer_minuten` speist die Minutensummen, ohne dass jemand die Vorlage umbenennen muss. Der `data_json`-Lesepfad wird je Dokumentationstyp aufgelöst.
- **+** Der Vollständigkeitshinweis meldet wieder nur echte Lücken. Eine Einrichtung mit vielen Hausverboten sieht ihn nicht mehr dauerhaft — und hört damit nicht auf hinzusehen.
- **−** **Zwei Felder mehr am Datenmodell** (netto eines, siehe Entscheid 7), und die Verwechslungsgefahr wandert von „`category` vs. `system_type`" zu „`report_key` vs. `system_type`". Entschärft allein durch die Sichtbarkeit aus E2 — deshalb war deren Reihenfolge nicht verhandelbar.
- **−** **Ein Paket ohne `report_key` ordnet nichts mehr zu.** Vor E3 genügte der Paket-Slug. Deshalb führt `default.yaml` den Schlüssel an allen neun Typen und den fünf berichtsspeisenden Feldvorlagen — mit exakt den Werten, die der Backfill vergibt (ein Doppellauf-Test hält das fest; sonst wäre der nächste Seed-Lauf gegen eine aktualisierte Einrichtung ein Immutabilitäts-Konflikt).
- **−** **Weglassen ≠ Entfernen.** Ein fehlender `report_key` im Paket lässt den Bestand unangetastet — bewusst anders als `system_type`. Der Grund ist der Zeitpunkt: `system_type` gibt es seit Schema v1, `report_key` kommt jetzt dazu, und der Backfill vergibt ihn an **Bestandszeilen**. Wäre Weglassen ein Fehler, machte diese Etappe jedes bereits veröffentlichte Fremdpaket gegen eine aktualisierte Instanz unladbar, ohne dass das Paket etwas falsch macht. Ein Entfernen lässt sich damit nicht ausdrücken — kein Verlust, es ist ohnehin verboten.
- **−** `E-I` lässt eine scharfe Kombination zu (`system_type=ban` mit Zähl-Schlüssel). Bewusst so entschieden: es wäre eine sichtbare Wahl, abgefedert durch die `excluded`-Vorbelegung und die „Erscheint in …"-Anzeige. Erweist sich das in der Praxis als Fehlkonfigurationsquelle, ist eine harte Validierung ein kleiner, rückwärtskompatibler Nachschlag.
- **~** **K-Anon-Nähe.** Die Auflösung steckt in Vollständigkeitspfaden und Suppressions-Eingaben. Der Umbau ist deshalb durch einen Golden-Pin über alle sechs Backends abgesichert (`test_reporting_report_key_parity.py`), und der Disclosure-Prüfpunkt aus #1819 ist als Regressionstest festgeschrieben: die Bezugsgröße `events_in_period` steht in keinem Artefakt, der „bewusst ausgeschlossen"-Rest bleibt unberechenbar.

## Abgrenzung

- **E-H gilt nur auf Typ-Ebene.** Ein `FieldTemplate.report_key`, den kein Backend kennt, wird **nicht** gemeldet — es gibt kein Feld-Pendant zu `unknown_report_keys`. Das ist konzepttreu (E-H spricht ausdrücklich von Dokumentationstypen) und hat einen sachlichen Grund: Auf Typ-Ebene ist die Zahl der betroffenen **Ereignisse** die Aussage, auf Feld-Ebene wäre sie es nicht — ein Feld mit unbekanntem Schlüssel liefert schlicht keine Werte, ohne dass sich sagen ließe, wie viele es hätten sein sollen. Sichtbar bleibt der Zustand trotzdem: „Erscheint in keinem Bericht." an der Feldvorlage (E2) und der `unbound`-Bucket des Inventar-Commands. Kommt die Meldung später doch, ist sie ein additiver Nachschlag, kein Umbau.
- **Kein Feld-Pendant zur Typ-Mehrfachbelegung.** Auf Typ-Ebene ist sie der Zweck (mehrere Typen in eine Berichtszeile), auf Feld-Ebene ist sie ein Fehler (E-J).
- **Die Bann-Logik bleibt unberührt.** `ban_overview`, Banner, Zeitstrom-Filter und Übergabe hängen weiter an `system_type=ban`; `ban` bleibt im Enum. Er ist deshalb ausdrücklich **kein** wählbarer `report_key`: ein über `system_type` gebundener Schlüssel im `report_key`-Dropdown wäre wählbar, ohne etwas zu bewirken.

## Alternatives considered

- **O1 — `system_type` einfach auf `ban`/`crisis` reduzieren, ohne Ersatz.** Verworfen: der Slug kann die Berichtszuordnung nicht tragen (Befund 1), und alle heute über den Systemtyp zugeordneten Typen fielen still aus dem Bericht.
- **Den Slug zum alleinigen Berichtsschlüssel machen.** Verworfen aus demselben Grund: er ist je Einrichtung eindeutig, Berichtskategorien sind es nicht.
- **Das Mapping konfigurierbar machen** (Einrichtung wählt, worauf ein Schlüssel abbildet). Verworfen — das machte Berichtszahlen per Klick verschiebbar und höhlte [ADR-038](038-reporting-plugin-backends.md) §4 aus.
- **`schema_version: 2` fürs Paketformat.** Verworfen wie schon bei ADR-040: ein Cutover machte jedes Bestands-Paket unladbar, obwohl sich an allem anderen nichts ändert. Die Erweiterung ist abwärtskompatibel.
- **`choices` am Modellfeld.** Verworfen: es machte `full_clean()` bei jedem Fremdwert hart und damit die Toleranz kaputt, die Fremdpakete und die Bearbeitung eines Typs mit entferntem Backend brauchen (E-H).
