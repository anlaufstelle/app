"""Externe Berichte enthalten keine Pseudonyme (DSGVO-Datenminimierung).

Refs Matrix AUD-SEC-EXPORT-02 (Welle 3 / Master #922).

Externe Berichte gehen an Traeger, Foerdergeber oder Jugendamt und
duerfen keine Re-Identifizierungs-Hinweise enthalten. Konkret:

- :func:`generate_report_pdf` mit ``internal_mode=False`` rendert die
  ``Top 5 Personen``-Tabelle gemaess Template-Branch nicht (siehe
  ``core/export/report_pdf.html`` Zeile 105f. — gated auf
  ``{% if internal_mode and stats.top_clients %}``). Refs #792 (C-24).
- :func:`get_jugendamt_statistics` aggregiert ueber DocumentType-
  Kategorien und Altersgruppen, ohne Pseudonyme zurueckzugeben — der
  Output enthaelt keine ``pseudonym``-Felder.

Die Tests verifizieren beide Pfade:

1. Das HTML, das ``generate_report_pdf`` an WeasyPrint uebergibt, muss
   pseudonymfrei sein. Wir rendern das Template direkt ueber
   ``django.template.loader.render_to_string`` — robuster als
   PDF-Bytes nach WeasyPrint zu durchsuchen (Font-Subsetting verschluckt
   Plain-Text-Strings).
2. Die Jugendamt-Aggregat-Funktion gibt strukturell keine Pseudonyme
   zurueck (Output-Schema-Check).

Hinweis: ``get_statistics`` (interne Statistik fuer Lead/Admin)
*enthaelt* ``top_clients`` mit Pseudonymen — das ist beabsichtigt, weil
sie nur intern gezeigt werden. Die externe Berichts-Pipeline filtert
das im Template-Layer aus.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from core.models import Client, Event
from core.services.export import get_jugendamt_statistics
from core.services.statistics import get_statistics

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


def _render_report_html(facility, stats, *, internal_mode: bool, date_from, date_to):
    """Spiegel von ``generate_report_pdf`` — rendert genau das Template,
    das WeasyPrint sonst kassiert. Statt PDF-Bytes pruefen wir das HTML
    direkt: dort sind Pseudonyme als Plaintext sichtbar, falls sie
    gerendert werden.
    """
    return render_to_string(
        "core/export/report_pdf.html",
        {
            "facility_name": facility.name,
            "date_from": date_from,
            "date_to": date_to,
            "stats": stats,
            "internal_mode": internal_mode,
            "generated_at": timezone.now(),
        },
    )


@pytest.mark.django_db
class TestExportExternalNoPseudonyms:
    """Externe Berichts-Pfade enthalten keine Pseudonyme."""

    def test_external_report_html_contains_no_pseudonyms(self, populated_facility):
        """``report_pdf.html`` mit ``internal_mode=False`` darf keine
        Pseudonyme rendern. Ohne den Template-Gate landeten sie sonst aus
        ``stats.top_clients`` direkt im PDF.
        """
        today = timezone.now().date()
        date_from = today - timedelta(days=7)
        stats = get_statistics(populated_facility, date_from, today)

        # Sanity: get_statistics enthaelt Pseudonyme — sonst koennte der
        # Template-Gate nichts ausblenden und der Test waere trivial.
        top_pseudonyms = {row["pseudonym"] for row in stats["top_clients"]}
        assert set(_PSEUDONYMS).issubset(top_pseudonyms), (
            f"Setup-Bug: erwartete Pseudonyme {_PSEUDONYMS} fehlen in top_clients={top_pseudonyms!r}."
        )

        html = _render_report_html(populated_facility, stats, internal_mode=False, date_from=date_from, date_to=today)

        for pseudonym in _PSEUDONYMS:
            assert pseudonym not in html, (
                f"Externes Report-HTML enthaelt Pseudonym {pseudonym!r}. "
                "internal_mode=False muss die Top-5-Tabelle ausblenden "
                "(Refs #792 / C-24)."
            )
        # Auch die Spalten-Ueberschrift der Top-5-Tabelle darf nicht
        # erscheinen — Indiz, dass der Gate-Block geschlossen ist.
        assert "Top 5 Personen" not in html

    def test_internal_report_html_contains_pseudonyms_sanity_check(self, populated_facility):
        """Negativkontrolle: ``internal_mode=True`` *muss* Pseudonyme im
        HTML rendern. Ohne diese Probe wuerde der Positivtest trivial
        bestehen, falls das Template gar keine Pseudonyme mehr rendert.
        """
        today = timezone.now().date()
        date_from = today - timedelta(days=7)
        stats = get_statistics(populated_facility, date_from, today)

        html = _render_report_html(populated_facility, stats, internal_mode=True, date_from=date_from, date_to=today)

        # Mindestens eines der Pseudonyme muss erscheinen (typischerweise
        # alle drei).
        found = [p for p in _PSEUDONYMS if p in html]
        assert found, (
            "Internal-Mode rendert keine Pseudonyme — Setup-Bug oder "
            "internal_mode-Gate defekt. Ohne diesen Negativtest sagt der "
            "Positivtest nichts aus."
        )
        assert "Top 5 Personen" in html

    def test_jugendamt_aggregate_has_no_pseudonym_keys(self, populated_facility):
        """``get_jugendamt_statistics`` liefert nur Aggregate ueber
        DocumentType-Kategorien und Altersgruppen — keine Pseudonyme,
        keine Klient-PKs. Damit ist der Jugendamt-Bericht von Haus aus
        re-identifizierungs-frei.
        """
        today = timezone.now().date()
        date_from = today - timedelta(days=7)

        stats = get_jugendamt_statistics(populated_facility, date_from, today)

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

        # by_age_cluster: nur Cluster-Labels + Counts.
        for row in stats["by_age_cluster"]:
            assert set(row.keys()) == {"cluster", "label", "count"}
            assert "pseudonym" not in row
