"""RLS-Policy fuer ``core_rectificationrequest`` (M4 Strang C, Refs #1060).

``RectificationRequest`` (Migration 0138) traegt einen **direkten**
``facility_id``-FK — die Policy ist daher der einfache Spaltenvergleich wie in
[0127_rls_offline_client_revocation.py](0127_rls_offline_client_revocation.py)
bzw. [0131_rls_attachment.py](0131_rls_attachment.py), nicht die
Subquery-Variante der JOIN-Tabellen.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin', true)
= 'true'``) ist in **forward und reverse** enthalten — ohne ihn waere der
super_admin fuer diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``). Der Rueckweg
(``migrate core 0138``) nimmt die Policy vollstaendig zurueck und schaltet RLS
ab: die Tabelle wird hier NEU eingefuehrt (kein Policy-Swap wie in 0131), der
Zustand vor dieser Migration ist also „kein RLS".

Die Abhaengigkeit zeigt bewusst auf 0138 (die Tabelle muss existieren, bevor
``ALTER TABLE`` laeuft); 0138 selbst haengt an 0135 — die parallel entstehenden
Nummern 0136/0137 aus Strang A werden bewusst NICHT referenziert und beim
Merge zentral umgehaengt.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_rectificationrequest ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_rectificationrequest FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_rectificationrequest
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_rectificationrequest;
ALTER TABLE core_rectificationrequest DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0138_rectificationrequest"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
