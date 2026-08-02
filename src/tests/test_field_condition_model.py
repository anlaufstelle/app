"""Tests für FieldCondition (M1 P2, Refs #1057).

Deckt die ``clean()``-Validierungsreihenfolge (Cross-Document, Quelltyp,
Operator×Typ-Matrix, Wert-Pflicht, Zyklen), den DB-``CheckConstraint`` gegen
Selbstreferenz, ``Meta.ordering`` sowie ``build_conditions_payload`` (E12,
Drop-Regel E8, ``None``-Fall ohne verbleibende Bedingungen) ab. Die
Auswertungssemantik selbst (Operator-Matrix-Werte, Leer-Definition, Kaskade)
lebt in ``core.services.conditions`` (ADR-035) und ist in
``test_conditions_evaluator.py`` DB-frei abgedeckt — hier wird nur geprüft,
dass das Modell gegen genau dieselben Konstanten validiert.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import DocumentTypeField, FieldCondition, FieldTemplate
from core.services.conditions import build_conditions_payload


def _make_dtf(document_type, *, name, field_type, sort_order=9, **extra):
    ft = FieldTemplate.objects.create(
        facility=document_type.facility,
        name=name,
        field_type=field_type,
        **extra,
    )
    return DocumentTypeField.objects.create(document_type=document_type, field_template=ft, sort_order=sort_order)


def _condition(*, target, source, operator, value=None, group_index=0, validate=True):
    condition = FieldCondition(
        target_field=target,
        depends_on_field=source,
        operator=operator,
        value=value,
        group_index=group_index,
    )
    if validate:
        condition.full_clean()
    condition.save()
    return condition


@pytest.fixture
def dauer_dtf(doc_type_contact):
    """Zahl-Feld ``dauer`` aus der ``doc_type_contact``-Fixture (D7)."""
    return doc_type_contact.fields.get(field_template__slug="dauer")


@pytest.fixture
def notiz_dtf(doc_type_contact):
    """Textbereich-Feld ``notiz`` aus der ``doc_type_contact``-Fixture (D7)."""
    return doc_type_contact.fields.get(field_template__slug="notiz")


@pytest.fixture
def aktiv_dtf(doc_type_contact):
    """Drittes Feld (boolean) für Zyklen mit >2 Knoten und Ordering-Tests."""
    return _make_dtf(doc_type_contact, name="Aktiv", field_type=FieldTemplate.FieldType.BOOLEAN, sort_order=5)


@pytest.fixture
def select_dtf(doc_type_contact):
    return _make_dtf(
        doc_type_contact,
        name="Auswahl",
        field_type=FieldTemplate.FieldType.SELECT,
        sort_order=6,
        options_json=[
            {"slug": "opt_a", "label": "Option A", "is_active": True},
            {"slug": "opt_b", "label": "Option B (inaktiv)", "is_active": False},
        ],
    )


@pytest.fixture
def encrypted_text_dtf(doc_type_contact):
    return _make_dtf(
        doc_type_contact,
        name="Vertraulich",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        sort_order=7,
        is_encrypted=True,
    )


@pytest.fixture
def file_dtf(doc_type_contact):
    return _make_dtf(doc_type_contact, name="Anhang", field_type=FieldTemplate.FieldType.FILE, sort_order=8)


@pytest.mark.django_db
class TestFieldConditionCrossDocument:
    def test_cross_document_rejected(self, doc_type_contact, foreign_doc_type, notiz_dtf):
        foreign_target = _make_dtf(foreign_doc_type, name="Fremdes Ziel", field_type=FieldTemplate.FieldType.TEXT)
        condition = FieldCondition(
            target_field=foreign_target,
            depends_on_field=notiz_dtf,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            value=None,
        )
        with pytest.raises(ValidationError, match="selben Dokumentationstyp"):
            condition.clean()


@pytest.mark.django_db
class TestFieldConditionSourceType:
    def test_encrypted_source_rejected(self, notiz_dtf, encrypted_text_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=encrypted_text_dtf,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            value=None,
        )
        with pytest.raises(ValidationError, match="Verschlüsselte"):
            condition.clean()

    def test_file_source_rejected(self, notiz_dtf, file_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=file_dtf,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            value=None,
        )
        # FILE ist immer verschluesselt (FieldTemplate.save()) — die Typ-Meldung
        # (nicht die Verschluesselungs-Meldung) muss zuerst greifen (Plan-Reihenfolge).
        with pytest.raises(ValidationError, match="Feldtyp") as excinfo:
            condition.clean()
        assert "Verschlüsselte" not in str(excinfo.value)


@pytest.mark.django_db
class TestFieldConditionOperatorMatrix:
    @pytest.mark.parametrize(
        ("field_type", "operator"),
        [
            (FieldTemplate.FieldType.BOOLEAN, FieldCondition.Operator.CONTAINS),
            (FieldTemplate.FieldType.BOOLEAN, FieldCondition.Operator.IS_EMPTY),
            (FieldTemplate.FieldType.SELECT, FieldCondition.Operator.GREATER_THAN),
            (FieldTemplate.FieldType.MULTI_SELECT, FieldCondition.Operator.EQUALS),
            (FieldTemplate.FieldType.MULTI_SELECT, FieldCondition.Operator.GREATER_THAN),
        ],
    )
    def test_operator_type_matrix_enforced(self, doc_type_contact, notiz_dtf, field_type, operator):
        source = _make_dtf(doc_type_contact, name=f"Quelle-{field_type}-{operator}", field_type=field_type)
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=source,
            operator=operator,
            value=None,
        )
        with pytest.raises(ValidationError, match="Operator"):
            condition.clean()


@pytest.mark.django_db
class TestFieldConditionValueRequirement:
    def test_valueless_operator_requires_none_value(self, notiz_dtf, dauer_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.IS_EMPTY,
            value=5,
        )
        with pytest.raises(ValidationError, match="kein Vergleichswert"):
            condition.clean()

    def test_valueless_operator_with_none_value_passes(self, notiz_dtf, dauer_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            value=None,
        )
        condition.clean()  # kein Fehler

    def test_value_required_for_value_operators(self, notiz_dtf, dauer_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value=None,
        )
        with pytest.raises(ValidationError, match="erforderlich"):
            condition.clean()

    def test_boolean_value_must_be_true_or_false(self, doc_type_contact, notiz_dtf, aktiv_dtf):
        condition = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=aktiv_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value="ja",
        )
        with pytest.raises(ValidationError, match="wahr"):
            condition.clean()


@pytest.mark.django_db
class TestFieldConditionSelectValue:
    def test_select_value_must_be_option_slug(self, notiz_dtf, select_dtf):
        invalid = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=select_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value="does-not-exist",
        )
        with pytest.raises(ValidationError, match="Options-Slug"):
            invalid.clean()

        valid = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=select_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value="opt_a",
        )
        valid.clean()  # kein Fehler

        # Inaktive Slugs sind zulaessig (Bestandsdaten, analog forms/events.py).
        inactive_ok = FieldCondition(
            target_field=notiz_dtf,
            depends_on_field=select_dtf,
            operator=FieldCondition.Operator.NOT_EQUALS,
            value="opt_b",
        )
        inactive_ok.clean()  # kein Fehler


@pytest.mark.django_db
class TestFieldConditionSelfReferenceConstraint:
    def test_self_reference_check_constraint(self, dauer_dtf):
        """DB-Backstop (CheckConstraint) greift auch bei direktem .save() ohne clean()."""
        condition = FieldCondition(
            target_field=dauer_dtf,
            depends_on_field=dauer_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value=5,
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            condition.save()


@pytest.mark.django_db
class TestFieldConditionCycles:
    def test_direct_cycle_rejected(self, notiz_dtf, dauer_dtf):
        _condition(
            target=notiz_dtf,
            source=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=10,
        )
        reverse = FieldCondition(
            target_field=dauer_dtf,
            depends_on_field=notiz_dtf,
            operator=FieldCondition.Operator.IS_NOT_EMPTY,
            value=None,
        )
        with pytest.raises(ValidationError, match="Zyklus"):
            reverse.clean()

    def test_transitive_cycle_rejected(self, notiz_dtf, dauer_dtf, aktiv_dtf):
        _condition(target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=10)
        _condition(target=aktiv_dtf, source=notiz_dtf, operator=FieldCondition.Operator.IS_NOT_EMPTY, value=None)
        closing = FieldCondition(
            target_field=dauer_dtf,
            depends_on_field=aktiv_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value=True,
        )
        with pytest.raises(ValidationError, match="Zyklus"):
            closing.clean()


@pytest.mark.django_db
class TestFieldConditionValidSave:
    def test_valid_condition_saves(self, notiz_dtf, dauer_dtf):
        condition = _condition(
            target=notiz_dtf,
            source=dauer_dtf,
            operator=FieldCondition.Operator.GREATER_THAN,
            value=10,
            group_index=2,
        )
        condition.refresh_from_db()
        assert condition.target_field_id == notiz_dtf.pk
        assert condition.depends_on_field_id == dauer_dtf.pk
        assert condition.operator == FieldCondition.Operator.GREATER_THAN
        assert condition.value == 10
        assert condition.group_index == 2


@pytest.mark.django_db
class TestFieldConditionOrdering:
    def test_ordering_group_then_id(self, doc_type_contact, dauer_dtf, notiz_dtf, aktiv_dtf, doc_type_field):
        # Alle Quellen = dauer (nie Ziel) -> keine Zyklen zwischen den drei Bedingungen.
        c_notiz = _condition(
            target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=5, group_index=0
        )
        c_aktiv = _condition(
            target=aktiv_dtf, source=dauer_dtf, operator=FieldCondition.Operator.LESS_THAN, value=100, group_index=0
        )
        c_other = _condition(
            target=doc_type_field,
            source=dauer_dtf,
            operator=FieldCondition.Operator.EQUALS,
            value=10,
            group_index=1,
        )

        ordered = list(FieldCondition.objects.filter(target_field__document_type=doc_type_contact))

        assert [c.group_index for c in ordered] == [0, 0, 1]
        assert ordered[2].pk == c_other.pk
        # Innerhalb group_index=0: aufsteigend nach id (UUID-Vergleich).
        group0_ids = sorted([c_notiz.pk, c_aktiv.pk])
        assert [c.pk for c in ordered[:2]] == group0_ids


@pytest.mark.django_db
class TestBuildConditionsPayload:
    """E12-Payload-Builder (P2-Ergaenzung, Plan-P1-Spezifikation)."""

    def test_payload_schema(self, doc_type_contact, dauer_dtf, notiz_dtf):
        _condition(
            target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=10, group_index=0
        )

        payload = build_conditions_payload(doc_type_contact, {"dauer", "notiz"})

        assert payload == {
            "fields": {"dauer": {"type": "number"}, "notiz": {"type": "textarea"}},
            "conditions": [
                {"target": "notiz", "source": "dauer", "operator": "greater_than", "value": 10, "group": 0},
            ],
        }

    def test_payload_drop_rule_source_not_in_form(self, doc_type_contact, dauer_dtf, notiz_dtf):
        """E8/#774: Quelle ausserhalb des uebergebenen Formular-Slug-Sets wird verworfen."""
        _condition(target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=10)

        # ``dauer`` (die Quelle) ist NICHT in form_field_slugs -> Bedingung faellt weg.
        payload = build_conditions_payload(doc_type_contact, {"notiz"})

        assert payload is None

    def test_payload_drop_rule_target_not_in_form(self, doc_type_contact, dauer_dtf, notiz_dtf):
        _condition(target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=10)

        # ``notiz`` (das Ziel) ist NICHT in form_field_slugs -> Bedingung faellt weg.
        payload = build_conditions_payload(doc_type_contact, {"dauer"})

        assert payload is None

    def test_payload_none_without_conditions(self, doc_type_contact):
        assert build_conditions_payload(doc_type_contact, {"dauer", "notiz"}) is None

    def test_payload_none_without_remaining_conditions_after_drop(self, doc_type_contact, dauer_dtf, notiz_dtf):
        """Auch wenn Bedingungen existieren: fallen ALLE der Drop-Regel zum Opfer -> ``None``."""
        _condition(target=notiz_dtf, source=dauer_dtf, operator=FieldCondition.Operator.GREATER_THAN, value=10)

        payload = build_conditions_payload(doc_type_contact, {"irgendetwas-anderes"})

        assert payload is None
