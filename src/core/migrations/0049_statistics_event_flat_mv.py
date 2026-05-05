"""Materialized View ``core_statistics_event_flat`` für Statistik-Aggregate.

Extrahiert aggregierbare Event-Spalten vorab in eine flache Tabelle,
damit Statistik-Querys keine JSON-Parsings oder komplexe Joins mehr
benötigen. Aktualisiert per Management-Command
(`refresh_statistics_view`) stündlich :15 (Refs #830, ops-runbook.md).

Materialized Views existieren nur in PostgreSQL. Bei anderen Backends
(z.B. SQLite in Unit-Tests einzelner Entwickler-Setups) ist die
Migration ein No-op, damit der Rest der Test-Suite nicht bricht.

Refs #544.
"""

from django.db import migrations

MV_NAME = "core_statistics_event_flat"

CREATE_SQL = f"""
CREATE MATERIALIZED VIEW {MV_NAME} AS
SELECT
    id,
    facility_id,
    occurred_at,
    date_trunc('month', occurred_at) AS month,
    date_trunc('year', occurred_at) AS year,
    document_type_id,
    client_id,
    is_anonymous,
    EXTRACT(DOW FROM occurred_at) AS day_of_week,
    EXTRACT(HOUR FROM occurred_at) AS hour_of_day
FROM core_event
WHERE is_deleted = false;
"""

# UNIQUE-Index ermöglicht ``REFRESH MATERIALIZED VIEW CONCURRENTLY``.
CREATE_UNIQUE_INDEX_SQL = f"CREATE UNIQUE INDEX {MV_NAME}_pk ON {MV_NAME} (id);"

CREATE_INDEX_FACILITY_MONTH_SQL = f"CREATE INDEX {MV_NAME}_facility_month_idx ON {MV_NAME} (facility_id, month);"

CREATE_INDEX_FACILITY_DOCTYPE_MONTH_SQL = (
    f"CREATE INDEX {MV_NAME}_facility_doctype_month_idx ON {MV_NAME} (facility_id, document_type_id, month);"
)

DROP_SQL = f"DROP MATERIALIZED VIEW IF EXISTS {MV_NAME};"


def _create_mv(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(CREATE_SQL)
        cursor.execute(CREATE_UNIQUE_INDEX_SQL)
        cursor.execute(CREATE_INDEX_FACILITY_MONTH_SQL)
        cursor.execute(CREATE_INDEX_FACILITY_DOCTYPE_MONTH_SQL)


def _drop_mv(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(DROP_SQL)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0048_retention_defer_and_bulk"),
    ]

    operations = [
        migrations.RunPython(_create_mv, _drop_mv),
    ]
