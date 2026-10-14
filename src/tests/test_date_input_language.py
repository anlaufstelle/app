"""Date entry follows the active application language, preserving ISO transport."""

from datetime import date

import pytest
from django.template.loader import render_to_string
from django.utils.translation import override

from core.forms.workitems import WorkItemForm


@pytest.mark.parametrize("language,placeholder", [("de", "TT.MM.JJJJ"), ("en", "YYYY-MM-DD")])
def test_task_widget_declares_application_format(language, placeholder):
    with override(language):
        html = str(WorkItemForm(initial={"due_date": date(2026, 10, 1)})["due_date"])
    assert f'data-date-placeholder="{placeholder}"' in html
    assert 'value="2026-10-01"' in html
    assert "x-app-date" in html


@pytest.mark.parametrize("language,placeholder", [("de", "TT.MM.JJJJ"), ("en", "YYYY-MM-DD")])
def test_filter_iso_query_has_application_format(language, placeholder):
    with override(language):
        html = render_to_string("core/events/partials/filter_form.html", {"filters": {"date_from": "2026-10-01"}})
    assert f'data-date-placeholder="{placeholder}"' in html
    assert 'value="2026-10-01"' in html


@pytest.mark.parametrize("language,placeholder", [("de", "TT.MM.JJJJ"), ("en", "YYYY-MM-DD")])
@pytest.mark.parametrize(
    "template",
    [
        "core/audit/partials/_filter_form.html",
        "core/statistics/_zeitraum_leiste.html",
    ],
)
def test_other_filters_keep_iso_and_language_metadata(language, placeholder, template):
    with override(language):
        html = render_to_string(
            template,
            {
                "filter_url_name": "core:audit_log",
                "date_from": date(2026, 10, 1),
                "date_to": date(2026, 10, 14),
                "filter_date_from": "2026-10-01",
                "filter_date_to": "2026-10-14",
            },
        )
    assert html.count(f'data-date-placeholder="{placeholder}"') == 2
    assert 'value="2026-10-01"' in html
    assert 'value="2026-10-14"' in html


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize(
    "kind,value,placeholder",
    [
        ("date", "2026-10-01", "YYYY-MM-DD"),
        ("time", "13:45:17", "HH:MM:SS"),
    ],
)
def test_condition_values_keep_iso_and_precision(language, kind, value, placeholder):
    with override(language):
        html = render_to_string(
            "core/config/document_types/partials/condition_value_input.html",
            {
                "field_type": kind,
                "current_value": value,
                "condition_id_prefix": "condition",
            },
        )
    if kind == "date" and language == "de":
        placeholder = "TT.MM.JJJJ"
    assert f'data-date-placeholder="{placeholder}"' in html
    assert f'value="{value}"' in html
    assert 'id="condition-value"' in html
    if kind == "time":
        assert 'step="1"' in html
