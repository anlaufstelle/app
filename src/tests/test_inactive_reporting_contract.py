"""Reports count active fields; deactivation retains values for reactivation."""

import csv
from io import StringIO

import pytest
from django.utils import timezone

from core.models import FieldTemplate
from core.reporting.backends.counseling_hours import CounselingHoursBackend
from core.services.client.export import _gather_entity_dynamic_data
from core.services.config import set_field_template_active
from core.services.events import update_event
from core.services.system.export import export_events_csv
from tests.test_sensitivity_transitions import make_record, request_for

pytestmark = pytest.mark.django_db


def test_report_minutes_follow_field_activation_without_losing_values(facility, lead_user, rf):
    field = FieldTemplate.objects.create(
        facility=facility, name="Recorded minutes", field_type="number", report_key="dauer_minuten"
    )
    record, _ = make_record(facility, lead_user, "event", field=field, value=45)
    record.document_type.report_key = "contact"
    record.document_type.save(update_fields=["report_key"])
    today = timezone.localdate()
    backend = CounselingHoursBackend()
    assert backend.build_dataset(facility, today, today).summary["minuten_gesamt"] == 45
    set_field_template_active(request_for(rf, facility, lead_user), field, active=False)
    update_event(record, lead_user, {})
    record.refresh_from_db()
    assert record.data_json[field.slug] == 45
    assert backend.build_dataset(facility, today, today).summary["minuten_gesamt"] == 0
    assert {"slug": field.slug, "label": field.name, "value": 45} in _gather_entity_dynamic_data(record)
    exported = "".join(export_events_csv(facility, today, today, user=lead_user))
    stream = StringIO(exported)
    next(stream)  # The export starts with a metadata row before the column header.
    assert next(csv.DictReader(stream))[field.name] == "45"
    set_field_template_active(request_for(rf, facility, lead_user), field, active=True)
    record.refresh_from_db()
    assert record.data_json[field.slug] == 45
    assert backend.build_dataset(facility, today, today).summary["minuten_gesamt"] == 45
