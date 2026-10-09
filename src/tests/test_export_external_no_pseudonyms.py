"""External reports contain no pseudonyms (GDPR data minimisation).

Refs matrix AUD-SEC-EXPORT-02 (master #922).

External reports go to providers, funders or authorities and must not contain
re-identification hints. Concretely:

- The document-type/age-group report of the "Allgemeine Kennzahlen" card
  carries no pseudonyms (``test_reporting_export_disclosure.py``; the former
  overview PDF paths with the pseudonym ranking no longer exist). Refs #792.
- ``get_sample_report_1_statistics`` (since M5 P3 the test helper from
  ``tests._reporting_helpers`` that points to
  ``SampleReport1Backend.build_dataset``) aggregates over DocumentType
  categories and age groups without returning pseudonyms — the output
  contains no ``pseudonym`` fields.

The test verifies that the sample report aggregate function structurally
returns no pseudonyms (output schema check).

Note: ``get_statistics`` (internal statistics for lead/admin) *does contain*
``top_clients`` with pseudonyms — this is intended because they are only
shown internally. The external report paths do not contain them.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Client, Event
from tests._reporting_helpers import get_sample_report_1_statistics

# Eindeutige Pseudonyme — wenn sie irgendwo im HTML auftauchen, ist das
# kein Zufall (kein Substring-Overlap mit Boilerplate).
_PSEUDONYMS = ("Stern-42", "Mond-7", "Sonne-13")


@pytest.fixture
def populated_facility(facility, doc_type_contact, staff_user):
    """Facility mit drei Klienten und gestaffelten Event-Counts (1/2/3),
    damit ``get_statistics`` ein Top-5-Ranking mit allen Pseudonymen
    aufbaut."""
    today = timezone.now()
    for i, pseudonym in enumerate(_PSEUDONYMS):
        client = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym=pseudonym,
            created_by=staff_user,
        )
        for _ in range(i + 1):
            Event.objects.create(
                facility=facility,
                client=client,
                document_type=doc_type_contact,
                occurred_at=today - timedelta(hours=1),
                data_json={"dauer": 15, "notiz": "Test"},
                created_by=staff_user,
            )
    return facility


@pytest.mark.django_db
class TestExportExternalNoPseudonyms:
    """Externe Berichts-Pfade enthalten keine Pseudonyme."""

    def test_sample_report_1_aggregate_has_no_pseudonym_keys(self, populated_facility):
        """``get_sample_report_1_statistics`` liefert nur Aggregate ueber
        DocumentType-Kategorien und Altersgruppen — keine Pseudonyme,
        keine Klient-PKs. Damit ist der Beispiel-Sachbericht von Haus aus
        re-identifizierungs-frei.
        """
        today = timezone.localdate()
        date_from = today - timedelta(days=7)

        stats = get_sample_report_1_statistics(populated_facility, date_from, today)

        # Top-Level-Keys: nur Aggregate, keine Klient-Referenzen.
        assert set(stats.keys()) == {"total", "by_category", "by_age_cluster", "unique_clients"}
        assert isinstance(stats["total"], int)
        assert isinstance(stats["unique_clients"], int)

        # by_category: (Kategoriename, Count) — keine Pseudonyme.
        for entry in stats["by_category"]:
            assert isinstance(entry, tuple)
            name, count = entry
            assert isinstance(name, str)
            assert isinstance(count, int)
            for pseudonym in _PSEUDONYMS:
                assert pseudonym not in name

        # by_age_cluster: nur Cluster-Labels, Counts und die DISTINCT-Personenzahl
        # des Buckets (K0-b, Refs #1770) — keine Klient-Referenzen. ``persons``
        # ist Suppressions-Eingabe im Roh-Aggregat und wird vor jeder Ausgabe
        # entfernt (siehe ``_suppress_small``).
        for row in stats["by_age_cluster"]:
            assert set(row.keys()) == {"cluster", "label", "count", "persons"}
            assert isinstance(row["persons"], int)
            assert "pseudonym" not in row
