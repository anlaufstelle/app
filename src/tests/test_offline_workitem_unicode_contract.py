"""Keep offline title whitespace identical to Python and Django CharField."""

import json
import re
import sys
from pathlib import Path

import pytest
from django.urls import reverse

from core.forms.workitems import WorkItemForm


def test_offline_whitespace_table_matches_python_isspace():
    source = (Path(__file__).resolve().parents[1] / "static/js/offline-form-fields.js").read_text()
    match = re.search(r'const PYTHON_WHITESPACE = ("[^"\n]*");', source)
    assert match is not None, "Offline normalization must declare its Python whitespace table."
    whitespace = json.loads(match[1])
    assert len(whitespace) == len(set(whitespace))
    assert set(whitespace) == {chr(codepoint) for codepoint in range(sys.maxunicode + 1) if chr(codepoint).isspace()}


@pytest.mark.django_db
@pytest.mark.parametrize("whitespace", ["\u0085", "\u001c", "\u001d", "\u001e", "\u001f", "\u00a0", "\u2028"])
def test_server_rejects_whitespace_and_accepts_trimmed_boundary(client, staff_user, facility, whitespace):
    values = {"title": whitespace, "item_type": "task", "priority": "normal"}
    form = WorkItemForm(data=values, facility=facility)
    assert not form.is_valid()
    assert form.errors.as_data()["title"][0].code == "required"
    client.force_login(staff_user)
    response = client.post(reverse("core:workitem_create"), values, HTTP_ACCEPT="application/json")
    assert response.status_code == 422
    form = WorkItemForm(data={**values, "title": whitespace + "a" * 200 + whitespace}, facility=facility)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["title"] == "a" * 200
