"""The rendered DPIA keeps every configured deadline in one Markdown table."""

import pytest

from core.services.client import render_document

pytestmark = pytest.mark.django_db


def test_request_deadline_belongs_to_the_rendered_dpia_table(facility, settings_obj):
    content, _ = render_document("dsfa", facility)
    lines = content.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("| # | Frist |"))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    rows = lines[start + 2 : end]
    assert len(rows) == 11
    assert rows[-1].startswith("| 11 | Abgeschlossene Lösch-, Zusammenführungs- und Herabstufungsanträge |")
    assert f"| {settings_obj.deletion_request_retention_days} Tage |" in rows[-1]
    assert "Harte Löschung ab Abschluss; offene Anträge bleiben" in rows[-1]
