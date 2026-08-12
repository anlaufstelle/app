"""Tests für ``inventory_report_bindings`` (E2, Refs #1848).

Wertet die Reporting-Registry aus: Backend-Katalog (Typ-/Feld-Schlüssel je
Backend) sowie je Einrichtung gebundene/ungebundene Dokumentationstypen und
Feldvorlagen. Zugleich das Prüfwerkzeug für E3 Schritt 7 (Slug-Zweitschlüssel
entfernen): die Kennzahl ``document_types.slug_only`` zählt Typen, deren
Zuordnung AUSSCHLIESSLICH über den Slug-Zweitschlüssel zustande kommt
(Erststufe/``system_type`` leer, Treffer nur über den Slug).

**RLS-Guard** (Review-Befund #1848): ``core_facility`` ist NICHT RLS-gescoped
— ``_resolve_facility`` gelingt also auch ohne Bypass-Kontext —, aber
``DocumentType``/``FieldTemplate`` stehen unter FORCE RLS. Ohne Bypass sähe
der Lauf 0 Zeilen und meldete fälschlich ein erfülltes E3-Gate. Fail-loud wie
``export_facility``/``purge_orphan_field_data``/``detect_breaches``
(``TestRlsGuard`` unten).
"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import DocumentType, DocumentTypeField, FieldTemplate


def _run(*args) -> str:
    out = StringIO()
    call_command("inventory_report_bindings", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestRlsGuard:
    """Ohne Bypass-Kontext fail-loud — sonst falsch-grünes E3-Gate (Review-Befund #1848)."""

    def test_aborts_loud_without_bypass_context(self, facility, monkeypatch):
        from core.management.commands import inventory_report_bindings as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: False)
        with pytest.raises(CommandError, match="RLS"):
            _run("--facility", str(facility.pk))

    def test_runs_through_with_bypass_context(self, facility, monkeypatch):
        from core.management.commands import inventory_report_bindings as cmd

        monkeypatch.setattr(cmd, "_has_rls_bypass_context", lambda: True)
        out = _run("--facility", str(facility.pk))
        assert facility.name in out


@pytest.mark.django_db
class TestFacilityResolution:
    """Muster ``export_facility._resolve_facility`` — UUID oder Name, sonst CommandError."""

    def test_resolves_by_uuid(self, facility):
        out = _run("--facility", str(facility.pk))
        assert facility.name in out

    def test_resolves_by_name(self, facility):
        out = _run("--facility", facility.name)
        assert facility.name in out

    def test_unknown_facility_is_command_error(self):
        with pytest.raises(CommandError, match="nicht gefunden"):
            _run("--facility", "00000000-0000-0000-0000-000000000000")

    def test_ambiguous_name_is_command_error(self, organization):
        from core.models import Facility

        Facility.objects.create(organization=organization, name="Doppelt")
        Facility.objects.create(organization=organization, name="Doppelt")
        with pytest.raises(CommandError, match="mehrdeutig"):
            _run("--facility", "Doppelt")


@pytest.mark.django_db
class TestBackendCatalog:
    def test_text_format_lists_backends_with_keys(self, facility):
        out = _run("--facility", str(facility.pk))
        assert "Backends:" in out
        assert "sample-report-1" in out
        assert "Kontakt" in out  # Label des Schlüssels "contact"

    def test_json_format_lists_backend_catalog(self, facility):
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        backend_slugs = {b["slug"] for b in data["backends"]}
        assert "sample-report-1" in backend_slugs
        sample = next(b for b in data["backends"] if b["slug"] == "sample-report-1")
        assert {"key": "contact", "label": "Kontakt"} in sample["type_keys"]


@pytest.mark.django_db
class TestDocumentTypeInventory:
    def test_bound_type_via_system_type(self, facility):
        DocumentType.objects.create(facility=facility, name="Spritzentausch", system_type="needle_exchange")
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        bound_names = {e["name"] for e in data["document_types"]["bound"]}
        assert "Spritzentausch" in bound_names
        assert data["document_types"]["slug_only"] == []

    def test_unbound_type_has_no_binding(self, facility):
        DocumentType.objects.create(facility=facility, name="Eigene Notiz")
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        unbound_names = {e["name"] for e in data["document_types"]["unbound"]}
        assert "Eigene Notiz" in unbound_names

    def test_slug_only_binding_is_flagged_as_own_metric(self, facility):
        """E3-Gate (Schritt 7): system_type leer, Slug 'kontakt' trifft ueber den Zweitschluessel."""
        DocumentType.objects.create(facility=facility, name="Eigener Kontakttyp", slug="kontakt", system_type=None)
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        slug_only_names = {e["name"] for e in data["document_types"]["slug_only"]}
        assert "Eigener Kontakttyp" in slug_only_names
        bound_names = {e["name"] for e in data["document_types"]["bound"]}
        assert "Eigener Kontakttyp" in bound_names  # slug_only ist eine Teilmenge von bound

    def test_type_bound_via_system_type_is_not_slug_only(self, facility):
        """Ein GESETZTER system_type zählt nicht als 'ausschließlich über Slug' —
        auch wenn zufällig derselbe Paket-Slug gesetzt ist."""
        DocumentType.objects.create(
            facility=facility, name="Regulärer Kontakttyp", slug="kontakt", system_type="contact"
        )
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        slug_only_names = {e["name"] for e in data["document_types"]["slug_only"]}
        assert "Regulärer Kontakttyp" not in slug_only_names

    def test_client_entity_type_is_excluded(self, facility):
        """NUR entity_type=event — an Personen-Typen ist die Berichtsfrage gegenstandslos."""
        DocumentType.objects.create(
            facility=facility,
            name="Personenprofil",
            entity_type=DocumentType.EntityType.CLIENT,
        )
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        all_names = {e["name"] for e in data["document_types"]["bound"] + data["document_types"]["unbound"]}
        assert "Personenprofil" not in all_names


@pytest.mark.django_db
class TestFieldTemplateInventory:
    def test_bound_field_via_slug(self, facility):
        # counseling-hours ist nur verfügbar, wenn "dauer" tatsächlich einem
        # passenden Dokumentationstyp zugeordnet ist (Review-Befund #1848: seit
        # der Verfügbarkeits-Filterung genügt der blosse Slug-Match nicht mehr).
        ft = FieldTemplate.objects.create(facility=facility, name="Dauer", field_type="number")
        dt = DocumentType.objects.create(facility=facility, name="Beratung", system_type="counseling")
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=0)
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        bound_names = {e["name"] for e in data["field_templates"]["bound"]}
        assert "Dauer" in bound_names

    def test_unbound_field_has_no_binding(self, facility):
        FieldTemplate.objects.create(facility=facility, name="Sonstiges Feld", field_type="text")
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        unbound_names = {e["name"] for e in data["field_templates"]["unbound"]}
        assert "Sonstiges Feld" in unbound_names


@pytest.mark.django_db
class TestOutputHandling:
    def test_output_writes_to_file(self, facility, tmp_path):
        target = tmp_path / "inventar.json"
        _run("--facility", str(facility.pk), "--format", "json", "--output", str(target))
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["facility"]["id"] == str(facility.pk)

    def test_text_summary_line_reports_gate_status(self, facility):
        out = _run("--facility", str(facility.pk))
        assert "Slug-Zweitschlüssel" in out

    def test_json_is_stable_and_ensure_ascii_false(self, facility):
        DocumentType.objects.create(facility=facility, name="Ünïcödé-Typ")
        out = _run("--facility", str(facility.pk), "--format", "json")
        assert "\\u" not in out  # ensure_ascii=False: Umlaute bleiben Klartext
        assert "Ünïcödé-Typ" in out
        # Zweimal ausgeführt -> dieselbe Struktur (kein Set-/Dict-Ordering-Zufall).
        out2 = _run("--facility", str(facility.pk), "--format", "json")
        assert json.loads(out) == json.loads(out2)
