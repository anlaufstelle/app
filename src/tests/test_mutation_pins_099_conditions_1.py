"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — Batch ``conditions-1``.

Quelle: ``welle-l-0.99/reports/m-scratch/batch-conditions-1.json`` (15
Survivor-Mutanten, Triage ``welle-l-0.99/reports/m-scratch/pins-conditions-1/
triage-099-conditions-1.json``). Ein Modul: ``core.services.conditions``
(der DB-freie Bedingungs-Evaluator + der modell-abhaengige E12-Payload-
Builder ``build_conditions_payload``, M1, Refs #1057).

**Fuenf Aequivalenz-Urteile (kein Pin):**

* ``x__normalize_number__mutmut_1``: ``value is None or value == ""`` ->
  ``value is None and value == ""``. Beide Teilbedingungen koennen nie
  gleichzeitig wahr sein — der ``and``-Zweig ist toter Code und faellt
  IMMER durch. Fuer ``value=None`` liefert der Fallthrough
  (``isinstance(None, str)`` ist falsch -> ``return value``) dasselbe
  ``None`` wie der Original-Fruehausstieg; fuer ``value=""`` liefert der
  Fallthrough (``isinstance("", str)`` ist wahr, ``float("")`` wirft
  ``ValueError`` -> ``None``) ebenfalls dasselbe Ergebnis. Kein Eingabewert
  unterscheidet die beiden Fassungen.
* ``x__normalize_number__mutmut_4``: ``value == ""`` -> ``value == "XXXX"``.
  ``value=""`` faellt beim Mutanten in denselben ``float("")``/
  ``ValueError``-Fallthrough wie oben (-> ``None``); ``value="XXXX"`` nimmt
  beim Original denselben ``float("XXXX")``/``ValueError``-Pfad (-> ``None``),
  den der Mutant per Literalvergleich direkt trifft. Beide Wege enden am
  selben Ergebnis.
* ``x_normalize_value__mutmut_12``: ``if field_type in (SELECT, TEXT,
  TEXTAREA):`` -> ``not in``. Der dadurch uebersprungene
  ``_normalize_nullable_value``-Aufruf (``None``/``""`` -> ``None``, sonst
  unveraendert) und der defensive Fallback direkt darunter (``if value ==
  "": return None; return value``) sind fuer JEDEN Wert deckungsgleich —
  welcher der beiden Pfade genommen wird, aendert das Ergebnis nie.
* ``x_find_cycle__mutmut_14``: Farbkonstante ``black`` wird von ``2`` auf
  ``3`` geaendert. ``black`` wird im gesamten Funktionskoerper nur
  ZUGEWIESEN (``color[node] = black``), nirgends per Gleichheit abgefragt —
  nur ``white``/``gray`` werden geprueft. Der konkrete Zahlenwert ist
  beliebig, solange er von ``white``(0)/``gray``(1) verschieden bleibt.
* ``x_find_cycle__mutmut_32``: ``color[node] = black`` -> ``color[node] =
  None``. Derselbe Befund wie bei ``mutmut_14`` — ``None`` erfuellt die
  Rolle des "fertig"-Markers genauso wie ``black``.

Alle fuenf Urteile sind zusaetzlich empirisch abgesichert (Diff-Skripte
gegen den extrahierten Original-/Mutanten-Code, ``find_cycle`` zusaetzlich
per Fuzz-Test ueber 2000 Zufallsgraphen) — Details in der Triage-Begruendung.

**Verbleibende zehn Survivors (Klasse ``test``):**

* ``x_evaluate_condition__mutmut_15`` / ``x__evaluate_group__mutmut_3``
  (Prio 2): ``CONTAINS`` bei ``field_type == MULTI_SELECT`` (Original:
  Mitgliedschaftspruefung) wird zu ``!= MULTI_SELECT`` (Original-Zweig
  vertauscht) bzw. ``_evaluate_group`` uebergibt IMMER ``field_type=None``
  statt des per ``fields[condition["source"]]`` nachgeschlagenen Typs —
  beide Male rutscht eine Mehrfachauswahl-Quelle in den Substring- statt
  den Membership-Pfad.
* ``x_evaluate_conditions__mutmut_26`` (Prio 2): Die dokumentierte
  Fixpunkt-Obergrenze ``range(len(targets) + 1)`` wird zu ``+ 2``.
  ``evaluate_conditions`` validiert selbst keine Zyklen (nur modellseitig
  ausvalidiert); bei einer echten oszillierenden Quell-Kaskade kippt die
  zusaetzliche Iteration das Endergebnis.
* ``x_find_cycle__mutmut_24`` (Prio 2): ``start = path.index(neighbour)``
  wird zu ``start = None`` — der zurueckgegebene Zykluspfad (fuer die
  Editor-Fehlermeldung) beginnt dann am DFS-Start statt am
  Zyklus-Einstiegsknoten.
* ``x_find_cycle__mutmut_28`` (Prio 2): ``if color[neighbour] == white:``
  wird zu ``!= white`` — an dieser Stelle (der Grau-/Zyklus-Fall ist bereits
  behandelt) bedeutet das effektiv ``== black``: die DFS rekursiert nie mehr
  in unbesuchte, dafuer in bereits fertige Knoten.
* ``x_build_conditions_payload__mutmut_4`` / ``_18`` / ``_19`` (Prio 2,
  Klassifikationsregel: Abfragezahl ist ein zulaessiges
  Unterscheidungsmerkmal): je ein verlorenes ``select_related``-Argument
  (``None`` statt ``"field_template"`` bzw. je eine der beiden
  ``FieldCondition``-Joins) fuehrt zu zusaetzlichen Lazy-Load-Queries
  (N+1); ein Pin mit fester, niedriger Query-Zahl toetet alle drei.
* ``x_build_conditions_payload__mutmut_7`` (Prio 1): verliert den
  ``document_type=``-Filter der ``DocumentTypeField``-Abfrage.
  ``FieldTemplate.slug`` ist nur PRO FACILITY eindeutig
  (``unique_facility_fieldtemplate_slug``) — ohne den Filter liefert die
  Query Zeilen JEDES DocumentType (auch fremder Facilities) mit passendem
  Slug (Mandanten-Scope-Verlust).
* ``x_build_conditions_payload__mutmut_8`` (Prio 1): verliert den
  ``field_template__slug__in=slugs``-Filter — ``fields`` enthaelt danach
  ALLE Felder des DocumentType statt nur die in ``form_field_slugs``
  angefragten (laut Docstring der bereits durch ``remove_restricted_fields``
  bereinigte, zulaessige Feldsatz — Datenschutz-/Berechtigungsrelevanz).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldTemplate
from core.services.conditions import (
    CONTAINS,
    IS_EMPTY,
    MULTI_SELECT,
    TEXT,
    _evaluate_group,
    build_conditions_payload,
    evaluate_condition,
    evaluate_conditions,
    find_cycle,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# evaluate_condition / _evaluate_group — CONTAINS-Zweig fuer multi_select
# ---------------------------------------------------------------------------


class TestEvaluateConditionContainsMultiSelect:
    def test_contains_bei_multi_select_prueft_mitgliedschaft_nicht_teilstring(self):
        """Pin ``x_evaluate_condition__mutmut_15``: ``field_type == MULTI_SELECT``
        wird zu ``!= MULTI_SELECT`` — der Membership- und der Substring-Pfad
        vertauschen sich. ``"a"`` ist KEIN Element der Auswahl ``["ab"]``
        (aber sehr wohl ein Teilstring von ``str(["ab"])``)."""
        assert evaluate_condition(CONTAINS, MULTI_SELECT, ["ab"], "a") is False

    def test_evaluate_group_reicht_den_tatsaechlichen_quelltyp_durch(self):
        """Pin ``x__evaluate_group__mutmut_3``: ``fields[condition["source"]]``
        wird zu ``None`` — ``_evaluate_group`` uebergaebe ``evaluate_condition``
        dann IMMER ``field_type=None`` statt des nachgeschlagenen Typs, wodurch
        derselbe Membership-/Substring-Fehler wie oben entstuende."""
        group = [{"operator": CONTAINS, "source": "quelle", "value": "a"}]
        fields = {"quelle": MULTI_SELECT}
        values = {"quelle": ["ab"]}

        assert _evaluate_group(group, fields, values, {"quelle"}) is False


# ---------------------------------------------------------------------------
# evaluate_conditions — Fixpunkt-Iterationsobergrenze
# ---------------------------------------------------------------------------


class TestEvaluateConditionsFixpunktObergrenze:
    def test_oszillierende_quell_kaskade_bricht_nach_dokumentierter_obergrenze_ab(self):
        """Pin ``x_evaluate_conditions__mutmut_26``: ``range(len(targets) + 1)``
        wird zu ``range(len(targets) + 2)``. ``evaluate_conditions`` validiert
        selbst keine Zyklen (Docstring: nur modellseitig ausvalidiert) — bei
        einer echten gegenseitigen ``is_empty``-Kaskade zwischen ``a`` und
        ``b`` oszilliert die Sichtbarkeit periodisch zwischen ``set()`` und
        ``{"a", "b"}``, ohne je einen Fixpunkt zu erreichen. Bei 2 Targets
        braucht das Original 3 Iterationen (Original endet bei ``set()``),
        der Mutant 4 (endet bei ``{"a", "b"}``) — die zusaetzliche Iteration
        aendert das zurueckgegebene Ergebnis."""
        fields = {"a": TEXT, "b": TEXT}
        conditions = [
            {"target": "a", "source": "b", "operator": IS_EMPTY, "value": None, "group": 0},
            {"target": "b", "source": "a", "operator": IS_EMPTY, "value": None, "group": 0},
        ]
        values = {"a": "x", "b": "y"}

        assert evaluate_conditions(fields, conditions, values) == set()


# ---------------------------------------------------------------------------
# find_cycle — Drei-Farben-DFS: Pfadstart und Rekursionsbedingung
# ---------------------------------------------------------------------------


class TestFindCyclePfad:
    def test_zyklus_pfad_beginnt_am_einstiegsknoten_nicht_am_dfs_start(self):
        """Pin ``x_find_cycle__mutmut_24``: ``start = path.index(neighbour)``
        wird zu ``start = None`` — ``path[None:]`` liefert den KOMPLETTEN
        bisherigen DFS-Pfad statt nur den Zyklus-Teilpfad. Mit einem
        azyklischen Vorlaufknoten ``x`` vor dem eigentlichen Zyklus ``a<->b``
        muss der zurueckgegebene Pfad bei ``a`` beginnen, nicht bei ``x``."""
        assert find_cycle([("x", "a"), ("a", "b"), ("b", "a")]) == ["a", "b", "a"]

    def test_rekursion_nur_in_unbesuchte_nicht_in_bereits_fertige_knoten(self):
        """Pin ``x_find_cycle__mutmut_28``: ``if color[neighbour] == white:``
        wird zu ``!= white`` — der Grau-/Zyklus-Fall ist zu diesem Zeitpunkt
        bereits durch den vorherigen ``if``-Zweig behandelt, ``!= white``
        bedeutet hier also effektiv ``== black``: die DFS rekursiert nie mehr
        in unbesuchte (weisse), dafuer in bereits fertig verarbeitete
        (schwarze) Knoten und faerbt sie erneut grau ein — das ergibt fuer
        den einfachen Zwei-Knoten-Zyklus einen ANDEREN (durch Re-Traversal
        entstandenen) Pfad."""
        assert find_cycle([("a", "b"), ("b", "a")]) == ["a", "b", "a"]


# ---------------------------------------------------------------------------
# build_conditions_payload — Scope- und Query-Regressionen
# ---------------------------------------------------------------------------


class TestBuildConditionsPayloadQueryCount:
    def test_dtf_und_condition_query_bleiben_bei_vollem_select_related_bei_zwei(self, doc_type_contact):
        """Pin ``x_build_conditions_payload__mutmut_4`` (``.select_related(
        "field_template")`` -> ``.select_related(None)`` bei der
        ``DocumentTypeField``-Abfrage), ``x_build_conditions_payload__mutmut_18``
        (``.select_related("target_field__field_template",
        "depends_on_field__field_template")`` verliert den ``target_field``-Join)
        und ``x_build_conditions_payload__mutmut_19`` (verliert stattdessen den
        ``depends_on_field``-Join). Alle drei Mutanten aendern den Rueckgabewert
        NICHT, wohl aber die ausgefuehrte Query-Zahl (N+1 beim Zugriff auf
        ``dtf.field_template`` bzw. ``condition.target_field.field_template``/
        ``condition.depends_on_field.field_template``) — Klassifikationsregel:
        Abfragezahl ist ein zulaessiges Unterscheidungsmerkmal, keine
        Aequivalenz. Mit vollem select_related bleibt es bei exakt 2 Queries
        (DocumentTypeField-Join + FieldCondition-Join); jeder der drei
        Mutanten treibt die Zahl auf 4 (je 2 zusaetzliche Lazy-Load-Queries)."""
        dauer_dtf = doc_type_contact.fields.get(field_template__slug="dauer")
        notiz_dtf = doc_type_contact.fields.get(field_template__slug="notiz")
        FieldCondition.objects.create(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=5,
            group_index=0,
        )

        with CaptureQueriesContext(connection) as ctx:
            payload = build_conditions_payload(doc_type_contact, {"dauer", "notiz"})

        assert payload is not None
        assert len(ctx.captured_queries) == 2, [q["sql"] for q in ctx.captured_queries]


class TestBuildConditionsPayloadScope:
    def test_ignoriert_document_type_fields_anderer_facilities_mit_gleichem_slug(
        self, doc_type_contact, second_facility
    ):
        """Pin ``x_build_conditions_payload__mutmut_7``: die
        ``DocumentTypeField``-Abfrage verliert ``document_type=document_type``
        und filtert nur noch nach ``field_template__slug__in=slugs``.
        ``FieldTemplate.slug`` ist ausschliesslich PRO FACILITY eindeutig
        (``unique_facility_fieldtemplate_slug``) — eine zweite Einrichtung
        kann also ein eigenes Feld mit einem Slug anlegen, der in
        ``form_field_slugs`` des aktuellen (fremden) Formulars vorkommt, ohne
        je einem ``DocumentTypeField`` von ``doc_type_contact`` zugeordnet zu
        sein. Original filtert diese Fremd-Zeile per ``document_type=``
        korrekt heraus, der Mutant liefert sie mit."""
        dauer_dtf = doc_type_contact.fields.get(field_template__slug="dauer")
        notiz_dtf = doc_type_contact.fields.get(field_template__slug="notiz")
        FieldCondition.objects.create(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=5,
            group_index=0,
        )

        foreign_doc_type = DocumentType.objects.create(facility=second_facility, name="Kontakt (fremd)")
        foreign_ft = FieldTemplate.objects.create(
            facility=second_facility,
            name="Fremdesfeld",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        DocumentTypeField.objects.create(document_type=foreign_doc_type, field_template=foreign_ft, sort_order=0)

        payload = build_conditions_payload(doc_type_contact, {"dauer", "notiz", "fremdesfeld"})

        assert payload is not None
        assert set(payload["fields"]) == {"dauer", "notiz"}

    def test_liefert_nur_angefragte_felder_nicht_alle_des_dokumenttyps(self, doc_type_contact):
        """Pin ``x_build_conditions_payload__mutmut_8``: die
        ``DocumentTypeField``-Abfrage verliert ``field_template__slug__in=slugs``
        und filtert nur noch nach ``document_type=document_type``. ``fields``
        enthaelt danach ALLE Felder des DocumentType, nicht nur die in
        ``form_field_slugs`` angefragten. ``form_field_slugs`` ist laut
        Docstring der bereits durch ``remove_restricted_fields`` bereinigte,
        fuer die aktuelle Sicht/Rolle zulaessige Feldsatz (ADR-035 § 8) — ein
        Feld, das ABSICHTLICH nicht angefragt wurde (hier ``notiz``), darf
        nicht ins Payload durchsickern."""
        dauer_dtf = doc_type_contact.fields.get(field_template__slug="dauer")
        ft_aktiv = FieldTemplate.objects.create(
            facility=doc_type_contact.facility,
            name="Aktiv",
            field_type=FieldTemplate.FieldType.BOOLEAN,
        )
        aktiv_dtf = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_aktiv, sort_order=2
        )
        FieldCondition.objects.create(
            target_field=aktiv_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=5,
            group_index=0,
        )

        # ``notiz`` ist Teil von ``doc_type_contact``, wird hier aber bewusst
        # NICHT angefragt.
        payload = build_conditions_payload(doc_type_contact, {"dauer", "aktiv"})

        assert payload is not None
        assert set(payload["fields"]) == {"dauer", "aktiv"}
