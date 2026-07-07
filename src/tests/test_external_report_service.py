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
class TestJugendamtStatsSuppression:
    """Refs #1278 (T1): Das Jugendamt-PDF — das am ehesten externe Artefakt —
    muss derselben k-Anon-Kleinstfallzahl-Suppression unterliegen wie der
    externe On-Screen-Bericht. ``suppress_jugendamt_stats`` leitet die
    Jugendamt-Aggregate durch dieselbe ``_suppress_small``-Logik (inkl.
    sekundaerer Suppression) und Nones ``unique_clients`` < k — die publizierte
    Randsumme ``total`` bleibt roh (analog ``total_contacts``).
    """

    def test_by_category_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_jugendamt_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [],
            "unique_clients": 11,
        }
        result = suppress_jugendamt_stats(facility, stats)
        cats = {r["name"]: r for r in result["by_category"]}
        # Zwei Zellen < 5 -> beide primaer unterdrueckt, keine Sekundaer-Suppression.
        assert cats["Beratung"]["count"] is None and cats["Beratung"]["suppressed"] is True
        assert cats["Versorgung"]["count"] is None and cats["Versorgung"]["suppressed"] is True
        # Zelle >= 5 bleibt sichtbar.
        assert cats["Kontakte"]["count"] == 12 and cats["Kontakte"]["suppressed"] is False

    def test_by_age_cluster_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_jugendamt_stats

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
        result = suppress_jugendamt_stats(facility, stats)
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
        from core.services.dashboard import suppress_jugendamt_stats

        stats = {"total": 3, "by_category": [], "by_age_cluster": [], "unique_clients": 2}
        result = suppress_jugendamt_stats(facility, stats)
        assert result["unique_clients"] is None  # 2 < 5
        assert result["total"] is None  # 3 < 5 -> Randsumme selbst unterdrueckt

    def test_unique_clients_above_threshold_kept(self, facility):
        from core.services.dashboard import suppress_jugendamt_stats

        stats = {"total": 30, "by_category": [], "by_age_cluster": [], "unique_clients": 9}
        assert suppress_jugendamt_stats(facility, stats)["unique_clients"] == 9

    def test_does_not_mutate_input(self, facility):
        import copy

        from core.services.dashboard import suppress_jugendamt_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 3}],
            "unique_clients": 11,
        }
        snapshot = copy.deepcopy(stats)
        suppress_jugendamt_stats(facility, stats)
        assert stats == snapshot

    def test_custom_threshold_from_settings_disables_suppression(self, facility):
        from core.models import Settings
        from core.services.dashboard import suppress_jugendamt_stats

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {"total": 1, "by_category": [("Kontakte", 1)], "by_age_cluster": [], "unique_clients": 1}
        result = suppress_jugendamt_stats(facility, stats)
        assert result["by_category"][0]["count"] == 1
        assert result["by_category"][0]["suppressed"] is False
        assert result["unique_clients"] == 1
        # Bei k=1 ist auch die Randsumme (1 >= 1) nicht unterdrueckt.
        assert result["total"] == 1


@pytest.mark.django_db
class TestMarginTotalSuppression:
    """Security R14: die Randsumme selbst ist eine Kleinstfallzahl, wenn sie
    unter der Schwelle liegt — dann None statt roher Wert.

    ``build_external_report`` (``total_contacts``), ``suppress_jugendamt_stats``
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

    def test_jugendamt_total_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_jugendamt_stats

        stats = {"total": 3, "unique_clients": 0, "by_category": [], "by_age_cluster": []}
        out = suppress_jugendamt_stats(facility, stats)
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
