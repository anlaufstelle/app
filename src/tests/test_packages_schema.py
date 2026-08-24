"""Tests für das teilbare Paketformat v1 — strikte Schema-Validierung (M2 P10, Refs #1058).

``core.packages.schema.parse_package`` ist die einzige Eintrittspforte für
fremde Paket-Dicts (das YAML-Parsing selbst liegt im Command, P11). Jede
Validierungsregel wird hier mit einer **pfadgenauen** Fehlermeldung belegt —
Operator*innen sollen aus der CLI-Ausgabe direkt die betroffene Stelle im YAML
finden (``document_types[2].field_refs[0].template_slug``).

Rein in-memory: kein ``django_db``-Marker, keine Fixtures — das Schema-Modul
importiert die Modelle nur, um gegen deren **echte** Choices zu validieren.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from core.models import DocumentType, FieldCondition, FieldTemplate
from core.packages.schema import (
    KEY_MISSING,
    ConditionDef,
    DocumentTypeDef,
    FieldRefDef,
    FieldTemplateDef,
    GroupDef,
    PackageV1,
    PackageValidationError,
    parse_package,
)


def _valid_raw() -> dict[str, Any]:
    """Minimal gültiges Paket — Basis für alle Negativ-Mutationen."""
    return {
        "schema_version": 1,
        "field_templates": [
            {"slug": "dauer", "name": "Dauer", "field_type": "number", "help_text": "Dauer in Minuten"},
            {"slug": "notiz", "name": "Notiz", "field_type": "textarea"},
        ],
        "document_types": [
            {
                "slug": "kontakt",
                "name": "Kontakt",
                "entity_type": "event",
                # E4 (Refs #1850): ``system_type`` traegt nur noch Verhalten —
                # die Berichts-Zuordnung steht seit E3 im ``report_key``.
                "report_key": "contact",
                "sensitivity": "normal",
                "icon": "users",
                "color": "indigo",
                "sort_order": 0,
                "groups": [{"slug": "basisdaten", "name": "Basisdaten", "sort_order": 0, "is_collapsible": False}],
                "field_refs": [
                    {"template_slug": "dauer", "group_slug": "basisdaten", "sort_order": 0},
                    {"template_slug": "notiz", "sort_order": 1},
                ],
                "conditions": [
                    {"target": "notiz", "depends_on": "dauer", "operator": "greater_than", "value": 10},
                ],
            }
        ],
    }


def _paths(raw: Any) -> list[str]:
    """Fehlerpfade eines ungültigen Pakets (leer = das Paket war gültig)."""
    with pytest.raises(PackageValidationError) as excinfo:
        parse_package(raw)
    return [path for path, _message in excinfo.value.errors]


class TestValidPackage:
    def test_full_example_parses_into_frozen_dataclasses(self) -> None:
        package = parse_package(_valid_raw())

        assert isinstance(package, PackageV1)
        assert package.schema_version == 1
        assert isinstance(package.field_templates, tuple)
        assert [template.slug for template in package.field_templates] == ["dauer", "notiz"]

        dauer = package.field_templates[0]
        assert isinstance(dauer, FieldTemplateDef)
        assert dauer.field_type == "number"
        assert dauer.help_text == "Dauer in Minuten"
        assert dauer.is_required is False
        assert dauer.is_encrypted is False
        assert dauer.sensitivity == ""
        assert dauer.options == ()
        assert dauer.default_value == ""

        doc_type = package.document_types[0]
        assert isinstance(doc_type, DocumentTypeDef)
        assert doc_type.slug == "kontakt"
        assert doc_type.entity_type == "event"
        assert doc_type.system_type is None
        assert doc_type.report_key == "contact"
        assert doc_type.min_contact_stage is None
        assert isinstance(doc_type.groups[0], GroupDef)
        assert isinstance(doc_type.field_refs[0], FieldRefDef)
        assert doc_type.field_refs[1].group_slug is None
        assert doc_type.conditions is not None
        assert isinstance(doc_type.conditions[0], ConditionDef)
        assert doc_type.conditions[0].value == 10
        assert doc_type.conditions[0].group_index == 0

    def test_dataclasses_are_frozen(self) -> None:
        package = parse_package(_valid_raw())
        with pytest.raises(FrozenInstanceError):
            package.field_templates[0].slug = "anders"  # type: ignore[misc]

    def test_missing_conditions_key_yields_none_not_empty_tuple(self) -> None:
        raw = _valid_raw()
        del raw["document_types"][0]["conditions"]
        assert parse_package(raw).document_types[0].conditions is None

    def test_empty_conditions_list_yields_empty_tuple(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"] = []
        assert parse_package(raw).document_types[0].conditions == ()

    def test_optional_keys_fall_back_to_model_defaults(self) -> None:
        raw = {
            "schema_version": 1,
            "field_templates": [{"slug": "dauer", "name": "Dauer"}],
            "document_types": [{"slug": "kontakt", "name": "Kontakt"}],
        }
        package = parse_package(raw)
        assert package.field_templates[0].field_type == FieldTemplate.FieldType.TEXT
        doc_type = package.document_types[0]
        assert doc_type.entity_type == DocumentType.EntityType.EVENT
        assert doc_type.sensitivity == DocumentType.Sensitivity.NORMAL
        assert doc_type.ignored_keys == ()
        assert doc_type.system_type is None
        assert doc_type.groups == ()
        assert doc_type.field_refs == ()

    def test_options_on_select_template(self) -> None:
        raw = _valid_raw()
        raw["field_templates"].append(
            {
                "slug": "alterscluster",
                "name": "Alterscluster",
                "field_type": "select",
                "options": [
                    {"slug": "u18", "label": "U18"},
                    {"slug": "27-plus", "label": "27+", "is_active": False},
                ],
            }
        )
        options = parse_package(raw).field_templates[2].options
        assert options == (
            {"slug": "u18", "label": "U18", "is_active": True},
            {"slug": "27-plus", "label": "27+", "is_active": False},
        )


class TestStructure:
    def test_non_mapping_root_is_rejected(self) -> None:
        assert _paths([{"schema_version": 1}]) == ["<root>"]

    def test_missing_schema_version(self) -> None:
        raw = _valid_raw()
        del raw["schema_version"]
        assert "schema_version" in _paths(raw)

    def test_wrong_schema_version(self) -> None:
        raw = _valid_raw()
        raw["schema_version"] = 2
        assert "schema_version" in _paths(raw)

    def test_unknown_top_level_key(self) -> None:
        raw = _valid_raw()
        raw["bogus"] = 1
        assert "bogus" in _paths(raw)

    def test_unknown_field_template_key(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["bogus"] = 1
        assert "field_templates[0].bogus" in _paths(raw)

    def test_unknown_document_type_key(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["bogus"] = 1
        assert "document_types[0].bogus" in _paths(raw)

    def test_unknown_group_key(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["groups"][0]["bogus"] = 1
        assert "document_types[0].groups[0].bogus" in _paths(raw)

    def test_unknown_field_ref_key(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"][1]["bogus"] = 1
        assert "document_types[0].field_refs[1].bogus" in _paths(raw)

    def test_unknown_condition_key(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"][0]["bogus"] = 1
        assert "document_types[0].conditions[0].bogus" in _paths(raw)

    def test_unknown_option_key(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["field_type"] = "select"
        raw["field_templates"][0]["options"] = [{"slug": "a", "label": "A", "bogus": 1}]
        assert "field_templates[0].options[0].bogus" in _paths(raw)

    def test_field_templates_must_be_a_list(self) -> None:
        raw = _valid_raw()
        raw["field_templates"] = {"dauer": {}}
        assert "field_templates" in _paths(raw)

    def test_field_templates_null_is_rejected(self) -> None:
        """(b) Haertung Refs #1058: explizites ``null`` ist ein Typfehler
        (Muster ``conditions``) — nur der FEHLENDE Schluessel bleibt erlaubt."""
        raw = _valid_raw()
        raw["field_templates"] = None
        assert "field_templates" in _paths(raw)

    def test_document_types_null_is_rejected(self) -> None:
        raw = _valid_raw()
        raw["document_types"] = None
        assert "document_types" in _paths(raw)

    def test_missing_field_templates_key_is_fine(self) -> None:
        """Gegenprobe: der fehlende Schluessel ist (anders als ``null``) gueltig."""
        raw = {"schema_version": 1, "document_types": [{"slug": "kontakt", "name": "Kontakt"}]}
        assert parse_package(raw).field_templates == ()

    def test_missing_document_types_key_is_fine(self) -> None:
        raw = {"schema_version": 1, "field_templates": [{"slug": "dauer", "name": "Dauer"}]}
        assert parse_package(raw).document_types == ()

    def test_field_template_entry_must_be_a_mapping(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][1] = "notiz"
        assert "field_templates[1]" in _paths(raw)

    def test_missing_required_key(self) -> None:
        raw = _valid_raw()
        del raw["field_templates"][0]["name"]
        assert "field_templates[0].name" in _paths(raw)

    def test_all_errors_are_collected_not_only_the_first(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["field_type"] = "quatsch"
        raw["document_types"][0]["sensitivity"] = "quatsch"
        paths = _paths(raw)
        assert "field_templates[0].field_type" in paths
        assert "document_types[0].sensitivity" in paths

    def test_error_message_contains_path(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"][0]["template_slug"] = "unbekannt"
        with pytest.raises(PackageValidationError) as excinfo:
            parse_package(raw)
        assert "document_types[0].field_refs[0].template_slug" in str(excinfo.value)


class TestFieldTypes:
    def test_bad_slug_format(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["slug"] = "Dauer Ding"
        assert "field_templates[0].slug" in _paths(raw)

    def test_bad_group_slug_format(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["groups"][0]["slug"] = "Basis Daten"
        assert "document_types[0].groups[0].slug" in _paths(raw)

    def test_sort_order_must_be_int(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["groups"][0]["sort_order"] = "0"
        assert "document_types[0].groups[0].sort_order" in _paths(raw)

    def test_bool_is_not_accepted_as_int(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["sort_order"] = True
        assert "document_types[0].sort_order" in _paths(raw)

    def test_flag_must_be_bool(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["is_required"] = "ja"
        assert "field_templates[0].is_required" in _paths(raw)

    def test_help_text_must_be_str(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["help_text"] = 5
        assert "field_templates[0].help_text" in _paths(raw)

    def test_empty_name_is_rejected(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["name"] = "   "
        assert "document_types[0].name" in _paths(raw)


class TestIgnorierteSchluessel:
    """``category`` wird angenommen, aber nicht angewendet (Refs #1749, ADR-040).

    Bewusst **kein** ``schema_version``-2-Cutover: Bestands-Pakete (und ältere
    Kopien von ``default.yaml``) müssen ladbar bleiben. Damit daraus kein
    Silent-Drop wird — was dem Strict-Design des Formats widerspräche —,
    vermerkt der Parser den Schlüssel, und der Loader macht daraus eine
    Warnung (siehe ``test_packages_loader``).
    """

    def test_category_ist_kein_unbekannter_schluessel(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["category"] = "contact"
        parse_package(raw)

    def test_category_wird_als_ignoriert_vermerkt(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["category"] = "contact"
        assert parse_package(raw).document_types[0].ignored_keys == ("category",)

    def test_beliebiger_category_wert_ist_kein_fehler_mehr(self) -> None:
        """Es gibt keine Werteliste mehr, gegen die geprüft werden könnte."""
        raw = _valid_raw()
        raw["document_types"][0]["category"] = "quatsch"
        assert parse_package(raw).document_types[0].ignored_keys == ("category",)

    def test_ohne_category_bleibt_ignored_keys_leer(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0].pop("category", None)
        assert parse_package(raw).document_types[0].ignored_keys == ()

    def test_schema_version_bleibt_bei_1(self) -> None:
        from core.packages.schema import SCHEMA_VERSION

        assert SCHEMA_VERSION == 1


class TestEnums:
    def test_unknown_field_type(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["field_type"] = "quatsch"
        assert "field_templates[0].field_type" in _paths(raw)

    def test_unknown_document_type_sensitivity(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["sensitivity"] = "quatsch"
        assert "document_types[0].sensitivity" in _paths(raw)

    def test_empty_sensitivity_is_invalid_for_document_types(self) -> None:
        # DocumentType.sensitivity ist NICHT blank — anders als FieldTemplate.
        raw = _valid_raw()
        raw["document_types"][0]["sensitivity"] = ""
        assert "document_types[0].sensitivity" in _paths(raw)

    def test_empty_sensitivity_is_valid_for_field_templates(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["sensitivity"] = ""
        assert parse_package(raw).field_templates[0].sensitivity == ""

    def test_unknown_entity_type(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["entity_type"] = "quatsch"
        assert "document_types[0].entity_type" in _paths(raw)

    def test_unknown_system_type(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["system_type"] = "quatsch"
        assert "document_types[0].system_type" in _paths(raw)

    def test_verhaltens_system_type_wird_uebernommen(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["system_type"] = "crisis"
        assert parse_package(raw).document_types[0].system_type == "crisis"

    def test_entfallener_system_type_laedt_und_entfaellt(self) -> None:
        """E4 (Refs #1850): ein Alt-Paket mit ``system_type: medical`` bleibt ladbar.

        Werte-Ebenen-Toleranz (Konzept #1847 §E3.8, hier auf den reduzierten
        Systemtyp angewendet): ``parse_package`` wirft, **bevor** ein
        ``PackageV1`` existiert — ein einziger Altwert machte sonst das ganze
        Paket unladbar, obwohl er nur eine Zeile betrifft und die Zuordnung
        laengst am ``report_key`` haengt. Der Wert wird nicht uebersetzt,
        sondern faellt weg; sichtbar bleibt er ueber ``removed_system_types``
        (der Loader macht daraus eine Warnung).
        """
        for entfallen in ("contact", "medical", "needle_exchange", "accompaniment", "counseling", "referral", "note"):
            raw = _valid_raw()
            raw["document_types"][0]["system_type"] = entfallen
            doc_type = parse_package(raw).document_types[0]
            assert doc_type.system_type is None
            assert doc_type.removed_system_types == (entfallen,)

    def test_entfallener_system_type_ist_auch_ausserhalb_von_ereignissen_kein_fehler(self) -> None:
        """Der ``entity_type``-Guard darf nicht auf einen Wert anspringen, der ohnehin entfaellt."""
        raw = _valid_raw()
        raw["document_types"].append(
            {"slug": "stammdaten", "name": "Stammdaten", "entity_type": "case", "system_type": "note"}
        )
        package = parse_package(raw)
        assert package.document_types[1].system_type is None
        assert package.document_types[1].removed_system_types == ("note",)

    def test_unknown_min_contact_stage(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["min_contact_stage"] = "quatsch"
        assert "document_types[0].min_contact_stage" in _paths(raw)

    def test_known_min_contact_stage_is_accepted(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["min_contact_stage"] = "qualified"
        assert parse_package(raw).document_types[0].min_contact_stage == "qualified"

    def test_unknown_operator(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"][0]["operator"] = "quatsch"
        assert "document_types[0].conditions[0].operator" in _paths(raw)

    def test_every_model_operator_is_accepted(self) -> None:
        for operator in FieldCondition.Operator.values:
            raw = _valid_raw()
            raw["document_types"][0]["conditions"][0]["operator"] = operator
            parse_package(raw)

    def test_negative_group_index(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"][0]["group_index"] = -1
        assert "document_types[0].conditions[0].group_index" in _paths(raw)


class TestCrossReferences:
    def test_unknown_template_slug_in_field_refs(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"][0]["template_slug"] = "unbekannt"
        assert "document_types[0].field_refs[0].template_slug" in _paths(raw)

    def test_unknown_group_slug_in_field_refs(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"][0]["group_slug"] = "unbekannt"
        assert "document_types[0].field_refs[0].group_slug" in _paths(raw)

    def test_condition_target_must_be_referenced_field(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"][0]["target"] = "dauer"
        raw["document_types"][0]["conditions"][0]["depends_on"] = "unbekannt"
        assert "document_types[0].conditions[0].depends_on" in _paths(raw)

    def test_condition_target_not_in_field_refs(self) -> None:
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "extern", "name": "Extern", "field_type": "text"})
        raw["document_types"][0]["conditions"][0]["target"] = "extern"
        assert "document_types[0].conditions[0].target" in _paths(raw)

    def test_condition_self_reference(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["conditions"][0]["target"] = "dauer"
        assert "document_types[0].conditions[0].depends_on" in _paths(raw)

    def test_duplicate_field_template_slug(self) -> None:
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "dauer", "name": "Dauer 2", "field_type": "text"})
        assert "field_templates[2].slug" in _paths(raw)

    def test_duplicate_document_type_slug(self) -> None:
        raw = _valid_raw()
        raw["document_types"].append({"slug": "kontakt", "name": "Kontakt 2"})
        assert "document_types[1].slug" in _paths(raw)

    def test_duplicate_group_slug(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["groups"].append({"slug": "basisdaten", "name": "Basisdaten 2"})
        assert "document_types[0].groups[1].slug" in _paths(raw)

    def test_duplicate_field_ref_template_slug(self) -> None:
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"].append({"template_slug": "dauer"})
        assert "document_types[0].field_refs[2].template_slug" in _paths(raw)

    def test_duplicate_field_template_name(self) -> None:
        """(c) Haertung Refs #1058: gleicher ``name`` bei zwei Feldvorlagen ist
        ein Namensdublett (unabhaengig vom Slug) und wird pfadgenau abgewiesen."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "dauer-2", "name": "Dauer", "field_type": "text"})
        assert "field_templates[2].name" in _paths(raw)

    def test_duplicate_document_type_name(self) -> None:
        """(c) gleicher ``name`` bei zwei Dokumentationstypen (Refs #1749).

        Bis ADR-040 war das Paar ``(name, category)`` massgeblich und derselbe
        Name in zwei Kategorien erlaubt — die Regel nimmt jetzt die
        DB-Constraint ``(facility, name)`` vorweg.
        """
        raw = _valid_raw()
        raw["document_types"].append({"slug": "kontakt-2", "name": "Kontakt"})
        assert "document_types[1].name" in _paths(raw)

    # --- Blockpositions-Eindeutigkeit (Härtung Refs #1727, ADR-036) ----------

    def test_duplicate_block_position_two_groups(self) -> None:
        """Zwei Gruppen auf derselben globalen Blockposition kollidieren."""
        raw = _valid_raw()
        raw["document_types"][0]["groups"].append({"slug": "weitere", "name": "Weitere", "sort_order": 0})
        assert "document_types[0].groups[1].sort_order" in _paths(raw)

    def test_duplicate_block_position_group_and_free_field(self) -> None:
        """Ein freies Feld auf der Blockposition einer Gruppe kollidiert — beide
        teilen sich die globale Block-Sequenz."""
        raw = _valid_raw()
        raw["document_types"][0]["field_refs"][1]["sort_order"] = 0  # notiz (frei) auf 0 wie Gruppe basisdaten
        assert "document_types[0].field_refs[1].sort_order" in _paths(raw)

    def test_duplicate_block_position_two_free_fields(self) -> None:
        """Zwei freie Felder auf derselben Blockposition kollidieren."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "extra", "name": "Extra", "field_type": "text"})
        raw["document_types"][0]["field_refs"].append({"template_slug": "extra", "sort_order": 1})  # wie notiz
        assert "document_types[0].field_refs[2].sort_order" in _paths(raw)

    def test_duplicate_inner_position_within_group(self) -> None:
        """Zwei Felder mit demselben Innen-Index in derselben Gruppe kollidieren."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "extra", "name": "Extra", "field_type": "text"})
        raw["document_types"][0]["field_refs"].append(
            {"template_slug": "extra", "group_slug": "basisdaten", "sort_order": 0}  # wie dauer
        )
        assert "document_types[0].field_refs[2].sort_order" in _paths(raw)

    def test_inner_index_may_equal_global_block_position(self) -> None:
        """Positivfall Gruppen-intern: ein Innen-Index (dauer=0 in basisdaten)
        darf gleich einer globalen Blockposition (basisdaten=0) sein — zwei
        unabhängige Sequenzen. ``_valid_raw`` demonstriert genau das."""
        parse_package(_valid_raw())  # wirft nicht

    def test_same_inner_index_in_different_groups_is_allowed(self) -> None:
        """Positivfall: gleicher Innen-Index in verschiedenen Gruppen ist ok."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "extra", "name": "Extra", "field_type": "text"})
        raw["document_types"][0]["groups"].append({"slug": "weitere", "name": "Weitere", "sort_order": 2})
        raw["document_types"][0]["field_refs"].append(
            {"template_slug": "extra", "group_slug": "weitere", "sort_order": 0}  # Innen-0 wie dauer, andere Gruppe
        )
        parse_package(raw)  # basisdaten=0, notiz(frei)=1, weitere=2 → global eindeutig


class TestSemanticRules:
    def test_file_field_on_client_entity_is_allowed(self) -> None:
        """Anforderungsaenderung M24 (Refs #1738, ADR-039 §9): Die E8-Sperre
        („Datei-Felder nur an Ereignis-Typen", Refs #1058) faellt ersatzlos —
        Anhaenge haengen jetzt auch an Personen und Faellen. Vorher war genau
        dieses Paket ein Validierungsfehler."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "anhang", "name": "Anhang", "field_type": "file", "is_encrypted": True})
        raw["document_types"].append(
            {
                "slug": "stammdaten",
                "name": "Stammdaten",
                "entity_type": "client",
                "field_refs": [{"template_slug": "anhang"}],
            }
        )
        package = parse_package(raw)
        assert package.document_types[1].field_refs[0].template_slug == "anhang"

    def test_file_field_on_case_entity_is_allowed(self) -> None:
        """Spiegel zum Personen-Typ (Anforderungsaenderung M24, Refs #1738)."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "beleg", "name": "Beleg", "field_type": "file", "is_encrypted": True})
        raw["document_types"].append(
            {
                "slug": "fallbogen",
                "name": "Fallbogen",
                "entity_type": "case",
                "field_refs": [{"template_slug": "beleg"}],
            }
        )
        package = parse_package(raw)
        assert package.document_types[1].field_refs[0].template_slug == "beleg"

    def test_file_field_on_event_entity_is_allowed(self) -> None:
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "anhang", "name": "Anhang", "field_type": "file", "is_encrypted": True})
        raw["document_types"][0]["field_refs"].append({"template_slug": "anhang", "sort_order": 2})
        parse_package(raw)

    def test_system_type_only_on_event_entity(self) -> None:
        raw = _valid_raw()
        raw["document_types"].append(
            {"slug": "stammdaten", "name": "Stammdaten", "entity_type": "case", "system_type": "crisis"}
        )
        assert "document_types[1].system_type" in _paths(raw)

    def test_high_sensitivity_requires_encryption(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][1]["sensitivity"] = "high"
        assert "field_templates[1].is_encrypted" in _paths(raw)

    def test_high_sensitivity_with_encryption_is_accepted(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][1]["sensitivity"] = "high"
        raw["field_templates"][1]["is_encrypted"] = True
        parse_package(raw)

    def test_options_only_for_select_types(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["options"] = [{"slug": "a", "label": "A"}]
        assert "field_templates[0].options" in _paths(raw)

    def test_duplicate_option_slug(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["field_type"] = "select"
        raw["field_templates"][0]["options"] = [
            {"slug": "a", "label": "A"},
            {"slug": "a", "label": "A2"},
        ]
        assert "field_templates[0].options[1].slug" in _paths(raw)

    def test_reserved_field_template_slug_is_rejected(self) -> None:
        """#1726: Ein Feldvorlagen-Slug, der einen festen Formularfeld-Namen
        trifft (hier ``title``), ist ein Schema-Fehler — dieselbe Reserved-Liste
        wie Form (``validate_slugifiable_name``) und Modell
        (``FieldTemplate.clean``/``save``)."""
        raw = _valid_raw()
        raw["field_templates"][1]["slug"] = "title"
        # Querverweise konsistent halten, damit NUR der Reserved-Slug-Fehler bleibt.
        raw["document_types"][0]["field_refs"][1]["template_slug"] = "title"
        raw["document_types"][0]["conditions"][0]["target"] = "title"
        assert _paths(raw) == ["field_templates[1].slug"]

    @pytest.mark.parametrize("reserved", ["notes", "client", "case", "pseudonym", "document-type"])
    def test_reserved_slug_variants(self, reserved: str) -> None:
        # ``document-type`` (Bindestrich) ist NICHT reserviert (die feste Feld-
        # kennung ist ``document_type`` mit Unterstrich) → muss gültig bleiben.
        raw = _valid_raw()
        raw["field_templates"][1]["slug"] = reserved
        raw["document_types"][0]["field_refs"][1]["template_slug"] = reserved
        raw["document_types"][0]["conditions"][0]["target"] = reserved
        paths = [] if reserved == "document-type" else ["field_templates[1].slug"]
        if reserved == "document-type":
            parse_package(raw)  # gültig, wirft nicht
        else:
            assert _paths(raw) == paths

    def test_option_requires_label(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["field_type"] = "select"
        raw["field_templates"][0]["options"] = [{"slug": "a"}]
        assert "field_templates[0].options[0].label" in _paths(raw)

    def test_file_field_with_explicit_is_encrypted_false_is_rejected(self) -> None:
        """(a) Haertung Refs #1058: explizites ``is_encrypted: false`` an einem
        FILE-Feld ist ein Widerspruch (FILE ist immer verschluesselt) und wird
        pfadgenau abgewiesen — analog zur HIGH-Sensitivity-Regel. Die
        Loader-Aufwaerts-Normalisierung bleibt fuer den FEHLENDEN Key."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "anhang", "name": "Anhang", "field_type": "file", "is_encrypted": False})
        assert "field_templates[2].is_encrypted" in _paths(raw)

    def test_file_field_without_is_encrypted_key_stays_valid_at_schema_level(self) -> None:
        """Gegenprobe: der fehlende Key bleibt hier unauffaellig — die
        Aufwaerts-Normalisierung auf ``True`` passiert erst im Loader."""
        raw = _valid_raw()
        raw["field_templates"].append({"slug": "anhang", "name": "Anhang", "field_type": "file"})
        template = parse_package(raw).field_templates[2]
        assert template.is_encrypted is False


class TestDocumentTypePresentationTriState:
    """Lokale Fach-UI-Werte duerfen beim Laden alter Pakete nicht verschwinden (#1886)."""

    def test_missing_keys_yield_key_missing_sentinel(self) -> None:
        raw = _valid_raw()
        for key in ("icon", "color", "sort_order"):
            del raw["document_types"][0][key]

        doc_type = parse_package(raw).document_types[0]

        assert doc_type.icon is KEY_MISSING
        assert doc_type.color is KEY_MISSING
        assert doc_type.sort_order is KEY_MISSING

    def test_explicit_values_are_preserved(self) -> None:
        doc_type = parse_package(_valid_raw()).document_types[0]

        assert doc_type.icon == "users"
        assert doc_type.color == "indigo"
        assert doc_type.sort_order == 0

    @pytest.mark.parametrize("key", ["icon", "color", "sort_order"])
    def test_explicit_null_is_rejected(self, key: str) -> None:
        raw = _valid_raw()
        raw["document_types"][0][key] = None

        assert f"document_types[0].{key}" in _paths(raw)


class TestNumberRangeTriState:
    """``min_value``/``max_value`` als echtes Tri-State (Refs #1934, Muster
    ``tally_enabled``): Schlüssel fehlt ⇒ :data:`KEY_MISSING` (der Loader lässt
    eine bestehende Grenze unangetastet), Schlüssel da mit ``null`` ⇒ ``None``
    (bewusstes Löschen), Schlüssel da mit Zahl ⇒ die Zahl (setzen). Vorher
    normalisierte der Parser fehlenden Schlüssel und explizites ``null`` beide
    auf ``None`` — der Loader konnte die Fälle nicht unterscheiden und
    überschrieb beide Grenzen bei jedem Lauf auf NULL.

    ``_valid_raw()["field_templates"][0]`` (``dauer``) ist bereits ein
    Zahlenfeld ohne ``min_value``/``max_value`` — dieselbe Ausgangslage wie ein
    reales v1-Bestandspaket.
    """

    def test_missing_key_yields_key_missing_sentinel(self) -> None:
        raw = _valid_raw()
        assert "min_value" not in raw["field_templates"][0]
        assert "max_value" not in raw["field_templates"][0]

        template = parse_package(raw).field_templates[0]

        assert template.min_value is KEY_MISSING
        assert template.max_value is KEY_MISSING

    def test_explicit_null_yields_none(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["min_value"] = None
        raw["field_templates"][0]["max_value"] = None

        template = parse_package(raw).field_templates[0]

        assert template.min_value is None
        assert template.max_value is None

    def test_explicit_value_yields_value(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["min_value"] = 0
        raw["field_templates"][0]["max_value"] = 480

        template = parse_package(raw).field_templates[0]

        assert template.min_value == 0
        assert template.max_value == 480

    def test_min_greater_than_max_is_still_rejected(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["min_value"] = 10
        raw["field_templates"][0]["max_value"] = 5
        assert _paths(raw) == ["field_templates[0].min_value", "field_templates[0].max_value"]

    def test_null_min_with_concrete_max_does_not_trigger_cross_check(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["min_value"] = None
        raw["field_templates"][0]["max_value"] = 5
        parse_package(raw)  # darf nicht werfen

    def test_missing_min_with_concrete_max_does_not_trigger_cross_check(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["max_value"] = 5
        parse_package(raw)  # min_value fehlt -> kein Cross-Check, darf nicht werfen

    def test_non_number_field_rejects_concrete_min_value(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][1]["min_value"] = 5  # "notiz" ist textarea
        assert "field_templates[1].min_value" in _paths(raw)

    def test_non_number_field_accepts_explicit_null(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][1]["min_value"] = None
        raw["field_templates"][1]["max_value"] = None
        parse_package(raw)  # darf nicht werfen — null heisst weiterhin "keine Grenze"

    def test_invalid_type_is_rejected_with_path(self) -> None:
        raw = _valid_raw()
        raw["field_templates"][0]["min_value"] = "fuenf"
        assert "field_templates[0].min_value" in _paths(raw)
