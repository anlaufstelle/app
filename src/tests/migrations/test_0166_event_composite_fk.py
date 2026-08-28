"""Composite-FK-Haertung 0166: Event → Client/Case/DocumentType (Refs #2066).

#1780 / Migration
``0133`` haben die Attachment-Owner-Kanten gehaertet und dabei die
UNIQUE-Begleitindizes ``(id, facility_id)`` auf ``core_event``, ``core_client``
und ``core_case`` bereits angelegt. Offen blieb die **Ereignis-Seite**:
``Event.facility == client.facility == case.facility == document_type.facility``
wird bis hierher ausschliesslich im Service erzwungen
(``services/events/crud.py``). Unter Superadmin-RLS-Bypass, Bulk-Updates oder
Migrationspfaden entsteht die Kombination trotzdem — und leakt dann ueber Joins
zwischen Einrichtungen.

0166 zieht dieselbe Zusicherung auf die DB-Ebene:

1. UNIQUE-Begleitindex ``(id, facility_id)`` auf ``core_documenttype`` (die drei
   anderen Zieltabellen haben ihn seit 0133).
2. Drei zusammengesetzte FKs von ``core_event`` auf die Ziel-Uniques.

Eigenheiten, die diese Tests praegen — analog
``test_0133_attachment_composite_fk``:

* Die FKs sind ``DEFERRABLE INITIALLY DEFERRED``; der Testlauf committet nie,
  deshalb erzwingt :func:`_flush_constraints` die Pruefung per
  ``SET CONSTRAINTS ALL IMMEDIATE``.
* ``client_id`` und ``case_id`` sind **nullable** (``on_delete=SET_NULL``).
  ``MATCH SIMPLE`` (Postgres-Default) laesst eine Zeile mit NULL-Owner-Spalte
  passieren — das ist tragend und wird hier gepinnt, nicht nur angenommen.
  ``document_type_id`` ist NOT NULL und feuert immer.
* Der Test-DB-User ist Superuser und bypasst RLS per Postgres-Default (siehe
  ``src/tests/test_rls.py``). Die Abweisung der Cross-Facility-Kombination hier
  ist also genau der geforderte Nachweis, dass die DB **auch unter RLS-Bypass**
  blockt — sie haengt nicht an einer Policy, sondern am Constraint.
"""

import importlib

import pytest
from django.apps import apps as global_apps
from django.db import DatabaseError, connection, transaction

from core.models import Event

MIGRATION = importlib.import_module("core.migrations.0166_event_composite_fk")

#: Erwartete Composite-FKs auf ``core_event``: Name → (Zieltabelle, Spalten).
EXPECTED_FKS = {
    "core_event_client_facility_fk": ("core_client", ["client_id", "facility_id"]),
    "core_event_case_facility_fk": ("core_case", ["case_id", "facility_id"]),
    "core_event_document_type_facility_fk": (
        "core_documenttype",
        ["document_type_id", "facility_id"],
    ),
}

#: Neu in dieser Migration — die drei anderen Ziel-Uniques kommen aus 0133.
NEW_UNIQUE_INDEX = ("core_documenttype", "core_documenttype_id_facility_uq")


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


@pytest.mark.postgres
@pytest.mark.django_db
class TestSchema0166:
    def test_unique_begleitindex_auf_core_documenttype(self):
        """Ohne UNIQUE auf ``(id, facility_id)`` gibt es kein FK-Ziel."""
        table, index_name = NEW_UNIQUE_INDEX
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT i.relname, pg_get_indexdef(i.oid)
                FROM pg_index x
                JOIN pg_class i ON i.oid = x.indexrelid
                JOIN pg_class t ON t.oid = x.indrelid
                WHERE x.indisunique AND t.relname = %s
                """,
                [table],
            )
            found = [name for name, indexdef in cursor.fetchall() if "(id, facility_id)" in indexdef]
        assert found == [index_name], f"UNIQUE-Begleitindex auf {table} fehlt/heisst anders: {found}"

    def test_zusammengesetzte_fks_auf_core_event(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.conname, c.confrelid::regclass::text,
                       (SELECT array_agg(a.attname ORDER BY k.ord)
                        FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum)
                FROM pg_constraint c
                WHERE c.conrelid = 'core_event'::regclass
                  AND c.contype = 'f' AND array_length(c.conkey, 1) = 2
                """
            )
            found = {row[0]: (row[1], list(row[2])) for row in cursor.fetchall()}
        assert found == EXPECTED_FKS, f"Composite-FKs weichen ab: {found}"

    def test_fks_sind_deferrable_initially_deferred(self):
        """Schema-Konvention (0104/0133) — sofortige Pruefung zerrisse Kaskaden."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname, condeferrable, condeferred FROM pg_constraint "
                "WHERE conrelid = 'core_event'::regclass AND conname = ANY (%s)",
                [sorted(EXPECTED_FKS)],
            )
            rows = cursor.fetchall()
        assert len(rows) == len(EXPECTED_FKS), f"nicht alle FKs gefunden: {rows}"
        for conname, deferrable, deferred in rows:
            assert deferrable and deferred, f"{conname} ist nicht DEFERRABLE INITIALLY DEFERRED"


@pytest.mark.postgres
@pytest.mark.django_db
class TestCrossFacilityLinkWirdAbgelehnt:
    """Ereignis in Einrichtung A, Owner-Zeile aus B → die DB blockt.

    ``objects.update()`` statt Service-Aufruf: ``services/events/crud.py`` haelt
    die Kombination auf App-Ebene ab. Genau diese Konvention sichert 0166
    unabhaengig davon ab — und zwar unter dem RLS-bypassenden Superuser der
    Testdatenbank, also auf reiner Constraint-Ebene.
    """

    def teardown_method(self):
        _reset_constraints()

    def _assert_divergenz_blockt(self, event, **update_kwargs):
        # Update UND Pruefung im selben Savepoint: der Rollback raeumt die
        # divergente Zeile mit weg.
        with pytest.raises(DatabaseError) as excinfo, transaction.atomic():
            Event.objects.filter(pk=event.pk).update(**update_kwargs)
            _flush_constraints()
        assert "facility_fk" in str(excinfo.value), f"andere Ursache als der Composite-FK: {excinfo.value}"

    def test_fremde_person(self, sample_event, foreign_client):
        self._assert_divergenz_blockt(sample_event, client=foreign_client)

    def test_fremder_fall(self, sample_event, foreign_case):
        self._assert_divergenz_blockt(sample_event, case=foreign_case)

    def test_fremder_dokumentationstyp(self, sample_event, foreign_doc_type):
        self._assert_divergenz_blockt(sample_event, document_type=foreign_doc_type)

    def test_umhaengen_der_einrichtung_am_ereignis_blockt(self, sample_event, other_facility):
        """Kein unterstuetzter Vorgang — laeuft jetzt fail-loud statt still."""
        self._assert_divergenz_blockt(sample_event, facility=other_facility)


@pytest.mark.postgres
@pytest.mark.django_db
class TestNullOwnerSpaltenPassieren:
    """MATCH SIMPLE: ``client_id``/``case_id`` sind nullable (``SET_NULL``) —
    eine NULL-Spalte darf ihren FK nicht ausloesen (Migrations-Docstring)."""

    def teardown_method(self):
        _reset_constraints()

    def test_ereignis_ohne_person_und_fall_passiert(self, sample_event):
        Event.objects.filter(pk=sample_event.pk).update(client=None, case=None)
        _flush_constraints()  # wirft nicht — beide NULL-Spalten passieren

    def test_ereignis_mit_person_und_fall_derselben_einrichtung_passiert(self, sample_event, case_open):
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        _flush_constraints()


@pytest.mark.postgres
@pytest.mark.django_db
class TestPreflightGuard0166:
    def teardown_method(self):
        _reset_constraints()

    def test_guard_ist_still_bei_sauberem_bestand(self, sample_event, case_open):
        Event.objects.filter(pk=sample_event.pk).update(case=case_open)
        MIGRATION.guard_no_cross_facility_event_links(global_apps, _SchemaEditorStub(connection))

    def test_guard_meldet_divergente_zeilen_mit_zahlen(self, sample_event, foreign_client):
        # Der FK existiert in der Testdatenbank schon; seine Pruefung bleibt bis
        # zum COMMIT aufgeschoben, der Guard sieht die Zeile also. Savepoint +
        # ``set_rollback``: Djangos Test-Teardown ruft ``check_constraints()``
        # (``SET CONSTRAINTS ALL IMMEDIATE``) — eine stehengelassene divergente
        # Zeile risse dort den Teardown auf.
        with transaction.atomic():
            Event.objects.filter(pk=sample_event.pk).update(client=foreign_client)
            with pytest.raises(RuntimeError, match="core_client: 1"):
                MIGRATION.guard_no_cross_facility_event_links(global_apps, _SchemaEditorStub(connection))
            transaction.set_rollback(True)


class TestReversePins0166:
    """Der Reverse laesst sich in der Testdatenbank nicht fahren (die Historie
    steht auf dem Endstand) — seine Vollstaendigkeit ist aber tragend: ein
    vergessener DROP macht die Migration unumkehrbar."""

    def test_reverse_droppt_alle_drei_fks(self):
        for name in EXPECTED_FKS:
            assert f"DROP CONSTRAINT IF EXISTS {name}" in MIGRATION.REVERSE_SQL

    def test_reverse_droppt_den_begleitindex(self):
        assert f"DROP INDEX IF EXISTS {NEW_UNIQUE_INDEX[1]}" in MIGRATION.REVERSE_SQL

    def test_reverse_droppt_fks_vor_index(self):
        """Ein Index, der ein FK-Ziel traegt, laesst sich vorher nicht droppen."""
        sql = MIGRATION.REVERSE_SQL
        last_fk = max(sql.index(f"DROP CONSTRAINT IF EXISTS {name}") for name in EXPECTED_FKS)
        first_index = sql.index(f"DROP INDEX IF EXISTS {NEW_UNIQUE_INDEX[1]}")
        assert last_fk < first_index

    def test_reverse_droppt_die_0133_indizes_nicht(self):
        """``core_event``/``core_client``/``core_case`` gehoeren 0133 — ein DROP
        hier risse deren Attachment-FKs mit."""
        for foreign_index in ("core_event_id_facility_uq", "core_client_id_facility_uq", "core_case_id_facility_uq"):
            assert foreign_index not in MIGRATION.REVERSE_SQL

    def test_forward_ohne_match_full(self):
        """MATCH FULL zerrisse jede Zeile ohne Person/Fall (facility_id NOT NULL)."""
        assert "MATCH FULL" not in MIGRATION.FORWARD_SQL


class TestOperationOrder0166:
    def test_preflight_guard_ist_erste_operation(self):
        """Rueckwaerts noop; vorwaerts MUSS er vor dem ``ALTER TABLE`` laufen."""
        from django.db import migrations as dj_migrations

        first = MIGRATION.Migration.operations[0]
        assert isinstance(first, dj_migrations.RunPython)
        assert first.code is MIGRATION.guard_no_cross_facility_event_links
        assert first.reverse_code is dj_migrations.RunPython.noop
