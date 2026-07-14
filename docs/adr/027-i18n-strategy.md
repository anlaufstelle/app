# ADR-027: i18n-/Übersetzungs-Strategie

- **Status:** Accepted
- **Date:** 2026-06-20
- **Deciders:** Tobias Nix
- **Refs:** #832, #1078 (Doc-Version-Gate), #813, #814 (`.po`-Baseline)
- **Verschriftlicht:** 2026-06-20 aus dem Code/Prozess als ADR (Refs #1071 §E, #1102).

## Context

Anlaufstelle ist deutsch-first (Zielgruppe: deutsche Träger und Beratungsstellen). Django-i18n ist aktiv: `USE_I18N = True`, `LANGUAGE_CODE = "de"`, `LANGUAGES = [de, en]`, `LOCALE_PATHS = [locale/]` ([`base.py`](../../src/anlaufstelle/settings/base.py)). Es gibt zwei Übersetzungsflächen — UI-Strings (gettext-`.po`) und Langform-Doku (`README`/`CONTRIBUTING`/`docs/en/`). Für beide existieren CI-Wächter, die faktisch eine Sync-Politik erzwingen — die **Politik selbst** war aber nie als Entscheidung festgehalten. Diese ADR schreibt den Ist-Stand fest, ohne ihn zu ändern.

## Decision

**DE ist die kanonische Quellsprache.** Alle Quell-Strings, Templates und Primär-Doku werden auf Deutsch verfasst (`LANGUAGE_CODE="de"`). EN ist die **eine** gepflegte Übersetzung (`LANGUAGES = de, en`) — kein „best effort".

**UI-Strings** laufen über Django-gettext (`locale/`). Qualität gegated durch [`scripts/check_translations.py`](../../scripts/check_translations.py) (Refs #813/#814): liest `msgfmt --statistics` pro `django.po` und fällt, wenn fuzzy/untranslated über einer **gepinnten Baseline** liegen. Das Skript erzwingt die Obergrenze; die Baseline wird per Konvention nur **gesenkt**, nie erhöht (Review-Disziplin) — so kann unübersetzte UI nicht still wachsen.

**Langform-Doku** (die 8 Dateien in `TRANSLATED_FILES`: `README.en.md`, `CONTRIBUTING.en.md`, `docs/en/*`) trägt zwei HTML-Kommentar-Header: `<!-- translation-source: … -->` und `<!-- translation-version: vX.Y.Z -->`. [`scripts/check_translation_versions.py`](../../scripts/check_translation_versions.py) prüft den Versions-Marker gegen `pyproject.toml`. **Hartes Release-Gate seit 2026-06-12 (#1078): `MAX_MINOR_BEHIND = 0`** — jede EN-Doku muss dem aktuellen *Minor* entsprechen (Patch toleriert); EN darf DE nie um einen Minor-Release hinterherhängen. „Ahead-of-source" und Major-Mismatch fallen ebenfalls.

**Sync-Zeitpunkt folgt aus dem Gate:** weil der Marker dem Release-Minor gleichen muss, wird die EN-Aktualisierung **mit** dem Version-Bump-Commit ausgeliefert (siehe `docs/dev/release-checklist.md`, dev-intern), nicht als nachgelagerter Follow-up. (Das ist die Antwort auf die in #1071 Block A aufgeworfene EN-Sync-Prozessfrage: „kein Minor-Rückstand", erzwungen — nicht „später best effort".)

**Bewusst DE-only:** Entwickler-/interne Doku außerhalb von `TRANSLATED_FILES` (ADRs, `docs/ai/*`, Runbooks, `threat-model.md`) bleibt absichtlich deutsch — sie adressiert Maintainer; eine Übersetzung brächte Sync-Kosten ohne Nutzerwert.

> **Stand 2026-07-14:** #1548 (öffentliches Pendant: [anlaufstelle/app#48](https://github.com/anlaufstelle/app/issues/48)) plant den Wechsel der **Entwicklungssprache** auf Englisch; neue ADRs würden dann auf Englisch verfasst. Der Meilenstein ist offen. Diese ADR wird bei der Umsetzung amendiert. UI-Strings, i18n-Katalog (`LANGUAGE_CODE="de"`) und deutsche Fachbegriffe sind davon nicht berührt.

## Consequences

- **+** Eine kanonische Sprache → keine Ambiguität über die Wahrheitsquelle; Reviews diffen gegen DE.
- **+** Beide Gates machen Übersetzungs-Drift zum Build-Fehler statt zur stillen Erosion — UI (Baseline) und Doku (Versions-Marker) fallen je in CI.
- **+** `MAX_MINOR_BEHIND=0` hält veröffentlichte EN-Doku zu jedem Release vertrauenswürdig.
- **−** EN-Doku-Updates sind an den Release gekoppelt: eine DE-Änderung an einer übersetzten Datei kann nicht ohne EN-Pendant + Marker-Bump mergen — gewollte Reibung.
- **−** Nur DE/EN; eine dritte Sprache vervielfacht die Gate-Fläche (kein aktueller Bedarf, YAGNI).
- **−** DE-only-Dev-Doku hebt die Hürde für nicht-deutschsprachige Contributor (akzeptierter Trade-off).

## Alternatives considered

- **Best-effort-EN-Sync (zwei Minor Toleranz).** War die frühere Regel; am 2026-06-12 (#1078) auf 0 verschärft, weil hinterherhängende EN-Doku ausgeliefertes Verhalten falsch darstellte.
- **EN als nachgelagerter Follow-up nach Release.** Verworfen: erzeugt genau den Drift, den das Gate verbietet.
- **Kein EN (reines DE-Produkt).** Verworfen: EN-`README`/-Guides verbreitern für ein OSS-Projekt die Contributor-/Eval-Zielgruppe spürbar.
- **Translation-Management-Plattform (Weblate/Crowdin).** Vertagt/YAGNI: in-repo `.po` + zwei Skripte reichen bei einer Zielsprache.

## References

- [`src/anlaufstelle/settings/base.py`](../../src/anlaufstelle/settings/base.py) — `LANGUAGE_CODE`, `LANGUAGES`, `LOCALE_PATHS`, `USE_I18N`
- [`scripts/check_translation_versions.py`](../../scripts/check_translation_versions.py) — Doc-Versions-Gate (`MAX_MINOR_BEHIND=0`, `TRANSLATED_FILES`)
- [`scripts/check_translations.py`](../../scripts/check_translations.py) — `.po`-Fuzzy/Untranslated-Baseline-Gate
- `docs/dev/release-checklist.md` (dev-intern) — EN-Sync-Schritt im Release-Flow
- Issues #1071 §E, #1102; Versions-Gate #832/#1078; `.po`-Baseline #813/#814
