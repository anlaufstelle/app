"""Externe Berichte enthalten keine Pseudonyme (DSGVO-Datenminimierung).

Refs Matrix AUD-SEC-EXPORT-02 (Master #922).

Externe Berichte gehen an Traeger, Foerdergeber oder Behoerden und
duerfen keine Re-Identifizierungs-Hinweise enthalten. Konkret:

- Der Dokumenttyp-/Altersgruppen-Bericht der Karte "Allgemeine Kennzahlen"
  traegt keine Pseudonyme (``test_reporting_export_disclosure.py``; die frueheren
  Uebersichts-PDF-Pfade samt Pseudonym-Rangliste existieren nicht mehr). Refs #792.
- ``get_sample_report_1_statistics`` (seit M5 P3 der Testhelfer aus
  ``tests._reporting_helpers``, der auf ``SampleReport1Backend.build_dataset``
  zeigt) aggregiert ueber DocumentType-Kategorien und Altersgruppen, ohne
  Pseudonyme zurueckzugeben — der Output enthaelt keine ``pseudonym``-Felder.

Der Test verifiziert, dass die Sachbericht-Aggregat-Funktion strukturell keine
Pseudonyme zurueckgibt (Output-Schema-Check).

Hinweis: ``get_statistics`` (interne Statistik fuer Lead/Admin)
*enthaelt* ``top_clients`` mit Pseudonymen — das ist beabsichtigt, weil
sie nur intern gezeigt werden. Die externen Berichts-Pfade enthalten sie
nicht.
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
