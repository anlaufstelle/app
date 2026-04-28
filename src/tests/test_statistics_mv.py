"""Tests für die Statistik-Materialized-View (Refs #544)."""

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.test.utils import override_settings
from django.utils import timezone

from core.models import Event
from core.services.statistics import (
    STATISTICS_MV_NAME,
    get_event_counts_by_month,
)

pytestmark = pytest.mark.django_db


def _is_postgres() -> bool:
    return connection.vendor == "postgresql"


def _refresh_view():
    """Refresh the MV — concurrent form requires no writes in same txn."""
    with connection.cursor() as cursor:
        cursor.execute(f"REFRESH MATERIALIZED VIEW {STATISTICS_MV_NAME}")


def _make_event(facility, doc_type, *, when, is_deleted=False, client=None):
    event = Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when,
        data_json={},
        is_deleted=is_deleted,
    )
    return event


# ---------------------------------------------------------------------------
# Migration-Existenz
# ---------------------------------------------------------------------------


class TestViewExistsAfterMigration:
    def test_view_exists_after_migration(self):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_matviews WHERE matviewname = %s",
                [STATISTICS_MV_NAME],
            )
            assert cursor.fetchone() is not None, f"Materialized View '{STATISTICS_MV_NAME}' existiert nicht."

    def test_view_has_expected_columns(self):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")
        # Materialized Views tauchen nicht in information_schema.columns auf —
        # Postgres-System-Katalog direkt abfragen.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.attname
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = %s
                  AND c.relkind = 'm'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                """,
                [STATISTICS_MV_NAME],
            )
            columns = {row[0] for row in cursor.fetchall()}
        expected = {
            "id",
            "facility_id",
            "occurred_at",
            "month",
            "year",
            "document_type_id",
            "client_id",
            "is_anonymous",
            "day_of_week",
            "hour_of_day",
        }
        missing = expected - columns
        assert not missing, f"Fehlende Spalten in {STATISTICS_MV_NAME}: {missing}"

    def test_view_has_unique_index_on_id(self):
        """CONCURRENTLY-Refresh benötigt einen UNIQUE-Index."""
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM pg_indexes
                WHERE tablename = %s AND indexdef ILIKE 'CREATE UNIQUE INDEX%%'
                """,
                [STATISTICS_MV_NAME],
            )
            assert cursor.fetchone() is not None, "Erwarte UNIQUE-Index für CONCURRENTLY-Refresh."


# ---------------------------------------------------------------------------
# Inhaltliche Filterung: gelöschte Events dürfen nicht erscheinen
# ---------------------------------------------------------------------------


class TestViewExcludesDeletedEvents:
    def test_view_excludes_deleted_events(self, facility, doc_type_contact):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")

        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 5, 10, 12, 0)),
            is_deleted=False,
        )
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 5, 11, 12, 0)),
            is_deleted=True,
        )

        _refresh_view()

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s",
                [facility.pk],
            )
            count = cursor.fetchone()[0]
        assert count == 1, "Gelöschte Events dürfen nicht in der Flat-View erscheinen."


# ---------------------------------------------------------------------------
# Management-Command
# ---------------------------------------------------------------------------


class TestRefreshCommand:
    def test_refresh_command_runs(self, facility, doc_type_contact):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")

        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 7, 3, 10, 0)),
        )

        # View ist in der Test-DB ggf. leer (Migration-Default) — einmal
        # populär füllen, damit CONCURRENTLY nicht an der leeren View scheitert.
        _refresh_view()

        # Kommando muss fehlerfrei durchlaufen und die View aktualisieren.
        call_command("refresh_statistics_view")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s",
                [facility.pk],
            )
            assert cursor.fetchone()[0] == 1

    def test_refresh_command_non_concurrent(self, facility, doc_type_contact):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")

        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 8, 4, 10, 0)),
        )

        call_command("refresh_statistics_view", "--no-concurrent")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s",
                [facility.pk],
            )
            assert cursor.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Service-Pfad: Flat-View vs. Fallback
# ---------------------------------------------------------------------------


class TestServicePathSelection:
    def test_fallback_path_without_flag(self, facility, doc_type_contact):
        """Ohne STATISTICS_USE_FLAT_VIEW läuft die klassische Queryset-Logik."""
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 3, 2, 9, 0)),
        )
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 3, 20, 9, 0)),
        )
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 7, 15, 9, 0)),
        )

        with override_settings(STATISTICS_USE_FLAT_VIEW=False):
            result = get_event_counts_by_month(facility, 2025)

        counts = {row["month"]: row["count"] for row in result}
        assert counts[3] == 2
        assert counts[7] == 1
        assert counts[1] == 0
        assert len(result) == 12

    def test_flat_view_path_with_flag(self, facility, doc_type_contact):
        if not _is_postgres():
            pytest.skip("Materialized View Pfad nur in Postgres aktiv.")

        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 2, 2, 9, 0)),
        )
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 2, 28, 9, 0)),
        )

        _refresh_view()

        with override_settings(STATISTICS_USE_FLAT_VIEW=True):
            result = get_event_counts_by_month(facility, 2025)

        counts = {row["month"]: row["count"] for row in result}
        assert counts[2] == 2
        # Andere Monate bleiben 0
        assert counts[1] == 0
        assert counts[12] == 0


# ---------------------------------------------------------------------------
# REFRESH-Robustheit: sequentielle Refreshes, CONCURRENTLY-Semantik (WP5)
# ---------------------------------------------------------------------------


class TestRefreshUnderLoad:
    """Stellt sicher, dass wiederholte Refreshs — auch CONCURRENTLY — keine
    UNIQUE-Violations werfen, wenn zwischendurch neue Events dazukommen.

    ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` darf laut Postgres-Doku
    nicht innerhalb einer Transaktion laufen; Django hält pytest-db-Tests
    in einer Outer-Transaktion. Wir testen daher nicht „Refresh in offener
    Transaktion", sondern die realistischere Variante: zwei aufeinander-
    folgende Refreshs (einmal manuell, einmal via Command) auf dem gleichen
    Datenstand.
    """

    def test_sequential_refreshes_do_not_raise(self, facility, doc_type_contact):
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")

        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 9, 1, 10, 0)),
        )

        # Erster Refresh — füllt die MV, damit CONCURRENTLY einen Zustand hat.
        _refresh_view()
        # Zweiter Refresh via Command — muss ohne UNIQUE-Violation laufen,
        # auch wenn CONCURRENTLY intern `pg_class`-Locks anders nutzt.
        call_command("refresh_statistics_view")
        # Dritter Refresh direkt (non-concurrent) — bestätigt Idempotenz.
        call_command("refresh_statistics_view", "--no-concurrent")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s",
                [facility.pk],
            )
            assert cursor.fetchone()[0] == 1

    def test_refresh_picks_up_new_event_after_commit(self, facility, doc_type_contact):
        """Nach ``commit`` eines neuen Events muss ein Refresh die Zählung
        erhöhen — Regressionstest gegen „Refresh liest stale snapshot"."""
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")

        _refresh_view()  # initial befüllen für CONCURRENTLY
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s", [facility.pk])
            before = cursor.fetchone()[0]

        with transaction.atomic():
            _make_event(
                facility,
                doc_type_contact,
                when=timezone.make_aware(timezone.datetime(2025, 10, 15, 9, 0)),
            )
        # atomic() committet beim Austritt — danach ist die Zeile sichtbar.
        call_command("refresh_statistics_view")

        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id = %s", [facility.pk])
            after = cursor.fetchone()[0]
        assert after == before + 1


# ---------------------------------------------------------------------------
# RLS-Kontext: Die MV hat *bewusst* keine RLS-Policy (WP5)
# ---------------------------------------------------------------------------


class TestMaterializedViewRLS:
    """Dokumentiert die Architekturentscheidung aus Migration 0047/0049:

    Die Statistik-MV ``core_statistics_event_flat`` ist *nicht* in der
    Liste der RLS-geschützten Tabellen. Zugriffsschutz basiert hier auf
    der Service-Schicht (``WHERE facility_id = %s`` in
    ``get_event_counts_by_month``), nicht auf einer DB-Policy.

    Dieser Test ist Regression-Guard: Wenn jemand versehentlich eine
    Policy anlegt oder die Spalte umbenennt, fliegt er. Gleichzeitig
    verhindert er, dass die Abwesenheit der Policy unbemerkt zur
    Sicherheitslücke wird — die Assertion ``facility_id IS NOT NULL``
    belegt, dass die MV zumindest immer eine Einrichtung zuordnet.
    """

    def test_mv_has_no_rls_policy_by_design(self):
        if not _is_postgres():
            pytest.skip("RLS existiert nur in Postgres.")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT polname FROM pg_policy p
                JOIN pg_class c ON p.polrelid = c.oid
                WHERE c.relname = %s
                """,
                [STATISTICS_MV_NAME],
            )
            policies = [row[0] for row in cursor.fetchall()]
        assert policies == [], (
            f"Unerwartete RLS-Policy auf MV: {policies}. Die MV ist bewusst policy-frei (Service filtert)."
        )

    def test_mv_rows_always_have_facility_id(self, facility, doc_type_contact):
        """Sanity-Check: Die MV-Spalte ``facility_id`` ist nie NULL — sonst
        wäre der Service-Filter ``WHERE facility_id = %s`` lückenhaft."""
        if not _is_postgres():
            pytest.skip("Materialized Views existieren nur in Postgres.")
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 11, 5, 8, 0)),
        )
        _refresh_view()
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {STATISTICS_MV_NAME} WHERE facility_id IS NULL")
            assert cursor.fetchone()[0] == 0

    def test_mv_facility_filter_scopes_results(self, facility, doc_type_contact, second_facility):
        """Service-Filter ``WHERE facility_id = %s`` isoliert die Einrichtungen.

        Ohne RLS-Policy muss der Facility-Filter im Service-Pfad die einzige
        Trennung sein — dieser Test belegt, dass er das auch leistet.
        """
        if not _is_postgres():
            pytest.skip("Materialized View Pfad nur in Postgres aktiv.")

        # Zweite Facility braucht einen eigenen DocumentType, weil
        # DocumentType.facility onCascade = CASCADE und unique(facility, name).
        from core.models import DocumentType

        dt_other = DocumentType.objects.create(
            facility=second_facility,
            category=DocumentType.Category.CONTACT,
            name="Kontakt-Zweit",
        )
        _make_event(
            facility,
            doc_type_contact,
            when=timezone.make_aware(timezone.datetime(2025, 6, 1, 9, 0)),
        )
        _make_event(
            second_facility,
            dt_other,
            when=timezone.make_aware(timezone.datetime(2025, 6, 2, 9, 0)),
        )

        _refresh_view()

        with override_settings(STATISTICS_USE_FLAT_VIEW=True):
            result = get_event_counts_by_month(facility, 2025)
        counts = {row["month"]: row["count"] for row in result}
        # Nur das Event der eigenen Facility zählt, nicht der zweiten.
        assert counts[6] == 1
