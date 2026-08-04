"""Composite-FK-Haertung der Attachment-Owner (Refs #1780, ADR-039 §2).

M24 leitet ``Attachment.facility`` per **Konvention** aus ``owner.facility``
ab (``store_encrypted_file``, ValueError-Guard + Pin-Test). Auf DB-Ebene war
die divergente Kombination — Owner in Einrichtung A, Anhang-Zeile mit
``facility_id`` von Einrichtung B — bis hierher moeglich: die von Django
erzeugten FKs pinnen nur ``owner_id``, nicht das Paar. Eine solche Zeile waere
unter der direkten RLS-Policy (0131) in der FALSCHEN Einrichtung sichtbar.

Diese Migration zieht die Zusicherung auf die DB-Ebene:

1. **UNIQUE-Begleitindizes** ``(id, facility_id)`` auf ``core_event``,
   ``core_client`` und ``core_case``. Ein zusammengesetzter FK braucht ein
   UNIQUE-Ziel; die PK allein deckt nur ``id`` ab. Die Indizes sind fachlich
   redundant (``id`` ist bereits eindeutig) und existieren ausschliesslich als
   FK-Ziel.
2. **Zusammengesetzte FKs** ``core_attachment (owner_fk, facility_id)`` →
   Zieltabelle ``(id, facility_id)``, je Owner-Sorte einer. Sie treten NEBEN
   die bestehenden Einspalten-FKs (Django-erzeugt) — die bleiben unangetastet,
   weil das ORM sie zur ``on_delete``-Abbildung und Introspektion braucht.

**NULL-Semantik (tragend).** Pro Zeile ist genau EIN Owner-FK gesetzt
(CheckConstraint ``attachment_exactly_one_owner``), die beiden anderen sind
NULL. Zusammengesetzte FKs verwenden per Default ``MATCH SIMPLE``: ist auch
nur eine der referenzierenden Spalten NULL, gilt die Bedingung als erfuellt —
die beiden ungenutzten Owner-FKs feuern also nie. Genau richtig hier;
``MATCH FULL`` waere das Gegenteil (alle Spalten NULL *oder* alle gesetzt) und
wuerde an ``facility_id`` NOT NULL jede client-/case-eigene Zeile zerreissen.

**Deferrable.** ``DEFERRABLE INITIALLY DEFERRED`` wie alle FK-Constraints
dieses Schemas (Django-Konvention, uebernommen in
[0104](0104_ondelete_hardening_legalhold_workitem_deletionrequest.py)): die
Pruefung faellt ans Transaktionsende, Kaskaden-Loeschungen und Fixture-Loads
bleiben reihenfolgen-tolerant. Sichtbare Folge fuer Tests: die Verletzung
schlaegt erst beim COMMIT bzw. bei ``SET CONSTRAINTS ALL IMMEDIATE`` zu, nicht
schon beim INSERT/UPDATE.

**Kein ``ON DELETE``/``ON UPDATE``.** Wie bei allen Django-FKs bleibt es bei
``NO ACTION``: die Kaskade fahren Djangos Collector bzw. die bestehenden
Einspalten-FKs. Ein ``UPDATE core_event SET facility_id = …`` (kein
unterstuetzter Vorgang — Ereignisse wechseln die Einrichtung nicht) laeuft
damit fail-loud in eine FK-Verletzung statt Zeilen still umzuhaengen.

**Kein Rollback-Guard** — anders als 0130/0131. Der Reverse nimmt nur
Haertung zurueck (drei Constraints, drei Indizes); er verliert keine Daten und
verdeckt keine Zeilen. Ein blockierender Guard waere hier reine
Rollback-Sperre ohne Schutzgut. Der Guard dieser Migration sitzt stattdessen
VORWAERTS an erster Stelle: er meldet divergente Bestandszeilen mit Zahlen
statt sie als rohe FK-Verletzung aus dem ``ALTER TABLE`` zu bekommen.
"""

from django.db import migrations

from core.utils.migration_guards import assert_migration_role_bypasses_rls

#: ``(Owner-Spalte, Zieltabelle)`` — Reihenfolge wie ``Attachment.OWNER_FIELDS``.
OWNER_TARGETS = (
    ("event_id", "core_event"),
    ("client_id", "core_client"),
    ("case_id", "core_case"),
)

FORWARD_SQL = """
CREATE UNIQUE INDEX core_event_id_facility_uq ON core_event (id, facility_id);
CREATE UNIQUE INDEX core_client_id_facility_uq ON core_client (id, facility_id);
CREATE UNIQUE INDEX core_case_id_facility_uq ON core_case (id, facility_id);

ALTER TABLE core_attachment
    ADD CONSTRAINT core_attachment_event_facility_fk
    FOREIGN KEY (event_id, facility_id) REFERENCES core_event (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE core_attachment
    ADD CONSTRAINT core_attachment_client_facility_fk
    FOREIGN KEY (client_id, facility_id) REFERENCES core_client (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE core_attachment
    ADD CONSTRAINT core_attachment_case_facility_fk
    FOREIGN KEY (case_id, facility_id) REFERENCES core_case (id, facility_id)
    DEFERRABLE INITIALLY DEFERRED;
"""

#: Erst die FKs, dann die Indizes — ein Index, der ein FK-Ziel traegt, laesst
#: sich nicht droppen, solange der FK existiert.
REVERSE_SQL = """
ALTER TABLE core_attachment DROP CONSTRAINT IF EXISTS core_attachment_event_facility_fk;
ALTER TABLE core_attachment DROP CONSTRAINT IF EXISTS core_attachment_client_facility_fk;
ALTER TABLE core_attachment DROP CONSTRAINT IF EXISTS core_attachment_case_facility_fk;

DROP INDEX IF EXISTS core_event_id_facility_uq;
DROP INDEX IF EXISTS core_client_id_facility_uq;
DROP INDEX IF EXISTS core_case_id_facility_uq;
"""


def guard_no_divergent_owner_facility(apps, schema_editor):
    """Vorwaerts-Preflight: meldet divergente Bestandszeilen fail-loud.

    Ohne diesen Guard scheitert das ``ALTER TABLE`` an der rohen
    FK-Verletzung ("Key (event_id, facility_id)=(…) is not present") — eine
    einzelne Beispielzeile ohne Hinweis, wie viele Zeilen betroffen sind und
    an welcher Owner-Sorte sie haengen. Hier gibt es stattdessen Zahlen je
    Owner-Sorte und den Verweis auf den Reparaturweg.

    ``assert_migration_role_bypasses_rls`` als erste Zeile (Konvention seit
    0123, Refs #1746): unter der App-Rolle wuerde die direkte
    ``facility_isolation``-Policy (0131) ohne gesetztes
    ``app.current_facility_id`` still 0 Zeilen liefern — der Guard meldete
    dann faelschlich "alles sauber", und das ``ALTER TABLE`` liefe doch in die
    rohe Verletzung.
    """
    assert_migration_role_bypasses_rls(schema_editor)
    divergent = {}
    with schema_editor.connection.cursor() as cursor:
        for owner_column, target_table in OWNER_TARGETS:
            # Beide Interpolationen sind Literale aus OWNER_TARGETS, keine Userdaten.
            cursor.execute(
                "SELECT count(*) FROM core_attachment a "
                f"JOIN {target_table} o ON o.id = a.{owner_column} "
                "WHERE a.facility_id <> o.facility_id"
            )
            (count,) = cursor.fetchone()
            if count:
                divergent[target_table] = count
    if divergent:
        details = ", ".join(f"{table}: {count}" for table, count in sorted(divergent.items()))
        raise RuntimeError(
            f"Composite-FK-Haertung abgebrochen: Attachment-Zeilen mit fremder Einrichtung ({details}). "
            "Diese Zeilen waeren unter der direkten RLS-Policy in der falschen Einrichtung sichtbar "
            "(ADR-039 §2). Vor dem erneuten Migrieren pro Zeile entscheiden: facility_id an den Owner "
            "angleichen oder die Zeile samt .enc-Datei entfernen."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0132_clear_retention_days_non_event"),
    ]

    operations = [
        # MUSS die erste Operation bleiben: nur vor dem ``ALTER TABLE`` kann
        # der Preflight die Bestandslage ueberhaupt noch melden. Position per
        # Test gepinnt.
        migrations.RunPython(guard_no_divergent_owner_facility, migrations.RunPython.noop),
        migrations.RunSQL(sql=FORWARD_SQL, reverse_sql=REVERSE_SQL),
    ]
