"""RLS-Policy fuer ``core_fieldgroup`` (M0, Refs #1056).

``FieldGroup`` (Migration 0110) hat keine eigene ``facility_id``-Spalte — der
Scope ergibt sich transitiv aus ``document_type.facility_id``. Die Policy
laueft daher als Subquery, analog zu ``core_documenttypefield`` in
[0063_rls_outcome_milestone_dtf.py](0063_rls_outcome_milestone_dtf.py).

Der Superadmin-Bypass-OR-Branch (``current_setting('app.is_super_admin',
true) = 'true'``) ist von Anfang an enthalten — anders als bei 0063, wo er
erst in 0091 nachgezogen wurde. Ohne ihn waere der super_admin (Refs #867) fuer
diese Tabelle cross-facility blind und ``test_rls.py`` failt
(``test_super_admin_bypass_branch_on_all_policies``).

``ENABLE`` + ``FORCE ROW LEVEL SECURITY``, Policy-Name ``facility_isolation``.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_fieldgroup ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_fieldgroup FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_fieldgroup
    USING (
        document_type_id IN (
            SELECT id FROM core_documenttype
            WHERE facility_id::text = current_setting('app.current_facility_id', true)
        )
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_fieldgroup;
ALTER TABLE core_fieldgroup DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0110_fieldgroup"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
