"""
RLS-Policy fuer core_auditlog erweitern: WITH CHECK erlaubt NULL-facility
(globale System-Events vor dem ersten Login), USING bleibt strikt fuer SELECT.

Refs #863. Adressiert die in Migration 0047 dokumentierte aber nicht
implementierte Annahme, dass NULL-Audit-Logs als system-wide events
moeglich sein muessen (LOGIN_FAILED bei unknown user etc.).
"""

from django.db import migrations

_NEW_POLICY_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_auditlog;
CREATE POLICY facility_isolation ON core_auditlog
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
    )
    WITH CHECK (
        facility_id IS NULL
        OR facility_id::text = current_setting('app.current_facility_id', true)
    );
"""

_REVERSE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_auditlog;
CREATE POLICY facility_isolation ON core_auditlog
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
    );
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0082_klientel_to_person_meta")]

    operations = [
        migrations.RunSQL(sql=_NEW_POLICY_SQL, reverse_sql=_REVERSE_SQL),
    ]
