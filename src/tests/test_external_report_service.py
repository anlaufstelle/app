"""Tests fuer external_report-Service (Refs #921).

Datenschutzfreundliche externe Berichte:
- Keine Pseudonyme im Output
- K-Anonymity-Schwelle auf Aggregate
- Datenschutzprofil-Metadaten im Report
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from core.models import Event


@pytest.fixture
def report_period():
    return date(2026, 1, 1), date(2026, 12, 31)


def _make_events(facility, client, doc_type, user, count: int):
    """Hilfsmethode: erzeugt ``count`` Events fuer einen Client."""
    return [
        Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=user,
        )
        for _ in range(count)
    ]


class TestSecondarySuppression:
    """A6.1 (Refs #1024 / #1016): komplementäre/Randsummen-Offenlegung.

    Ist in einer Gruppe genau EINE Zelle k-anon-unterdrückt, lässt sie sich aus
    der publizierten Randsumme (z.B. total_contacts) und den sichtbaren Zellen
    zurückrechnen. Sekundäre Suppression unterdrückt dann zusätzlich die
    nächstkleinere sichtbare Zelle, sodass mindestens zwei Unbekannte bleiben.
    """

    def test_list_single_suppression_triggers_secondary(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 8}, {"name": "C", "count": 3}]
        result = _suppress_small(rows, threshold=5)
        by_name = {r["name"]: r for r in result}
        assert by_name["C"]["suppressed"] is True  # primär (<5)
        assert by_name["B"]["suppressed"] is True  # sekundär (nächstkleinere sichtbare)
        assert by_name["B"]["count"] is None
        assert by_name["A"]["suppressed"] is False  # größte bleibt sichtbar

    def test_list_two_already_suppressed_no_secondary(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 3}, {"name": "C", "count": 2}]
        result = _suppress_small(rows, threshold=5)
        by_name = {r["name"]: r for r in result}
        # Zwei sind ohnehin unterdrückt -> keine zusätzliche, A bleibt sichtbar.
        assert by_name["A"]["suppressed"] is False
        assert by_name["B"]["suppressed"] is True
        assert by_name["C"]["suppressed"] is True

    def test_list_no_suppression_below_count_leaves_all_visible(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 8}]
        result = _suppress_small(rows, threshold=5)
        assert all(r["suppressed"] is False for r in result)

    def test_stage_dict_single_suppression_triggers_secondary(self):
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 10, "identifiziert": 8, "qualifiziert": 3}
        result = _suppress_stage_dict(stages, threshold=5)
        assert result["qualifiziert"] is None  # primär
        assert result["identifiziert"] is None  # sekundär (nächstkleinere sichtbare)
        assert result["anonym"] == 10


class TestKanonischeOrdnungUnterdrueckterZeilen:
    """K5 (Refs #1767): die Zeilenreihenfolge darf keine Rangaussage tragen.

    ``by_age_cluster`` und ``by_document_type`` entstehen absteigend nach Anzahl
    (``order_by("-count")``, im Hybrid-Pfad erneut so sortiert); ``_suppress_small``
    nullte bisher nur die Werte und liess die Reihenfolge unangetastet. Damit
    stand die unterdrueckte Kleinstzelle **immer** am Tabellenende — das Artefakt
    verriet ohne jede Arithmetik, WELCHE Altersgruppe bzw. WELCHER Dokumenttyp
    die Kleinstfallzahl traegt, und die Zuordnung von r unterdrueckten Werten zu
    Labels sank von r! auf 1.

    Unterdrueckte Zeilen stehen deshalb als Block in kanonischer,
    **wertunabhaengiger** Ordnung am Listenende (Altersgruppen in
    Modellreihenfolge, Dokumenttypen nach ``(category, name)``); sichtbare
    Zeilen duerfen count-sortiert bleiben, ihre Werte stehen ohnehin im Klartext.
    """

    AGE_LABELS = {"u18": "Unter 18", "18_26": "18–26", "27_plus": "27+", "unknown": "Unbekannt"}

    def _age_rows(self, counts: dict[str, int]) -> list[dict]:
        """Altersgruppen-Zeilen in der Query-Ordnung (absteigend nach Anzahl)."""
        rows = [{"cluster": key, "label": self.AGE_LABELS[key], "count": value} for key, value in counts.items()]
        return sorted(rows, key=lambda row: row["count"], reverse=True)

    def _doc_rows(self, counts: dict[tuple[str, str], int]) -> list[dict]:
        rows = [{"name": name, "category": category, "count": value} for (category, name), value in counts.items()]
        return sorted(rows, key=lambda row: row["count"], reverse=True)

    def test_unterdrueckte_altersgruppen_stehen_als_block_in_modellreihenfolge(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = self._age_rows({"27_plus": 20, "unknown": 9, "18_26": 3, "u18": 2})
        result = _suppress_small(rows, threshold=5)

        assert [row["cluster"] for row in result] == ["27_plus", "unknown", "u18", "18_26"]
        assert [row["suppressed"] for row in result] == [False, False, True, True]

    def test_vertauschte_zellwerte_erzeugen_dieselbe_reihenfolge(self):
        """Ordnungstest (security-notes § Regressionsschutz (2)): zwei Welten, die
        sich nur in der Zuordnung der unterdrueckten Werte unterscheiden, muessen
        dieselbe Publikationsreihenfolge erzeugen — sonst ist die Position selbst
        die Aussage, welche Zelle die kleinere ist.
        """
        from core.services.dashboard.external_report import _suppress_small

        welt_a = _suppress_small(self._age_rows({"27_plus": 20, "18_26": 4, "u18": 1}), threshold=5)
        welt_b = _suppress_small(self._age_rows({"27_plus": 20, "18_26": 1, "u18": 4}), threshold=5)

        assert [row["cluster"] for row in welt_a] == [row["cluster"] for row in welt_b]
        assert [row["count"] for row in welt_a] == [row["count"] for row in welt_b]

    def test_unterdrueckte_dokumenttypen_nach_kategorie_und_name(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = self._doc_rows(
            {
                ("contact", "Kontakt"): 20,
                ("note", "Notiz"): 9,
                ("service", "Beratung"): 3,
                ("contact", "Aufnahme"): 2,
            }
        )
        result = _suppress_small(rows, threshold=5)

        assert [row["name"] for row in result] == ["Kontakt", "Notiz", "Aufnahme", "Beratung"]
        assert [row["suppressed"] for row in result] == [False, False, True, True]

    def test_sichtbare_zeilen_bleiben_count_sortiert(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = self._doc_rows(
            {
                ("contact", "Aufnahme"): 7,
                ("note", "Notiz"): 30,
                ("service", "Beratung"): 12,
                ("contact", "Kontakt"): 1,
                ("service", "Vermittlung"): 2,
            }
        )
        result = _suppress_small(rows, threshold=5)

        sichtbar = [row["name"] for row in result if not row["suppressed"]]
        assert sichtbar == ["Notiz", "Beratung", "Aufnahme"]

    def test_ohne_unterdrueckung_bleibt_die_reihenfolge_unveraendert(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = self._age_rows({"27_plus": 20, "u18": 9, "18_26": 7})
        result = _suppress_small(rows, threshold=5)

        assert [row["cluster"] for row in result] == ["27_plus", "u18", "18_26"]

    def test_by_category_zeilen_ohne_kanonische_ordnung_bleiben_unberuehrt(self):
        """Dokumentierte Grenze: ``by_category`` traegt keinen Rangkanal.

        Die Kategoriezeilen des Beispiel-Sachberichts entstehen in fester
        Kategorien-Ordnung (Konsolidierung ueber ``system_type``), nicht nach
        Anzahl — ihre Position ist keine Rangaussage. Sie werden deshalb
        bewusst NICHT umsortiert (sonst braeche der Bestandstest
        ``test_records_spiegeln_die_suppressierten_zellen`` ohne Sicherheitsgewinn).
        """
        from core.services.dashboard.external_report import _suppress_small

        rows = [{"name": "Kontakte", "count": 12}, {"name": "Beratung", "count": 3}, {"name": "Versorgung", "count": 2}]
        result = _suppress_small(rows, threshold=5)

        assert [row["name"] for row in result] == ["Kontakte", "Beratung", "Versorgung"]


@pytest.mark.django_db
class TestKanonischeOrdnungInDenWrappern:
    """K5 wirkt an EINEM Eingriffspunkt und damit in allen drei Wrappern —
    Templates, JSON und XML erben ihn ohne eigene Anpassung.
    """

    def test_suppress_report_stats_ordnet_beide_tabellen_kanonisch(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [
                {"name": "Notiz", "category": "note", "count": 30},
                {"name": "Beratung", "category": "service", "count": 4},
                {"name": "Aufnahme", "category": "contact", "count": 3},
            ],
            "by_age_cluster": [
                {"cluster": "27_plus", "label": "27+", "count": 30},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4},
                {"cluster": "u18", "label": "Unter 18", "count": 3},
            ],
        }
        out = suppress_report_stats(facility, stats)

        assert [row["name"] for row in out["by_document_type"]] == ["Notiz", "Aufnahme", "Beratung"]
        assert [row["cluster"] for row in out["by_age_cluster"]] == ["27_plus", "u18", "unknown"]

    def test_suppress_sample_report_1_stats_ordnet_die_altersgruppen_kanonisch(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 12,
            "by_category": [("Kontakte", 30), ("Beratung", 4), ("Versorgung", 3)],
            "by_age_cluster": [
                {"cluster": "27_plus", "label": "27+", "count": 30},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4},
                {"cluster": "18_26", "label": "18–26", "count": 3},
            ],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert [row["cluster"] for row in out["by_age_cluster"]] == ["27_plus", "18_26", "unknown"]

    def test_externer_bericht_publiziert_die_kleinstzelle_nicht_mehr_als_letzte_zeile(
        self, facility, client_qualified, doc_type_contact, doc_type_crisis, staff_user, report_period
    ):
        """Ende-zu-Ende ueber ``build_external_report``: der Dokumenttyp mit der
        Kleinstfallzahl (``Kontakt``, 2) darf nicht laenger daran erkennbar
        sein, dass er als letzte Zeile steht.
        """
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_crisis, staff_user, 9)
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 2)

        rows = build_external_report(facility, *report_period)["by_document_type"]

        # Query-Ordnung waere ["Krisengespräch", "Kontakt"] (9 vor 2). Kanonisch
        # steht ``Kontakt`` (category "contact") vor ``Krisengespräch``
        # (category "service") — beide sind unterdrueckt (primaer/sekundaer),
        # die Position sagt also nichts mehr ueber ihre Groesse.
        assert [row["name"] for row in rows] == ["Kontakt", "Krisengespräch"]
        assert all(row["suppressed"] for row in rows)


@pytest.mark.django_db
class TestExternalReportContent:
    """Inhaltliche Tests: keine Pseudonyme, K-Anon-Schwelle wirkt."""

    def test_report_contains_no_pseudonyms(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 6)

        report = build_external_report(facility, *report_period)

        # Pseudonym darf NICHT im Output sein:
        report_str = str(report)
        assert client_qualified.pseudonym not in report_str
        # top_clients-Key darf nicht im Report enthalten sein:
        assert "top_clients" not in report

    def test_report_includes_total_contacts(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 7)

        report = build_external_report(facility, *report_period)

        assert "total_contacts" in report
        assert report["total_contacts"] == 7

    def test_report_includes_aggregates_above_threshold(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Aggregate >= k_anonymity_threshold (Default 5) bleiben unveraendert."""
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 8)

        report = build_external_report(facility, *report_period)

        by_dt = report["by_document_type"]
        # 8 Events vom Dokumenttyp "Kontakt" -> ueber Schwelle 5 -> count bleibt
        kontakt_row = next((r for r in by_dt if r["name"] == "Kontakt"), None)
        assert kontakt_row is not None
        assert kontakt_row["count"] == 8

    def test_report_suppresses_aggregates_below_threshold(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Aggregate < k_anonymity_threshold werden unterdrueckt."""
        from core.services.dashboard import build_external_report

        # 3 Events -> unter Default-Schwelle 5
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 3)

        report = build_external_report(facility, *report_period)

        by_dt = report["by_document_type"]
        # Kategorie mit 3 Events: Count wird durch Sentinel ersetzt
        kontakt_row = next((r for r in by_dt if r["name"] == "Kontakt"), None)
        assert kontakt_row is not None
        # Suppressed-Sentinel: 0 oder None oder spezielles Token — wir nutzen `None`
        # damit der Fakt der Unterdrueckung explizit ist.
        assert kontakt_row["count"] is None
        assert kontakt_row.get("suppressed") is True


@pytest.mark.django_db
class TestExternalReportMetadata:
    """Datenschutzprofil-Metadaten im Report-Kopf."""

    def test_metadata_block_present(self, facility, report_period):
        from core.services.dashboard import build_external_report

        report = build_external_report(facility, *report_period)

        meta = report["metadata"]
        assert meta["facility"] == facility.name
        assert meta["date_from"] == report_period[0].isoformat()
        assert meta["date_to"] == report_period[1].isoformat()
        assert meta["k_anonymity_threshold"] == 5  # Default aus Settings
        assert "generated_at" in meta
        assert meta["privacy_profile"] == "external"

    def test_metadata_weist_die_live_datenquelle_aus(self, facility, report_period):
        """Refs #1743: der externe Bericht liest live — und sagt das im Artefakt.

        Stabiler Code fuer Maschinen (``?format=json``), uebersetzte Kurzform
        fuer den Kopf der HTML-Fassung.
        """
        from core.services.dashboard import build_external_report

        meta = build_external_report(facility, *report_period)["metadata"]

        assert meta["data_source"] == "live"
        assert meta["data_source_note"] == "Live-Abfrage zum Erstellungszeitpunkt"

    def test_metadata_uses_custom_threshold_from_settings(self, facility, report_period):
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(
            facility=facility,
            defaults={"k_anonymity_threshold": 10},
        )

        report = build_external_report(facility, *report_period)
        assert report["metadata"]["k_anonymity_threshold"] == 10


@pytest.mark.django_db
class TestExternalReportEdgeCases:
    """Edge-Cases: leere Periode, threshold=1."""

    def test_empty_period_suppresses_total_and_returns_empty_lists(self, facility, report_period):
        # Security R14: die Randsumme ``total_contacts`` unterliegt jetzt selbst
        # der k-Schwelle. Eine leere Periode liefert total_contacts=0 < 5 und
        # wird daher wie jede andere Kleinstfallzahl auf ``None`` unterdrueckt
        # (konsistent mit ``unique_clients``, das schon immer 0 -> None abbildet).
        from core.services.dashboard import build_external_report

        report = build_external_report(facility, *report_period)

        assert report["total_contacts"] is None
        assert report["by_document_type"] == []

    def test_threshold_1_disables_suppression(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Bei k=1 wird nichts unterdrueckt (alle Counts sichtbar)."""
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(
            facility=facility,
            defaults={"k_anonymity_threshold": 1},
        )

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 1)

        report = build_external_report(facility, *report_period)
        by_dt = report["by_document_type"]
        assert by_dt[0]["count"] == 1
        assert by_dt[0].get("suppressed", False) is False


@pytest.mark.django_db
class TestSampleReport1StatsSuppression:
    """Refs #1278 (T1): Das Sachbericht-PDF — das am ehesten externe Artefakt —
    muss derselben k-Anon-Kleinstfallzahl-Suppression unterliegen wie der
    externe On-Screen-Bericht. ``suppress_sample_report_1_stats`` leitet die
    Sachbericht-Aggregate durch dieselbe ``_suppress_small``-Logik (inkl.
    sekundaerer Suppression) und Nones ``unique_clients`` < k — die publizierte
    Randsumme ``total`` bleibt roh (analog ``total_contacts``).
    """

    def test_by_category_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [],
            "unique_clients": 11,
        }
        result = suppress_sample_report_1_stats(facility, stats)
        cats = {r["name"]: r for r in result["by_category"]}
        # Zwei Zellen < 5 -> beide primaer unterdrueckt, keine Sekundaer-Suppression.
        assert cats["Beratung"]["count"] is None and cats["Beratung"]["suppressed"] is True
        assert cats["Versorgung"]["count"] is None and cats["Versorgung"]["suppressed"] is True
        # Zelle >= 5 bleibt sichtbar.
        assert cats["Kontakte"]["count"] == 12 and cats["Kontakte"]["suppressed"] is False

    def test_by_age_cluster_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 20,
            "by_category": [],
            "by_age_cluster": [
                {"cluster": "u18", "label": "Unter 18", "count": 3},
                {"cluster": "18_26", "label": "18–26", "count": 2},
                {"cluster": "27_plus", "label": "27+", "count": 15},
            ],
            "unique_clients": 11,
        }
        result = suppress_sample_report_1_stats(facility, stats)
        ages = {r["cluster"]: r for r in result["by_age_cluster"]}
        assert ages["u18"]["count"] is None and ages["u18"]["suppressed"] is True
        assert ages["18_26"]["count"] is None and ages["18_26"]["suppressed"] is True
        assert ages["27_plus"]["count"] == 15 and ages["27_plus"]["suppressed"] is False
        # Labels bleiben fuer die Template-Darstellung erhalten.
        assert ages["u18"]["label"] == "Unter 18"

    def test_unique_clients_and_total_below_threshold_both_suppressed(self, facility):
        # Security R14: Ist die publizierte Randsumme ``total`` selbst < k, ist
        # sie eine Kleinstfallzahl und wird ebenfalls unterdrueckt (frueher blieb
        # sie hier roh — genau der von R14 adressierte Leak).
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 3, "by_category": [], "by_age_cluster": [], "unique_clients": 2}
        result = suppress_sample_report_1_stats(facility, stats)
        assert result["unique_clients"] is None  # 2 < 5
        assert result["total"] is None  # 3 < 5 -> Randsumme selbst unterdrueckt

    def test_unique_clients_above_threshold_kept(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 30, "by_category": [], "by_age_cluster": [], "unique_clients": 9}
        assert suppress_sample_report_1_stats(facility, stats)["unique_clients"] == 9

    def test_does_not_mutate_input(self, facility):
        import copy

        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 3}],
            "unique_clients": 11,
        }
        snapshot = copy.deepcopy(stats)
        suppress_sample_report_1_stats(facility, stats)
        assert stats == snapshot

    def test_custom_threshold_from_settings_disables_suppression(self, facility):
        from core.models import Settings
        from core.services.dashboard import suppress_sample_report_1_stats

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {"total": 1, "by_category": [("Kontakte", 1)], "by_age_cluster": [], "unique_clients": 1}
        result = suppress_sample_report_1_stats(facility, stats)
        assert result["by_category"][0]["count"] == 1
        assert result["by_category"][0]["suppressed"] is False
        assert result["unique_clients"] == 1
        # Bei k=1 ist auch die Randsumme (1 >= 1) nicht unterdrueckt.
        assert result["total"] == 1


@pytest.mark.django_db
class TestMarginTotalSuppression:
    """Security R14: die Randsumme selbst ist eine Kleinstfallzahl, wenn sie
    unter der Schwelle liegt — dann None statt roher Wert.

    ``build_external_report`` (``total_contacts``), ``suppress_sample_report_1_stats``
    (``total``) und ``suppress_report_stats`` (``total_contacts``) publizierten die
    Gesamtsumme bislang roh. Liegt sie selbst < k, ist sie eine Kleinstfallzahl —
    dann ``None`` + Template-Marker. Randsummen >= Schwelle bleiben roh (die
    sekundaere Suppression schuetzt bereits die Rueckrechnung einzelner Zellen).
    """

    def test_external_report_total_below_threshold_suppressed(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        # 2 Events gesamt -> Randsumme 2 < Default-Schwelle 5.
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 2)

        report = build_external_report(facility, *report_period)
        assert report["total_contacts"] is None

    def test_external_report_total_at_or_above_threshold_stays_raw(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        # 7 Events gesamt -> Randsumme 7 >= Schwelle 5 bleibt roh.
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 7)

        report = build_external_report(facility, *report_period)
        assert report["total_contacts"] == 7

    def test_sample_report_1_total_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 3, "unique_clients": 0, "by_category": [], "by_age_cluster": []}
        out = suppress_sample_report_1_stats(facility, stats)
        assert out["total"] is None

    def test_report_stats_total_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 3,
            "unique_clients": 0,
            "by_contact_stage": {},
            "by_document_type": [],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)
        assert out["total_contacts"] is None

    @pytest.mark.parametrize("total_contacts", [5, 12])
    def test_report_stats_total_at_or_above_threshold_stays_raw(self, facility, total_contacts):
        """Final-Review Befund 4: die ``>=``-Vergleichsgrenze selbst (Randsumme
        exakt gleich der Default-Schwelle 5) und ein Wert deutlich darueber (12)
        bleiben beide roh — nur ``< k`` ist eine Kleinstfallzahl, ``>= k`` nicht,
        auch nicht direkt an der Grenze."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": total_contacts,
            "unique_clients": 0,
            "by_contact_stage": {},
            "by_document_type": [],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)
        assert out["total_contacts"] == total_contacts
