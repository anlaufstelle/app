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
        result = _suppress_stage_dict(
            stages, threshold=5, persons={"anonym": 10, "identifiziert": 8, "qualifiziert": 3}
        )
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
            "persons_by_contact_stage": {"anonym": 12, "identifiziert": 9, "qualifiziert": 6},
            "by_age_cluster": [
                {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 11},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4, "persons": 4},
                {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
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
                {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 11},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4, "persons": 4},
                {"cluster": "18_26", "label": "18–26", "count": 3, "persons": 3},
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
class TestPersonenabgeleiteteTabellenFailSafe:
    """K0-Fail-safe (Refs #1768): Personenzahl < k ⇒ personenabgeleitete Tabellen weg.

    Die k-Schwelle wird gegen **Ereignis**zahlen geprueft (``Count("id")``), nicht
    gegen Personen. Eine Alterszelle „Unter 18: 7" kann damit von einer einzigen
    Person stammen und wurde trotzdem publiziert; selbstbeweisend wurde der Fall,
    sobald ``unique_clients`` unterdrueckt war — dann stand im Artefakt, dass die
    Gesamtpersonenmenge < k ist, waehrend die Verteilung genau dieser Menge im
    Klartext danebenstand.

    Fail-safe: liegt ``unique_clients`` unter der Schwelle, faellt die **gesamte**
    personenabgeleitete Aufschluesselung (``by_age_cluster``, ``by_contact_stage``),
    nicht nur die Kennzahl. Ereignisabgeleitete Aggregate (``total_contacts``,
    ``by_document_type``) bleiben unberuehrt — sie zaehlen Ereignisse, nicht Personen.
    """

    def _clients(self, facility, staff_user, count: int):
        from core.models import Client

        return [
            Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.QUALIFIED,
                pseudonym=f"K0-{index:02d}",
                created_by=staff_user,
            )
            for index in range(count)
        ]

    def test_externer_bericht_laesst_personen_tabellen_ganz_fallen(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Eine Person, neun Ereignisse: die Ereigniszahlen liegen ueber der
        Schwelle, die Personenmenge (1) nicht — die Altersverteilung dieser
        einen Person darf im Artefakt nicht mehr auftauchen.
        """
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 9)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] is None
        assert "by_age_cluster" not in report
        assert "by_contact_stage" not in report
        # Ereignisabgeleitete Aggregate bleiben — sie zaehlen keine Personen.
        assert report["total_contacts"] == 9
        assert [row["count"] for row in report["by_document_type"]] == [9]

    def test_externer_bericht_behaelt_die_tabellen_ab_der_schwelle(
        self, facility, doc_type_contact, staff_user, report_period
    ):
        """Gegenprobe: fuenf Personen (= Default-Schwelle) ⇒ unveraendert."""
        from core.services.dashboard import build_external_report

        for person in self._clients(facility, staff_user, 5):
            _make_events(facility, person, doc_type_contact, staff_user, 2)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] == 5
        assert report["by_age_cluster"]
        assert report["by_contact_stage"]

    def test_suppress_report_stats_laesst_beide_personen_tabellen_fallen(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 3,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_report_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert "by_contact_stage" not in out
        assert out["unique_clients"] is None
        assert out["total_contacts"] == 40
        assert out["by_document_type"][0]["count"] == 40

    def test_suppress_report_stats_behaelt_die_tabellen_ab_der_schwelle(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 5,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "persons_by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40, "persons": 5}],
        }
        out = suppress_report_stats(facility, stats)

        assert out["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 40, "suppressed": False}]
        assert out["by_contact_stage"] == {"anonym": 20, "identifiziert": 12, "qualifiziert": 8}

    def test_suppress_sample_report_1_stats_laesst_die_altersgruppen_fallen(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 4,
            "by_category": [("Kontakte", 40)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert out["unique_clients"] is None
        # Die Kategorie-Aufschluesselung zaehlt Ereignisse und bleibt.
        assert out["by_category"][0]["count"] == 40

    def test_ohne_personenzahl_greift_der_fail_safe(self, facility):
        """``unique_clients`` fehlt/ist ``None`` ⇒ wie „unter der Schwelle" behandeln."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": None,
            "by_contact_stage": {"anonym": 40, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert "by_contact_stage" not in out

    def test_schwelle_1_deaktiviert_auch_den_fail_safe(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Bei k=1 ist jede Personenmenge >= 1 gross genug — der Guard schweigt."""
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 1)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] == 1
        assert report["by_age_cluster"]
        assert report["by_contact_stage"]


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
                {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
                {"cluster": "18_26", "label": "18–26", "count": 2, "persons": 2},
                {"cluster": "27_plus", "label": "27+", "count": 15, "persons": 6},
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


class TestPersonenbasiertesZellkriterium:
    """K0-b (Refs #1770): die k-Schwelle prueft die DISTINCT-Personenzahl.

    Bis #1770 verglich ``_suppress_small`` die Schwelle ausschliesslich gegen
    eine **Ereignis**zahl (``Count("id")``). Eine Alterszelle „Unter 18: 7"
    konnte damit von einer einzigen Person stammen und wurde trotzdem
    publiziert — die Aequivalenzklasse, die k-Anonymitaet schuetzen soll, hatte
    Groesse 1. Personenabgeleitete Zeilen tragen deshalb die DISTINCT-
    Personenzahl ihres Buckets mit; unterdrueckt wird, sobald die **strengere**
    der beiden Zahlen unter der Schwelle liegt (min-Logik).

    Die Personenzahl ist reine Suppressions-*Eingabe* und darf nie in einem
    Artefakt landen — sonst waere sie selbst ein neuer Kleinstfallzahl-Kanal.
    """

    def test_zelle_mit_vielen_ereignissen_einer_person_wird_unterdrueckt(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 7, "persons": 1},
        ]
        result = _suppress_small(rows, threshold=5)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True
        assert by_cluster["u18"]["count"] is None

    def test_ereigniszahl_bleibt_das_strengere_kriterium_wenn_sie_kleiner_ist(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
        ]
        result = _suppress_small(rows, threshold=5)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True

    def test_zelle_mit_genug_personen_bleibt_sichtbar(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 8, "persons": 6},
        ]
        result = _suppress_small(rows, threshold=5)

        assert all(row["suppressed"] is False for row in result)

    def test_unbekannte_personenzahl_wird_unterdrueckt(self):
        """``persons=None`` = nicht rekonstruierbar ⇒ fail-safe unterdruecken."""
        from core.services.dashboard.external_report import _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 20, "persons": None},
        ]
        result = _suppress_small(rows, threshold=5)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True

    def test_personenzahl_verlaesst_die_suppression_nie(self):
        from core.services.dashboard.external_report import _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 7, "persons": 1},
        ]
        result = _suppress_small(rows, threshold=5)

        assert all("persons" not in row for row in result)

    def test_ereignisabgeleitete_zeilen_ohne_personenfeld_bleiben_ereignisbasiert(self):
        """``by_document_type``/``by_category`` zaehlen Ereignisse — kein Personenfeld,
        keine Personenpruefung (sonst waere jede Dokumenttyp-Zeile unterdrueckt)."""
        from core.services.dashboard.external_report import _suppress_small

        rows = [{"name": "Kontakt", "category": "contact", "count": 30}]
        result = _suppress_small(rows, threshold=5)

        assert result[0]["suppressed"] is False

    def test_stage_dict_unterdrueckt_bei_zu_wenigen_personen(self):
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 40, "identifiziert": 30, "qualifiziert": 20}
        result = _suppress_stage_dict(
            stages, threshold=5, persons={"anonym": 40, "identifiziert": 30, "qualifiziert": 2}
        )

        assert result["qualifiziert"] is None
        # Sekundaersuppression trifft zusaetzlich die kleinste sichtbare Stufe.
        assert result["identifiziert"] is None
        assert result["anonym"] == 40

    def test_stage_dict_ohne_personenzahlen_unterdrueckt_alles(self):
        """Alt-Format ohne ``persons_by_contact_stage`` ⇒ unbekannt ⇒ fail-safe."""
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 40, "identifiziert": 30, "qualifiziert": 20}
        result = _suppress_stage_dict(stages, threshold=5, persons=None)

        assert result == {"anonym": None, "identifiziert": None, "qualifiziert": None}


@pytest.mark.django_db
class TestPersonenkriteriumInDenWrappern:
    """K0-b wirkt in allen drei Wrappern — und Alt-Format ist fail-safe."""

    def test_externer_bericht_unterdrueckt_zelle_einer_einzelnen_person(
        self, facility, doc_type_contact, doc_type_crisis, staff_user, report_period
    ):
        """Acht Personen (⇒ ``unique_clients`` >= k, der #1768-Guard schweigt),
        aber zwei Altersgruppen mit vielen Ereignissen je EINER Person.

        Zwei Einzelpersonen-Cluster, damit die Sekundaer-Suppression
        (``len(suppressed) != 1``) nicht anspringt und der Test allein das
        Personenkriterium misst.
        """
        from core.models import Client
        from core.services.dashboard import build_external_report

        solo_a = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.U18,
            pseudonym="K0b-solo-a",
            created_by=staff_user,
        )
        _make_events(facility, solo_a, doc_type_contact, staff_user, 7)
        solo_b = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.UNKNOWN,
            pseudonym="K0b-solo-b",
            created_by=staff_user,
        )
        _make_events(facility, solo_b, doc_type_contact, staff_user, 6)
        for index in range(6):
            person = Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.QUALIFIED,
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
                pseudonym=f"K0b-{index:02d}",
                created_by=staff_user,
            )
            _make_events(facility, person, doc_type_crisis, staff_user, 3)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] == 8
        by_cluster = {row["cluster"]: row for row in report["by_age_cluster"]}
        assert by_cluster["u18"]["suppressed"] is True, "Altersgruppe mit nur einer Person muss fallen"
        assert by_cluster["unknown"]["suppressed"] is True
        assert by_cluster["27_plus"]["suppressed"] is False, "6 Personen/18 Ereignisse bleiben sichtbar"
        assert all("persons" not in row for row in report["by_age_cluster"])

    def test_suppress_report_stats_behandelt_alt_format_ohne_personenzahlen_fail_safe(self, facility):
        """Alt-Snapshot-Format: ``by_age_cluster`` ohne ``persons``,
        kein ``persons_by_contact_stage`` ⇒ alles unterdrueckt."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_report_stats(facility, stats)

        assert out["by_age_cluster"][0]["suppressed"] is True
        assert out["by_contact_stage"] == {"anonym": None, "identifiziert": None, "qualifiziert": None}
        # Ereignisabgeleitete Aggregate bleiben unberuehrt.
        assert out["by_document_type"][0]["count"] == 40

    def test_suppress_report_stats_gibt_die_personenzahlen_nicht_weiter(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "persons_by_contact_stage": {"anonym": 9, "identifiziert": 7, "qualifiziert": 6},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40, "persons": 12}],
        }
        out = suppress_report_stats(facility, stats)

        assert "persons_by_contact_stage" not in out
        assert out["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 40, "suppressed": False}]

    def test_suppress_sample_report_1_stats_behandelt_alt_format_fail_safe(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 12,
            "by_category": [("Kontakte", 40)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert out["by_age_cluster"][0]["suppressed"] is True
        assert out["by_category"][0]["count"] == 40

    def test_sample_report_1_backend_liefert_personenzahlen(
        self, facility, doc_type_contact, staff_user, report_period
    ):
        """Der Snapshot-Payload traegt ``persons`` je Cluster (Formatänderung).

        Bewusst mapping-agnostisch: die Buckets entstehen weiterhin allein aus
        ``system_type``/``age_cluster``, damit der Slug-Zweitschluessel aus
        #1741 hier konfliktfrei aufsetzen kann.
        """
        from core.models import Client, DocumentType
        from core.reporting.registry import get_backend

        doc_type_contact.system_type = DocumentType.SystemType.CONTACT
        doc_type_contact.save()
        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.U18,
            pseudonym="K0b-payload",
            created_by=staff_user,
        )
        _make_events(facility, person, doc_type_contact, staff_user, 4)

        summary = get_backend("sample-report-1").build_dataset(facility, *report_period).summary

        assert summary["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 4, "persons": 1}]
