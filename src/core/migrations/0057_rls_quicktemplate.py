"""Zieht die RLS-Policy fuer ``core_quicktemplate`` nach.

``QuickTemplate`` wurde in Migration ``0050_quick_template`` eingefuehrt, aber
versehentlich nicht zur RLS-Setup-Migration (``0047_postgres_rls_setup``)
hinzugefuegt — Defense-in-Depth-Luecke beim Cross-Facility-Scoping.

Policy analog zu den uebrigen direkt gescopten Tabellen: ``facility_id``
wird gegen die Session-Variable ``app.current_facility_id`` verglichen, die
die ``FacilityScopeMiddleware`` pro Request via ``set_config(..., false)``
setzt.

Refs #598 (Audit 2026-04-21, Finding S-1), #599 (Phase-1-Umsetzung),
#600 (Retro-Audit weiterer Modelle).
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_quicktemplate ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_quicktemplate FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_quicktemplate
    USING (facility_id::text = current_setting('app.current_facility_id', true));
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_quicktemplate;
ALTER TABLE core_quicktemplate DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0056_validate_trigram_threshold"),
        ("core", "0050_quick_template"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=DISABLE_SQL,
        ),
    ]
