"""Pins for the public free-text review form API.

Source: mutation delta of the release-0.99.9 checkpoint, 32 mutants.
Groups: facility isolation (1), marking validation and field errors (19),
model requirement and keyword binding (3), editor values and labels (2),
filter widget styling (5), date validation boundaries (2).
"""

from datetime import date, timedelta

import pytest
from django.utils.html import escape
from django.utils.translation import gettext as _

from core.forms.free_text_review import FreeTextReviewForm, ReviewFilterForm
from core.models import User


@pytest.fixture
def review_date(monkeypatch):
    """Keep calendar boundaries stable while exercising the public validation API."""
    today = date(2026, 10, 28)
    monkeypatch.setattr("core.forms.free_text_review.timezone.localdate", lambda: today)
    return today


def test_editor_choices_exclude_foreign_facility_candidates(facility, second_facility, staff_user):
    """Candidate IDs cannot expose an editor from another facility.

    xǁReviewFilterFormǁ__init____mutmut_39
    """
    foreign = User.objects.create_user(username="aaa_foreign_editor", facility=second_facility)
    form = ReviewFilterForm(
        facility=facility,
        user=staff_user,
        editor_ids=[foreign.pk, staff_user.pk],
    )

    assert {value for value, _label in form.fields["editor"].choices} == {"", str(staff_user.pk)}
    assert foreign.username not in str(form["editor"])


def test_mark_requires_date_and_fingerprint():
    """Marking reports both missing inputs on their fields.

    xǁFreeTextReviewFormǁclean__mutmut_3
    xǁFreeTextReviewFormǁclean__mutmut_4
    xǁFreeTextReviewFormǁclean__mutmut_5
    xǁFreeTextReviewFormǁclean__mutmut_7
    xǁFreeTextReviewFormǁclean__mutmut_8
    """
    form = FreeTextReviewForm(data={"action": "mark"})

    assert not form.is_valid()
    assert form.errors == {
        "reviewed_until": [_("Bitte wählen Sie heute oder ein zukünftiges Datum.")],
        "fingerprint": [_("Bitte laden Sie die Liste neu und prüfen Sie den Text erneut.")],
    }
    assert "reviewed_until" not in form.cleaned_data
    assert "fingerprint" not in form.cleaned_data


@pytest.mark.parametrize("offset", [None, -1], ids=["missing", "past"])
def test_mark_rejects_missing_or_past_date_on_date_field(review_date, offset):
    """Invalid review dates produce a readable field error and are removed from cleaned data.

    xǁFreeTextReviewFormǁclean__mutmut_13
    xǁFreeTextReviewFormǁclean__mutmut_16
    xǁFreeTextReviewFormǁclean__mutmut_17
    xǁFreeTextReviewFormǁclean__mutmut_18
    xǁFreeTextReviewFormǁclean__mutmut_19
    xǁFreeTextReviewFormǁclean__mutmut_20
    xǁFreeTextReviewFormǁclean__mutmut_21
    xǁFreeTextReviewFormǁclean__mutmut_22
    """
    value = "" if offset is None else (review_date + timedelta(days=offset)).isoformat()
    form = FreeTextReviewForm(data={"action": "mark", "reviewed_until": value, "fingerprint": "a" * 64})

    assert not form.is_valid()
    expected = _("Bitte wählen Sie heute oder ein zukünftiges Datum.")
    assert form.errors == {"reviewed_until": [expected]}
    assert "reviewed_until" not in form.cleaned_data
    assert escape(expected) in str(form["reviewed_until"].errors)


@pytest.mark.parametrize("offset", [0, 1], ids=["today", "future"])
def test_mark_accepts_today_and_future_dates(review_date, offset):
    """A fingerprint permits marking through today or a later date.

    xǁFreeTextReviewFormǁclean__mutmut_15
    """
    until = review_date + timedelta(days=offset)
    form = FreeTextReviewForm(data={"action": "mark", "reviewed_until": until.isoformat(), "fingerprint": "a" * 64})

    assert form.is_valid(), form.errors
    assert form.cleaned_data["reviewed_until"] == until
    assert form.cleaned_data["fingerprint"] == "a" * 64


def test_mark_without_fingerprint_has_readable_field_error(review_date):
    """A missing fingerprint renders the reload instruction at its field and removes the input.

    xǁFreeTextReviewFormǁclean__mutmut_30
    xǁFreeTextReviewFormǁclean__mutmut_31
    xǁFreeTextReviewFormǁclean__mutmut_32
    xǁFreeTextReviewFormǁclean__mutmut_33
    xǁFreeTextReviewFormǁclean__mutmut_34
    xǁFreeTextReviewFormǁclean__mutmut_35
    xǁFreeTextReviewFormǁclean__mutmut_36
    """
    form = FreeTextReviewForm(data={"action": "mark", "reviewed_until": review_date.isoformat()})

    assert not form.is_valid()
    expected = _("Bitte laden Sie die Liste neu und prüfen Sie den Text erneut.")
    assert form.errors == {"fingerprint": [expected]}
    assert "fingerprint" not in form.cleaned_data
    assert escape(expected) in str(form["fingerprint"].errors)


def test_filter_requires_model_when_choices_exist(facility, staff_user):
    """Available models make the model selection mandatory for submitted filters.

    xǁReviewFilterFormǁ__init____mutmut_11
    xǁReviewFilterFormǁ__init____mutmut_14
    """
    form = ReviewFilterForm({"model": "", "editor": ""}, facility=facility, user=staff_user)

    assert list(form.fields["model"].choices)
    assert form.fields["model"].required
    assert not form.is_valid()
    assert form.errors["model"] == [form.fields["model"].error_messages["required"]]


def test_filter_preserves_keyword_bound_data(facility, staff_user):
    """Keyword data binds the form and preserves the selected model, editor and cutoff date.

    xǁReviewFilterFormǁ__init____mutmut_2
    """
    form = ReviewFilterForm(
        data={"model": "Event", "changed_before": "2026-01-02", "editor": str(staff_user.pk), "risk": ""},
        facility=facility,
        user=staff_user,
        editor_ids=[staff_user.pk],
    )

    assert form.is_bound
    assert form.is_valid(), form.errors
    assert form.cleaned_data == {
        "model": "Event",
        "changed_before": date(2026, 1, 2),
        "editor": str(staff_user.pk),
        "risk": "",
    }


def test_all_editors_option_submits_empty_filter(facility, staff_user):
    """Selecting all editors cleans to the empty editor filter used by the list service.

    xǁReviewFilterFormǁ__init____mutmut_29
    """
    unbound = ReviewFilterForm(facility=facility, user=staff_user, editor_ids=[staff_user.pk])
    value, label = next(iter(unbound.fields["editor"].choices))
    assert str(label) == _("Alle")
    form = ReviewFilterForm(
        {"model": "Event", "editor": value},
        facility=facility,
        user=staff_user,
        editor_ids=[staff_user.pk],
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["editor"] == ""


def test_editor_labels_use_display_name_with_username_fallback(facility, staff_user):
    """Editor labels prefer a display name and fall back to a nonempty username.

    xǁReviewFilterFormǁ__init____mutmut_35
    """
    named = User.objects.create_user(username="named_editor", display_name="Visible editor", facility=facility)
    unnamed = User.objects.create_user(username="unnamed_editor", display_name="", facility=facility)
    form = ReviewFilterForm(facility=facility, user=staff_user, editor_ids=[named.pk, unnamed.pk])

    labels = dict(form.fields["editor"].choices)
    assert labels[str(named.pk)] == "Visible editor"
    assert labels[str(unnamed.pk)] == "unnamed_editor"


def test_filter_widgets_expose_and_render_styling_classes(facility, staff_user):
    """Every filter widget exposes its case-sensitive styling through the class attribute and HTML.

    xǁReviewFilterFormǁ__init____mutmut_59
    xǁReviewFilterFormǁ__init____mutmut_60
    xǁReviewFilterFormǁ__init____mutmut_61
    xǁReviewFilterFormǁ__init____mutmut_62
    xǁReviewFilterFormǁ__init____mutmut_63
    """
    form = ReviewFilterForm(facility=facility, user=staff_user)
    expected = {"rounded", "border", "border-ink-muted", "bg-surface", "p-2", "text-ink"}

    for name, field in form.fields.items():
        classes = field.widget.attrs["class"]
        assert expected <= set(classes.split())
        assert f'class="{classes}"' in str(form[name])
