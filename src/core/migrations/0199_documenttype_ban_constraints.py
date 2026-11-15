"""Database backstop: a ban document type names a person and lives on events.

Two CHECK constraints on the type and three triggers carry the person rule:

* ``event_ban_requires_person_insert`` refuses a new event without a person
  whose stored type is a ban, on every INSERT (``save``, ``bulk_create``, raw
  SQL, Django's UPDATE-then-INSERT fallback).
* ``event_ban_requires_person_update`` refuses an UPDATE that leaves a row
  without a person and either moves it to a ban type or removes its person.
  An existing anonymous ban that keeps its type and stays without a person
  can still be saved and deactivated.
* ``documenttype_ban_without_personless_events`` refuses to make a type a ban
  while events of that type have no person.

The event triggers read the type row ``FOR SHARE``: a concurrent change of the
type waits, and under READ COMMITTED the trigger then reads the committed
state. A type row the trigger cannot read (row-level security of the
facility) counts as a refusal, never as "not a ban". The table and column
names are those of ``Event._meta`` and ``DocumentType._meta``. All three
triggers are ``ENABLE ALWAYS`` like ``0116``, so a replica session does not
skip them.

No data step: there is no existing data to repair, and an install that still
holds a ban type without a stage, or on a non-event type, fails here on purpose
instead of being rewritten silently. Rolling back drops the constraints, the
triggers and their functions.
"""

from django.db import migrations, models

TRIGGER_SQL = """
    CREATE OR REPLACE FUNCTION event_ban_requires_person() RETURNS trigger AS $$
    DECLARE
        stored_system_type text;
    BEGIN
        SELECT system_type INTO stored_system_type
          FROM core_documenttype
         WHERE id = NEW.document_type_id
           FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'event_ban_requires_person: the type % of an event without a person is not readable',
                NEW.document_type_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF stored_system_type = 'ban' THEN
            RAISE EXCEPTION 'event_ban_requires_person: a ban event must name a person'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER event_ban_requires_person_insert
    BEFORE INSERT ON core_event
    FOR EACH ROW
    WHEN (NEW.client_id IS NULL)
    EXECUTE FUNCTION event_ban_requires_person();

    CREATE TRIGGER event_ban_requires_person_update
    BEFORE UPDATE OF document_type_id, client_id ON core_event
    FOR EACH ROW
    WHEN (
        NEW.client_id IS NULL
        AND (OLD.document_type_id IS DISTINCT FROM NEW.document_type_id OR OLD.client_id IS NOT NULL)
    )
    EXECUTE FUNCTION event_ban_requires_person();

    CREATE OR REPLACE FUNCTION documenttype_ban_rejects_personless_events() RETURNS trigger AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM core_event WHERE document_type_id = NEW.id AND client_id IS NULL) THEN
            RAISE EXCEPTION 'documenttype_ban_without_personless_events: type % has events without a person',
                NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER documenttype_ban_without_personless_events
    BEFORE UPDATE OF system_type ON core_documenttype
    FOR EACH ROW
    WHEN (NEW.system_type = 'ban' AND OLD.system_type IS DISTINCT FROM NEW.system_type)
    EXECUTE FUNCTION documenttype_ban_rejects_personless_events();

    ALTER TABLE core_event ENABLE ALWAYS TRIGGER event_ban_requires_person_insert;
    ALTER TABLE core_event ENABLE ALWAYS TRIGGER event_ban_requires_person_update;
    ALTER TABLE core_documenttype ENABLE ALWAYS TRIGGER documenttype_ban_without_personless_events;
"""

REVERSE_TRIGGER_SQL = """
    DROP TRIGGER IF EXISTS event_ban_requires_person_insert ON core_event;
    DROP TRIGGER IF EXISTS event_ban_requires_person_update ON core_event;
    DROP TRIGGER IF EXISTS documenttype_ban_without_personless_events ON core_documenttype;
    DROP FUNCTION IF EXISTS event_ban_requires_person();
    DROP FUNCTION IF EXISTS documenttype_ban_rejects_personless_events();
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0198_user_ban_details_fingerprint")]

    operations = [
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("system_type", "ban"), _negated=True),
                    models.Q(
                        ("min_contact_stage__in", ["identified", "qualified"]), ("min_contact_stage__isnull", False)
                    ),
                    _connector="OR",
                ),
                name="documenttype_ban_requires_contact_stage",
            ),
        ),
        migrations.AddConstraint(
            model_name="documenttype",
            constraint=models.CheckConstraint(
                condition=models.Q(("system_type", "ban"), _negated=True) | models.Q(("entity_type", "event")),
                name="documenttype_ban_only_on_events",
            ),
        ),
        migrations.RunSQL(sql=TRIGGER_SQL, reverse_sql=REVERSE_TRIGGER_SQL),
    ]
