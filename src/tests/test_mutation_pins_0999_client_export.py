"""Pin export fallbacks, typed carriers and eager type loading.

Source: mutation delta of the release-0.99.9 checkpoint, 7 mutants.
Groups: missing field templates (2), typed episodes and tasks (4), task orphans (1).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import DocumentType, Episode, Event, WorkItem
from core.services.client import export

pytestmark = pytest.mark.django_db


def test_pdf_formats_lists_without_a_field_template(facility, client_identified, lead_user):
    """PDF HTML keeps list values readable after a template disappears.

    x__format_pdf_event_data__mutmut_15.
    """
    event_type = DocumentType.objects.create(facility=facility, name="Legacy event")
    Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=event_type,
        occurred_at=timezone.now(),
        data_json={"missing-field": ["first", "second"]},
    )
    html = export.render_client_export_html(client_identified, facility, lead_user)
    assert "first, second" in html


@pytest.mark.parametrize("entity", ["episode", "workitem"])
def test_typed_carriers_keep_type_names_and_do_not_reload_types(
    facility, client_identified, case_open, lead_user, entity
):
    """Export keeps carrier types without a separate type read per record.

    x__gather_episodes__mutmut_13, x__gather_episodes__mutmut_33,
    x__gather_workitems__mutmut_13, x__gather_workitems__mutmut_31,
    x__gather_workitems__mutmut_39, x__gather_entity_orphan_data__mutmut_23.
    """
    expected = {}
    for index in range(3):
        doc = DocumentType.objects.create(facility=facility, name=f"Carrier type {index}", entity_type=entity)
        title = f"Carrier {index}"
        kwargs = {"title": title, "document_type": doc, "data_json": {"lost": "Old value"}}
        if entity == "episode":
            Episode.objects.create(**kwargs, case=case_open, started_at=timezone.localdate())
        else:
            WorkItem.objects.create(**kwargs, facility=facility, client=client_identified)
        expected[title] = doc.name

    with CaptureQueriesContext(connection) as queries:
        data = export.export_client_data(client_identified, facility, lead_user)

    entries = data["episodes" if entity == "episode" else "work_items"]
    assert {entry["title"]: entry["document_type"] for entry in entries} == expected
    assert all(
        entry["dynamic_data_orphaned"] == [{"slug": "lost", "label": "lost", "value": "Old value"}] for entry in entries
    )
    assert not any(
        'FROM "core_documenttype"' in query["sql"] and '"core_documenttype"."id" =' in query["sql"] for query in queries
    )
