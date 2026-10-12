"""Pins for settings persistence across default-type facilities.

Source: mutation delta of the release-0.99.7 checkpoint, 5 mutants.
Group: update_settings includes the incoming default type's facility in the
shared protection gates before persistence and audit. Gate repetitions and
statement counts are unrestricted.
"""

import pytest
from django.db import connection

from core.models import AuditLog, DocumentType
from core.services.system.settings import update_settings

pytestmark = pytest.mark.django_db


@pytest.mark.postgres
def test_incoming_default_type_facility_is_gated_before_writes(settings_obj, facility, second_facility, admin_user):
    """Settings and the incoming type's facility are protected before writes.

    Kills x_update_settings__mutmut_2, x_update_settings__mutmut_4,
    x_update_settings__mutmut_5, x_update_settings__mutmut_6 and
    x_update_settings__mutmut_7 through update_settings.
    """
    incoming = DocumentType.objects.create(facility=second_facility, name="Incoming default type")
    gates = set()
    protected = {str(facility.pk), str(second_facility.pk)}
    writes = []

    def observe(execute, sql, params, many, context):
        if "pg_advisory_xact_lock_shared" in sql:
            gates.update(str(value) for value in params if str(value) in protected)
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE")) and any(
            table in sql for table in ('"core_settings"', '"core_auditlog"')
        ):
            writes.append(sql)
            assert protected <= gates, "Both facilities must be gated before settings or audit persistence"
        return execute(sql, params, many, context)

    with connection.execute_wrapper(observe):
        update_settings(settings_obj, admin_user, default_document_type=incoming)

    assert writes
    settings_obj.refresh_from_db()
    assert settings_obj.default_document_type_id == incoming.pk
    assert AuditLog.objects.filter(
        facility=facility, action=AuditLog.Action.SETTINGS_CHANGE, target_id=str(settings_obj.pk)
    ).exists()
