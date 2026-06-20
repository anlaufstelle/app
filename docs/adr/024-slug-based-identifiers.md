# ADR-024: Slug-basierte stabile Identifikatoren

- **Status:** Accepted
- **Date:** 2026-03-21
- **Deciders:** Tobias Nix
- **Refs:** #210, #175, #212
- **Migriert:** 2026-06-14 aus dem Meta-Archiv (`anlaufstelle-meta/app-archive/docs/decisions/adr-slug-based-identifiers.md`) ins Haupt-Repo, auf MADR-Light verschlankt (Refs #1071).

## Context

`DocumentType`, `FieldTemplate` und Select-Optionen wurden an vielen Stellen über ihren **Anzeigenamen** identifiziert — als Lookup-Key in der Business-Logik, als Schlüssel in `Event.data_json` und als gespeicherter Wert von Select-/MultiSelect-Feldern. Ändert ein Admin einen Namen, brechen Bans, Export und Statistik **still** (keine Exception): `data_json`-Einträge werden unsichtbar, Jugendamt-Kategorien liefern 0, Statistik-Buckets splitten sich. Betroffen waren vier Kategorien fragiler String-Referenzen (DocumentType-Lookup, `data_json`-Keys, Options-Klartext, hartcodierte Feldnamen in `bans.py`). Das System war in der Entwicklungsphase ohne Produktionsdaten — der Umbau war damit migrationsfrei möglich.

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

## References

- [`src/core/models/document_type.py`](../../src/core/models/document_type.py) — `DocumentType.system_type` (immutabler Enum), `FieldTemplate.slug`, `_generate_unique_slug`, `save()`-Immutabilität, `choices`-Property (`{slug, label}`)
- [`src/core/services/system/bans.py`](../../src/core/services/system/bans.py), [`src/core/services/system/export.py`](../../src/core/services/system/export.py), [`src/core/forms/events.py`](../../src/core/forms/events.py) — `system_type`-/Slug-basierte Lookups und `data_json`-Keys (`system_type`-Selektion in `bans.py`, Options-Slug→Label-Auflösung in `system/export.py`)
- Issues #210, #175, #212; Migration im Rahmen von #1071
