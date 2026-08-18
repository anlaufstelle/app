"""RLS-Policy fuer ``core_banrevision`` (Refs #1917).

``BanRevision`` (Migration 0153) traegt einen **direkten** ``facility_id``-FK
(OneToOne) — die Policy ist daher der einfache Spaltenvergleich wie in
[0139_rls_rectificationrequest.py](0139_rls_rectificationrequest.py), nicht
die Subquery-Variante der JOIN-Tabellen.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin', true)
= 'true'``) ist in **forward und reverse** enthalten — ohne ihn waere der
super_admin fuer diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``). Der Rueckweg
(``migrate core 0153``) nimmt die Policy vollstaendig zurueck und schaltet RLS
ab: die Tabelle wird hier NEU eingefuehrt, der Zustand vor dieser Migration
ist also „kein RLS".

Ohne WITH-CHECK-Klausel gilt der USING-Ausdruck auch fuer INSERTs — der
Lazy-Row-Create in ``core.services.system.ban_revision.bump_ban_revision``
schreibt ausschliesslich die Zeile der eigenen Request-Facility und bleibt
damit policy-konform; Retention-/Management-Pfade laufen als BYPASSRLS-Admin.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_banrevision ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_banrevision FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_banrevision
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_banrevision;
ALTER TABLE core_banrevision DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0153_banrevision"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
