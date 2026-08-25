"""Gemeinsamer Evaluator fuer die bedingte Feldlogik (M1, Refs #1057).

Dieses Modul ist die **Python-Seite** der einen, gemeinsamen Semantik fuer
Feld-zu-Feld-Bedingungen innerhalb eines ``DocumentType`` ("zeige X, wenn
Y = Z"). Die identische Semantik liegt zusaetzlich in JavaScript
(``src/static/js/alpine/conditions.js``, P5); beide Seiten werden von der
ausfuehrbaren Spezifikation ``src/tests/data/condition_vectors.json``
gegeneinander abgesichert (parametrisierte pytest-Faelle + Playwright-
Paritaetstest). Prozessregel (ADR-035): **jede Semantikaenderung wird zuerst
dort als Vektor** ergaenzt.

Der Kern ist bewusst **DB-frei** und ohne Django-Model-Import — reine
Funktionen auf einfachen Datenstrukturen. Der modell-abhaengige
``build_conditions_payload`` (E12, P2) importiert ``FieldCondition``/
``DocumentTypeField`` erst innerhalb der Funktion (bzw. nur unter
``TYPE_CHECKING`` fuer Annotationen) — der restliche Evaluator-Kern bleibt
importierbar ohne App-Registry.

Kanonischer Identifier ist der ``FieldTemplate.slug`` (E1): ``fields`` bildet
jeden Formular-Slug auf seinen Feldtyp ab, ``values`` traegt die bereits
normalisierten Ist-Werte, das Ergebnis ist die Menge sichtbarer Slugs.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Nur fuer Typannotationen (PEP 563 macht sie ohnehin lazy) — kein
    # Laufzeit-Import, damit der Evaluator-Kern ohne Django-App-Registry
    # importierbar bleibt (Modulheader oben).
    from core.models import DocumentType

# --- Operator-Strings -------------------------------------------------------
# Identisch zu den ``FieldCondition.Operator``-Choices-Values (P2) und zu den
# JS-Konstanten (P5). Lowercase, stabil — als Keys in ``data_json``/Payload.
EQUALS = "equals"
NOT_EQUALS = "not_equals"
CONTAINS = "contains"
GREATER_THAN = "greater_than"
LESS_THAN = "less_than"
IS_EMPTY = "is_empty"
IS_NOT_EMPTY = "is_not_empty"

# Operatoren ohne Vergleichswert (``value is None``): pruefen nur die Leere.
VALUELESS_OPERATORS: frozenset[str] = frozenset({IS_EMPTY, IS_NOT_EMPTY})

# --- Feldtyp-Strings --------------------------------------------------------
# Bewusst als Literale gespiegelt (statt Import aus
# ``core.models.field_template.FieldTemplate.FieldType``), damit dieser
# Evaluator ohne Django-App-Registry importierbar bleibt. Werte MUESSEN mit
# der ``FieldType``-TextChoices uebereinstimmen — der ``FieldCondition``-
# Modell-clean() (P2) nutzt dieselben Konstanten aus diesem Modul.
TEXT = "text"
TEXTAREA = "textarea"
NUMBER = "number"
DATE = "date"
TIME = "time"
BOOLEAN = "boolean"
SELECT = "select"
MULTI_SELECT = "multi_select"
FILE = "file"

# Zulaessige Quelltypen: alle ausser ``file`` (E9). Verschluesselte Felder
# werden zusaetzlich modell-/editorseitig ausgeschlossen (P2/P6, #774).
ALLOWED_SOURCE_FIELD_TYPES: frozenset[str] = frozenset(
    {TEXT, TEXTAREA, NUMBER, DATE, TIME, BOOLEAN, SELECT, MULTI_SELECT}
)

# Operator x Typ-Matrix (E9). Zentrale Wahrheitsquelle — der Modell-clean()
# (P2) und das Editor-Form (P6) validieren gegen genau diese Mengen.
OPERATORS_BY_FIELD_TYPE: dict[str, frozenset[str]] = {
    TEXT: frozenset({EQUALS, NOT_EQUALS, CONTAINS, IS_EMPTY, IS_NOT_EMPTY}),
    TEXTAREA: frozenset({EQUALS, NOT_EQUALS, CONTAINS, IS_EMPTY, IS_NOT_EMPTY}),
    NUMBER: frozenset({EQUALS, NOT_EQUALS, GREATER_THAN, LESS_THAN, IS_EMPTY, IS_NOT_EMPTY}),
    # date/time vergleichen ISO-Strings lexikographisch = chronologisch.
    DATE: frozenset({EQUALS, NOT_EQUALS, GREATER_THAN, LESS_THAN, IS_EMPTY, IS_NOT_EMPTY}),
    TIME: frozenset({EQUALS, NOT_EQUALS, GREATER_THAN, LESS_THAN, IS_EMPTY, IS_NOT_EMPTY}),
    BOOLEAN: frozenset({EQUALS, NOT_EQUALS}),
    SELECT: frozenset({EQUALS, NOT_EQUALS, IS_EMPTY, IS_NOT_EMPTY}),
    # contains = Options-Slug ist in der Auswahl enthalten.
    MULTI_SELECT: frozenset({CONTAINS, IS_EMPTY, IS_NOT_EMPTY}),
}


def is_empty(value: Any) -> bool:
    """Leer-Definition (E10).

    Leer sind: ``None`` (bzw. fehlend), der leere String ``""`` und die leere
    Liste ``[]``/das leere Tupel. ``False`` und ``0`` sind bewusst **nicht**
    leer — eine deaktivierte Checkbox bzw. eine Null-Zahl sind gesetzte Werte
    (deshalb kennt ``boolean`` auch keinen ``is_empty``-Operator, E9).
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value == ""
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def normalize_value(field_type: str, value: Any) -> Any:
    """Bringt einen Roh-Feldwert der **Python-Seite** in die kanonische Form (E11).

    Der Kern-Evaluator arbeitet ausschliesslich auf normalisierten Werten; die
    JS-Seite (P5) implementiert dieselbe Abbildung. Ergebnisformen:

    * ``number``            -> Zahl oder ``None`` (leer)
    * ``date``             -> ``"YYYY-MM-DD"`` oder ``None``
    * ``time``             -> ``"HH:MM:SS"`` oder ``None`` (``time.isoformat``
      mit ``timespec="seconds"`` — kappt Mikrosekunden, damit ``time``-Objekte
      mit Sub-Sekunden-Praezision nicht an der JS-Seite vorbeidriften; die
      JS-Seite haengt an ``"HH:MM"`` ein ``":00"`` an — sonst schlagen
      ``equals``-Vergleiche fehl)
    * ``boolean``          -> ``bool``
    * ``select``/``text``/``textarea`` -> String oder ``None`` (``""`` -> ``None``)
    * ``multi_select``     -> Liste von Slugs
    """
    if field_type in (DATE, TIME):
        if value is None or value == "":
            return None
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            if field_type == TIME:
                # Mikrosekunden kappen (isoformat(timespec="seconds")) — sonst
                # weicht die Python-Seite von der JS-Seite ab, die aus dem DOM
                # nur "HH:MM"/"HH:MM:SS" liest und nie Mikrosekunden kennt.
                try:
                    return isoformat(timespec="seconds")
                except TypeError:
                    return isoformat()
            return isoformat()
        return value
    if field_type == NUMBER:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return value
    if field_type == BOOLEAN:
        return bool(value)
    if field_type == MULTI_SELECT:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]
    if field_type in (SELECT, TEXT, TEXTAREA):
        if value is None or value == "":
            return None
        return value
    # Unbekannter Typ (Defensive): leerer String -> None, sonst unveraendert.
    if value == "":
        return None
    return value


def evaluate_condition(operator: str, field_type: str, actual: Any, expected: Any) -> bool:
    """Wertet **eine** atomare Bedingung aus (Operator-Semantik E9).

    ``actual`` ist der normalisierte Ist-Wert des Quellfelds, ``expected`` der
    (bereits typisierte) Vergleichswert der Bedingung. Ein leerer Ist-Wert
    erfuellt **keinen** Wert-Operator (auch ``not_equals`` nicht) — nur
    ``is_empty`` schlaegt dann an. String-Vergleiche sind exakt (kein
    Trim/Casefold, KISS — ADR-035 § 2).
    """
    if operator == IS_EMPTY:
        return is_empty(actual)
    if operator == IS_NOT_EMPTY:
        return not is_empty(actual)
    # Ab hier Wert-Operatoren: leerer Ist-Wert erfuellt nie.
    if is_empty(actual):
        return False
    if operator == EQUALS:
        return bool(actual == expected)
    if operator == NOT_EQUALS:
        return bool(actual != expected)
    if operator == CONTAINS:
        if field_type == MULTI_SELECT:
            # Membership: gesuchter Options-Slug ist in der Auswahl.
            return expected in actual
        # Substring bei Text/Textbereich.
        return str(expected) in str(actual)
    if operator == GREATER_THAN:
        # number: numerisch; date/time: lexikographisch (= chronologisch).
        return bool(actual > expected)
    if operator == LESS_THAN:
        return bool(actual < expected)
    # Unbekannter Operator (Defensive): niemals sichtbar schalten.
    return False


def evaluate_conditions(
    fields: Mapping[str, str],
    conditions: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
) -> set[str]:
    """Berechnet die Menge sichtbarer Feld-Slugs (Drop-Regel E8 + Kaskade E7).

    Parameter:

    * ``fields``     -- Abbildung ``{slug: field_type}`` **aller** Felder des
      aktuellen Formulars (die aus dem E12-Payload abgeleitete Kurzform).
    * ``conditions`` -- E12-Bedingungen; jede mit den Schluesseln ``target``,
      ``source``, ``operator``, ``value`` und ``group``.
    * ``values``     -- bereits normalisierte Ist-Werte je Slug (E14).

    Regeln:

    * Felder ohne (verbleibende) Bedingung sind immer sichtbar (E5).
    * Bedingungen, deren ``source`` **oder** ``target`` nicht im Formular
      liegt, werden vor der Auswertung verworfen (Drop-Regel E8, #774) — das
      verhindert, dass Vergleichswerte sensibilitaets-beschraenkter Felder ins
      Frontend gelangen und haelt FE/BE deckungsgleich.
    * ``AND`` innerhalb einer ``group``, ``OR`` ueber Gruppen (E5).
    * Ein **unsichtbares** Quellfeld gilt als leer (Kaskade E7). Die
      Fixpunkt-Iteration konvergiert, weil Zyklen modellseitig ausvalidiert
      sind; die Obergrenze ist die Anzahl Zielfelder + 1.
    """
    all_slugs = set(fields)
    relevant = [
        condition for condition in conditions if condition["source"] in fields and condition["target"] in fields
    ]
    targets = {condition["target"] for condition in relevant}

    conditions_by_target: dict[str, list[Mapping[str, Any]]] = {}
    for condition in relevant:
        conditions_by_target.setdefault(condition["target"], []).append(condition)

    visible: set[str] = set(all_slugs)
    for _ in range(len(targets) + 1):
        nxt = all_slugs - targets
        for target in targets:
            groups: dict[int, list[Mapping[str, Any]]] = {}
            for condition in conditions_by_target[target]:
                groups.setdefault(int(condition.get("group", 0)), []).append(condition)
            target_visible = any(
                all(
                    evaluate_condition(
                        str(condition["operator"]),
                        fields[condition["source"]],
                        # Unsichtbare Quelle wie leer auswerten (E7).
                        values.get(condition["source"]) if condition["source"] in visible else None,
                        condition.get("value"),
                    )
                    for condition in group
                )
                for group in groups.values()
            )
            if target_visible:
                nxt.add(target)
        if nxt == visible:
            break
        visible = nxt
    return visible


def find_cycle(edges: Iterable[tuple[str, str]]) -> list[str] | None:
    """Sucht einen gerichteten Zyklus in den ``(source, target)``-Kanten.

    Drei-Farben-DFS (weiss/grau/schwarz). Rueckgabe: der geschlossene
    Zykluspfad ``[a, b, ..., a]`` (Start = Ende) fuer die Editor-Fehlermeldung
    (z.B. ``["dauer", "notiz", "dauer"]``), sonst ``None`` bei einem
    azyklischen Graphen. Wird vom ``FieldCondition``-Modell-clean() (P2) mit
    den bestehenden Kanten plus der neuen/geaenderten Kante aufgerufen.
    """
    graph: dict[str, list[str]] = {}
    for source, target in edges:
        graph.setdefault(source, []).append(target)
        graph.setdefault(target, [])

    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(graph, white)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = gray
        path.append(node)
        for neighbour in graph[node]:
            if color[neighbour] == gray:
                start = path.index(neighbour)
                return path[start:] + [neighbour]
            if color[neighbour] == white:
                found = visit(neighbour)
                if found is not None:
                    return found
        path.pop()
        color[node] = black
        return None

    for node in graph:
        if color[node] == white:
            found = visit(node)
            if found is not None:
                return found
    return None


def build_conditions_payload(
    document_type: DocumentType,
    form_field_slugs: Collection[str],
    conditions=None,
) -> dict[str, Any] | None:
    """Baut das E12-Payload-Dict fuer ein konkretes Formular, oder ``None``.

    Modell-abhaengig (P2) — der Django-Model-Import bleibt lokal in dieser
    Funktion, damit der Rest des Moduls (Evaluator-Kern) weiterhin ohne
    App-Registry importierbar ist (Modulheader).

    * ``fields``     -- ``{slug: {"type": field_type}}`` fuer alle
      ``form_field_slugs`` dieses ``document_type`` (die Kurzform, die
      ``evaluate_conditions`` als ``fields``-Parameter erwartet).
    * ``conditions`` -- alle ``FieldCondition``s des ``document_type``,
      E12-Format; Bedingungen mit ``source`` oder ``target`` ausserhalb von
      ``form_field_slugs`` werden verworfen (Drop-Regel E8, #774).

    ``conditions`` (optional, #1716): bereits geladene ``FieldCondition``-Objekte
    des ``document_type`` (select_related auf beide ``field_template``-Ketten).
    Ist der Parameter gesetzt, entfaellt der eigene Condition-Query — der Aufrufer
    (``DynamicEventDataForm``) hat sie in ``__init__`` schon einmal geladen. Der
    Vertrag (ADR-035 § 8) bleibt unangetastet: der Payload-Bau laeuft weiterhin
    NACH ``remove_restricted_fields``, die Drop-Regel unveraendert hier.

    Rueckgabe ``None``, wenn nach der Drop-Regel keine Bedingung uebrig
    bleibt (kein Payload noetig — auch wenn ``FieldCondition``s fuer den
    ``document_type`` existieren, aber keine davon zum aktuellen
    Formular-Feldset passt).
    """
    from core.models import DocumentTypeField, FieldCondition

    slugs = set(form_field_slugs)

    dtfs = DocumentTypeField.objects.filter(document_type=document_type, field_template__slug__in=slugs).select_related(
        "field_template"
    )
    fields = {dtf.field_template.slug: {"type": dtf.field_template.field_type} for dtf in dtfs}

    if conditions is None:
        conditions = FieldCondition.objects.filter(target_field__document_type=document_type).select_related(
            "target_field__field_template", "depends_on_field__field_template"
        )
    result_conditions: list[dict[str, Any]] = []
    for condition in conditions:
        source_slug = condition.depends_on_field.field_template.slug
        target_slug = condition.target_field.field_template.slug
        if source_slug not in slugs or target_slug not in slugs:
            continue  # Drop-Regel E8/#774
        result_conditions.append(
            {
                "target": target_slug,
                "source": source_slug,
                "operator": condition.operator,
                "value": condition.value,
                "group": condition.group_index,
            }
        )

    if not result_conditions:
        return None
    return {"fields": fields, "conditions": result_conditions}
