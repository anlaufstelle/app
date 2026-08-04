"""Composite-FK-Haertung 0133: Schema, NULL-Semantik, Preflight-Guard (Refs #1780).

ADR-039 §2 hatte die Haertung bewusst aus M24 ausgeklammert: ``Attachment.facility``
war nur per Service-Konvention an ``owner.facility`` gebunden. 0133 pinnt das Paar
``(owner_fk, facility_id)`` auf DB-Ebene.

Zwei Eigenheiten praegen diese Tests:

* Die FKs sind ``DEFERRABLE INITIALLY DEFERRED`` — die Verletzung schlaegt erst
  beim COMMIT zu. Der Testlauf committet nie (Transaktion je Test), deshalb
  erzwingt :func:`_flush_constraints` die Pruefung per
  ``SET CONSTRAINTS ALL IMMEDIATE``.
* Die divergente Kombination laesst sich NICHT ueber den Service-Layer bauen —
  ``store_encrypted_file`` leitet ``facility`` aus dem Owner ab und wirft sonst
  ``ValueError``. Die Tests schreiben deshalb per ``objects.update()`` direkt an
  der DB, am Service-Guard vorbei.
"""

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import DatabaseError, connection, transaction

from core.models import Attachment, FieldTemplate

MIGRATION = importlib.import_module("core.migrations.0133_attachment_composite_fk")

#: Erwartete Composite-FKs: Name → (Zieltabelle, Spaltenreihenfolge).
EXPECTED_FKS = {
    "core_attachment_event_facility_fk": ("core_event", ["event_id", "facility_id"]),
    "core_attachment_client_facility_fk": ("core_client", ["client_id", "facility_id"]),
    "core_attachment_case_facility_fk": ("core_case", ["case_id", "facility_id"]),
}
#: Erwartete UNIQUE-Begleitindizes: Zieltabelle → Indexname.
EXPECTED_UNIQUE_INDEXES = {
    "core_event": "core_event_id_facility_uq",
    "core_client": "core_client_id_facility_uq",
    "core_case": "core_case_id_facility_uq",
}


class _SchemaEditorStub:
    """Reicht nur ``connection`` durch — mehr nutzt der Guard nicht."""

    def __init__(self, conn):
        self.connection = conn


def _flush_constraints():
    """Erzwingt die Pruefung der aufgeschobenen FKs innerhalb der Transaktion."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _reset_constraints():
    """Default wiederherstellen, damit Folge-Statements nicht sofort pruefen."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def _attachment_kwargs(facility, name):
    ft = FieldTemplate.objects.create(facility=facility, name=name, field_type=FieldTemplate.FieldType.FILE)
    return {
        "facility": facility,
        "field_template": ft,
        "storage_filename": f"{name}.enc",
        "original_filename_encrypted": {"v": 1},
        "file_size": 1,
        "mime_type": "application/pdf",
    }


@pytest.mark.django_db
class TestSchema0133:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Schema-Introspektion braucht PostgreSQL")

    def test_unique_begleitindizes_auf_den_zieltabellen(self):
        """Ohne UNIQUE auf ``(id, facility_id)`` gibt es kein Ziel fuer den FK."""
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.relname, i.relname, pg_get_indexdef(i.oid)
                FROM pg_index x
                JOIN pg_class i ON i.oid = x.indexrelid
                JOIN pg_class t ON t.oid = x.indrelid
                WHERE x.indisunique AND t.relname IN ('core_event', 'core_client', 'core_case')
                """
            )
            found = {table: index for table, index, indexdef in cursor.fetchall() if "(id, facility_id)" in indexdef}
        assert found == EXPECTED_UNIQUE_INDEXES, f"UNIQUE-Begleitindizes fehlen/heissen anders: {found}"

    def test_zusammengesetzte_fks_auf_core_attachment(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.conname, c.confrelid::regclass::text,
                       (SELECT array_agg(a.attname ORDER BY k.ord)
                        FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
                FROM pg_constraint c
                WHERE c.conrelid = 'core_attachment'::regclass
                  AND c.contype = 'f' AND array_length(c.conkey, 1) = 2
                """
            )
            found = {row[0]: (row[1], list(row[2])) for row in cursor.fetchall()}
        assert found == EXPECTED_FKS, f"Composite-FKs weichen ab: {found}"

    def test_fks_sind_deferrable_initially_deferred(self):
        """Schema-Konvention (0104) — sofortige Pruefung zerrisse Kaskaden-Loeschungen."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, condeferrable, condeferred FROM pg_constraint "
                "WHERE conrelid = 'core_attachment'::regclass AND conname = ANY (%s)",
                [sorted(EXPECTED_FKS)],
            )
            rows = cursor.fetchall()
        assert len(rows) == len(EXPECTED_FKS), f"nicht alle FKs gefunden: {rows}"
        for conname, deferrable, deferred in rows:
            assert deferrable and deferred, f"{conname} ist nicht DEFERRABLE INITIALLY DEFERRED"


@pytest.mark.django_db
class TestDivergenteKombinationWirdAbgelehnt:
    """Owner in Einrichtung A, Anhang-Zeile mit ``facility_id`` von B → DB blockt.

    ``objects.update()`` statt Service-Aufruf: ``store_encrypted_file`` leitet
    ``facility`` aus dem Owner ab und liesse die Kombination nie entstehen — genau
    diese Konvention sichert die DB jetzt unabhaengig ab (ADR-039 §2).
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Composite-FKs brauchen PostgreSQL")

    def teardown_method(self):
        _reset_constraints()

    def _assert_divergenz_blockt(self, attachment, other_facility):
        # Update UND Pruefung im selben Savepoint: der Rollback raeumt die
        # divergente Zeile mit weg.
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            Attachment.objects.filter(pk=attachment.pk).update(facility=other_facility)
            _flush_constraints()
        assert "facility_fk" in str(excinfo.value), f"andere Ursache als der Composite-FK: {excinfo.value}"

    def test_ereignis_eigener_anhang(self, facility, other_facility, sample_event):
        att = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility, "divergent-event"))
        self._assert_divergenz_blockt(att, other_facility)

    def test_personen_eigener_anhang(self, facility, other_facility, client_identified):
        att = Attachment.objects.create(client=client_identified, **_attachment_kwargs(facility, "divergent-client"))
        self._assert_divergenz_blockt(att, other_facility)

    def test_fall_eigener_anhang(self, facility, other_facility, case_open):
        att = Attachment.objects.create(case=case_open, **_attachment_kwargs(facility, "divergent-case"))
        self._assert_divergenz_blockt(att, other_facility)


@pytest.mark.django_db
class TestNullOwnerSpaltenPassieren:
    """MATCH SIMPLE: die beiden ungenutzten Owner-Spalten je Zeile sind NULL und
    duerfen ihre FKs nicht ausloesen (Migrations-Docstring)."""

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Composite-FKs brauchen PostgreSQL")

    def teardown_method(self):
        _reset_constraints()

    @pytest.mark.parametrize("owner_field", ["event", "client", "case"])
    def test_konsistente_zeile_passiert_alle_drei_fks(
        self, owner_field, facility, sample_event, client_identified, case_open
    ):
        owner = {"event": sample_event, "client": client_identified, "case": case_open}[owner_field]
        Attachment.objects.create(**{owner_field: owner}, **_attachment_kwargs(facility, f"ok-{owner_field}"))
        _flush_constraints()  # wirft nicht — die beiden NULL-Spalten passieren


@pytest.mark.django_db
class TestPreflightGuard0133:
    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("Guard liest per Roh-SQL")

    def teardown_method(self):
        _reset_constraints()

    def test_guard_ist_still_bei_sauberem_bestand(self, facility, sample_event):
        Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility, "guard-clean"))
        MIGRATION.guard_no_divergent_owner_facility(global_apps, _SchemaEditorStub(connection))

    def test_guard_meldet_divergente_zeilen_mit_zahlen(self, facility, other_facility, client_identified):
        att = Attachment.objects.create(client=client_identified, **_attachment_kwargs(facility, "guard-divergent"))
        # Der FK existiert in der Testdatenbank schon; seine Pruefung bleibt bis
        # zum COMMIT aufgeschoben, der Guard sieht die Zeile also. Savepoint +
        # ``set_rollback``: Djangos Test-Teardown ruft ``check_constraints()``
        # (``SET CONSTRAINTS ALL IMMEDIATE``) — eine stehengelassene divergente
        # Zeile risse dort den Teardown auf.
        with transaction.atomic():
            Attachment.objects.filter(pk=att.pk).update(facility=other_facility)
            with pytest.raises(RuntimeError, match="core_client: 1"):
                MIGRATION.guard_no_divergent_owner_facility(global_apps, _SchemaEditorStub(connection))
            transaction.set_rollback(True)


class TestReversePins0133:
    """Der Reverse laesst sich in der Testdatenbank nicht fahren (die Historie
    steht auf dem Endstand) — seine Vollstaendigkeit ist aber tragend: ein
    vergessener DROP macht die Migration unumkehrbar."""

    def test_reverse_droppt_alle_drei_fks(self):
        for name in EXPECTED_FKS:
            assert f"DROP CONSTRAINT IF EXISTS {name}" in MIGRATION.REVERSE_SQL

    def test_reverse_droppt_alle_drei_begleitindizes(self):
        for index_name in EXPECTED_UNIQUE_INDEXES.values():
            assert f"DROP INDEX IF EXISTS {index_name}" in MIGRATION.REVERSE_SQL

    def test_reverse_droppt_fks_vor_indizes(self):
        """Ein Index, der ein FK-Ziel traegt, laesst sich vorher nicht droppen."""
        sql = MIGRATION.REVERSE_SQL
        last_fk = max(sql.index(f"DROP CONSTRAINT IF EXISTS {name}") for name in EXPECTED_FKS)
        first_index = min(sql.index(f"DROP INDEX IF EXISTS {name}") for name in EXPECTED_UNIQUE_INDEXES.values())
        assert last_fk < first_index

    def test_forward_ohne_match_full(self):
        """MATCH FULL zerrisse jede client-/case-eigene Zeile (facility_id NOT NULL)."""
        assert "MATCH FULL" not in MIGRATION.FORWARD_SQL


class TestOperationOrder0133:
    def test_preflight_guard_ist_erste_operation(self):
        """Rueckwaerts noop; vorwaerts MUSS er vor dem ``ALTER TABLE`` laufen."""
        from django.db import migrations as dj_migrations

        first = MIGRATION.Migration.operations[0]
        assert isinstance(first, dj_migrations.RunPython)
        assert first.code is MIGRATION.guard_no_divergent_owner_facility
        assert first.reverse_code is dj_migrations.RunPython.noop

    def test_kein_rollback_guard(self):
        """Bewusste Abweichung von 0130/0131 (Migrations-Docstring): der Reverse
        nimmt nur Haertung zurueck — ein blockierender Guard haette kein Schutzgut."""
        from django.db import migrations as dj_migrations

        reverse_guards = [
            op
            for op in MIGRATION.Migration.operations
            if isinstance(op, dj_migrations.RunPython) and op.reverse_code is not dj_migrations.RunPython.noop
        ]
        assert not reverse_guards
