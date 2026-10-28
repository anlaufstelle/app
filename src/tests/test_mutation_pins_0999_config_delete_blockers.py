"""Pin empty-slug guards and readable dynamic deletion reasons.

Source: mutation delta of the release-0.99.9 checkpoint, 3 mutants.
Groups: empty slug (2), dynamic carrier blocker text (1).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.translation import gettext

from core.models import Episode, FieldTemplate, WorkItem
from core.services.config.delete_blockers import (
    ConfigDeleteBlocked,
    delete_field_template,
    field_template_delete_blockers,
)

pytestmark = pytest.mark.django_db


def test_empty_slug_has_no_dynamic_carrier_blocker_or_lookup(facility, case_open, client_identified):
    """An empty slug skips dynamic carrier lookups and produces no reasons.

    x_field_template_delete_blockers__mutmut_113, x_field_template_delete_blockers__mutmut_116.
    """
    field = FieldTemplate.objects.create(facility=facility, name="Unused field")
    field.slug = ""
    Episode.objects.create(
        case=case_open,
        title="Legacy episode",
        started_at=timezone.localdate(),
        data_json={"": "Legacy"},
    )
    WorkItem.objects.create(facility=facility, client=client_identified, title="Legacy task", data_json={"": "Legacy"})

    with CaptureQueriesContext(connection) as queries:
        reasons = field_template_delete_blockers(field)

    assert reasons == []
    assert not any(
        f'FROM "{table}"' in query["sql"] for query in queries for table in ("core_episode", "core_workitem")
    )


@pytest.mark.parametrize("entity", ["episode", "workitem"])
def test_dynamic_carrier_reason_is_preserved_by_delete(facility, case_open, client_identified, rf, admin_user, entity):
    """Orphaned details block deletion with the exact readable reason.

    x_field_template_delete_blockers__mutmut_117.
    """
    field = FieldTemplate.objects.create(facility=facility, name="Retained field")
    kwargs = {"title": "Retained record", "data_json": {field.slug: "Retained value"}}
    if entity == "episode":
        record = Episode.objects.create(**kwargs, case=case_open, started_at=timezone.localdate())
    else:
        record = WorkItem.objects.create(**kwargs, facility=facility, client=client_identified)
    expected = gettext("%(entity)s: %(n)d Datensatz/Datensätze enthalten Daten unter dem Slug „%(slug)s“.") % {
        "entity": record._meta.verbose_name_plural,
        "n": 1,
        "slug": field.slug,
    }
    assert field_template_delete_blockers(field) == [expected]
    request = rf.post("/config/")
    request.user = admin_user
    request.current_facility = facility
    with pytest.raises(ConfigDeleteBlocked) as error:
        delete_field_template(request, field)
    assert error.value.reasons == [expected]
    assert str(error.value) == expected
    assert FieldTemplate.objects.filter(pk=field.pk).exists()
