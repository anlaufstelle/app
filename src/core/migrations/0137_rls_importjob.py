"""RLS-Policy fuer ``core_importjob`` (M4 Strang A, Refs #1060).

``ImportJob`` (Migration 0136) traegt einen **direkten** ``facility_id``-FK —
die Policy ist daher der einfache Spaltenvergleich wie in
[0131_rls_attachment.py](0131_rls_attachment.py) bzw.
[0127_rls_offline_client_revocation.py](0127_rls_offline_client_revocation.py),
nicht die Subquery-Variante der JOIN-Tabellen.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin', true)
= 'true'``) ist von Anfang an enthalten — ohne ihn waere der super_admin fuer
diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``). ``EXPECTED_TABLES`` dort
ist ableitungsbasiert, ``core_importjob`` landet also automatisch in der
Pruefmenge.

``ENABLE`` + ``FORCE ROW LEVEL SECURITY``, Policy-Name ``facility_isolation``.
Der Rueckweg (``migrate core 0135``) nimmt Policy und RLS-Flags wieder zurueck —
die Tabelle selbst raeumt 0136 ab.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_importjob ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_importjob FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_importjob
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_importjob;
ALTER TABLE core_importjob DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0136_importjob"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
