"""Owner-/Facility-Migration 0130: Schema, Constraint, Index, Reverse-Guard (Refs #1738).

ADR-039 §11: 0130 fuegt ``facility`` (NOT NULL nach GUC-Backfill), ``client``/
``case`` hinzu, macht ``event`` nullable und pinnt genau-ein-Owner als
DB-Constraint. Der Reverse-Guard ist die LETZTE Operation der Migration —
beim Rollback laeuft er damit als ERSTE und bricht ab, sobald client-/case-
eigene Zeilen existieren (ihre Owner-Spalten wuerden sonst kommentarlos
weggeworfen).
"""

import importlib
import uuid

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction

from core.models import Attachment, FieldTemplate

MIGRATION = importlib.import_module("core.migrations.0130_attachment_owner_facility")


class _SchemaEditorStub:
    """Reicht nur ``connection`` durch — mehr nutzt der Guard/Backfill nicht."""

    def __init__(self, conn):
        self.connection = conn


def _column_meta(column):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'core_attachment' AND column_name = %s",
            [column],
        )
        row = cursor.fetchone()
    return row[0] if row else None


@pytest.mark.django_db
class TestSchema0130:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Schema-Introspektion braucht PostgreSQL")

    def test_facility_not_null_mit_fk(self):
        assert _column_meta("facility_id") == "NO"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT confrelid::regclass::text FROM pg_constraint
                WHERE conrelid = 'core_attachment'::regclass AND contype = 'f'
                  AND conname LIKE '%facility%'
                """
            )
            targets = [row[0] for row in cursor.fetchall()]
        assert targets == ["core_facility"], f"facility-FK zeigt auf: {targets}"

    def test_owner_spalten_nullable(self):
        assert _column_meta("event_id") == "YES"
        assert _column_meta("client_id") == "YES"
        assert _column_meta("case_id") == "YES"

    def test_index_facility_created_at(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'core_attachment'")
            names = {row[0] for row in cursor.fetchall()}
        assert "attachment_facility_ca_idx" in names, f"Index fehlt; vorhanden: {sorted(names)}"

    def test_check_constraint_auf_db_ebene(self, facility):
        """Roh-INSERT ohne Owner muss am CheckConstraint scheitern — die
        ORM-Ebene testet test_attachment_owner_model.py; hier geht es um die
        DB als letzte Verteidigungslinie (direkter SQL-Zugriff, Alt-Code)."""
        ft = FieldTemplate.objects.create(facility=facility, name="CC", field_type=FieldTemplate.FieldType.FILE)
        with pytest.raises(IntegrityError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO core_attachment
                        (id, entry_id, sort_order, facility_id, field_template_id,
                         storage_filename, original_filename_encrypted, file_size,
                         mime_type, detected_mime, is_current, created_at)
                    VALUES (%s, %s, 0, %s, %s, %s, '{}'::jsonb, 1, 'x/y', '', true, NOW())
                    """,
                    [str(uuid.uuid4()), str(uuid.uuid4()), str(facility.pk), str(ft.pk), f"{uuid.uuid4().hex}.enc"],
                )


@pytest.mark.django_db
class TestReverseGuard0130:
    def _kwargs(self, facility):
        ft = FieldTemplate.objects.create(
            facility=facility, name=f"RG {uuid.uuid4().hex[:6]}", field_type=FieldTemplate.FieldType.FILE
        )
        return {
            "facility": facility,
            "field_template": ft,
            "storage_filename": f"{uuid.uuid4().hex}.enc",
            "original_filename_encrypted": {"v": 1},
            "file_size": 1,
            "mime_type": "application/pdf",
        }

    def test_guard_blockt_bei_client_owned_zeilen(self, facility, client_identified):
        Attachment.objects.create(client=client_identified, **self._kwargs(facility))
        with pytest.raises(RuntimeError, match="client-/case-eigene"):
            MIGRATION.guard_no_owner_rows(global_apps, _SchemaEditorStub(connection))

    def test_guard_erlaubt_bei_nur_event_owned_zeilen(self, facility, sample_event):
        Attachment.objects.create(event=sample_event, **self._kwargs(facility))
        MIGRATION.guard_no_owner_rows(global_apps, _SchemaEditorStub(connection))
