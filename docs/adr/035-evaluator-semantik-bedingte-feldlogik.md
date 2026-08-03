# ADR-035: Evaluator-Semantik der bedingten Feldlogik

- **Status:** Accepted
- **Date:** 2026-08-02
- **Deciders:** Tobias Nix
- **Refs:** #1057 (M1, Planung), [anlaufstelle/app#34](https://github.com/anlaufstelle/app/issues/34) (M1-Feature), #774 (Sensitivitäts-Leak), [ADR-024](024-slug-based-identifiers.md) (Slug-basierte Identifikatoren), [ADR-003](003-htmx-alpine-tailwind.md) (HTMX + Alpine + CSP)

> **Nachtrag (2026-08-03, #1058, M2):** Der unter „Context" genannte Ort der Feld-Logik ist seit M2 präziser [`src/core/forms/dynamic.py`](../../src/core/forms/dynamic.py) — der Form-Builder ist dort entity-neutral als `DynamicDataForm` gebündelt; [`forms/events.py`](../../src/core/forms/events.py) hält nur noch die Ereignis-Ausprägung `DynamicEventDataForm` (plus Re-Export für Importpfad-Stabilität). An der hier festgeschriebenen **Semantik** ändert das nichts; die Person- und Fall-Ausprägungen (`DynamicClientDataForm` in [`forms/clients.py`](../../src/core/forms/clients.py), das Fall-Pendant in [`forms/cases.py`](../../src/core/forms/cases.py)) werten dieselben Regeln über dieselbe Basisklasse aus. Siehe [ADR-037](037-konfigurationspakete-dynamische-felder.md).

## Context

M1 („Bedingte Feldlogik") führt Feld-zu-Feld-Bedingungen innerhalb **eines** `DocumentType` ein — „zeige Feld X, wenn Feld Y = Z". Die Sichtbarkeit wird rein clientseitig ohne Backend-Roundtrip ausgewertet (Alpine.js), damit ein Feld unmittelbar beim Tippen erscheint oder verschwindet. Dasselbe Regelwerk muss aber **serverseitig** in `DynamicEventDataForm.clean()` erneut ausgewertet werden, denn ob ein versteckter Pflichtwert erzwungen wird, darf nie am manipulierbaren Frontend hängen.

Damit existieren zwangsläufig **zwei Implementierungen derselben Semantik** — eine in Python (`src/core/services/conditions.py`) und eine in JavaScript (`src/static/js/alpine/conditions.js`). Zwei Implementierungen driften ohne Gegenmaßnahme auseinander; eine solche Drift ist nicht nur ein Anzeigefehler, sondern kann eine Pflichtvalidierung unterlaufen. Es braucht eine **eine, verbindlich festgeschriebene Semantik** und einen mechanischen Weg, Drift aufzudecken.

Zusätzliche Randbedingungen:

- **Slugs sind der kanonische Identifier.** Formularfeld-Namen und `data_json`-Keys **sind** die `FieldTemplate.slug`s ([`forms/events.py`](../../src/core/forms/events.py), [ADR-024](024-slug-based-identifiers.md)). Der Evaluator arbeitet auf Slugs, nicht auf DB-PKs.
- **Sensitivitäts-Leak (#774).** Eine Bedingung mit sensibilitäts-beschränktem Quellfeld darf deren Vergleichswert niemals ins Frontend-JSON tragen, wenn die auswertende Person das Quellfeld gar nicht sehen darf.
- **CSP.** Die Auswertung läuft im Alpine-CSP-Build (`script-src 'self'`, [ADR-003](003-htmx-alpine-tailwind.md)) — kein `eval`, keine Inline-Ausdrücke mit Logik.
- **Typisierte Werte.** Ein `number`-Vergleich, ein `boolean` und ein `multi_select`-Slug lassen sich nicht sinnvoll als reine Strings vergleichen; Roh-Eingaben liegen je Seite in unterschiedlicher Form vor (JS `input[type=time].value` liefert `"HH:MM"`, Python `time.isoformat()` liefert `"HH:MM:SS"`).

## Decision

Wir legen **eine** Evaluator-Semantik fest, implementieren sie **identisch** in Python und JavaScript und sichern die Gleichheit über **gemeinsame JSON-Testvektoren** als ausführbare Spezifikation ab.

### 1. Kanonischer Identifier: Slug

Alle Datenstrukturen des Evaluators sind slug-basiert. `evaluate_conditions(fields, conditions, values)` liefert die Menge sichtbarer **Slugs** — die Model-Fremdschlüssel zeigen weiterhin auf `DocumentTypeField`, aber der Evaluator kennt nur Slugs.

### 2. Operator × Typ-Matrix (zentral `OPERATORS_BY_FIELD_TYPE`)

Nur die folgenden Kombinationen sind gültig. Die Matrix ist die **einzige Wahrheitsquelle**; Modell-`clean()` (P2) und Editor-Form (P6) validieren gegen genau diese Mengen, unbekannte Kombinationen werden abgewiesen.

| Feldtyp | Gültige Operatoren |
|---|---|
| `text`, `textarea` | `equals`, `not_equals`, `contains`, `is_empty`, `is_not_empty` |
| `number` | `equals`, `not_equals`, `greater_than`, `less_than`, `is_empty`, `is_not_empty` |
| `date`, `time` | `equals`, `not_equals`, `greater_than`, `less_than`, `is_empty`, `is_not_empty` |
| `boolean` | `equals`, `not_equals` |
| `select` | `equals`, `not_equals`, `is_empty`, `is_not_empty` |
| `multi_select` | `contains`, `is_empty`, `is_not_empty` |

- Quelltypen sind **alle außer `file`** (`ALLOWED_SOURCE_FIELD_TYPES`, 8 Typen). Verschlüsselte Felder werden zusätzlich modell-/editorseitig als Quelle ausgeschlossen.
- `contains` bedeutet bei `text`/`textarea` **Substring**, bei `multi_select` **Membership** (der gesuchte Options-Slug ist in der Auswahl enthalten).
- `date`/`time` werden als ISO-Strings **lexikographisch** verglichen — das ist bei ISO-Format identisch zur chronologischen Ordnung.
- **String-Vergleiche sind exakt.** Kein Trim, kein Casefold (KISS, s. Abschnitt „Consequences" / bewusste Auslassung). `"Ja" ≠ "ja"`, `"ja " ≠ "ja"`.

### 3. Leer-Definition (E10)

Leer sind: `None`/fehlend, der leere String `""` und die leere Liste `[]`. **`false` und `0` sind ausdrücklich nicht leer** — eine deaktivierte Checkbox und eine Null-Zahl sind gesetzte Werte. Genau deshalb kennt `boolean` keinen `is_empty`-Operator (eine Checkbox ist nie „leer").

Ein **leerer Ist-Wert erfüllt keinen Wert-Operator** — auch `not_equals` nicht. Nur `is_empty` schlägt bei einem leeren Wert an. Damit blendet ein noch nicht ausgefülltes Quellfeld kein abhängiges Feld versehentlich ein.

### 4. Gruppen-Logik (E5): AND innerhalb, OR über Gruppen

Bedingungen tragen einen `group`-Index. Innerhalb einer Gruppe gilt **AND** (alle Bedingungen müssen erfüllt sein), über Gruppen hinweg **OR** (eine erfüllte Gruppe genügt). Es gibt keine verschachtelten Gruppen und keine NOT-Gruppen — die Zweistufigkeit ist fest. **Felder ohne (verbleibende) Bedingung sind immer sichtbar.**

### 5. Kaskade (E7): verstecktes Quellfeld gilt als leer

Ist ein Quellfeld selbst versteckt, wird es bei der Auswertung seiner Abhängigen **wie leer** behandelt. Dadurch verschwinden abhängige Felder mit ihrem versteckten Auslöser (Kette A→B→C: wird A geleert, verschwindet B und mit ihm C — selbst wenn B einen Wert trägt). Die Auswertung ist eine **Fixpunkt-Iteration** mit Obergrenze „Anzahl Zielfelder + 1"; sie konvergiert garantiert, weil Zyklen modellseitig ausvalidiert sind (Drei-Farben-DFS `find_cycle`, Selbstreferenz zusätzlich per DB-`CheckConstraint`).

### 6. Drop-Regel (E8, #774): Konsistenz FE↔BE und kein Wert-Leak

Der Evaluator-Input ist **exakt die Felder des aktuellen Formulars** (nach `remove_restricted_fields`). Bedingungen, deren `source` **oder** `target` nicht im Formular liegt, werden **vor** der Auswertung verworfen — auf beiden Seiten identisch. Das erreicht zweierlei: Frontend und Backend werten immer dieselbe Regelmenge aus, **und** der Vergleichswert eines sensibilitäts-beschränkten (herausgefilterten) Quellfelds gelangt nie in das an den Client ausgelieferte JSON.

### 7. Normalisierung als dünne Schicht je Seite (E11)

Der Kern-Evaluator arbeitet ausschließlich auf **normalisierten** Werten. Die seitenspezifische Roh→Normal-Abbildung ist:

| Feldtyp | Normalisiert | Python-Seite | JS-Seite |
|---|---|---|---|
| `number` | Zahl \| `null` | `""`→`None`, sonst Zahl | `parseFloat`, `""`/`NaN`→`null` |
| `date` | `"YYYY-MM-DD"` \| `null` | `date.isoformat()` | `input.value` (bereits ISO) |
| `time` | `"HH:MM:SS"` \| `null` | `time.isoformat()` (liefert `:SS`) | `input.value` ist `"HH:MM"` → **`":00"` anhängen** |
| `boolean` | `true`/`false` | `bool(value)` | `checkbox.checked` |
| `select` / `text` / `textarea` | String \| `null` | `""`→`None` | `""`→`null` |
| `multi_select` | Array von Slugs | Liste unverändert | alle `input[name=slug]:checked` |

**Die `time`-Falle ist die riskanteste Detailentscheidung:** ohne das `":00"`-Anhängen auf der JS-Seite verglichen die Seiten `"14:30"` (JS) gegen `"14:30:00"` (Python) und jeder `equals`-Vergleich schlüge fehl. Beide Seiten werden dazu mit expliziten `normalize`-Asserts abgesichert.

### 8. Payload-Format (E12)

Der Server liefert die Regeln als `json_script` (`id="field-conditions"`) **innerhalb** des HTMX-geswappten Partials `#dynamic-fields` aus — läge es außerhalb, veraltete es beim DocType-Wechsel:

```json
{
  "fields": {"<slug>": {"type": "<field_type>"}},
  "conditions": [
    {"target": "<slug>", "source": "<slug>", "operator": "<op>", "value": <json>, "group": <int>}
  ]
}
```

Beide Seiten reduzieren `fields` vor dem Aufruf des Kerns auf die Kurzform `{slug: field_type}` (die Signatur `evaluate_conditions(fields: Mapping[str, str], …)`). Die `value`-Werte werden typisiert 1:1 durchgereicht; für `is_empty`/`is_not_empty` ist `value` stets `null`.

### 9. „Behalten" (E6): Werte versteckter Felder bleiben erhalten

Ein verstecktes Feld verliert seinen Wert nicht. `x-show` blendet nur per `display:none` aus — der Input submittet weiter, `data_json` behält den Wert. Die Sichtbarkeit steuert ausschließlich UI **und** Pflichtvalidierung, nie die Datenhaltung. Beim Wiedereinblenden ist der Wert unverändert da.

### 10. Testvektoren als ausführbare Spezifikation (E13/E14) — Prozessregel

`src/tests/data/condition_vectors.json` ist die **verbindliche, ausführbare** Fassung dieser Semantik: Jeder Fall trägt `fields`, `conditions`, `values` (bereits normalisiert) und `expected_visible`. Konsumiert wird die Datei von (a) parametrisierten pytest-Fällen (`test_conditions_evaluator.py`) und (b) einem Playwright-Paritätstest, der jeden Vektor gegen `window.AnlaufstelleConditions.evaluate(...)` laufen lässt. Die Normalisierungs-Schicht wird je Seite separat getestet, weil Roh-Inputs seitenspezifisch sind (Python `datetime.date` ist nicht JSON-serialisierbar).

**Prozessregel (verbindlich):** Jede Änderung der Semantik wird **zuerst als Vektor** in `condition_vectors.json` niedergeschrieben, danach in beiden Implementierungen umgesetzt. Die Vektoren-Datei ist die einzige Stelle, an der FE/BE-Drift mechanisch auffällt.

## Consequences

- **+** Es gibt genau **eine** normative Semantik; die Gleichheit von Python- und JS-Auswertung ist mechanisch abgesichert (gemeinsame Vektoren + Paritätstest), nicht bloß per Code-Review.
- **+** Die Sicherheitsgrenze ist klar: die Sichtbarkeit ist UI-Komfort, die **Pflichtvalidierung** entscheidet der Server über dieselbe Semantik — kein Verlass auf manipulierbares Frontend.
- **+** Die Drop-Regel schließt das #774-Leak strukturell (kein Vergleichswert eines herausgefilterten Felds im Client-JSON) und hält FE/BE deckungsgleich.
- **+** Der Kern ist DB-frei und ohne Django-Model-Import importierbar — leicht testbar, keine App-Registry nötig.
- **−** Zwei Implementierungen bleiben Pflege­aufwand. Die Prozessregel („zuerst als Vektor") und der Paritätstest sind die dauerhafte Gegenmaßnahme; wird sie umgangen, kehrt die Drift-Gefahr zurück.
- **−** Exakte String-Vergleiche (kein Trim/Casefold) können Anwender:innen überraschen (`"Ja"` ≠ `"ja"`). Bewusst akzeptiert (KISS, vorhersagbar); eine Lockerung wäre eine Semantikänderung und liefe zuerst über einen neuen Vektor.
- **−** Die `time`-`":00"`-Normalisierung ist eine leicht zu übersehende Detailfalle; sie ist deshalb mit dedizierten beidseitigen `normalize`-Asserts abgesichert.
- **−** `boolean` ohne `is_empty` ist eine bewusste Einschränkung: „Checkbox nicht gesetzt" wird über `equals false` ausgedrückt, nicht über Leere.

## Alternatives considered

- **Serverseitige Sichtbarkeits-Auswertung per HTMX-Roundtrip statt Client-Evaluator.** Verworfen: jeder Tastendruck ein Request wäre spürbar träge und widerspricht der HTMX/Alpine-Direktheit ([ADR-003](003-htmx-alpine-tailwind.md)). Der Server wertet weiterhin mit — aber nur zur Pflichtvalidierung in `clean()`, nicht für die Live-Anzeige.
- **Nur eine Implementierung (z.B. WASM-Python im Browser oder JS-Transpilat).** Verworfen: neue, schwergewichtige Abhängigkeit gegen die „keine neuen Dependencies"-Regel; die gemeinsamen Vektoren erreichen dasselbe Ziel (verlässliche Parität) ohne neuen Stack.
- **`field_id`/PK statt Slug als Identifier.** Verworfen: Formular-Keys und `data_json`-Keys **sind** Slugs; ein PK-basierter Evaluator bräuchte an jeder Grenze eine Übersetzung ([ADR-024](024-slug-based-identifiers.md)).
- **Werte versteckter Felder beim Ausblenden löschen (Clear-on-Hide).** Verworfen für v1: „Behalten" ist die getroffene Nutzer-Entscheidung (E6) und verhindert Datenverlust beim versehentlichen Aus-/Wiedereinblenden. Eine Datenminimierungs-Variante bleibt ein mögliches späteres Follow-up.
- **Trim/Casefold bei String-Vergleichen, zusätzliche Operatoren (BETWEEN, REGEX, IN), verschachtelte/NOT-Gruppen.** Verworfen (KISS/YAGNI): die zweistufige AND/OR-Logik mit exakten Vergleichen deckt die M1-Anforderung ab; jede Erweiterung wäre eine Semantikänderung und liefe zuerst über einen Vektor.

## References

- [`src/core/services/conditions.py`](../../src/core/services/conditions.py) — Python-Evaluator (Konstanten, `OPERATORS_BY_FIELD_TYPE`, `is_empty`, `normalize_value`, `evaluate_condition`, `evaluate_conditions`, `find_cycle`)
- [`src/tests/data/condition_vectors.json`](../../src/tests/data/condition_vectors.json) — ausführbare Spezifikation (gemeinsame Testvektoren)
- [`src/tests/test_conditions_evaluator.py`](../../src/tests/test_conditions_evaluator.py) — parametrisierte Python-Tests über die Vektoren
- [`src/static/js/alpine/conditions.js`](../../src/static/js/alpine/conditions.js) — JS-Pendant (P5); `window.AnlaufstelleConditions.evaluate(...)`
- Issue #1057 — M1-Planung (Grundsatzentscheidungen E5–E14)
- Issue #774 — Sensitivitäts-Leak, Grundlage der Drop-Regel
- [ADR-024](024-slug-based-identifiers.md) — Slug-basierte, stabile Identifikatoren
- [ADR-003](003-htmx-alpine-tailwind.md) — HTMX + Alpine.js (CSP-Rahmen der clientseitigen Auswertung)
