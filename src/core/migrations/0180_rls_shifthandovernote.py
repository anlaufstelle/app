"""RLS-Policy fuer ``core_shifthandovernote`` (Refs #2042).

``ShiftHandoverNote`` (Migration 0179) traegt einen **direkten**
``facility_id``-FK — die Policy ist daher der einfache Spaltenvergleich wie in
[0154_rls_banrevision.py](0154_rls_banrevision.py), nicht die Subquery-Variante
der JOIN-Tabellen. ``test_rls.py`` leitet die erwartete Tabellenmenge aus der
Model-Registry ab (Refs #1096): das neue Modell landet dort automatisch, und
ohne diese Migration reisst ``test_rls_drift`` sofort.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin', true)
= 'true'``) ist in **forward und reverse** enthalten — ohne ihn waere der
super_admin fuer diese Tabelle cross-facility blind und
``test_super_admin_bypass_branch_on_all_policies`` failt. Der Rueckweg
(``migrate core 0179``) nimmt die Policy vollstaendig zurueck und schaltet RLS
ab: die Tabelle wird in 0179 NEU eingefuehrt, der Zustand davor ist also
„kein RLS".

Ohne WITH-CHECK-Klausel gilt der USING-Ausdruck auch fuer INSERTs — der
Schreibpfad in ``core.services.case.handover_note.save_note`` schreibt
ausschliesslich die Zeile der eigenen Request-Facility und bleibt damit
policy-konform; der Aufbewahrungslauf (``enforce_handover_notes``) und die
Schluesselrotation laufen als BYPASSRLS-Admin.

Reines ``RunSQL``, kein ``RunPython`` -> kein RLS-Rollen-Guard noetig
(#2114-Muster greift nur fuer ``RunPython``).
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_shifthandovernote ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_shifthandovernote FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_shifthandovernote
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_shifthandovernote;
ALTER TABLE core_shifthandovernote DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0179_shifthandovernote"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
