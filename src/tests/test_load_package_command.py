"""Tests für den management-command `load_package` (M2 P11, Refs #1058).

Der Command ist die Operator-CLI um das bereits P10-getestete Schema/Loader-
Paar (`core.packages.schema.parse_package` / `core.packages.loader.load_package`).
Hier wird NICHT erneut die Fach-Logik geprüft (Idempotenz, Immutables,
Dry-Run-Rollback — das deckt `test_packages_loader.py` ab), sondern nur die
CLI-Fassade: Datei-/YAML-/Facility-/Schema-/Apply-Fehlerpfade landen als
`CommandError`, und die Report-Ausgabe ist robust gegenüber nicht-eindeutigen
Bezeichnern (siehe P10-Review-Hinweis: Bedingungs-Bezeichner können bei
Teiländerungen mehrfach im selben Bucket auftauchen).
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import CommandError, call_command

from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate

_MINI_PACKAGE: dict[str, Any] = {
    "schema_version": 1,
    "field_templates": [
        {"slug": "dauer", "name": "Dauer", "field_type": "number"},
    ],
    "document_types": [
        {
            "slug": "kontakt",
            "name": "Kontakt",
            "entity_type": "event",
            "field_refs": [{"template_slug": "dauer"}],
        }
    ],
}


def _write_yaml(tmp_path: Path, data: Any, name: str = "package.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _run(*, file: Path | str, facility: Any, dry_run: bool = False) -> str:
    out = io.StringIO()
    call_command(
        "load_package",
        file=str(file),
        facility=str(facility),
        dry_run=dry_run,
        stdout=out,
    )
    return out.getvalue()


def _counts() -> tuple[int, int, int, int, int]:
    return (
        FieldTemplate.objects.count(),
        DocumentType.objects.count(),
        FieldGroup.objects.count(),
        DocumentTypeField.objects.count(),
        FieldCondition.objects.count(),
    )


@pytest.mark.django_db
class TestRoundtrip:
    def test_first_run_creates_and_reports_angelegt(self, tmp_path, facility):
        path = _write_yaml(tmp_path, _MINI_PACKAGE)

        output = _run(file=path, facility=facility.id)

        assert "angelegt" in output
        assert FieldTemplate.objects.filter(facility=facility, slug="dauer").exists()
        assert DocumentType.objects.filter(facility=facility, slug="kontakt").exists()

    def test_second_run_reports_unveraendert(self, tmp_path, facility):
        path = _write_yaml(tmp_path, _MINI_PACKAGE)
        _run(file=path, facility=facility.id)

        output = _run(file=path, facility=facility.id)

        assert "unverändert" in output


@pytest.mark.django_db
class TestDryRun:
    def test_dry_run_writes_nothing(self, tmp_path, facility):
        path = _write_yaml(tmp_path, _MINI_PACKAGE)
        before = _counts()

        output = _run(file=path, facility=facility.id, dry_run=True)

        assert _counts() == before
        assert "Dry-Run" in output

    def test_dry_run_with_apply_errors_still_raises_command_error(self, tmp_path, facility):
        """P10-Review-Hinweis: Dry-Run mit Fehlern raist ebenfalls — nicht als
        "kann nicht fehlschlagen" behandeln."""
        FieldTemplate.objects.create(facility=facility, slug="dauer", name="Dauer", field_type="text")
        conflicting = dict(_MINI_PACKAGE)
        conflicting["field_templates"] = [{"slug": "dauer", "name": "Dauer", "field_type": "number"}]
        path = _write_yaml(tmp_path, conflicting)
        before = _counts()

        with pytest.raises(CommandError):
            _run(file=path, facility=facility.id, dry_run=True)

        assert _counts() == before


@pytest.mark.django_db
class TestFileErrors:
    def test_missing_file_raises_command_error(self, tmp_path, facility):
        missing = tmp_path / "nicht-da.yaml"
        with pytest.raises(CommandError):
            _run(file=missing, facility=facility.id)

    def test_invalid_yaml_syntax_raises_command_error(self, tmp_path, facility):
        path = tmp_path / "kaputt.yaml"
        path.write_text("field_templates: [{slug: dauer, name: Dauer\n", encoding="utf-8")

        with pytest.raises(CommandError):
            _run(file=path, facility=facility.id)

    def test_duplicate_yaml_key_raises_command_error(self, tmp_path, facility):
        """Härtung Refs #1727: Duplikat-Keys (mit ``yaml.safe_dump`` nicht
        erzeugbar → roher Text) sind ein Fehler, kein stiller Last-Write-Wins."""
        path = tmp_path / "dup.yaml"
        path.write_text(
            "schema_version: 1\nfield_templates:\n  - slug: dauer\n    name: Dauer\n    name: Dauer 2\n",
            encoding="utf-8",
        )
        with pytest.raises(CommandError):
            _run(file=path, facility=facility.id)

    def test_yaml_list_top_level_raises_command_error(self, tmp_path, facility):
        path = _write_yaml(tmp_path, ["a", "b"])

        with pytest.raises(CommandError):
            _run(file=path, facility=facility.id)


@pytest.mark.django_db
class TestFacilityErrors:
    def test_unknown_facility_uuid_raises_command_error(self, tmp_path, facility):
        path = _write_yaml(tmp_path, _MINI_PACKAGE)
        unknown = uuid.uuid4()

        with pytest.raises(CommandError):
            _run(file=path, facility=unknown)

    def test_malformed_facility_uuid_raises_command_error(self, tmp_path, facility):
        path = _write_yaml(tmp_path, _MINI_PACKAGE)

        with pytest.raises(CommandError):
            _run(file=path, facility="keine-uuid")


@pytest.mark.django_db
class TestSchemaErrors:
    def test_unknown_key_raises_command_error_with_path(self, tmp_path, facility):
        broken = {
            "schema_version": 1,
            "field_templates": [{"slug": "dauer", "name": "Dauer", "bogus_key": 1}],
        }
        path = _write_yaml(tmp_path, broken)

        with pytest.raises(CommandError, match=r"field_templates\[0\]\.bogus_key"):
            _run(file=path, facility=facility.id)


@pytest.mark.django_db
class TestApplyErrors:
    def test_immutable_conflict_raises_command_error_and_applies_nothing(self, tmp_path, facility):
        FieldTemplate.objects.create(facility=facility, slug="dauer", name="Dauer alt", field_type="text")
        conflicting = dict(_MINI_PACKAGE)
        conflicting["field_templates"] = [{"slug": "dauer", "name": "Dauer neu", "field_type": "number"}]
        path = _write_yaml(tmp_path, conflicting)

        with pytest.raises(CommandError):
            _run(file=path, facility=facility.id)

        template = FieldTemplate.objects.get(facility=facility, slug="dauer")
        assert template.name == "Dauer alt"
        assert not DocumentType.objects.filter(facility=facility, slug="kontakt").exists()


@pytest.mark.django_db
class TestReportRobustness:
    def test_duplicate_condition_identifiers_are_counted_and_listed_without_dedup(self, tmp_path, facility):
        """P10-Review-Hinweis: Conditions-Bezeichner können Duplikate tragen
        (zwei Bedingungen mit demselben Ziel-/Quellfeld, anderem Operator) —
        die Ausgabe darf sie weder deduplizieren noch die Zählung daran vorbei
        rechnen."""
        package = {
            "schema_version": 1,
            "field_templates": [
                {"slug": "dauer", "name": "Dauer", "field_type": "number"},
                {"slug": "notiz", "name": "Notiz", "field_type": "textarea"},
            ],
            "document_types": [
                {
                    "slug": "kontakt",
                    "name": "Kontakt",
                    "entity_type": "event",
                    # Distinkte Blockpositionen: seit Härtung Refs #1727 dürfen
                    # zwei freie Felder nicht beide auf Default-Position 0 liegen.
                    "field_refs": [
                        {"template_slug": "dauer", "sort_order": 0},
                        {"template_slug": "notiz", "sort_order": 1},
                    ],
                    "conditions": [
                        {"target": "notiz", "depends_on": "dauer", "operator": "greater_than", "value": 5},
                        {"target": "notiz", "depends_on": "dauer", "operator": "less_than", "value": 100},
                    ],
                }
            ],
        }
        path = _write_yaml(tmp_path, package)

        output = _run(file=path, facility=facility.id)

        assert FieldCondition.objects.count() == 2
        assert output.count("kontakt/notiz<-dauer") == 2
        assert "angelegt (2)" in output


@pytest.mark.django_db
class TestRlsBypassGuard:
    """Final-Review A (Refs #1058): ``load_package`` schreibt in fünf
    FORCE-RLS-Tabellen (``core_documenttype``, ``core_fieldtemplate``,
    ``core_documenttypefield``, ``core_fieldgroup``, ``core_fieldcondition``).
    Ohne Bypass-Kontext läuft der Loader RLS-blind: Bestands-Lookups liefern 0
    Zeilen, der erste INSERT stirbt an einer ``ProgrammingError`` (SQLSTATE
    42501), die weder ``_save()`` noch die CLI fängt — roher Traceback statt
    Report. Fail-loud wie bei den Schwester-Commands (``enforce_retention``
    Refs #1016, ``detect_breaches`` Refs #1512)."""

    def test_aborts_loud_without_bypass_context(self, monkeypatch, tmp_path, facility):
        from core.management.commands import load_package as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        path = _write_yaml(tmp_path, _MINI_PACKAGE)

        with pytest.raises(CommandError, match="RLS"):
            _run(file=path, facility=facility.id)

        assert not DocumentType.objects.filter(facility=facility, slug="kontakt").exists()

    def test_dry_run_also_aborts_without_bypass_context(self, monkeypatch, tmp_path, facility):
        """Auch der Dry-Run liest Bestand — ein RLS-blinder Lauf meldete sonst
        jedes Objekt fälschlich als „angelegt“."""
        from core.management.commands import load_package as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        path = _write_yaml(tmp_path, _MINI_PACKAGE)

        with pytest.raises(CommandError, match="RLS"):
            _run(file=path, facility=facility.id, dry_run=True)

    def test_runs_through_with_bypass_context(self, monkeypatch, tmp_path, facility):
        from core.management.commands import load_package as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        path = _write_yaml(tmp_path, _MINI_PACKAGE)

        output = _run(file=path, facility=facility.id)

        assert "Import abgeschlossen" in output
        assert DocumentType.objects.filter(facility=facility, slug="kontakt").exists()
