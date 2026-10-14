"""Offline entry carries application-language metadata without a round trip."""

import pytest
from django.template.loader import render_to_string
from django.utils.translation import override


@pytest.mark.parametrize("language,placeholder", [("de", "TT.MM.JJJJ"), ("en", "YYYY-MM-DD")])
def test_offline_task_entry_declares_format(language, placeholder):
    with override(language):
        html = render_to_string("core/clients/_offline_workitem_fields.html", {"id_prefix": "task"})
    assert html.count(f'data-date-placeholder="{placeholder}"') == 2
    assert 'x-model="wiValues.due_date"' in html
    assert 'x-model="wiValues.remind_at"' in html


@pytest.mark.parametrize("language", ["de", "en"])
def test_offline_dynamic_fields_declare_format(language):
    with override(language):
        html = render_to_string("core/clients/_offline_event_fields.html")
    assert f'data-date-language="{language}"' in html
    assert "x-app-date" in html
