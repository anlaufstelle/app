"""Owner-/Facility-Migration 0130: Schema, Constraint, Index, Reverse-Guard (Refs #1738).

ADR-039 §11: 0130 fuegt ``facility`` (NOT NULL nach Backfill unter der
BYPASSRLS-Migrationsrolle, Refs #1746), ``client``/``case`` hinzu, macht
``event`` nullable und pinnt genau-ein-Owner als DB-Constraint. Der
Reverse-Guard ist die LETZTE Operation der Migration — beim Rollback laeuft
er damit als ERSTE und bricht ab, sobald client-/case-eigene Zeilen
existieren (ihre Owner-Spalten wuerden sonst kommentarlos weggeworfen).
"""

import importlib
import uuid

import pytest
from django.apps import apps as global_apps
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django_test_migrations.contrib.unittest_case import MigratorTestCase

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
        """``array_length(conkey, 1) = 1``: gemeint ist der EINSPALTIGE
        ``facility_id``-FK. Seit 0133 (Refs #1780) tragen ``core_attachment``
        zusaetzlich drei ZWEIspaltige ``(owner_fk, facility_id)``-FKs auf die
        Owner-Tabellen — die matchen den Namensfilter ebenfalls und sind hier
        nicht gemeint (eigene Pins in ``test_0133_attachment_composite_fk.py``).
        """
        assert _column_meta("facility_id") == "NO"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT confrelid::regclass::text FROM pg_constraint
                WHERE conrelid = 'core_attachment'::regclass AND contype = 'f'
                  AND conname LIKE '%facility%' AND array_length(conkey, 1) = 1
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


class TestMigrate0130Backfill(MigratorTestCase):
    """Echter Backfill-Lauf 0129→0130 (G2-Befund: der einzige datenberuehrende
    Schritt der Kette war ungetestet). Zwei Facilities pinnen die Zeilen-
    genauigkeit — ein konstanter statt zeilenweiser Backfill wuerde rot."""

    migrate_from = ("core", "0129_rename_eventattachment_to_attachment")
    migrate_to = ("core", "0130_attachment_owner_facility")

    def prepare(self):
        apps = self.old_state.apps
        Organization = apps.get_model("core", "Organization")
        Facility = apps.get_model("core", "Facility")
        User = apps.get_model("core", "User")
        Client = apps.get_model("core", "Client")
        DocumentType = apps.get_model("core", "DocumentType")
        Event = apps.get_model("core", "Event")
        FieldTemplate = apps.get_model("core", "FieldTemplate")
        Attachment = apps.get_model("core", "Attachment")

        org = Organization.objects.create(name="Mig-0130-Org")
        atts = {}
        for suffix in ("a", "b"):
            fac = Facility.objects.create(organization=org, name=f"Stelle {suffix.upper()}")
            user = User.objects.create(username=f"mig-0130-{suffix}", facility=fac, role="staff")
            person = Client.objects.create(
                facility=fac, pseudonym=f"Mig-0130-{suffix}", contact_stage="identified", created_by=user
            )
            doc_type = DocumentType.objects.create(facility=fac, name=f"Kontakt {suffix}")
            event = Event.objects.create(
                facility=fac,
                client=person,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                created_by=user,
            )
            ft = FieldTemplate.objects.create(facility=fac, name=f"Anhang {suffix}", field_type="file")
            att = Attachment.objects.create(
                event=event,
                field_template=ft,
                storage_filename=f"mig-0130-{suffix}.enc",
                original_filename_encrypted={"v": 1},
                file_size=1,
                mime_type="application/pdf",
            )
            atts[suffix] = (att.pk, fac.pk)
        self.atts = atts

    def test_facility_zeilengenau_aus_event_backfilled(self):
        Attachment = self.new_state.apps.get_model("core", "Attachment")
        for att_pk, fac_pk in self.atts.values():
            assert Attachment.objects.get(pk=att_pk).facility_id == fac_pk


class TestOperationOrder0130:
    """G2-Befund: Die Guard-Position ist tragend (rueckwaerts = erste Op) und
    ADR-nah dokumentiert — hier gepinnt, damit ein wohlmeinendes Umsortieren
    rot wird, bevor es einen echten Rollback Daten kostet."""

    def test_reverse_guard_ist_letzte_operation(self):
        from django.db import migrations as dj_migrations

        last = MIGRATION.Migration.operations[-1]
        assert isinstance(last, dj_migrations.RunPython)
        assert last.reverse_code is MIGRATION.guard_no_owner_rows
        assert last.code is dj_migrations.RunPython.noop

    def test_guard_kommt_nur_einmal_vor(self):
        from django.db import migrations as dj_migrations

        guards = [
            op
            for op in MIGRATION.Migration.operations
            if isinstance(op, dj_migrations.RunPython) and op.reverse_code is MIGRATION.guard_no_owner_rows
        ]
        assert len(guards) == 1
