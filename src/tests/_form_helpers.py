"""Helper utilities for ``core.forms.*``-Unit-Tests (Refs #922 / #925).

Diese Helfer kapseln wiederkehrende Assertions, damit die Forms-Tests
strikt bleiben und Fehlermeldungen sprechend sind, ohne dass jedes File
seinen eigenen ``form.errors``-Dump baut.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django import forms


def assert_field_error(form: forms.Form, field_name: str, expected_substring: str) -> None:
    """Assert that a bound form has an error containing *expected_substring*.

    Fails with the full ``form.errors`` dump if either the field has no
    error or none of the errors contain the substring. ``form.is_valid()``
    is called implicitly, so callers do not need to do that first.
    """
    if form.is_valid():
        raise AssertionError(
            f"Form was unexpectedly valid — expected error for {field_name!r} "
            f"containing {expected_substring!r}.\nCleaned data: {form.cleaned_data}"
        )
    errors = form.errors.get(field_name) or form.errors.get("__all__") or []
    if not any(expected_substring in msg for msg in errors):
        raise AssertionError(
            f"Expected error on {field_name!r} containing {expected_substring!r}; got {dict(form.errors)!r} instead."
        )


def assert_no_field_errors(form: forms.Form, *fields: str) -> None:
    """Assert that none of the given fields have errors (other fields may)."""
    form.is_valid()  # trigger validation, ignore return
    for field in fields:
        assert field not in form.errors, (
            f"Unexpected error on {field!r}: {form.errors[field]!r}. Full errors: {dict(form.errors)!r}"
        )


def assert_clean_value(form: forms.Form, field_name: str, expected: Any) -> None:
    """Assert that ``form.cleaned_data[field_name]`` equals *expected*.

    Validates the form first; fails with the full error-dict if invalid.
    """
    if not form.is_valid():
        raise AssertionError(f"Form invalid: {dict(form.errors)!r}")
    actual = form.cleaned_data.get(field_name)
    assert actual == expected, f"cleaned_data[{field_name!r}] = {actual!r}, expected {expected!r}"


def assert_form_valid(form: forms.Form) -> None:
    """Assert the form is valid; on failure include the full error-dict."""
    assert form.is_valid(), f"Form invalid: {dict(form.errors)!r}"


def queryset_pks(form: forms.Form, field_name: str) -> list[Any]:
    """Return the primary keys of a ``ModelChoiceField``/``ModelMultipleChoiceField``
    queryset — handy for asserting which choices a user is allowed to pick.
    """
    field = form.fields[field_name]
    return list(field.queryset.values_list("pk", flat=True))  # type: ignore[attr-defined]
