"""Tests für den idempotenten Paket-Loader (M2 P10, Refs #1058).

``core.packages.loader.load_package`` spielt ein validiertes
:class:`~core.packages.schema.PackageV1` gegen eine Einrichtung ein. Die
beiden Eigenschaften, die hier hart abgesichert werden:

* **Idempotenz** — derselbe Lauf zweimal meldet beim zweiten Mal ausschließlich
  ``unchanged``; das ist der Kernbeweis dafür, dass ein Paket gefahrlos erneut
  eingespielt werden kann (E12).
* **Alles-oder-nichts** — jeder Report-``error`` führt zum Gesamtrollback und
  zum Raise; die DB darf danach kein Teilergebnis tragen (E12/E13).

Dazu die deklarative Bedingungs-Semantik (E14: Schlüssel fehlt = unangetastet,
``[]`` = leeren, sonst Wholesale-Replace), der Dry-Run (E15) und die
Block-Sequenz nach ADR-036/#1713.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate
from core.packages.loader import PackageLoadError, PackageReport, load_package
from core.packages.schema import parse_package
from core.services.config import iter_blocks_in_order


def _raw(**overrides: Any) -> dict[str, Any]:
    """Basis-Paket: 2 Feldvorlagen, 1 Ereignis-Typ, 1 Gruppe, 2 Zuordnungen."""
    raw: dict[str, Any] = {
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
                "system_type": "crisis",
                "icon": "users",
                "color": "indigo",
                "sort_order": 3,
                "groups": [{"slug": "basisdaten", "name": "Basisdaten", "sort_order": 0}],
                "field_refs": [
                    {"template_slug": "dauer", "group_slug": "basisdaten", "sort_order": 0},
                    {"template_slug": "notiz", "sort_order": 1},
                ],
            }
        ],
    }
    raw.update(overrides)
    return raw


def _load(facility: Any, raw: dict[str, Any], *, dry_run: bool = False) -> PackageReport:
    return load_package(facility, parse_package(raw), dry_run=dry_run)


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        FieldGroup.objects.count(),
        DocumentTypeField.objects.count(),
        FieldCondition.objects.count(),
    )


@pytest.mark.django_db
class TestCreate:
    def test_first_run_creates_everything(self, facility):
        report = _load(facility, _raw())

        assert report.errors == []
        assert report.warnings == []
        assert report.field_templates.created == ["dauer", "notiz"]
        assert report.document_types.created == ["kontakt"]
        assert report.groups.created == ["kontakt/basisdaten"]
        assert report.field_refs.created == ["kontakt/dauer", "kontakt/notiz"]
        assert report.dry_run is False

        doc_type = DocumentType.objects.get(facility=facility, slug="kontakt")
        assert doc_type.name == "Kontakt"
        assert doc_type.entity_type == "event"
        assert doc_type.system_type == "crisis"
        assert doc_type.sort_order == 3
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        assert dauer.field_type == "number"
        assert dauer.help_text == "Dauer in Minuten"
        assert doc_type.fields.get(field_template=dauer).field_group.slug == "basisdaten"
        assert doc_type.fields.get(field_template__slug="notiz").field_group is None

    def test_missing_presentation_keys_use_model_defaults_for_new_type(self, facility):
        raw = _raw()
        for key in ("icon", "color", "sort_order"):
            del raw["document_types"][0][key]

        report = _load(facility, raw)

        doc_type = DocumentType.objects.get(slug="kontakt")
        assert report.document_types.created == ["kontakt"]
        assert (doc_type.icon, doc_type.color, doc_type.sort_order) == ("", "", 0)

    def test_second_run_is_completely_unchanged(self, facility):
        raw = _raw()
        raw["document_types"][0]["conditions"] = [
            {"target": "notiz", "depends_on": "dauer", "operator": "greater_than", "value": 10}
        ]
        _load(facility, raw)
        before = _counts()

        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.created == [] and report.field_templates.updated == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]
        assert report.document_types.unchanged == ["kontakt"]
        assert report.groups.unchanged == ["kontakt/basisdaten"]
        assert report.field_refs.unchanged == ["kontakt/dauer", "kontakt/notiz"]
        assert report.conditions.unchanged == ["kontakt/notiz<-dauer"]
        assert report.conditions.created == []
        assert _counts() == before

    def test_created_and_updated_mix(self, facility):
        FieldTemplate.objects.create(facility=facility, slug="dauer", name="Dauer alt", field_type="number")

        report = _load(facility, _raw())

        assert report.field_templates.updated == ["dauer"]
        assert report.field_templates.created == ["notiz"]
        assert FieldTemplate.objects.get(slug="dauer").name == "Dauer"

    def test_file_template_without_explicit_encryption_stays_idempotent(self, facility):
        # ``FieldTemplate.save()`` erzwingt is_encrypted fuer FILE — ohne dieselbe
        # Normalisierung im Loader waere der zweite Lauf ein Immutable-Konflikt.
        raw = _raw()
        raw["field_templates"].append({"slug": "anhang", "name": "Anhang", "field_type": "file"})
        raw["document_types"][0]["field_refs"].append({"template_slug": "anhang", "sort_order": 2})

        assert _load(facility, raw).field_templates.created == ["dauer", "notiz", "anhang"]
        assert FieldTemplate.objects.get(slug="anhang").is_encrypted is True

        report = _load(facility, raw)
        assert report.errors == []
        assert report.field_templates.unchanged == ["dauer", "notiz", "anhang"]


@pytest.mark.django_db
class TestUpdate:
    def test_missing_presentation_keys_leave_local_values_untouched(self, facility):
        raw = _raw()
        _load(facility, raw)
        doc_type = DocumentType.objects.get(slug="kontakt")
        doc_type.icon = "heart"
        doc_type.color = "rose"
        doc_type.sort_order = 41
        doc_type.save(update_fields=["icon", "color", "sort_order"])
        for key in ("icon", "color", "sort_order"):
            del raw["document_types"][0][key]

        report = _load(facility, raw)

        doc_type.refresh_from_db()
        assert report.document_types.unchanged == ["kontakt"]
        assert (doc_type.icon, doc_type.color, doc_type.sort_order) == ("heart", "rose", 41)

    def test_explicit_presentation_values_overwrite_local_values(self, facility):
        raw = _raw()
        _load(facility, raw)
        raw["document_types"][0].update(icon="heart", color="rose", sort_order=9)

        report = _load(facility, raw)

        doc_type = DocumentType.objects.get(slug="kontakt")
        assert report.document_types.updated == ["kontakt"]
        assert (doc_type.icon, doc_type.color, doc_type.sort_order) == ("heart", "rose", 9)

    def test_mutable_attributes_are_updated(self, facility):
        _load(facility, _raw())
        raw = _raw()
        raw["field_templates"][0]["name"] = "Dauer (Minuten)"
        raw["field_templates"][0]["is_required"] = True
        raw["document_types"][0]["name"] = "Kontakt neu"
        raw["document_types"][0]["sensitivity"] = "elevated"
        raw["document_types"][0]["min_contact_stage"] = "qualified"
        raw["document_types"][0]["groups"][0]["name"] = "Basis"
        raw["document_types"][0]["groups"][0]["is_collapsible"] = True
        raw["document_types"][0]["field_refs"][1]["group_slug"] = "basisdaten"

        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.updated == ["dauer"]
        assert report.field_templates.unchanged == ["notiz"]
        assert report.document_types.updated == ["kontakt"]
        assert report.groups.updated == ["kontakt/basisdaten"]
        assert report.field_refs.updated == ["kontakt/notiz"]
        assert report.field_refs.unchanged == ["kontakt/dauer"]

        doc_type = DocumentType.objects.get(slug="kontakt")
        assert (doc_type.name, doc_type.sensitivity) == ("Kontakt neu", "elevated")
        assert doc_type.min_contact_stage == "qualified"
        assert FieldTemplate.objects.get(slug="dauer").is_required is True
        assert doc_type.fields.get(field_template__slug="notiz").field_group.slug == "basisdaten"

    def test_options_are_written_and_diffed(self, facility):
        raw = _raw()
        raw["field_templates"].append(
            {
                "slug": "leistungen",
                "name": "Leistungen",
                "field_type": "multi_select",
                "options": [{"slug": "beratung", "label": "Beratung"}, {"slug": "essen", "label": "Essen"}],
            }
        )
        raw["document_types"][0]["field_refs"].append({"template_slug": "leistungen", "sort_order": 2})
        _load(facility, raw)

        assert FieldTemplate.objects.get(slug="leistungen").options_json == [
            {"slug": "beratung", "label": "Beratung", "is_active": True},
            {"slug": "essen", "label": "Essen", "is_active": True},
        ]

        raw["field_templates"][2]["options"][1]["label"] = "Essensausgabe"
        report = _load(facility, raw)
        assert report.field_templates.updated == ["leistungen"]
        assert report.warnings == []

    def test_disappearing_option_slug_is_only_a_warning(self, facility):
        raw = _raw()
        raw["field_templates"].append(
            {
                "slug": "leistungen",
                "name": "Leistungen",
                "field_type": "select",
                "options": [{"slug": "beratung", "label": "Beratung"}, {"slug": "essen", "label": "Essen"}],
            }
        )
        raw["document_types"][0]["field_refs"].append({"template_slug": "leistungen", "sort_order": 2})
        _load(facility, raw)

        del raw["field_templates"][2]["options"][1]
        report = _load(facility, raw)

        assert report.errors == []
        assert [path for path, _message in report.warnings] == ["leistungen"]
        assert "essen" in report.warnings[0][1]
        assert report.field_templates.updated == ["leistungen"]


@pytest.mark.django_db
class TestImmutables:
    def _seed(self, facility):
        _load(facility, _raw())

    def _expect_rejection(self, facility, raw, expected_identifier):
        before = _counts()
        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, raw)
        report = excinfo.value.report
        assert expected_identifier in [identifier for identifier, _message in report.errors]
        assert _counts() == before
        return report

    def test_field_type_change_is_rejected(self, facility):
        self._seed(facility)
        raw = _raw()
        raw["field_templates"][0]["field_type"] = "text"
        raw["field_templates"][0]["name"] = "Anders"
        self._expect_rejection(facility, raw, "dauer")
        assert FieldTemplate.objects.get(slug="dauer").name == "Dauer"

    def test_is_encrypted_change_is_rejected(self, facility):
        self._seed(facility)
        raw = _raw()
        raw["field_templates"][1]["is_encrypted"] = True
        self._expect_rejection(facility, raw, "notiz")
        assert FieldTemplate.objects.get(slug="notiz").is_encrypted is False

    def test_entity_type_change_is_rejected(self, facility):
        self._seed(facility)
        raw = _raw()
        raw["document_types"][0]["entity_type"] = "client"
        del raw["document_types"][0]["system_type"]
        raw["document_types"][0]["name"] = "Kontakt neu"
        self._expect_rejection(facility, raw, "kontakt")
        assert DocumentType.objects.get(slug="kontakt").name == "Kontakt"

    def test_system_type_change_is_rejected(self, facility):
        self._seed(facility)
        raw = _raw()
        raw["document_types"][0]["system_type"] = "ban"
        self._expect_rejection(facility, raw, "kontakt")
        assert DocumentType.objects.get(slug="kontakt").system_type == "crisis"

    def test_system_type_removal_is_rejected(self, facility):
        self._seed(facility)
        raw = _raw()
        del raw["document_types"][0]["system_type"]
        self._expect_rejection(facility, raw, "kontakt")
        assert DocumentType.objects.get(slug="kontakt").system_type == "crisis"

    def test_system_type_none_to_value_is_allowed(self, facility):
        raw = _raw()
        del raw["document_types"][0]["system_type"]
        _load(facility, raw)
        assert DocumentType.objects.get(slug="kontakt").system_type is None

        report = _load(facility, _raw())

        assert report.errors == []
        assert report.document_types.updated == ["kontakt"]
        assert DocumentType.objects.get(slug="kontakt").system_type == "crisis"

    def test_partial_work_is_rolled_back_completely(self, facility):
        # Erster Typ ist sauber, zweiter kollidiert — nichts darf ankommen.
        self._seed(facility)
        raw = _raw()
        raw["field_templates"].append({"slug": "extra", "name": "Extra", "field_type": "text"})
        raw["document_types"].append({"slug": "notizen", "name": "Notizen", "field_refs": [{"template_slug": "extra"}]})
        raw["document_types"][0]["entity_type"] = "case"
        del raw["document_types"][0]["system_type"]

        with pytest.raises(PackageLoadError):
            _load(facility, raw)

        assert not FieldTemplate.objects.filter(slug="extra").exists()
        assert not DocumentType.objects.filter(slug="notizen").exists()

    def test_field_ref_of_conflicting_template_is_not_falsely_reported_as_created(self, facility):
        """Haertung (Refs #1058, Review-Punkt 3): eine bestehende Feldvorlage mit
        Immutable-Konflikt darf nicht in die Erfolgs-Map wandern — sonst meldet
        der Report eine davon abhaengige (neue) Feldzuordnung faelschlich als
        'created', obwohl sie wegen des Fehlers nie entsteht (Alles rollt
        zurueck). Spiegelt das DocumentType-Muster (Konflikt ⇒ None ⇒ Kinder
        werden nicht mehr verarbeitet)."""
        self._seed(facility)
        raw = _raw()
        raw["field_templates"][0]["field_type"] = "text"  # dauer: number -> text (Immutable-Konflikt)
        raw["document_types"].append({"slug": "notizen", "name": "Notizen", "field_refs": [{"template_slug": "dauer"}]})

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, raw)

        report = excinfo.value.report
        assert "notizen/dauer" not in report.field_refs.created, (
            "Feldzuordnung darf nicht als 'created' gemeldet werden, wenn ihre "
            "Feldvorlage wegen eines Immutable-Konflikts gescheitert ist"
        )
        identifiers = [identifier for identifier, _message in report.errors]
        assert "dauer" in identifiers
        assert "notizen/dauer" in identifiers


@pytest.mark.django_db
class TestErrors:
    def test_name_unique_conflict_is_reported_cleanly(self, facility):
        # Anderer Slug, gleicher Name → UniqueConstraint (facility, name).
        # Der Loader muss das als ValidationError aus full_clean() melden,
        # nicht als IntegrityError die Transaktion vergiften.
        DocumentType.objects.create(facility=facility, slug="kontakt-alt", name="Kontakt")
        before = _counts()

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw())

        report = excinfo.value.report
        assert [identifier for identifier, _message in report.errors] == ["kontakt"]
        # Aus full_clean(), nicht aus einem IntegrityError — sonst stünde hier
        # der Postgres-Constraint-Name.
        assert "existiert bereits" in report.errors[0][1]
        assert _counts() == before
        assert not DocumentType.objects.filter(slug="kontakt").exists()

    def test_error_message_of_exception_contains_identifiers(self, facility):
        DocumentType.objects.create(facility=facility, slug="kontakt-alt", name="Kontakt")
        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw())
        assert "kontakt" in str(excinfo.value)

    def test_template_name_conflict_is_reported(self, facility):
        FieldTemplate.objects.create(facility=facility, slug="dauer-alt", name="Dauer", field_type="text")
        before = _counts()

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, _raw())

        # Zusätzlich zum Vorlagen-Fehler wird die davon abhängige Zuordnung als
        # übersprungen gemeldet — der Report sagt, was alles ausgefallen wäre.
        errors = excinfo.value.report.errors
        assert [identifier for identifier, _message in errors] == ["dauer", "kontakt/dauer"]
        assert "existiert bereits" in errors[0][1]
        assert "übersprungen" in errors[1][1]
        assert _counts() == before

    def test_failed_template_update_keeps_existing_template_available_for_field_refs(self, facility):
        _load(facility, _raw())
        FieldTemplate.objects.create(facility=facility, slug="kollision", name="Kollision", field_type="text")
        raw = _raw()
        raw["field_templates"][0]["name"] = "Kollision"

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, raw)

        report = excinfo.value.report
        assert [identifier for identifier, _message in report.errors] == ["dauer"]
        assert report.field_templates.updated == []
        assert report.field_refs.unchanged == ["kontakt/dauer", "kontakt/notiz"]
        assert FieldTemplate.objects.get(slug="dauer").name == "Dauer"

    def test_failed_document_type_update_keeps_existing_type_available_for_children(self, facility):
        _load(facility, _raw())
        DocumentType.objects.create(facility=facility, slug="kollision", name="Kollision")
        raw = _raw()
        raw["document_types"][0]["name"] = "Kollision"

        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, raw)

        report = excinfo.value.report
        assert [identifier for identifier, _message in report.errors] == ["kontakt"]
        assert report.document_types.updated == []
        assert report.groups.unchanged == ["kontakt/basisdaten"]
        assert report.field_refs.unchanged == ["kontakt/dauer", "kontakt/notiz"]
        assert DocumentType.objects.get(slug="kontakt").name == "Kontakt"


@pytest.mark.django_db
class TestConditions:
    def _raw_with_condition(self, value: int = 10) -> dict[str, Any]:
        raw = _raw()
        raw["document_types"][0]["conditions"] = [
            {"target": "notiz", "depends_on": "dauer", "operator": "greater_than", "value": value}
        ]
        return raw

    def test_conditions_are_created(self, facility):
        report = _load(facility, self._raw_with_condition())

        assert report.errors == []
        assert report.conditions.created == ["kontakt/notiz<-dauer"]
        condition = FieldCondition.objects.get()
        assert condition.target_field.field_template.slug == "notiz"
        assert condition.depends_on_field.field_template.slug == "dauer"
        assert condition.operator == "greater_than"
        assert condition.value == 10

    def test_present_key_replaces_existing_conditions(self, facility):
        _load(facility, self._raw_with_condition(10))
        report = _load(facility, self._raw_with_condition(42))

        assert report.errors == []
        assert report.conditions.created == ["kontakt/notiz<-dauer"]
        assert [path for path, _message in report.warnings] == ["kontakt/notiz<-dauer"]
        assert FieldCondition.objects.count() == 1
        assert FieldCondition.objects.get().value == 42

    def test_empty_list_clears_conditions(self, facility):
        _load(facility, self._raw_with_condition())
        raw = _raw()
        raw["document_types"][0]["conditions"] = []

        report = _load(facility, raw)

        assert report.errors == []
        assert FieldCondition.objects.count() == 0
        assert [path for path, _message in report.warnings] == ["kontakt/notiz<-dauer"]

    def test_missing_key_leaves_ui_maintained_conditions_untouched(self, facility):
        _load(facility, self._raw_with_condition())
        condition_pk = FieldCondition.objects.get().pk

        report = _load(facility, _raw())

        assert report.errors == []
        assert report.conditions.created == []
        assert report.conditions.unchanged == []
        assert FieldCondition.objects.get().pk == condition_pk

    def test_model_validation_error_is_collected(self, facility):
        # ``contains`` ist fuer NUMBER-Quellen nicht zulaessig (Operator-x-Typ-Matrix).
        raw = _raw()
        raw["document_types"][0]["conditions"] = [
            {"target": "notiz", "depends_on": "dauer", "operator": "contains", "value": "5"}
        ]
        with pytest.raises(PackageLoadError) as excinfo:
            _load(facility, raw)
        assert [identifier for identifier, _message in excinfo.value.report.errors] == ["kontakt/notiz<-dauer"]
        assert _counts() == (0, 0, 0, 0, 0)


@pytest.mark.django_db
class TestDryRun:
    def test_dry_run_writes_nothing(self, facility):
        report = _load(facility, _raw(), dry_run=True)

        assert report.dry_run is True
        assert report.errors == []
        assert report.field_templates.created == ["dauer", "notiz"]
        assert report.document_types.created == ["kontakt"]
        assert _counts() == (0, 0, 0, 0, 0)

    def test_run_after_dry_run_still_creates(self, facility):
        _load(facility, _raw(), dry_run=True)
        report = _load(facility, _raw())

        assert report.document_types.created == ["kontakt"]
        assert DocumentType.objects.filter(slug="kontakt").exists()

    def test_dry_run_report_matches_real_run(self, facility):
        dry = _load(facility, _raw(), dry_run=True)
        real = _load(facility, _raw())

        assert dry.field_templates.created == real.field_templates.created
        assert dry.document_types.created == real.document_types.created
        assert dry.groups.created == real.groups.created
        assert dry.field_refs.created == real.field_refs.created

    def test_dry_run_reports_errors_and_raises_too(self, facility):
        _load(facility, _raw())
        raw = _raw()
        raw["document_types"][0]["system_type"] = "ban"

        with pytest.raises(PackageLoadError):
            _load(facility, raw, dry_run=True)


@pytest.mark.django_db
class TestBlockOrder:
    def test_declared_sort_orders_yield_declared_block_sequence(self, facility):
        # ADR-036/#1713: ``groups[].sort_order`` = Blockposition der Gruppe,
        # ``field_refs[].sort_order`` = Position IN der Gruppe bzw. eigene
        # Blockposition ohne Gruppe. Das freie Feld steht hier VOR der Gruppe.
        raw = _raw()
        raw["field_templates"].append({"slug": "extra", "name": "Extra", "field_type": "text"})
        raw["document_types"][0]["groups"][0]["sort_order"] = 1
        raw["document_types"][0]["field_refs"] = [
            {"template_slug": "notiz", "sort_order": 0},
            {"template_slug": "dauer", "group_slug": "basisdaten", "sort_order": 1},
            {"template_slug": "extra", "group_slug": "basisdaten", "sort_order": 0},
        ]

        _load(facility, raw)

        doc_type = DocumentType.objects.get(slug="kontakt")
        blocks = iter_blocks_in_order(doc_type)
        assert [block["kind"] for block in blocks] == ["field", "group"]
        assert blocks[0]["field"].field_template.slug == "notiz"
        assert blocks[1]["group"].slug == "basisdaten"
        assert [dtf.field_template.slug for dtf in blocks[1]["fields"]] == ["extra", "dauer"]


@pytest.mark.django_db
class TestSensitivityDowngradeWarning:
    """Final-Review G (Refs #1058): ``sensitivity`` ist per E13 updatebar — ein
    Paketlauf darf sie also senken. Weil das die Lese-Schranke bereits erfasster
    Werte rueckwirkend absenkt (``effective_sensitivity`` = max(Typ, Feld)),
    muss der Report das aber sichtbar machen. Muster:
    ``_warn_on_disappearing_options``."""

    def test_warns_on_document_type_downgrade(self, facility):
        raw = _raw()
        raw["document_types"][0]["sensitivity"] = "high"
        _load(facility, raw)

        raw["document_types"][0]["sensitivity"] = "normal"
        report = _load(facility, raw)

        assert report.errors == []
        assert report.document_types.updated == ["kontakt"]
        assert [identifier for identifier, _message in report.warnings] == ["kontakt"]
        assert "Zugriffsschranke gesenkt" in report.warnings[0][1]

    def test_warns_on_field_template_downgrade(self, facility):
        raw = _raw()
        raw["field_templates"][1]["sensitivity"] = "elevated"
        _load(facility, raw)

        raw["field_templates"][1]["sensitivity"] = ""
        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.updated == ["notiz"]
        assert [identifier for identifier, _message in report.warnings] == ["notiz"]
        assert "Zugriffsschranke gesenkt" in report.warnings[0][1]

    def test_no_warning_on_raise_or_equal(self, facility):
        _load(facility, _raw())

        raw = _raw()
        raw["document_types"][0]["sensitivity"] = "elevated"
        raw["field_templates"][1]["sensitivity"] = "elevated"
        report = _load(facility, raw)

        assert report.warnings == []

        # Zweiter, identischer Lauf: unveraendert, weiterhin keine Warnung.
        report = _load(facility, raw)
        assert report.warnings == []
        assert report.document_types.unchanged == ["kontakt"]

    def test_no_warning_when_object_is_created(self, facility):
        raw = _raw()
        raw["document_types"][0]["sensitivity"] = "normal"
        report = _load(facility, raw)

        assert report.warnings == []

    # --- effektive Sensibilität (Härtung Refs #1727) -------------------------

    def test_field_downgrade_absorbed_by_type_gives_no_warning(self, facility):
        """Ticket-Kernfall #1727: Vorlage ``elevated`` → ``""`` bei Typ
        ``elevated`` ⇒ KEINE Warnung. Die effektive Schranke bleibt
        max(elevated, „erbt") = elevated — der bisherige Roh-Vergleich wertete
        ``""`` als Rang 0 und meldete hier einen False-Positive."""
        raw = _raw()
        raw["document_types"][0]["sensitivity"] = "elevated"
        raw["field_templates"][1]["sensitivity"] = "elevated"
        _load(facility, raw)

        raw["field_templates"][1]["sensitivity"] = ""
        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.updated == ["notiz"]
        assert report.warnings == []

    def test_field_downgrade_under_normal_type_still_warns(self, facility):
        """Gegenprobe: dieselbe Vorlagen-Absenkung ``elevated`` → ``""`` unter
        Typ ``normal`` senkt effektiv von 1 auf 0 ⇒ Warnung."""
        raw = _raw()
        raw["document_types"][0]["sensitivity"] = "normal"
        raw["field_templates"][1]["sensitivity"] = "elevated"
        _load(facility, raw)

        raw["field_templates"][1]["sensitivity"] = ""
        report = _load(facility, raw)

        assert report.errors == []
        assert [identifier for identifier, _message in report.warnings] == ["notiz"]
        assert "Zugriffsschranke gesenkt" in report.warnings[0][1]

    def test_unreferenced_field_downgrade_falls_back_to_raw_warning(self, facility):
        """Ohne referenzierenden Typ (Vorlage nicht zugeordnet) greift der
        konservative Roh-Vergleich: ``elevated`` → ``""`` warnt."""
        raw = {
            "schema_version": 1,
            "field_templates": [
                {"slug": "solo", "name": "Solo", "field_type": "text", "sensitivity": "elevated"},
            ],
            "document_types": [],
        }
        _load(facility, raw)

        raw["field_templates"][0]["sensitivity"] = ""
        report = _load(facility, raw)

        assert report.errors == []
        assert [identifier for identifier, _message in report.warnings] == ["solo"]


@pytest.mark.django_db
class TestIgnorierteSchluessel:
    """``category`` lädt weiter, wirkt nicht mehr und warnt (Refs #1749, ADR-040).

    Der Entscheid gegen einen ``schema_version``-2-Cutover hängt genau an
    diesen drei Eigenschaften: Bestands-Pakete bleiben ladbar (kein Fehler),
    der Wert wird nicht angewendet (es gibt kein Feld mehr) und er
    verschwindet nicht still (Warnung im Report).
    """

    def test_paket_mit_category_laedt_ohne_fehler(self, facility):
        raw = _raw()
        raw["document_types"][0]["category"] = "service"

        report = _load(facility, raw)

        assert report.errors == []
        assert report.document_types.created == ["kontakt"]

    def test_category_erzeugt_eine_warnung_je_typ(self, facility):
        raw = _raw()
        raw["document_types"][0]["category"] = "service"

        report = _load(facility, raw)

        assert [identifier for identifier, _message in report.warnings] == ["kontakt"]
        assert "category" in report.warnings[0][1]

    def test_ohne_category_keine_warnung(self, facility):
        report = _load(facility, _raw())
        assert report.warnings == []

    def test_zweiter_lauf_meldet_unveraendert_trotz_category(self, facility):
        """Der tote Schlüssel darf keinen Dauer-Diff erzeugen (Idempotenz)."""
        raw = _raw()
        raw["document_types"][0]["category"] = "service"
        _load(facility, raw)

        report = _load(facility, raw)

        assert report.document_types.unchanged == ["kontakt"]
        assert report.document_types.updated == []


@pytest.mark.django_db
class TestEntfalleneSystemtypen:
    """Alt-Pakete mit einem der sieben entfallenen Systemtypen (E4, Refs #1850).

    Dieselben drei Eigenschaften wie bei :class:`TestIgnorierteSchluessel`, nur
    eine Ebene tiefer: dort ist ein ganzer **Schlüssel** tot, hier sind es
    einzelne **Werte** eines weiterhin gültigen Schlüssels. Der Unterschied ist
    für den Betrieb entscheidend, weil ``parse_package`` wirft, *bevor* ein
    ``PackageV1`` existiert: ohne diese Toleranz machte ein einziges
    ``system_type: medical`` das ganze fremde Paket unladbar — der Grund, warum
    der Mechanismus Vorbedingung dieser Etappe war (Konzept #1847 §E4).
    """

    def _alt_paket(self) -> dict[str, Any]:
        raw = _raw()
        raw["document_types"][0]["system_type"] = "medical"
        return raw

    def test_altpaket_laedt_ohne_fehler(self, facility):
        report = _load(facility, self._alt_paket())

        assert report.errors == []
        assert report.document_types.created == ["kontakt"]

    def test_entfallener_wert_kommt_nicht_in_der_zeile_an(self, facility):
        _load(facility, self._alt_paket())

        assert DocumentType.objects.get(facility=facility, slug="kontakt").system_type is None

    def test_entfallener_wert_erzeugt_eine_warnung_je_typ(self, facility):
        report = _load(facility, self._alt_paket())

        assert [identifier for identifier, _message in report.warnings] == ["kontakt"]
        assert "medical" in report.warnings[0][1]

    def test_zweiter_lauf_bleibt_konfliktfrei(self, facility):
        """Der E13-Guard darf am Altwert nicht anschlagen.

        Der Systemtyp ist unveränderbar („gesetzt → anders/weg" ist ein
        Konflikt). Käme der Altwert in der Zeile an, stünde der zweite Lauf
        desselben Pakets gegen einen Bestand, den das Enum nicht mehr kennt —
        und die Einrichtung hätte einen dauerhaft unladbaren Katalog.
        """
        _load(facility, self._alt_paket())

        report = _load(facility, self._alt_paket())

        assert report.errors == []
        assert report.document_types.unchanged == ["kontakt"]
        assert report.document_types.updated == []


@pytest.mark.django_db
class TestNumberRangeTriState:
    """``min_value``/``max_value`` als echtes Tri-State (Refs #1934, Muster
    ``tally_enabled``): Schlüssel fehlt ⇒ Bestand unangetastet, Schlüssel da
    mit ``null`` ⇒ Grenze bewusst gelöscht, Schlüssel da mit Zahl ⇒ Grenze
    gesetzt.

    **Kompatibilitätsentscheid (Task M1-B2, siehe Report):** gilt sofort für
    jedes Bestands-Paket, ohne ``schema_version``-Bump — obwohl das ein echter
    Semantikwechsel ist, kein Bugfix an unspezifiziertem Verhalten: Vorher zog
    jeder Lauf eine weggelassene Grenze **dokumentiert und absichtlich** auf
    NULL zurück (``docs/packages.md`` führte ``min_value``/``max_value`` bis
    dahin ausdrücklich in der Rücksync-Tabelle als "wird zurückgesetzt"). Die
    Sofort-Geltung ist trotzdem sicher, weil der Effekt nachweislich einseitig
    schützend ist: Die neue Semantik löscht in keinem Fall mehr, als die alte
    gelöscht hätte — Weglassen löscht jetzt nie, wo es vorher immer löschte;
    ein explizites ``null`` löscht in beiden Fassungen unverändert.
    ``dauer`` in :func:`_raw` ist ein Zahlenfeld ohne ``min_value``/
    ``max_value`` — dieselbe Ausgangslage wie ein reales v1-Bestandspaket
    (z. B. ``dauer`` in ``default.yaml``).
    """

    def test_missing_key_uses_model_default_on_create(self, facility):
        report = _load(facility, _raw())

        assert report.errors == []
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        assert dauer.min_value is None
        assert dauer.max_value is None

    def test_explicit_value_is_set_on_create(self, facility):
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 0
        raw["field_templates"][0]["max_value"] = 480

        report = _load(facility, raw)

        assert report.errors == []
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        assert (dauer.min_value, dauer.max_value) == (0, 480)

    def test_missing_key_leaves_admin_configured_bound_untouched(self, facility):
        """Kernfall der Kompatibilitätsentscheidung: ein Paket, das (wie ein
        v1-Bestandspaket) ``min_value``/``max_value`` nicht erwähnt, darf eine
        per Konfigurationsoberfläche gesetzte Grenze nicht mehr auf NULL
        zurücksetzen."""
        _load(facility, _raw())
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        dauer.min_value = 5
        dauer.max_value = 120
        dauer.full_clean()
        dauer.save()

        report = _load(facility, _raw())

        assert report.errors == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]
        assert report.field_templates.updated == []
        dauer.refresh_from_db()
        assert (dauer.min_value, dauer.max_value) == (5, 120)

    def test_explicit_null_clears_existing_bound(self, facility):
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 5
        raw["field_templates"][0]["max_value"] = 120
        _load(facility, raw)

        raw_clear = _raw()
        raw_clear["field_templates"][0]["min_value"] = None
        raw_clear["field_templates"][0]["max_value"] = None
        report = _load(facility, raw_clear)

        assert report.errors == []
        assert report.field_templates.updated == ["dauer"]
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        assert dauer.min_value is None
        assert dauer.max_value is None

    def test_explicit_value_change_is_synced_back(self, facility):
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 5
        _load(facility, raw)

        raw["field_templates"][0]["min_value"] = 10
        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.updated == ["dauer"]
        assert FieldTemplate.objects.get(slug="dauer").min_value == 10

    def test_second_run_with_only_one_bound_set_leaves_other_untouched(self, facility):
        """Unabhängigkeit von ``min_value``/``max_value``: Nur ``min_value``
        wird im Paket geführt, ein Admin-gepflegtes ``max_value`` bleibt
        stehen."""
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 0
        _load(facility, raw)
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        dauer.max_value = 999
        dauer.full_clean()
        dauer.save()

        report = _load(facility, raw)

        assert report.errors == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]
        dauer.refresh_from_db()
        assert (dauer.min_value, dauer.max_value) == (0, 999)

    def test_dry_run_reports_deletion_without_writing(self, facility):
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 5
        _load(facility, raw)

        raw_clear = _raw()
        raw_clear["field_templates"][0]["min_value"] = None
        report = _load(facility, raw_clear, dry_run=True)

        assert report.errors == []
        assert report.field_templates.updated == ["dauer"]
        assert FieldTemplate.objects.get(slug="dauer").min_value == 5

    def test_dry_run_reports_setting_a_value_without_writing(self, facility):
        """Minor aus Review-Runde 1: Löschen und Unverändert waren bereits
        dry-run-getestet, Setzen (Update auf einen neuen expliziten Wert)
        fehlte."""
        raw = _raw()
        raw["field_templates"][0]["min_value"] = 5
        _load(facility, raw)

        raw["field_templates"][0]["min_value"] = 10
        report = _load(facility, raw, dry_run=True)

        assert report.errors == []
        assert report.field_templates.updated == ["dauer"]
        assert FieldTemplate.objects.get(slug="dauer").min_value == 5

    def test_dry_run_reports_unchanged_when_key_is_missing(self, facility):
        _load(facility, _raw())
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        dauer.min_value = 5
        dauer.full_clean()
        dauer.save()

        report = _load(facility, _raw(), dry_run=True)

        assert report.errors == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]
        assert FieldTemplate.objects.get(slug="dauer").min_value == 5
        # Betreiber-Sichtbarkeit (Controller-Ruling Review-Runde 1, Refs #1934):
        # gilt identisch im Dry-Run — derselbe Code-Pfad, derselbe Report.
        assert [identifier for identifier, _message in report.preserved] == ["dauer"]
        assert "min_value=5" in report.preserved[0][1]
        assert report.warnings == []


@pytest.mark.django_db
class TestNumberRangePreservedVisibility:
    """Betreiber-Sichtbarkeit ohne Dauerwarnung (Controller-Ruling
    Review-Runde 1, Refs #1934): der Report muss „unverändert, weil der
    Schlüssel fehlt (Bestand erhalten)" von „unverändert, weil Paket- und
    Bestandswert gleich sind" unterscheidbar machen — als eigener, jeden Lauf
    frisch berechneter ``report.preserved``-Bucket, NICHT als ``warnings``-
    Eintrag (der würde jeden gewöhnlichen Lauf zuspammen, in dem ein Paket
    Grenzen einfach nicht verwaltet)."""

    def test_report_distinguishes_preserved_from_value_equal_unchanged(self, facility):
        raw = _raw()
        raw["field_templates"].append({"slug": "menge", "name": "Menge", "field_type": "number", "min_value": 0})
        _load(facility, raw)
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        dauer.min_value = 5
        dauer.full_clean()
        dauer.save()

        report = _load(facility, raw)

        assert report.errors == []
        # Beide bleiben "unverändert" — aus unterschiedlichen Gruenden.
        assert set(report.field_templates.unchanged) == {"dauer", "notiz", "menge"}
        # Nur "dauer" (Schluessel fehlt, Bestand erhalten) landet im
        # preserved-Bucket; "menge" (Paket fuehrt min_value=0, Bestand ist
        # bereits 0 -> wertgleich) NICHT.
        assert [identifier for identifier, _message in report.preserved] == ["dauer"]
        assert "min_value=5" in report.preserved[0][1]
        # Kein Umweg ueber die Warnungen -- exakt die per Ruling geforderte
        # Trennung von der abgelehnten Dauerwarnung.
        assert report.warnings == []

    def test_no_preserved_entry_when_nothing_to_preserve(self, facility):
        """Der haeufigste Fall (Paket verwaltet keine Grenze, es existiert
        keine zu erhaltende) darf NICHT im preserved-Report auftauchen --
        sonst waere es genau die per Ruling abgelehnte Dauerwarnung."""
        report = _load(facility, _raw())
        assert report.preserved == []

        report = _load(facility, _raw())
        assert report.preserved == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]

    def test_preserved_entry_disappears_once_package_takes_over_the_key(self, facility):
        """Sobald das Paket den Schluessel selbst fuehrt, ist es kein
        'unangetastet, weil unerwaehnt' mehr -- der Eintrag verschwindet
        wieder (kein Erstlauf-Zustand, der haengen bleibt)."""
        _load(facility, _raw())
        dauer = FieldTemplate.objects.get(facility=facility, slug="dauer")
        dauer.min_value = 5
        dauer.full_clean()
        dauer.save()
        preserved_report = _load(facility, _raw())
        assert [identifier for identifier, _message in preserved_report.preserved] == ["dauer"]

        raw = _raw()
        raw["field_templates"][0]["min_value"] = 5
        report = _load(facility, raw)

        assert report.errors == []
        assert report.preserved == []
        assert report.field_templates.unchanged == ["dauer", "notiz"]
