"""Exercise the English surface guard without starting a browser or server."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.e2e import test_english_surfaces_acceptance as surfaces


@pytest.mark.parametrize("translation_key", ["Filtern", ("Filtern", 0)])
def test_english_catalog_translation_is_not_reported_as_german(monkeypatch, translation_key):
    catalog = {"Filter": "Filters", translation_key: "Filter", "Speichern": "Save"}
    monkeypatch.setattr(surfaces.gettext, "GNUTranslations", Mock(return_value=SimpleNamespace(_catalog=catalog)))

    german_ids = surfaces._translated_message_ids()

    assert {"Filter"} & german_ids == set()
    assert {"Speichern"} & german_ids == {"Speichern"}


def test_german_text_without_an_english_catalog_collision_is_reported(monkeypatch):
    catalog = {"Speichern": "Save", "Abbrechen": "Cancel"}
    monkeypatch.setattr(surfaces.gettext, "GNUTranslations", Mock(return_value=SimpleNamespace(_catalog=catalog)))

    assert {"Speichern", "Abbrechen", "Save", "Cancel"} & surfaces._translated_message_ids() == {
        "Speichern",
        "Abbrechen",
    }


def test_actual_catalog_accepts_filter_and_still_reports_german_text():
    german_ids = surfaces._translated_message_ids()

    assert {"Filter", "Speichern", "Abbrechen"} & german_ids == {"Speichern", "Abbrechen"}
