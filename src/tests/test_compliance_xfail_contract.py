"""Keep pending-catalog failures limited to translation assertions."""

from importlib import import_module

import pytest


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("test_compliance_i18n_branches", "test_english_request_has_no_german_card_text"),
        ("test_compliance_sensitivity_i18n", "test_english_board_has_no_german_protection_text"),
    ],
)
def test_catalog_xfail_only_accepts_assertions(module, name):
    test = getattr(import_module(f"tests.{module}"), name)
    marker = next(marker for marker in test.pytestmark if marker.name == "xfail")
    assert marker.kwargs["strict"] is True
    assert marker.kwargs.get("raises") is AssertionError
