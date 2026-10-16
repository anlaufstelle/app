"""Pins for case choice labels through the event metadata form.

Source: mutation delta of the release-0.99.8 checkpoint, 2 mutants.
Groups: missing client relation (1), empty pseudonym fallback (1).
"""

import pytest

from core.forms.events import EventMetaForm
from core.models import Case, Client

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("has_client", [False, True], ids=["unassigned", "empty-pseudonym"])
def test_case_choice_without_pseudonym_uses_title_only(facility, staff_user, has_client):
    """The form's label API accepts unassigned cases and empty pseudonyms.

    x__case_label__mutmut_3, x__case_label__mutmut_5.
    """
    person = Client(facility=facility, pseudonym="") if has_client else None
    case = Case(facility=facility, client=person, title="Case title")
    form = EventMetaForm(facility=facility, user=staff_user)
    assert form.fields["case"].label_from_instance(case) == case.title
