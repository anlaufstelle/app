"""Rollback-Sicherheit des ``backend_data``-Schemas aus 0121 (M5 P3, Refs #1061).

``0121`` legt die Spalte NOT NULL an. Ohne DB-seitigen Default verwirft Django
den Python-Default direkt nach dem ``ADD COLUMN`` — ein INSERT, der die Spalte
nicht nennt, faellt dann in eine ``NotNullViolation``. Genau das passiert beim
Standard-Rollback einer als "rein additiv" eingestuften Migration: der App-Code
geht auf den Stand vor M5 zurueck, das Schema bleibt stehen. Alt-Code kennt
``backend_data`` nicht und schreibt jeden **neuen** Monats-Snapshot ohne die
Spalte (``create_statistics_snapshots``, ``enforce_retention``).

Der Test simuliert genau diesen INSERT auf SQL-Ebene — die ORM-Ebene wuerde die
Spalte immer mitschicken und den Fall nie treffen.
"""

import json
import uuid

import pytest
from django.db import connection


@pytest.mark.django_db
def test_insert_ohne_backend_data_spalte_nutzt_db_default(facility):
    """Alt-Code-INSERT (ohne die Spalte) laeuft gegen das neue Schema durch."""
    snapshot_id = uuid.uuid4()

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO core_statisticssnapshot
                (id, facility_id, year, month, data, jugendamt_data, created_at, updated_at)
            VALUES (%s, %s, 2026, 1, '{}'::jsonb, '{}'::jsonb, NOW(), NOW())
            """,
            [snapshot_id, facility.id],
        )
        cursor.execute("SELECT backend_data FROM core_statisticssnapshot WHERE id = %s", [snapshot_id])
        (backend_data,) = cursor.fetchone()

    # Der Roh-Cursor liefert ``jsonb`` als Text (kein ORM-Feld, das dekodiert).
    assert json.loads(backend_data) == {}


@pytest.mark.django_db
def test_spalte_traegt_einen_db_default():
    """Der Default steht im Schema, nicht nur im Python-Model."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_default FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'core_statisticssnapshot'
              AND column_name = 'backend_data'
            """
        )
        (column_default,) = cursor.fetchone()

    assert column_default is not None
