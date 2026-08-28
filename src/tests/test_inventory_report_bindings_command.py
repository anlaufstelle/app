"""Tests für ``inventory_report_bindings`` (E2, Refs #1848).

Wertet die Reporting-Registry aus: Backend-Katalog (Typ-/Feld-Schlüssel je
Backend) sowie je Einrichtung gebundene/ungebundene Dokumentationstypen und
Feldvorlagen. Der Command war zugleich das Gate für E3 Schritt 7; seit der
Slug-Zweitschlüssel entfernt ist (Entscheid E-E, Refs #1849), ist die Kennzahl
``slug_only`` gegenstandslos und aus dem Report verschwunden.

**RLS-Guard** (Review-Befund #1848): ``core_facility`` ist NICHT RLS-gescoped
— ``_resolve_facility`` gelingt also auch ohne Bypass-Kontext —, aber
``DocumentType``/``FieldTemplate`` stehen unter FORCE RLS. Ohne Bypass sähe
der Lauf 0 Zeilen und meldete fälschlich eine restlos gebundene Einrichtung.
Fail-loud wie ``export_facility``/``purge_orphan_field_data``/
``detect_breaches`` (``TestRlsGuard`` unten).
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
    """Ohne Bypass-Kontext fail-loud — sonst falsch-grünes Inventar (Review-Befund #1848)."""

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

    @pytest.mark.postgres
    def test_ambiguous_name_is_command_error(self, organization):
        from django.db import connection

        from core.models import Facility

        # Refs #2068 (a): ``Facility.name`` ist seit dem Schema-Hygiene-Buendel
        # UNIQUE — der Doppelname laesst sich regulaer nicht mehr herstellen.
        # Der Mehrdeutigkeits-Zweig bleibt als Defense-in-Depth fuer Bestaende,
        # die ohne den Constraint entstanden sind, und wird hier weiter
        # gepinnt. Der Constraint faellt nur innerhalb der Testtransaktion —
        # PostgreSQL faehrt DDL transaktional, der Rollback stellt ihn her.
        with connection.cursor() as cursor:
            # Aufgeschobene FK-Trigger der Fixtures wuerden das ALTER TABLE
            # sonst mit "pending trigger events" abweisen.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'core_facility'::regclass AND contype = 'u'"
            )
            for (conname,) in cursor.fetchall():
                cursor.execute(f"ALTER TABLE core_facility DROP CONSTRAINT {conname}")

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
    def test_bound_type_via_report_key(self, facility):
        DocumentType.objects.create(facility=facility, name="Spritzentausch", report_key="needle_exchange")
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        bound_names = {e["name"] for e in data["document_types"]["bound"]}
        assert "Spritzentausch" in bound_names

    def test_unbound_type_has_no_binding(self, facility):
        DocumentType.objects.create(facility=facility, name="Eigene Notiz")
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        unbound_names = {e["name"] for e in data["document_types"]["unbound"]}
        assert "Eigene Notiz" in unbound_names

    def test_ausdruecklich_ausgeschlossener_typ_ist_kein_mangel(self, facility):
        """Vier Zustände statt zwei (Konzept #1847 §2, Disclosure-Review).

        Landete der Sentinel bei den echten Lücken, warnte der Command bei jeder
        default-geseedeten Einrichtung dauerhaft — dieselbe Vermengung, die
        #1819 für den Vollständigkeitshinweis beseitigt hat.
        """
        DocumentType.objects.create(facility=facility, name="Hausnotiz", report_key="excluded")
        data = json.loads(_run("--facility", str(facility.pk), "--format", "json"))

        assert "Hausnotiz" in {e["name"] for e in data["document_types"]["excluded"]}
        assert "Hausnotiz" not in {e["name"] for e in data["document_types"]["unbound"]}

    def test_unbekannter_schluessel_bekommt_einen_eigenen_bucket(self, facility):
        """Entscheid E-H: „ich habe zugeordnet, es passiert nur nichts"."""
        DocumentType.objects.create(facility=facility, name="Fremdpaket-Typ", report_key="fantasie")
        data = json.loads(_run("--facility", str(facility.pk), "--format", "json"))

        assert "Fremdpaket-Typ" in {e["name"] for e in data["document_types"]["unknown"]}
        assert "Fremdpaket-Typ" not in {e["name"] for e in data["document_types"]["unbound"]}

    def test_paket_slug_allein_bindet_nicht_mehr(self, facility):
        """Entscheid E-E (Refs #1849): ohne Berichtsschlüssel keine Zuordnung —
        auch wenn der Slug einer der Paket-Slugs ist."""
        DocumentType.objects.create(facility=facility, name="Eigener Kontakttyp", slug="kontakt", report_key=None)
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        assert "Eigener Kontakttyp" in {e["name"] for e in data["document_types"]["unbound"]}

    def test_report_key_bindet_unabhaengig_vom_slug(self, facility):
        DocumentType.objects.create(
            facility=facility, name="Regulärer Kontakttyp", slug="eigener-name", report_key="contact"
        )
        out = _run("--facility", str(facility.pk), "--format", "json")
        data = json.loads(out)
        eintrag = next(e for e in data["document_types"]["bound"] if e["name"] == "Regulärer Kontakttyp")
        assert eintrag["report_key"] == "contact"

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
    def test_bound_field_via_report_key(self, facility):
        # counseling-hours ist nur verfügbar, wenn die Vorlage tatsächlich einem
        # passenden Dokumentationstyp zugeordnet ist (Review-Befund #1848: seit
        # der Verfügbarkeits-Filterung genügt der blosse Schluessel-Match nicht).
        ft = FieldTemplate.objects.create(
            facility=facility, name="Dauer", field_type="number", report_key="dauer_minuten"
        )
        dt = DocumentType.objects.create(facility=facility, name="Beratung", report_key="counseling")
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

    def test_text_summary_line_reports_unbound_types(self, facility):
        DocumentType.objects.create(facility=facility, name="Ohne Zuordnung")
        out = _run("--facility", str(facility.pk))
        assert "ohne Berichtszuordnung" in out

    def test_success_zweig_ist_erreichbar(self, facility):
        """Eine Einrichtung, deren Typen alle eine Aussage tragen, meldet Erfolg —
        auch wenn einige ausdrücklich in keinen Bericht gehören.
        """
        DocumentType.objects.create(facility=facility, name="Kontaktdoku", report_key="contact")
        DocumentType.objects.create(facility=facility, name="Hausnotiz", report_key="excluded")

        out = _run("--facility", str(facility.pk))

        assert "trägt eine Aussage" in out
        assert "ohne Berichtszuordnung" not in out

    def test_json_is_stable_and_ensure_ascii_false(self, facility):
        DocumentType.objects.create(facility=facility, name="Ünïcödé-Typ")
        out = _run("--facility", str(facility.pk), "--format", "json")
        assert "\\u" not in out  # ensure_ascii=False: Umlaute bleiben Klartext
        assert "Ünïcödé-Typ" in out
        # Zweimal ausgeführt -> dieselbe Struktur (kein Set-/Dict-Ordering-Zufall).
        out2 = _run("--facility", str(facility.pk), "--format", "json")
        assert json.loads(out) == json.loads(out2)
