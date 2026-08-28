"""Facility-Pinning der Ereignis-Kanten via Composite-FKs (Refs #2066).

``Event.facility == client.facility == case.facility == document_type.facility``
war bis hierher **nur** eine Service-Konvention
(``core/services/events/crud.py``). Die von Django erzeugten FKs pinnen je nur
die Ziel-``id``, nicht das Paar aus ``id`` und Einrichtung. Unter
Superadmin-RLS-Bypass, Bulk-Updates oder Migrationspfaden ist die divergente
Kombination — Ereignis in Einrichtung A, Person/Fall/Dokumentationstyp aus B —
also erzeugbar und leakt danach ueber Joins zwischen den Einrichtungen.

[0133](0133_attachment_composite_fk.py) hat dieselbe Haertung fuer die
Attachment-Owner-Kanten gefahren (Refs #1780, ADR-039 §2) und dabei die
UNIQUE-Begleitindizes ``(id, facility_id)`` auf ``core_event``, ``core_client``
und ``core_case`` bereits angelegt. Diese Migration ist der verbleibende Rest:

1. **UNIQUE-Begleitindex** ``(id, facility_id)`` auf ``core_documenttype`` —
   die einzige der vier Zieltabellen, die ihn noch nicht hat. Ein
   zusammengesetzter FK braucht ein UNIQUE-Ziel; die PK allein deckt nur ``id``
   ab. Der Index ist fachlich redundant (``id`` ist bereits eindeutig) und
   existiert ausschliesslich als FK-Ziel.
2. **Drei zusammengesetzte FKs** von ``core_event`` auf die Ziel-Uniques, je
   Kante einer. Sie treten NEBEN die bestehenden Einspalten-FKs (Django-erzeugt)
   — die bleiben unangetastet, weil das ORM sie zur ``on_delete``-Abbildung und
   Introspektion braucht.

**NULL-Semantik (tragend).** ``client_id`` und ``case_id`` sind nullable
(``on_delete=SET_NULL``): ein Ereignis ohne zugeordnete Person oder ohne Fall
ist der Normalfall, und beim Loeschen einer Person nullt Djangos Collector die
Spalte. Zusammengesetzte FKs verwenden per Default ``MATCH SIMPLE``: ist auch
nur eine der referenzierenden Spalten NULL, gilt die Bedingung als erfuellt —
diese Zeilen passieren also weiter. ``MATCH FULL`` waere das Gegenteil (alle
Spalten NULL *oder* alle gesetzt) und wuerde an ``facility_id`` NOT NULL jedes
personenlose Ereignis zerreissen. ``document_type_id`` ist NOT NULL und feuert
folglich auf jeder Zeile.

**Deferrable.** ``DEFERRABLE INITIALLY DEFERRED`` wie alle FK-Constraints
dieses Schemas (Django-Konvention, uebernommen in 0104/0133): die Pruefung
faellt ans Transaktionsende, Kaskaden-Loeschungen und Fixture-Loads bleiben
reihenfolgen-tolerant. Sichtbare Folge fuer Tests: die Verletzung schlaegt erst
beim COMMIT bzw. bei ``SET CONSTRAINTS ALL IMMEDIATE`` zu.

**Kein ``ON DELETE``/``ON UPDATE``.** Wie bei allen Django-FKs bleibt es bei
``NO ACTION``: die Kaskade fahren Djangos Collector bzw. die bestehenden
Einspalten-FKs. Ein ``UPDATE core_event SET facility_id = …`` (kein
unterstuetzter Vorgang — Ereignisse wechseln die Einrichtung nicht) laeuft damit
fail-loud in eine FK-Verletzung statt Zeilen still umzuhaengen.

**Kein Rollback-Guard** — wie 0133. Der Reverse nimmt nur Haertung zurueck (drei
Constraints, ein Index); er verliert keine Daten und verdeckt keine Zeilen. Der
Guard sitzt stattdessen VORWAERTS an erster Stelle: er meldet divergente
Bestandszeilen mit Zahlen je Kante, statt sie als rohe FK-Verletzung aus dem
``ALTER TABLE`` zu bekommen. Der Reverse laesst die drei 0133-Indizes bewusst
stehen — sie tragen dort die Attachment-FKs.
"""

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls

#: ``(Ereignis-Spalte, Zieltabelle)`` — Reihenfolge wie im Event-Model.
EDGE_TARGETS = (
    ("client_id", "core_client"),
    ("case_id", "core_case"),
    ("document_type_id", "core_documenttype"),
)

FORWARD_SQL = """
CREATE UNIQUE INDEX core_documenttype_id_facility_uq ON core_documenttype (id, facility_id);

ALTER TABLE core_event
    ADD CONSTRAINT core_event_client_facility_fk
    FOREIGN KEY (client_id, facility_id) REFERENCES core_client (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE core_event
    ADD CONSTRAINT core_event_case_facility_fk
    FOREIGN KEY (case_id, facility_id) REFERENCES core_case (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE core_event
    ADD CONSTRAINT core_event_document_type_facility_fk
    FOREIGN KEY (document_type_id, facility_id) REFERENCES core_documenttype (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
"""

#: Erst die FKs, dann der Index — ein Index, der ein FK-Ziel traegt, laesst sich
#: nicht droppen, solange der FK existiert. Die drei ``*_id_facility_uq`` aus
#: 0133 bleiben stehen: sie gehoeren dort hin.
REVERSE_SQL = """
ALTER TABLE core_event DROP CONSTRAINT IF EXISTS core_event_client_facility_fk;
ALTER TABLE core_event DROP CONSTRAINT IF EXISTS core_event_case_facility_fk;
ALTER TABLE core_event DROP CONSTRAINT IF EXISTS core_event_document_type_facility_fk;

DROP INDEX IF EXISTS core_documenttype_id_facility_uq;
"""


def guard_no_cross_facility_event_links(apps, schema_editor):
    """Vorwaerts-Preflight: meldet divergente Bestandszeilen fail-loud.

    Ohne diesen Guard scheitert das ``ALTER TABLE`` an der rohen FK-Verletzung
    ("Key (client_id, facility_id)=(…) is not present") — eine einzelne
    Beispielzeile ohne Hinweis, wie viele Zeilen betroffen sind und an welcher
    Kante sie haengen. Hier gibt es stattdessen Zahlen je Kante und den Verweis
    auf den Reparaturweg.

    ``assert_migration_role_bypasses_rls`` als erste Zeile (Konvention seit
    0123, Refs #1746): unter der App-Rolle greift die
    ``facility_isolation``-Policy auf ``core_event`` — ohne gesetztes
    ``app.current_facility_id`` liefe der Zaehler still auf 0, der Guard meldete
    faelschlich "alles sauber", und das ``ALTER TABLE`` liefe doch in die rohe
    Verletzung.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    divergent = {}
    with schema_editor.connection.cursor() as cursor:
        for edge_column, target_table in EDGE_TARGETS:
            # Beide Interpolationen sind Literale aus EDGE_TARGETS, keine Userdaten.
            # Der JOIN schluckt NULL-Kanten (MATCH SIMPLE) ohne Sonderfall.
            cursor.execute(
                "SELECT count(*) FROM core_event e "
                f"JOIN {target_table} t ON t.id = e.{edge_column} "
                "WHERE e.facility_id <> t.facility_id"
            )
            (count,) = cursor.fetchone()
            if count:
                divergent[target_table] = count
    if divergent:
        details = ", ".join(f"{table}: {count}" for table, count in sorted(divergent.items()))
        raise RuntimeError(
            f"Composite-FK-Haertung abgebrochen: Ereignis-Zeilen mit fremder Einrichtung ({details}). "
            "Solche Verknuepfungen leaken ueber Joins zwischen Einrichtungen (Refs #2066). "
            "Vor dem erneuten Migrieren pro Zeile entscheiden: die Verknuepfung auf eine Zeile der "
            "eigenen Einrichtung umhaengen (bei Person/Fall auch: auf NULL setzen) oder das Ereignis "
            "entfernen."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0165_client_anonymized_at"),
    ]

    operations = [
        # MUSS die erste Operation bleiben: nur vor dem ``ALTER TABLE`` kann der
        # Preflight die Bestandslage ueberhaupt noch melden. Position per Test
        # gepinnt.
        migrations.RunPython(guard_no_cross_facility_event_links, migrations.RunPython.noop),
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
