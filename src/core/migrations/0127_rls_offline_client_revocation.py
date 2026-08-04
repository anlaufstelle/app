"""RLS-Policy fuer ``core_offlineclientrevocation`` (Refs #1760).

``OfflineClientRevocation`` (Migration 0126) traegt einen **direkten**
``facility_id``-FK — die Policy ist daher der einfache Spaltenvergleich wie in
[0047_postgres_rls_setup.py](0047_postgres_rls_setup.py) ``DIRECT_TABLES``, nicht
die Subquery-Variante der JOIN-Tabellen.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin', true) =
'true'``) ist von Anfang an enthalten — ohne ihn waere der super_admin fuer
diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``).

``ENABLE`` + ``FORCE ROW LEVEL SECURITY``, Policy-Name ``facility_isolation``.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_offlineclientrevocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_offlineclientrevocation FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_offlineclientrevocation
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_offlineclientrevocation;
ALTER TABLE core_offlineclientrevocation DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0126_offline_client_revocation"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
