"""DB-Trigger: FieldGroup/FieldTemplate.slug ist unveraenderlich (M2, Refs #1058, #1709).

Vorbild ``0024_auditlog_immutable_trigger.py`` (Funktion + Trigger) kombiniert
mit der ``ENABLE ALWAYS``-Haertung aus ``0107_append_only_triggers_enable_always.py``
(direkt statt als Nachtrag, da hier von Anfang an bekannt: der Slug ist der
M2-Paketformat-Identifier, ein Replica-Bypass-Vektor waere von Tag 1 ein
Risiko). Die Modelle (``document_type.py``) blocken Slug-Aenderungen bereits
Python-seitig in ``save()`` — dieser Trigger schliesst die ORM-Bypasses
(``QuerySet.update()``, ``bulk_update()``, Raw SQL, Admin-Bulk-Actions,
kuenftige Migrationen), die an ``save()`` vorbeigehen.

``IS DISTINCT FROM`` (nicht ``!=``) ist Pflicht: der Seed nutzt
``update_or_create(document_type=…, slug=…)`` (bzw. ``facility=…, slug=…``)
— eine UPDATE-Anweisung, die den Slug auf seinen BISHERIGEN Wert setzt, muss
durchgelassen werden (keine Aenderung liegt vor). ``!=`` waere hier zwar
Wert-aequivalent (kein NULL im Spiel, ``slug`` ist ``NOT NULL``), aber
``IS DISTINCT FROM`` ist der im Repo etablierte NULL-sichere Standard fuer
Aenderungs-Checks in Triggern.

Bewusst NICHT in ``core.services.system._db_admin._APPEND_ONLY_TRIGGERS`` /
``bypass_replication_triggers`` aufgenommen: die Slug-Immutabilitaet soll
AUCH fuer Wartungs-/Import-Pfade gelten, die die Append-Only-Trigger
umgehen (``prune_auditlog``, ``anonymize``, ``backfill_audit_chain``,
``reencrypt_fields``) — neue Slugs entstehen dort ohnehin nur per INSERT,
nie per UPDATE.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0115_global_block_sequence")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION prevent_slug_mutation()
                RETURNS TRIGGER AS $$
                BEGIN
                    IF NEW.slug IS DISTINCT FROM OLD.slug THEN
                        RAISE EXCEPTION 'FieldGroup/FieldTemplate slug is immutable (M2 package format identifier).';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER fieldgroup_slug_immutable
                BEFORE UPDATE ON core_fieldgroup
                FOR EACH ROW EXECUTE FUNCTION prevent_slug_mutation();

                CREATE TRIGGER fieldtemplate_slug_immutable
                BEFORE UPDATE ON core_fieldtemplate
                FOR EACH ROW EXECUTE FUNCTION prevent_slug_mutation();

                ALTER TABLE core_fieldgroup    ENABLE ALWAYS TRIGGER fieldgroup_slug_immutable;
                ALTER TABLE core_fieldtemplate ENABLE ALWAYS TRIGGER fieldtemplate_slug_immutable;
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS fieldgroup_slug_immutable ON core_fieldgroup;
                DROP TRIGGER IF EXISTS fieldtemplate_slug_immutable ON core_fieldtemplate;
                DROP FUNCTION IF EXISTS prevent_slug_mutation();
            """,
        ),
    ]
