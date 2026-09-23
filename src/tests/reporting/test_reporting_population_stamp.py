"""Grundgesamtheit + Herkunfts-/Stand-Stempel der Berichts-Artefakte.

K3-A3 und K4c der Disclosure-Analyse (Refs #1770, Analyse
#1742):

* **K3-A3** — In jedem Artefakt stehen Aggregate ueber **verschiedenen
  Basismengen** nebeneinander, und bis hierher benannte kein Artefakt, welche
  Tabelle welche meint. Das ist nicht nur ein Leck (die Differenz zweier
  Grundgesamtheiten ist K3 selbst), sondern inhaltlich irrefuehrend.
* **K4c** — #1743 wies
  die **Quelle** jedes Artefakts aus (live vs. Monats-Aggregate), nicht aber
  deren **Stand**. Ohne Datum kann eine Empfaengerin nicht erkennen, dass sie
  zwei Artefakte verschiedenen Standes vergleicht — und genau die Differenz
  zwischen eingefrorenem Aggregat und Live-Abfrage ist der Fingerabdruck des
  Geloeschten.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from core.reporting.data_source import (
    ALL_EVENTS,
    EVENTS_WITH_PERSON,
    LIVE,
    MAPPED_EVENTS,
    POPULATION_NOTES,
    SNAPSHOT_HYBRID,
    TYPED_EVENTS,
    table_note,
)


class TestGrundgesamtheitsAusweis:
    """Der Ausweis setzt auf der #1743-Infrastruktur auf, statt sie zu duplizieren."""

    def test_note_nennt_grundgesamtheit_und_quelle(self):
        note = str(table_note(ALL_EVENTS, LIVE))

        assert "Grundgesamtheit" in note
        assert "alle Ereignisse im Zeitraum" in note
        assert "Live-Abfrage zum Erstellungszeitpunkt" in note

    def test_verschiedene_grundgesamtheiten_sind_unterscheidbar(self):
        """Der Ausweis muss die Differenz benennen, um die es in K3 geht."""
        assert str(table_note(MAPPED_EVENTS, SNAPSHOT_HYBRID)) != str(table_note(EVENTS_WITH_PERSON, SNAPSHOT_HYBRID))

    def test_unbekannter_code_ist_fail_closed(self):
        with pytest.raises(KeyError):
            table_note("gibt-es-nicht", LIVE)


@pytest.mark.django_db
class TestGrundgesamtheitInDenArtefakten:
    """Jede Tabelle der sichtbaren Artefakte traegt ihren Ausweis."""

    def test_externer_bericht_html(self, facility):
        from core.services.dashboard import suppress_report_stats

        seite = render_to_string(
            "core/statistics/external_report.html",
            {
                "report": suppress_report_stats(
                    facility,
                    {
                        "total_contacts": 40,
                        "unique_clients": 20,
                        "by_contact_stage": {},
                        "persons_by_contact_stage": {},
                        "by_document_type": [{"name": "Kontakt", "count": 40}],
                        "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": 30, "persons": 20}],
                    },
                ),
                "population_notes": {
                    "by_document_type": table_note(ALL_EVENTS, LIVE),
                    "by_age_cluster": table_note(ALL_EVENTS, LIVE),
                },
            },
        )

        assert seite.count("Grundgesamtheit:") == 2, "beide Tabellen brauchen den Ausweis"

    def test_externer_bericht_json_traegt_stabile_codes(self, facility):
        """Fuer Maschinen der Code, fuer Menschen der uebersetzte Text — dieselbe
        Trennung wie bei ``data_source`` (#1743)."""
        from core.services.dashboard import build_external_report

        report = build_external_report(facility, date(2026, 1, 1), date(2026, 12, 31))

        populations = report["metadata"]["populations"]
        assert populations["by_document_type"] == ALL_EVENTS
        # Seit der K3-A1-Restzeile zaehlt die Alterstabelle ebenfalls ALLE
        # Ereignisse des Zeitraums — das ist der Punkt der Restzeile.
        assert populations["by_age_cluster"] == ALL_EVENTS

    def test_halbjahres_sachbericht_pdf_datenpfad(self, facility):
        from core.services.dashboard import suppress_report_stats

        seite = render_to_string(
            "core/export/report_pdf.html",
            {
                "stats": suppress_report_stats(
                    facility,
                    {
                        "total_contacts": 40,
                        "unique_clients": 20,
                        "by_contact_stage": {},
                        "persons_by_contact_stage": {},
                        "by_document_type": [{"name": "Kontakt", "count": 40}],
                        "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": 30, "persons": 20}],
                    },
                ),
                "population_notes": {
                    "by_document_type": table_note(ALL_EVENTS, SNAPSHOT_HYBRID),
                    "by_age_cluster": table_note(ALL_EVENTS, SNAPSHOT_HYBRID),
                },
            },
        )

        assert seite.count("Grundgesamtheit:") == 2

    def test_beispiel_sachbericht_pdf_nennt_zwei_verschiedene_mengen(self, facility):
        """Hier ist der Ausweis nicht Kosmetik: ``total``/``by_category``
        zaehlen die **gemappten** Ereignisse, die Altersverteilung die mit
        **Personenbezug** — zwei verschiedene Basismengen im selben Bericht."""
        from core.services.dashboard import suppress_sample_report_1_stats

        seite = render_to_string(
            "core/export/sample_report_1_pdf.html",
            {
                "stats": suppress_sample_report_1_stats(
                    facility,
                    {
                        "total": 30,
                        "unique_clients": 20,
                        "by_category": [("Kontakte", 30)],
                        "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": 25, "persons": 20}],
                    },
                ),
                "population_notes": {
                    "by_category": table_note(MAPPED_EVENTS, SNAPSHOT_HYBRID),
                    "by_age_cluster": table_note(EVENTS_WITH_PERSON, SNAPSHOT_HYBRID),
                },
            },
        )

        assert "Ereignisse mit Berichtszuordnung im Zeitraum" in seite
        assert "Ereignisse mit Personenbezug im Zeitraum" in seite

    def test_backend_deklariert_seine_grundgesamtheit(self):
        from core.reporting.base import DatasetBackend
        from core.reporting.registry import get_backend

        assert get_backend("sample-report-1").population == MAPPED_EVENTS
        assert DatasetBackend.population == ALL_EVENTS, "weiteste Menge als Default, engere muessen ueberschreiben"

    def test_jedes_registrierte_backend_hat_eine_uebersetzte_kurzform(self):
        """Fail-closed, aber erst zur Renderzeit — und das ist zu spaet.

        :func:`table_note` schlaegt ``POPULATION_NOTES`` nach und wirft bei einem
        unbekannten Code ``KeyError``. Ein Backend, das einen Code deklariert,
        den niemand registriert hat, faellt damit erst beim Panel-/Export-Aufruf
        auf — also in der Oberflaeche der Empfaengerin. Dieser Guard zieht den
        Fehlschlag in die Testsuite vor (Refs #1754).
        """
        from core.reporting.data_source import POPULATION_NOTES
        from core.reporting.registry import get_backends

        fehlend = {b.slug: b.population for b in get_backends() if b.population not in POPULATION_NOTES}

        assert not fehlend, f"Backends mit unregistrierter Grundgesamtheit: {fehlend}"

    def test_kurzformen_sind_datenunabhaengig_und_ziffernfrei(self):
        """Der Ausweis steht in **jedem** Artefakt, auch im suppressierten.

        Eine Kurzform, die eine Zahl oder eine Bezugsgroesse nennt, waere damit
        eine Angabe, die nie durch eine Suppressionsregel gelaufen ist —
        dieselbe Lehre wie beim Vollstaendigkeitsblock (K6, Refs #1769). Gilt je
        Code und in **jeder** Sprache; eine Uebersetzung darf keine Zahl
        ergaenzen.
        """
        from core.reporting.data_source import POPULATION_NOTES

        mit_ziffern = {
            code: str(note) for code, note in POPULATION_NOTES.items() if any(c.isdigit() for c in str(note))
        }

        assert not mit_ziffern, mit_ziffern

    def test_neue_codes_sind_von_den_bestehenden_unterscheidbar(self):
        """K3 lebt von der Differenz zweier Grundgesamtheiten — zwei Codes mit
        demselben Text waeren im Artefakt nicht auseinanderzuhalten."""
        from core.reporting.data_source import POPULATION_NOTES

        texte = [str(note) for note in POPULATION_NOTES.values()]

        assert len(set(texte)) == len(texte), texte

    def test_fall_und_hausverbots_backends_nennen_ihre_eigene_menge(self):
        """Refs #1754: ``PERSONS``/``ALL_EVENTS`` waren dort
        sachlich falsch — ein Fall setzt kein Ereignis voraus, und zwei der drei
        Hausverbots-Zahlen ignorieren ``occurred_at`` voellig."""
        from core.reporting.data_source import BANS_IN_PERIOD, CASES_IN_PERIOD
        from core.reporting.registry import get_backend

        assert get_backend("case-metrics").population == CASES_IN_PERIOD
        assert get_backend("ban-overview").population == BANS_IN_PERIOD

    @pytest.mark.parametrize("slug", ["needle-exchange-balance", "counseling-hours", "service-distribution"])
    def test_feldwert_backends_deklarieren_die_engere_menge(self, slug):
        """Refs #1754: Die Feldwert-Backends filtern auf die Dokumentationstypen,
        an denen ihr Feld haengt — sie zaehlen also NICHT alle Ereignisse des
        Zeitraums.

        Der Default ``ALL_EVENTS`` waere dort eine schlicht falsche Angabe, und
        zwar genau die Sorte Falschangabe, die K3-A3 beseitigen sollte: „alle
        Ereignisse im Zeitraum" neben einer Zahl, die eine echte Teilmenge
        zaehlt, laedt zur Differenzbildung mit jeder anderen Tabelle ein.
        """
        from core.reporting.registry import get_backend

        assert get_backend(slug).population == TYPED_EVENTS

    def test_typed_events_note_ist_datenunabhaengig_und_ziffernfrei(self):
        """Die Note steht in jedem Artefakt dieser Backends — unabhaengig davon,
        WELCHE Typen die Einrichtung konfiguriert hat und wie viele es sind.

        Eine Note, die die Typen aufzaehlte oder ihre Anzahl naennte, waere
        selbst eine Aussage ueber die Konfiguration; eine mit Ziffern waere ein
        Kanal wie die frueheren bezifferten Vollstaendigkeits-Meldungen
        (K6, Refs #1769).
        """
        note = str(POPULATION_NOTES[TYPED_EVENTS])

        assert not any(char.isdigit() for char in note)
        assert str(table_note(TYPED_EVENTS, LIVE)) != str(table_note(ALL_EVENTS, LIVE))


@pytest.mark.django_db
class TestGrundgesamtheitImEchtenViewRender:
    """Refs #1775: Die Template-Tests oben setzen ``population_notes`` **selbst**
    in den Kontext.

    Sie belegen damit nur, dass das Template den Ausweis rendert, wenn er
    ankommt — faellt er in der View weg, bleiben sie gruen. Genau diese Luecke
    war der Befund. Diese Tests gehen deshalb ueber den echten Request-Pfad
    (Client → View → Template) und lesen das ausgelieferte HTML.

    Der Marker ist die CSS-Klasse ``report-population``, die alle drei
    HTML-Flaechen um den Ausweis legen — sie zeigt, wo der Ausweis stehen SOLL,
    und deckt damit auch den Fall "Absatz da, Inhalt leer" auf.
    """

    @staticmethod
    def _ausweise(html: str) -> list[str]:
        """Die Inhalte aller ``report-population``-Absaetze der Seite."""
        import re

        return [treffer.strip() for treffer in re.findall(r'class="[^"]*report-population"[^>]*>(.*?)</p>', html, re.S)]

    def test_externer_bericht_view(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_external_report"))

        assert response.status_code == 200
        ausweise = self._ausweise(response.content.decode())
        assert len(ausweise) == 2, f"beide Tabellen brauchen den Ausweis, gefunden: {ausweise}"
        assert all("Grundgesamtheit:" in a for a in ausweise), ausweise
        assert all("alle Ereignisse im Zeitraum" in a for a in ausweise), ausweise

    def test_statistik_dashboard_view(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics"))

        assert response.status_code == 200
        ausweise = self._ausweise(response.content.decode())
        assert ausweise, "Dashboard liefert keinen Grundgesamtheits-Ausweis aus"
        assert all("Grundgesamtheit:" in a for a in ausweise), ausweise

    def test_berichts_panel_view(self, client, lead_user):
        client.force_login(lead_user)

        response = client.get(reverse("core:statistics_report_panel"), {"backend": "sample-report-1"})

        assert response.status_code == 200
        ausweise = self._ausweise(response.content.decode())
        assert len(ausweise) == 1, f"Vorschautabelle braucht ihren Ausweis, gefunden: {ausweise}"
        # Das Backend deklariert die engere Menge — der Ausweis liest sie aus,
        # statt einen Text zu wiederholen.
        assert "Ereignisse mit Berichtszuordnung im Zeitraum" in ausweise[0], ausweise


@pytest.mark.django_db
class TestSnapshotStandStempel:
    """K4c: Der Stand der Monats-Aggregate, nicht nur ihre Existenz."""

    def _snapshot(self, facility, year, month):
        from core.services.dashboard.snapshot import create_or_update_snapshot

        create_or_update_snapshot(facility, year, month)

    def test_hybrid_pfad_stempelt_den_snapshot_stand(self, facility, doc_type_contact, staff_user):
        from core.models import StatisticsSnapshot
        from core.services.dashboard import get_statistics_hybrid
        from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

        self._snapshot(facility, 2026, 1)
        stand = StatisticsSnapshot.objects.get(facility=facility, year=2026, month=1).updated_at

        merged = get_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 1, 31))

        assert merged[SNAPSHOT_AS_OF_KEY] == stand

    def test_ohne_beitragenden_snapshot_bleibt_der_stempel_leer(self, facility):
        """Kein Segment kam aus einem Aggregat ⇒ keine behauptete Herkunft."""
        from core.services.dashboard import get_statistics_hybrid
        from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

        merged = get_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 1, 31))

        assert merged[SNAPSHOT_AS_OF_KEY] is None

    def test_beispiel_sachbericht_hybrid_stempelt_ebenfalls(self, facility):
        from core.models import StatisticsSnapshot
        from core.services.dashboard import get_sample_report_1_statistics_hybrid
        from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

        self._snapshot(facility, 2026, 1)
        stand = StatisticsSnapshot.objects.get(facility=facility, year=2026, month=1).updated_at

        merged = get_sample_report_1_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 1, 31))

        assert merged[SNAPSHOT_AS_OF_KEY] == stand

    def test_stempel_ueberlebt_die_suppression(self, facility):
        """Der Stempel ist Metadatum, kein Aggregat — die Suppression darf ihn
        nicht mitnehmen, sonst waere das Artefakt wieder herkunftslos."""
        from django.utils import timezone

        from core.services.dashboard import suppress_report_stats
        from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

        moment = timezone.now()
        out = suppress_report_stats(
            facility,
            {
                "total_contacts": 40,
                "unique_clients": 20,
                "by_contact_stage": {},
                "persons_by_contact_stage": {},
                "by_document_type": [],
                "by_age_cluster": [],
                SNAPSHOT_AS_OF_KEY: moment,
            },
        )

        assert out[SNAPSHOT_AS_OF_KEY] == moment

    def test_pdf_rendert_den_stand(self, facility):
        from django.utils import timezone

        from core.services.dashboard.snapshot import SNAPSHOT_AS_OF_KEY

        seite = render_to_string(
            "core/export/report_pdf.html",
            {"stats": {SNAPSHOT_AS_OF_KEY: timezone.now(), "by_document_type": [], "by_age_cluster": []}},
        )

        assert "Stand der Monats-Aggregate:" in seite


@pytest.mark.django_db
class TestInternerModusWeistSeineGrundgesamtheitAus:
    """Der ``?internal=1``-Modus des Halbjahres-PDF **ueberspringt die
    Suppression** (Security R4: er ist das interne Artefakt mit
    Pseudonym-Ranking und INTERN-Banner) — und damit auch die K3-A1-Restzeile,
    die erst dort entsteht.

    Die Alterstabelle zaehlt in diesem Modus also weiterhin **nur Ereignisse mit
    Personenbezug**. Ein Ausweis „alle Ereignisse im Zeitraum" waere dort eine
    schlicht falsche Angabe — und zwar genau die Sorte Falschangabe, die K3-A3
    beseitigen sollte.
    """

    def _rendered(self, facility, *, internal_mode):
        from unittest.mock import patch

        from core.services.system.export import generate_report_pdf

        stats = {
            "total_contacts": 40,
            "unique_clients": 20,
            "by_contact_stage": {"anonym": 10, "identifiziert": 15, "qualifiziert": 15},
            "persons_by_contact_stage": {"anonym": 10, "identifiziert": 15, "qualifiziert": 15},
            "by_document_type": [{"name": "Kontakt", "count": 40}],
            "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": 30, "persons": 20}],
            "top_clients": [],
        }
        captured = {}

        def _capture(template, context):
            captured["context"] = context
            return "<html></html>"

        with patch("core.services.system.export.render_to_string", side_effect=_capture):
            generate_report_pdf(facility, date(2026, 1, 1), date(2026, 6, 30), stats, internal_mode=internal_mode)
        return captured["context"]

    def test_interner_modus_nennt_die_engere_menge(self, facility):
        context = self._rendered(facility, internal_mode=True)

        assert str(context["population_notes"]["by_age_cluster"]).find("Ereignisse mit Personenbezug") >= 0, context[
            "population_notes"
        ]["by_age_cluster"]
        # Die Dokumenttyp-Tabelle zaehlt in beiden Modi alle Ereignisse.
        assert "alle Ereignisse im Zeitraum" in str(context["population_notes"]["by_document_type"])

    def test_externer_modus_nennt_alle_ereignisse(self, facility):
        """Gegenprobe: dort laeuft die Suppression und mit ihr die Restzeile."""
        context = self._rendered(facility, internal_mode=False)

        assert "alle Ereignisse im Zeitraum" in str(context["population_notes"]["by_age_cluster"])

    def test_interner_modus_hat_wirklich_keine_restzeile(self, facility):
        """Anker: waere die Restzeile doch da, waere der Test oben falsch."""
        from core.services.dashboard.external_report import NO_PERSON_CLUSTER

        context = self._rendered(facility, internal_mode=True)

        assert all(row["cluster"] != NO_PERSON_CLUSTER for row in context["stats"]["by_age_cluster"])


@pytest.mark.django_db
class TestGrundgesamtheitImBerichtsExport:
    """K3-A3 muss auch die **maschinenlesbaren** Artefakte erreichen.

    Panel und Download zeigen denselben Datensatz; benennt nur das Panel seine
    Grundgesamtheit, ist die Angabe genau dort weg, wo sie am dringendsten
    gebraucht wird — in der Datei, die das Haus verlaesst und weiterverarbeitet
    wird. Dasselbe Muster wie beim Quellenausweis (#1743): stabiler Code plus
    uebersetzte Kurzform.
    """

    def _dataset(self):
        from core.reporting.base import DatasetResult

        return DatasetResult(
            mode="aggregate",
            records=[{"category": "Kontakte", "count": 30}],
            summary={"total": 30, "unique_clients": 20, "by_category": [("Kontakte", 30)], "by_age_cluster": []},
            completeness=[],
        )

    def _render(self, facility, renderer):
        from django.utils import timezone

        from core.reporting.export import _render_csv, _render_json, _render_xml
        from core.reporting.registry import get_backend

        backend = get_backend("sample-report-1")
        prepared = backend.prepare_export(facility, self._dataset())
        moment = timezone.now()
        if renderer == "csv":
            return "".join(_render_csv(backend, facility, date(2026, 1, 1), date(2026, 6, 30), prepared, moment))
        render = _render_json if renderer == "json" else _render_xml
        return render(backend, date(2026, 1, 1), date(2026, 6, 30), prepared, moment).decode("utf-8")

    @pytest.mark.parametrize("renderer", ["csv", "json", "xml"])
    def test_export_nennt_die_grundgesamtheit(self, facility, renderer):
        ausgabe = self._render(facility, renderer)

        assert MAPPED_EVENTS in ausgabe, "stabiler Code fuer Maschinen (Paritaet zu data_source)"
        assert "Ereignisse mit Berichtszuordnung im Zeitraum" in ausgabe, "uebersetzte Kurzform fuer Menschen"

    @pytest.mark.parametrize("renderer", ["csv", "json", "xml"])
    def test_der_code_folgt_der_backend_deklaration(self, facility, renderer, monkeypatch):
        """Gegenprobe: der Ausweis ist kein hartkodierter Text, sondern liest
        ``DatasetBackend.population``."""
        from core.reporting.registry import get_backend

        monkeypatch.setattr(get_backend("sample-report-1"), "population", ALL_EVENTS)

        ausgabe = self._render(facility, renderer)

        assert "alle Ereignisse im Zeitraum" in ausgabe
