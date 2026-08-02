"""RLS-Policy fuer ``core_fieldcondition`` (M1, Refs #1057).

``FieldCondition`` (Migration 0113) hat keine eigene ``facility_id``-Spalte —
der Scope ergibt sich doppelt-transitiv ueber ``target_field`` (FK auf
``core_documenttypefield``) -> ``document_type_id`` (FK auf
``core_documenttype``) -> ``facility_id``. Das Muster ist analog zu
``core_milestone`` (doppelt-transitiv, [0063_rls_outcome_milestone_dtf.py]
(0063_rls_outcome_milestone_dtf.py)), aber mit ``core_documenttypefield``
als Zwischentabelle statt ``core_outcomegoal``.

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin',
true) = 'true'``) ist von Anfang an enthalten — wie in
[0111_rls_fieldgroup.py](0111_rls_fieldgroup.py) und anders als 0063, wo er
erst in 0091 nachgezogen wurde. Ohne ihn waere der super_admin (Refs #867)
fuer diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``).

``ENABLE`` + ``FORCE ROW LEVEL SECURITY``, Policy-Name ``facility_isolation``.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_fieldcondition ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_fieldcondition FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_fieldcondition
    USING (
        target_field_id IN (
            SELECT id FROM core_documenttypefield WHERE document_type_id IN (
                SELECT id FROM core_documenttype
                WHERE facility_id::text = current_setting('app.current_facility_id', true)
            )
        )
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_fieldcondition;
ALTER TABLE core_fieldcondition DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0113_fieldcondition"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
