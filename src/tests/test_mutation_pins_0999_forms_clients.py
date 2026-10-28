"""Pin contact-stage cleaning for new people.

Source: mutation delta of the release-0.99.9 checkpoint, 1 mutant.
Groups: new forms avoid existing-person reads (1).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.forms.clients import ClientForm
from core.models import Client

pytestmark = pytest.mark.django_db


def test_new_person_stage_does_not_read_existing_people(facility, staff_user):
    """New-person stage cleaning performs no person lookup.

    xǁClientFormǁclean_contact_stage__mutmut_4.
    """
    form = ClientForm(facility=facility, user=staff_user)
    form.cleaned_data = {"contact_stage": Client.ContactStage.IDENTIFIED}

    with CaptureQueriesContext(connection) as queries:
        stage = form.clean_contact_stage()

    assert stage == Client.ContactStage.IDENTIFIED
    assert not any('FROM "core_client"' in query["sql"] for query in queries)
