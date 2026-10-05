# ADR-024: Slug-basierte stabile Identifikatoren

- **Status:** Accepted
- **Date:** 2026-03-21
- **Deciders:** Tobias Nix
- **Refs:** #210, #175, #212
- **Migriert:** 2026-06-14 aus dem Meta-Archiv (`anlaufstelle-meta/app-archive/docs/decisions/adr-slug-based-identifiers.md`) ins Haupt-Repo, auf MADR-Light verschlankt (Refs #1071).

## Context

`DocumentType`, `FieldTemplate` und Select-Optionen wurden an vielen Stellen über ihren **Anzeigenamen** identifiziert — als Lookup-Key in der Business-Logik, als Schlüssel in `Event.data_json` und als gespeicherter Wert von Select-/MultiSelect-Feldern. Ändert ein Admin einen Namen, brechen Bans, Export und Statistik **still** (keine Exception): `data_json`-Einträge werden unsichtbar, Beispiel-Sachbericht-Kategorien liefern 0, Statistik-Buckets splitten sich. Betroffen waren vier Kategorien fragiler String-Referenzen (DocumentType-Lookup, `data_json`-Keys, Options-Klartext, hartcodierte Feldnamen in `bans.py`). Das System war in der Entwicklungsphase ohne Produktionsdaten — der Umbau war damit migrationsfrei möglich.

## Decision

Stabile, **unveränderliche** interne Identifikatoren auf drei Ebenen — ein immutabler Enum-Wert für Dokumenttypen, generierte Slugs für Feldtemplates und Optionen:

1. `DocumentType.system_type` — immutabler `SystemType`-Enum als Identifier für Business-Logik (`system_type="ban"` statt Lookup über `name="Hausverbot"`; `bans.py` selektiert darüber).
2. `FieldTemplate.slug` — Schlüssel in `Event.data_json` (`{"aktiv": true, "bis": "2026-06-01"}`).
3. Options als `{"slug": …, "label": …}` in `options_json`; `data_json` speichert den Options-Slug.

Slugs werden bei Erstellung aus dem Namen generiert (`slugify` + Umlaut-Map, Kollisions-Suffix `-2`, `-3`, …) und sind danach **immutable** — `save()` wirft `ValidationError` bei Änderungsversuch; ein leerer Slug ist ein Fehler, kein stiller Fallback. Die Anzeige nach außen (UI-Labels, Export-Header) bleibt der frei änderbare `name`/`label`. Der `system_type` der Dokumenttypen ist dagegen ein fester `TextChoices`-Wert (im Seed vergeben, nach Erstellung via `clean()`/`save()` gesperrt) — kein generierter Slug. `UniqueConstraint(facility, slug)` sichert Eindeutigkeit der Feldtemplate-Slugs pro Einrichtung.

## Consequences

- **+** Label-Umbenennung bricht weder Business-Logik noch Export/Statistik — die interne Identität bleibt stabil.
- **+** `data_json` bleibt lesbar (sprechende Slugs statt UUIDs); kein JOIN für die Grundfunktionalität.
- **+** Immutabilität ist hart erzwungen (Constraint + `ValidationError`), nicht nur per Konvention.
- **−** Anzeige braucht Label-Auflösung (`prefetch`/In-Memory-Cache); Statistik aggregiert über Slugs + Lookup-Dict.
- **−** Form-Feldnamen wechseln von `name="Dauer"` zu `name="dauer"` — betrifft einige E2E-/Unit-Tests.
- **−** `QuerySet.update()` umgeht `save()` und damit die Immutabilität — akzeptiertes, dokumentiertes Restrisiko (Slug-Änderung ist kein Use Case).

## Alternatives considered

- **UUID-Keys in `data_json`.** Verworfen — unleserlich (`{"a1b2…": true}`), JOIN-Pflicht, höhere Komplexität.
- **UUID + Name-Cache.** Verworfen — Redundanz ohne Mehrwert gegenüber Slugs.
- **Rename-Sperre.** Verworfen — verschiebt das Problem; Einrichtungen müssen Labels anpassen dürfen.
- **Zusätzlicher `system_key` neben dem Slug.** Verworfen (YAGNI) — nur ein System-Typ (Hausverbot) hat harte Logik; `slug + category` reicht und ist nachrüstbar.

## Update 2026-08-03: `SystemType`-Werteliste eingefroren (RFC #1748)

Dieses ADR hat den Enum eingeführt, seine **Werteliste** aber nie begründet. Sie entstand auf einen Schlag als 1:1-Abbild der damaligen neun Seed-Dokumentationstypen — nicht, weil neun Typen Anwendungslogik brauchten, sondern weil `system_type` vor M2 der einzige rename-sichere Schlüssel war und deshalb auch die reine Berichts-Zuordnung tragen musste (`DocumentType.slug` gibt es erst seit [ADR-037](037-konfigurationspakete-dynamische-felder.md)).

RFC #1748 hat das nachgeholt: Die neun Werte füllen **drei Rollen** — Verhaltens-Schlüssel (`ban`, `crisis`), Berichts-Zuordnung (die sechs Werte in `CATEGORY_MAP`, ohne jede App-Logik) und Ausschluss-Marker (`note`, exportseitig identisch mit `NULL`). Entscheid (Maintainer, 2026-08-03), **Option O1**:

- **Redaktionsregel: keine neuen `SystemType`-Werte ohne echte App-Logik.** Neue Berichts-Zuordnungen laufen über das Backend-Mapping, nicht über dieses Enum. Ein Freeze-Guard pinnt Werteliste und Rollenaufteilung (`src/tests/test_system_type.py`); eine Erweiterung braucht einen neuen RFC-Entscheid.
- **Keine Reduktion.** Der naheliegende Rückbau auf `ban`/`crisis` (Bericht mappt dann über `slug`) ist **nicht** geplant: Das `system_type`-Dropdown ist heute der einzige Weg, mit dem eine Einrichtung einen eigenen Dokumentationstyp in den Sachbericht bekommt. Ein hartkodiertes Slug-Dict im Backend könnte das nicht ersetzen — die Option wird erst wieder bewertet, wenn der offene Produktentscheid zum Slug-Zweitschlüssel (#1741) auf ein **konfigurierbares** Mapping fällt. Sie wäre zudem eine kenntlich zu machende Abweichung von [ADR-038](038-reporting-plugin-backends.md) („Slug als *Ersatz* verworfen").

  > **Überholt seit 2026-08-12** — siehe das Update unten. Die Begründung („einziger Weg") ist mit `report_key` entfallen, die Reduktion ist beschlossen.
- **Capability-Flags** (`ban`/`crisis` als Fähigkeitsschalter) bleiben verworfen — wie schon unter „Alternatives considered": Die Bann-Logik hängt zusätzlich am Feld-Kontrakt (Aktiv/Bis/Grund), ein Flag allein reicht nicht.

Der `help_text` des Feldes benennt die Rollentrennung jetzt selbst (Migration [`0125`](../../src/core/migrations/0125_systemtype_help_text_rollen.py)), der [Admin-Guide § Systemtyp](../admin-guide.md) ebenso.

## Update 2026-08-12: Die Berichtsrolle ist weg — Reduktion beschlossen ([ADR-042](042-report-key-berichtszuordnung.md))

Der Punkt „Keine Reduktion" oben stand auf **einer** Begründung: `system_type` sei der einzige sichtbare Weg, einen eigenen Dokumentationstyp in den Sachbericht zu bekommen. Diese Begründung ist mit #1849 entfallen — die Berichtszuordnung hat mit `report_key` eine **eigene Achse** an `DocumentType` und `FieldTemplate`, deren Werteraum aus den Backend-Deklarationen kommt. Auch der Slug-Zweitschlüssel aus #1741, auf den der Absatz als Alternative verweist, existiert nicht mehr (Entscheid E-E).

Für dieses ADR gilt damit:

- **Die Redaktionsregel bleibt — mit neuer Begründung.** Keine neuen `SystemType`-Werte; nicht mehr, weil das Enum die Berichtsrolle mitträgt, sondern weil es sie **nicht mehr** trägt: Eine neue Berichtskategorie ist ein Eintrag in einer Backend-Deklaration und braucht das Kern-Enum nicht.
- **Die Reduktion ist beschlossen**, nicht mehr abgelehnt: `system_type` schrumpft auf `ban`/`crisis` (Konzept #1847 Etappe E4, Umsetzung #1850). Die sieben übrigen Werte tragen seit E3 keine Wirkung mehr.
- **Die Slug-Rolle dieses ADR ist unberührt.** `slug` bleibt der stabile, rename-sichere Identifikator für Paket-Lookup und `data_json`-Schlüssel — er war nie der Berichtsschlüssel und ist es nach E3 erst recht nicht. Was entfallen ist, war seine **Zweitrolle** in der Berichtsauflösung, nicht seine Identität.

## References

- [`src/core/models/document_type.py`](../../src/core/models/document_type.py) — `DocumentType.system_type` (immutabler Enum), `FieldTemplate.slug`, `_generate_unique_slug`, `save()`-Immutabilität, `choices`-Property (`{slug, label}`)
- [`src/core/services/system/bans.py`](../../src/core/services/system/bans.py), [`src/core/services/system/export.py`](../../src/core/services/system/export.py), [`src/core/forms/events.py`](../../src/core/forms/events.py) — `system_type`-/Slug-basierte Lookups und `data_json`-Keys (`system_type`-Selektion in `bans.py`, Options-Slug→Label-Auflösung in `system/export.py`)
- Issues #210, #175, #212; Migration im Rahmen von #1071
