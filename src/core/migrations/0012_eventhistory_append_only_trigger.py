from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0011_fieldtemplate_unique_name")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION prevent_eventhistory_update()
                RETURNS TRIGGER AS $$
                BEGIN
                    RAISE EXCEPTION 'EventHistory records are append-only — UPDATE not allowed';
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER eventhistory_no_update
                BEFORE UPDATE ON core_eventhistory
                FOR EACH ROW
                EXECUTE FUNCTION prevent_eventhistory_update();

                CREATE OR REPLACE FUNCTION prevent_eventhistory_delete()
                RETURNS TRIGGER AS $$
                BEGIN
                    RAISE EXCEPTION 'EventHistory records are append-only — DELETE not allowed';
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER eventhistory_no_delete
                BEFORE DELETE ON core_eventhistory
                FOR EACH ROW
                EXECUTE FUNCTION prevent_eventhistory_delete();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS eventhistory_no_update ON core_eventhistory;
                DROP TRIGGER IF EXISTS eventhistory_no_delete ON core_eventhistory;
                DROP FUNCTION IF EXISTS prevent_eventhistory_update();
                DROP FUNCTION IF EXISTS prevent_eventhistory_delete();
            """,
        ),
    ]
