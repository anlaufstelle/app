"""Apply direct facility isolation to review metadata."""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_freetextreview ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_freetextreview FORCE ROW LEVEL SECURITY;
CREATE POLICY facility_isolation ON core_freetextreview
USING (
    facility_id::text = current_setting('app.current_facility_id', true)
    OR current_setting('app.is_super_admin', true) = 'true'
)
WITH CHECK (
    facility_id::text = current_setting('app.current_facility_id', true)
    OR current_setting('app.is_super_admin', true) = 'true'
);
"""

DISABLE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_freetextreview;
ALTER TABLE core_freetextreview NO FORCE ROW LEVEL SECURITY;
ALTER TABLE core_freetextreview DISABLE ROW LEVEL SECURITY;
"""


class Migration(migrations.Migration):
    dependencies = [("core", "0192_free_text_review")]
    operations = [migrations.RunSQL(ENABLE_SQL, DISABLE_SQL)]
