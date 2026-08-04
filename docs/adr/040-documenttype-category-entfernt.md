# ADR-040: `DocumentType.category` wird entfernt

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Tobias Nix
- **Refs:** #1749 (RFC + Maintainer-Entscheid), #1785 (Sammel-Issue des Umsetzungslaufs), #434 (früherer Behalten-Entscheid, hiermit revidiert), [ADR-034](034-config-ui-category-system-type.md) (teilweise superseded), [ADR-038](038-reporting-plugin-backends.md) (Reporting nutzt `category` nie), #1731, #1741, #1748 (Schwester-RFC `SystemType`)

## Context

`DocumentType` trug zwei Klassifikationsfelder nebeneinander: `system_type` (stabile, immutable Systemkennung für Bann-Logik, Krisen-Highlight und Berichts-Opt-in) und `category` (`contact`/`service`/`admin`/`note`). Der `help_text` beschrieb `category` seit jeher als *„reine UI-Gruppierung für Statistik-Anzeige und Admin-Filter"*.

Die Nutzungsanalyse in #1749 hat alle Konsumenten-Flächen systematisch abgesucht und kam zu drei Befunden:

1. **Fürs Reporting dauerhaft ungenutzt.** [ADR-038](038-reporting-plugin-backends.md) §4 legt fest: Mapping über `system_type`, Zweitschlüssel `slug`, `category` nie. Eine frei editierbare UI-Gruppierung darf Berichtszahlen nicht verschieben.
2. **Keine wertabhängige Geschäftslogik.** Ein Sweep über `src/` fand nirgends ein `if category == …`; Bann-, Krisen- und Export-Logik hängen ausschließlich an `system_type`.
3. **Aber vier strukturelle Rollen:** Zweitschlüssel im Natural Key `(facility, name, category)`, Bestandteil des M2-Paketformats, editierbares Feld plus Listen-Filter in der Konfigurations-UI und Django-Admin, sowie eine Anzeigespalte in Statistik-Dashboard, Berichts-PDF und Beispiel-Backend. Ersatzlos löschen ging deshalb nicht — nur als geplanter Eingriff.

Frühere Entscheide fielen zweimal auf *behalten*: #434 („Kann eines der Felder entfallen? → Nein") und [ADR-034](034-config-ui-category-system-type.md), die „`category` weglassen" als „separater Datenmodell-Eingriff außerhalb von M0" ausklammerte — ohne ihn je zu beschließen oder abzulehnen. ADR-034 benannte zugleich den eigentlichen Fix des schwachen Natural Keys: ein stabiler `slug`. Der existiert seit M2 (#1058) und ist der Lookup-Key des Paketformats — der Snapshot-Pfad war aber nie darauf umgestellt worden.

## Decision

**`DocumentType.category` wird ersatzlos entfernt** (Maintainer-Entscheid 2026-08-04, Option A des RFC). Das **revidiert bewusst** #434 und den offen gelassenen Punkt aus [ADR-034](034-config-ui-category-system-type.md).

Im Einzelnen:

1. **Natural Key.** `UniqueConstraint(facility, name, category)` wird zu `UniqueConstraint(facility, name)` (`unique_facility_documenttype_name`). Zwei gleichnamige Typen einer Einrichtung sind damit verboten — sie waren für Nutzerinnen ohnehin nicht unterscheidbar, weil keine Oberfläche nach Kategorie gruppiert.
2. **Migration `0134` mit hartem Guard.** Erste Operation ist ein `RunPython`-Guard, der duplizierte `(facility_id, name)`-Paare sucht und bei Fund **abbricht** — mit Einrichtung und Name jedes Konflikts in der Meldung. **Kein Auto-Dedupe:** welcher zweier gleichnamiger Typen der „richtige" ist, kann nur die Einrichtung entscheiden; ein stilles Zusammenführen riskierte falsch zugeordnete Ereignisse. Danach `RemoveConstraint` → `AddConstraint` → `RemoveField`.
3. **Reverse ist strukturell, nicht inhaltlich.** Der Rollback legt das Feld mit dem Modell-Default `contact` und die alte Tripel-Constraint wieder an — die **ursprünglichen Kategoriewerte sind verloren**. Der Rückweg existiert, damit ein Deploy-Rollback nicht am Schema hängenbleibt, nicht als Datenrettung. Wer die Werte braucht, spielt ein Backup ein.
4. **Statistik/Snapshot.** `by_document_type` gruppiert nur noch über `name`; der Anreicherungs-Lookup in `snapshot.py` nutzt `{name: dt}`. Der `category__in`-Filter entfällt.
5. **Alt-Snapshots werden nicht migriert.** Persistierte `StatisticsSnapshot.data`-Payloads tragen den `category`-Schlüssel weiter. `_merge_stats` liest ihn tolerant (`.get`), mergt über `name` und **verwirft** `category` beim Kollabieren: Segmente, die sich nur darin unterschieden, summieren zu **einer** Zeile statt als Doppelzeile im Bericht zu erscheinen. Ein Rewrite über alle Bestands-Snapshots hätte keinen Nutzen — der Wert ist für die Anzeige bedeutungslos geworden.
6. **Paketformat: v1 nimmt `category` weiter an, wendet ihn aber nicht an** — mit **sichtbarer Warnung** je betroffenem Typ im Load-Report. Bewusst **kein `schema_version`-2-Cutover** (siehe *Alternatives*). Die Duplikatvalidierung im Schema läuft auf `name` statt `(name, category)` und nimmt damit weiter die DB-Constraint vorweg. `default.yaml` führt den Schlüssel nicht mehr.
7. **UI/Admin.** Formularfeld, Listen-Filter, Tabellen-/Detail-Spalte, Django-Admin-`list_display`/`list_filter`, die Anzeigespalte des Beispiel-Backends sowie die „Kategorie"-Spalten in Statistik-Dashboard und Berichts-PDF entfallen. Das Offline-Bundle serialisiert den Schlüssel nicht mehr (kein Client-JS hat ihn je gelesen).

## Consequences

- **+** Ein Klassifikationsfeld weniger — die wiederkehrende Verwechslung `category` vs. `system_type` (Werteräume überlappen: beide kennen `contact`) ist erledigt. Das entlastet #1731 und #1741.
- **+** Der Natural Key `(facility, name)` ist strenger und deckt sich mit dem, was Nutzerinnen sehen. Die latente Kollisionsfalle in `seed/events.py` und `seed/quick_templates.py` (name-only-Dicts `{dt.name: dt}`, in denen gleichnamige Typen still kollabierten) ist damit strukturell geschlossen.
- **+** Zwei Roh-Wert-Artefakte verschwinden: Statistik-Dashboard und Berichts-PDF zeigten `contact` statt „Kontakt" — im extern zirkulierenden PDF ein sichtbarer Makel.
- **−** **Sichtbare UI-Änderung.** Der Kategorie-Filter der Typen-Liste, das Formularfeld und die Spalten sind weg; der zugehörige Abschnitt im Admin-Guide entfällt. Bestehende Bookmarks mit `?category=…` schränken die Liste nicht mehr ein (400/500 gibt es nicht — der Parameter wird schlicht ignoriert).
- **−** **Gleichnamige Typen einer Einrichtung sind nicht mehr anlegbar.** Wer bisher „Notiz" als Verwaltung *und* als Notiz führte, muss vor der Migration umbenennen; sonst bricht sie ab. Zum Zeitpunkt des Entscheids existierten keinerlei Echtdaten, deshalb läuft die Prüfung als Migrations-Guard statt als manueller Bestands-Task.
- **−** **Historische Kategorien sind nach der Migration nicht mehr rekonstruierbar** (Reverse setzt alles auf `contact`). Das ist akzeptiert: das Feld hatte keine steuernde Wirkung, und die Berichtszahlen hingen nie daran.
- **−** Bestands-Pakete mit `category` erzeugen bei jedem Lauf eine Warnung, bis sie gepflegt sind. Gewollt: eine Warnung ist billiger als ein unlesbares Paket.

## Alternatives considered

- **Behalten (Option B des RFC).** Verworfen. Kostete zwar nichts außer den Nebenbefund-Fixes, hielt aber ein Feld am Leben, das seit [ADR-038](038-reporting-plugin-backends.md) dauerhaft ohne Wirkung ist und dessen Werteraum sich mit `system_type` überschneidet.
- **`schema_version: 2` mit hartem Cutover fürs Paketformat.** Verworfen. Ein Cutover machte jedes Bestands-Paket und jede ältere Kopie von `default.yaml` unladbar, obwohl sich an allem anderen nichts ändert. Das Format hat mit M24 ([ADR-039](039-owner-generische-dateianhaenge.md) §9) bereits präzediert, dass eine rein *lockernde* Änderung keinen Versionssprung rechtfertigt.
- **`category` im Paket still ignorieren (Silent Drop).** Verworfen. Das Schema ist bewusst strikt (unbekannte Schlüssel = Fehler, damit Tippfehler auffallen); ein stumm verschluckter Schlüssel widerspräche genau dieser Zusage. Die Warnung im Report ist der Kompromiss: laden ja, schweigen nein.
- **Auto-Dedupe in der Migration (gleichnamige Typen automatisch umbenennen oder mergen).** Verworfen. Ein Merge zöge Ereignisse auf einen Typ um, den niemand ausgewählt hat; ein Auto-Rename („Notiz (2)") erzeugte Namen, die keine Einrichtung so gewollt hat. Beides sind Datenentscheide — deshalb harter Abbruch mit einer Meldung, die die Konflikte benennt.
- **Alt-Snapshots migrieren (`category` aus den JSON-Payloads strippen).** Verworfen (YAGNI). Der Lesepfad muss ohnehin tolerant sein — Snapshots älterer Releases erreichen die App über Backups/Restores auch nach einem Rewrite. Ein zusätzlicher Batch-Rewrite über alle Bestands-Snapshots brächte keinen Nutzen, nur Laufzeit und Risiko.

## References

- [`src/core/models/document_type.py`](../../src/core/models/document_type.py) — `UniqueConstraint(facility, name)`
- [`src/core/migrations/0134_remove_documenttype_category.py`](../../src/core/migrations/0134_remove_documenttype_category.py) — Guard, Constraint-Wechsel, `RemoveField`
- [`src/core/services/dashboard/snapshot.py`](../../src/core/services/dashboard/snapshot.py) — Anreicherungs-Lookup und `_merge_stats` (Alt-Snapshot-Toleranz)
- [`src/core/packages/schema.py`](../../src/core/packages/schema.py) / [`loader.py`](../../src/core/packages/loader.py) — `ignored_keys` und die Report-Warnung
- [ADR-034](034-config-ui-category-system-type.md) — teilweise superseded: die dort offen gelassene Entfernung ist hiermit entschieden
- [ADR-038](038-reporting-plugin-backends.md) §4 — `category` fürs Reporting nie verwendet
- [docs/packages.md](../packages.md) — Paketformat, ignorierter Schlüssel
