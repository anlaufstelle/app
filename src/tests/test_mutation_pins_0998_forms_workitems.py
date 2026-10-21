"""Pin public WorkItem form choices and localized date messages.

Source: mutation delta of the release-0.99.8 checkpoint, 17 mutants.
Groups: historical assignment boundaries (1), recipient labels (2),
widget messages (5), and upper-bound validation messages (2).
The remaining 7 mutants are equivalent and need no pins.
"""

from datetime import timedelta

import pytest
from django.utils.formats import date_format
from django.utils.translation import gettext, override

from core.forms.workitems import WorkItemForm, max_workitem_date
from core.models import User, WorkItem

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("saved", [False, True], ids=["new-prefilled", "foreign-existing"])
def test_historical_recipient_requires_saved_item_in_same_facility(facility, other_facility, lead_user, saved):
    """Reject inactive recipients outside a saved task's own facility context.

    Pins xǁWorkItemFormǁ__init____mutmut_14 through form choices and validation.
    """
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    item = WorkItem(
        facility=other_facility if saved else facility,
        assigned_to=lead_user,
        title="Prefilled assignment",
    )
    if saved:
        item.save()
    form = WorkItemForm(
        instance=item,
        facility=facility,
        data={"title": item.title, "item_type": "task", "priority": "normal", "assigned_to": lead_user.pk},
    )

    assert lead_user.pk not in form.fields["assigned_to"].queryset.values_list("pk", flat=True)
    assert not form.is_valid()
    assert form.errors.as_data()["assigned_to"][0].code == "invalid_choice"


def test_only_current_recipient_has_current_label(facility, lead_user, staff_user):
    """Other eligible recipients retain their own unadorned display names.

    Pins xǁWorkItemFormǁ__init____mutmut_31 and xǁWorkItemFormǁ__init____mutmut_33.
    """
    User.objects.filter(pk=lead_user.pk).update(is_active=False)
    item = WorkItem.objects.create(facility=facility, assigned_to=lead_user, title="Historical assignment")
    with override("de"):
        form = WorkItemForm(instance=item, facility=facility)
        labels = {str(value): label for value, label in form.fields["assigned_to"].choices}

        current_label = gettext("aktuell")
        assert labels[str(lead_user.pk)] == f"{lead_user} ({current_label})"
        assert labels[str(staff_user.pk)] == str(staff_user)


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("field_name", ["due_date", "remind_at"])
def test_date_widget_messages_use_exact_localized_wording(language, field_name):
    """Both date widgets expose the translated bounds messages verbatim.

    Pins xǁWorkItemFormǁ__init____mutmut_88, xǁWorkItemFormǁ__init____mutmut_89,
    xǁWorkItemFormǁ__init____mutmut_95, xǁWorkItemFormǁ__init____mutmut_96,
    and xǁWorkItemFormǁ__init____mutmut_97.
    """
    with override(language):
        form = WorkItemForm()
        attrs = form.fields[field_name].widget.attrs
        expected_max = date_format(max_workitem_date(), "DATE_FORMAT")

        assert attrs["data-msg-too-early"] == gettext("Das Datum darf nicht in der Vergangenheit liegen.")
        assert attrs["data-msg-too-late"] == gettext("Das Datum darf höchstens am %(max)s liegen.") % {
            "max": expected_max
        }


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("due_date", "Das Fälligkeitsdatum darf höchstens am %(max)s liegen."),
        ("remind_at", "Die Erinnerung darf höchstens am %(max)s liegen."),
    ],
    ids=["due-date", "reminder"],
)
def test_date_above_maximum_reports_exact_localized_error(facility, language, field_name, message):
    """An excessive date names the localized upper bound in its field error.

    Pins xǁWorkItemFormǁclean__mutmut_29 and xǁWorkItemFormǁclean__mutmut_41.
    """
    maximum = max_workitem_date()
    with override(language):
        form = WorkItemForm(
            facility=facility,
            data={
                "title": "Excessive date",
                "item_type": "task",
                "priority": "normal",
                field_name: (maximum + timedelta(days=1)).isoformat(),
            },
        )

        assert not form.is_valid()
        assert form.errors == {field_name: [gettext(message) % {"max": date_format(maximum, "DATE_FORMAT")}]}
