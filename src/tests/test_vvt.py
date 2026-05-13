"""Tests fuer ``core.services.vvt`` (Verzeichnis Verarbeitungstaetigkeiten).

Refs #876. Testet die statische Konstante ``PROCESSING_ACTIVITIES``:

* Schema: jeder Eintrag hat alle Pflichtfelder.
* Mindestabdeckung: alle 6 Pflicht-Verarbeitungstaetigkeiten sind
  registriert.
* Helper: ``get_processing_activities`` und ``get_activity`` verhalten
  sich erwartungsgemaess.
"""

from django.utils.functional import Promise

from core.services.vvt import (
    PROCESSING_ACTIVITIES,
    get_activity,
    get_processing_activities,
)

# Pflichtfelder pro Eintrag laut Auftrag.
REQUIRED_KEYS = {
    "id",
    "title",
    "purpose",
    "legal_basis",
    "data_categories",
    "recipients",
    "retention_period",
    "toms",
}

# Mindest-IDs, die im Verzeichnis vorhanden sein muessen (Refs #876).
REQUIRED_IDS = {
    "klienten_stammdaten",
    "falldaten",
    "auditlog",
    "auth_login",
    "backup",
    "dsgvo_requests",
}


class TestProcessingActivitiesSchema:
    """Jeder Eintrag erfuellt das Pflicht-Schema."""

    def test_all_entries_have_required_keys(self):
        for entry in PROCESSING_ACTIVITIES:
            missing = REQUIRED_KEYS - set(entry.keys())
            assert missing == set(), f"Eintrag {entry.get('id', '?')} fehlen Felder: {missing}"

    def test_id_is_unique(self):
        ids = [entry["id"] for entry in PROCESSING_ACTIVITIES]
        assert len(ids) == len(set(ids)), f"Doppelte IDs gefunden: {ids}"

    def test_id_is_str(self):
        for entry in PROCESSING_ACTIVITIES:
            assert isinstance(entry["id"], str)

    def test_text_fields_are_translatable(self):
        """``title``, ``purpose``, ``legal_basis``, ``retention_period`` sind
        ``gettext_lazy``-Strings (also ``Promise``-Instanzen) — wichtig
        fuer die spaetere Lokalisierung (Refs #878).
        """
        for entry in PROCESSING_ACTIVITIES:
            for field in ("title", "purpose", "legal_basis", "retention_period"):
                value = entry[field]
                assert isinstance(value, (str, Promise)), f"Feld {field!r} in {entry['id']} ist weder str noch lazy."

    def test_list_fields_are_lists_of_translatable_strings(self):
        for entry in PROCESSING_ACTIVITIES:
            for field in ("data_categories", "recipients", "toms"):
                value = entry[field]
                assert isinstance(value, list), f"Feld {field!r} in {entry['id']} ist keine Liste."
                assert len(value) > 0, f"Feld {field!r} in {entry['id']} ist leer."
                for item in value:
                    assert isinstance(item, (str, Promise)), (
                        f"Element in {field!r} von {entry['id']} ist weder str noch lazy."
                    )


class TestProcessingActivitiesCoverage:
    """Pflicht-Verarbeitungstaetigkeiten sind registriert."""

    def test_minimum_six_activities(self):
        assert len(PROCESSING_ACTIVITIES) >= 6, (
            f"Erwartet mindestens 6 Eintraege, gefunden: {len(PROCESSING_ACTIVITIES)}."
        )

    def test_required_ids_present(self):
        present_ids = {entry["id"] for entry in PROCESSING_ACTIVITIES}
        missing = REQUIRED_IDS - present_ids
        assert missing == set(), f"Fehlende Pflicht-IDs: {missing}"


class TestVVTHelpers:
    """``get_processing_activities`` und ``get_activity``."""

    def test_get_processing_activities_returns_full_list(self):
        result = get_processing_activities()
        assert isinstance(result, list)
        assert len(result) == len(PROCESSING_ACTIVITIES)

    def test_get_processing_activities_returns_snapshot(self):
        """Aenderungen am Resultat duerfen die Konstante nicht mutieren."""
        before = len(PROCESSING_ACTIVITIES)
        result = get_processing_activities()
        result.pop()
        assert len(PROCESSING_ACTIVITIES) == before

    def test_get_activity_known_id(self):
        entry = get_activity("klienten_stammdaten")
        assert entry is not None
        assert entry["id"] == "klienten_stammdaten"

    def test_get_activity_all_required_ids(self):
        for activity_id in REQUIRED_IDS:
            entry = get_activity(activity_id)
            assert entry is not None, f"get_activity({activity_id!r}) lieferte None."
            assert entry["id"] == activity_id

    def test_get_activity_unknown_returns_none(self):
        assert get_activity("does-not-exist") is None
