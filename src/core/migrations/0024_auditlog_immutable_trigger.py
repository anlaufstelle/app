from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0023_audit_detail_convert_text_to_json")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION prevent_auditlog_mutation()
                RETURNS TRIGGER AS $$
                BEGIN
                    RAISE EXCEPTION 'AuditLog entries are immutable and cannot be modified or deleted.';
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER auditlog_immutable
                BEFORE UPDATE OR DELETE ON core_auditlog
                FOR EACH ROW EXECUTE FUNCTION prevent_auditlog_mutation();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS auditlog_immutable ON core_auditlog;
                DROP FUNCTION IF EXISTS prevent_auditlog_mutation();
            """,
        ),
    ]
