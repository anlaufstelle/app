"""Tests für den management-command `generate_dsgvo_package`.

Minimale Coverage: Command erzeugt für eine Facility N Markdown-Dateien
im angegebenen Output-Verzeichnis. Fehlerpfad: ungültiger Facility-Name
→ CommandError.
"""

import pytest
from django.core.management import CommandError, call_command

from core.services.dsgvo_package import DOCUMENTS


@pytest.mark.django_db
class TestGenerateDsgvoPackageCommand:
    def test_writes_all_documents_to_output_dir(self, tmp_path, facility, settings_obj):
        """Command erzeugt für jedes slug in DOCUMENTS eine Datei im Output."""
        call_command(
            "generate_dsgvo_package",
            facility=facility.name,
            output_dir=str(tmp_path),
        )

        # Jedes Slug aus DOCUMENTS produziert genau eine Datei.
        files = sorted(p.name for p in tmp_path.iterdir())
        assert len(files) == len(DOCUMENTS), f"Erwartet: {len(DOCUMENTS)} Dateien, erhalten: {len(files)} ({files})"

        # Alle Dateien sind Markdown und nicht leer.
        for path in tmp_path.iterdir():
            assert path.suffix == ".md", f"Erwartet .md, erhalten: {path}"
            content = path.read_text(encoding="utf-8")
            assert content.strip(), f"{path.name} ist leer"

    def test_output_contains_facility_name(self, tmp_path, facility, settings_obj):
        """Rendered Dateien enthalten den Facility-Namen (Platzhalter-Ersatz)."""
        call_command(
            "generate_dsgvo_package",
            facility=facility.name,
            output_dir=str(tmp_path),
        )

        # Mindestens eine Datei muss den Facility-Namen enthalten.
        any_contains = any(facility.name in p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
        assert any_contains, "Keine Datei enthält den Facility-Namen"

    def test_unknown_facility_raises_command_error(self, tmp_path):
        """Unbekannter Facility-Name → CommandError mit hilfreicher Meldung."""
        with pytest.raises(CommandError, match="nicht gefunden|not found"):
            call_command(
                "generate_dsgvo_package",
                facility="gibts-nicht-12345",
                output_dir=str(tmp_path),
            )

    def test_output_dir_is_created_if_missing(self, tmp_path, facility, settings_obj):
        """Der Command legt das Output-Verzeichnis an, wenn es noch nicht existiert."""
        target = tmp_path / "nested" / "deeper"
        assert not target.exists()

        call_command(
            "generate_dsgvo_package",
            facility=facility.name,
            output_dir=str(target),
        )

        assert target.exists()
        assert list(target.iterdir()), "Output-Dir wurde angelegt, aber keine Dateien erzeugt"

    def test_overwrites_existing_files(self, tmp_path, facility, settings_obj):
        """Beim zweiten Lauf werden bestehende Dateien überschrieben (nicht angehängt)."""
        call_command(
            "generate_dsgvo_package",
            facility=facility.name,
            output_dir=str(tmp_path),
        )
        first_files = sorted(p.name for p in tmp_path.iterdir())
        first_sizes = {p.name: p.stat().st_size for p in tmp_path.iterdir()}

        call_command(
            "generate_dsgvo_package",
            facility=facility.name,
            output_dir=str(tmp_path),
        )
        second_files = sorted(p.name for p in tmp_path.iterdir())
        second_sizes = {p.name: p.stat().st_size for p in tmp_path.iterdir()}

        assert first_files == second_files, "Datei-Menge unterscheidet sich zwischen Läufen"
        # Gleicher Inhalt → gleiche Größe (kein Append)
        assert first_sizes == second_sizes, "Dateigrößen weichen ab — Überschreiben oder Append?"
