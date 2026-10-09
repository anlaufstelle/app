"""Rendered operational promises must cover attachment rotation and key retirement."""

import pytest

from core.services.client import render_document


@pytest.mark.django_db
def test_rendered_tom_names_file_rotation_and_retirement_conditions(facility):
    content, _ = render_document("toms", facility)
    item = next(line for line in content.splitlines() if "Schlüsselrotation ohne Datenverlust" in line)
    for required in ("Dateianhänge", "reencrypt_fields", "reencrypt_files", "--verify", "Backups", "Altschlüssel"):
        assert required in item
