"""Maintenance commands report every dynamic carrier they process."""

from io import StringIO

import pytest
from django.core.management import call_command

from tests.test_episode_workitem_dynamic_data import carrier as carrier
from tests.test_episode_workitem_dynamic_data import make_record


@pytest.mark.django_db
def test_rotation_command_reports_new_carriers(carrier):
    record = make_record(carrier)
    output = StringIO()
    call_command("reencrypt_fields", stdout=output)
    record.refresh_from_db()
    assert f"1 {carrier[0]}s" in output.getvalue()
