"""RLS-Swap fuer ``core_attachment``: Join-Policy → direkte Policy (Refs #1738).

Dritter Schritt der Kette 0129→0130→0131 (ADR-039 §11). Vorlage:
[0127_rls_offline_client_revocation.py](0127_rls_offline_client_revocation.py)
(direkter Spaltenvergleich inkl. Superadmin-Bypass-Branch).

Abweichungen von der Vorlage — hier wird RLS NICHT neu eingefuehrt, sondern
eine bestehende Policy ersetzt:

- Forward: idempotentes ``ENABLE``+``FORCE`` (Flags bestehen seit 0047),
  ``DROP POLICY IF EXISTS`` entfernt die Join-Policy (0047/0085) **auf
  ``core_attachment``** — die Policy folgt der Tabellen-OID, nicht dem
  Altnamen. RunSQL laeuft in einer Transaktion (ACCESS EXCLUSIVE); ein
  Zwischenzustand „RLS an ohne Policy" waere default-deny, nie offen.
- Reverse: stellt die Join-Policy in der **0085-Variante inkl.
  ``is_super_admin``-OR-Branch** wieder her (0047 hatte den Branch nicht —
  sonst faellt ``test_super_admin_bypass_branch_on_all_policies``) und laesst
  RLS bewusst AKTIV (kein ``DISABLE ROW LEVEL SECURITY`` — anders als
  0111/0127, die RLS neu einfuehrten).

0130+0131 duerfen nie in getrennten Releases landen: die USING-Klausel wirkt
implizit als WITH CHECK — unter der Join-Policy lehnt die DB INSERTs mit
``event_id IS NULL`` (client-/case-owned) ab, fail-closed.
"""

from django.db import migrations

ENABLE_SQL = """
ALTER TABLE core_attachment ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_attachment FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS facility_isolation ON core_attachment;
CREATE POLICY facility_isolation ON core_attachment
    USING (
        facility_id::text = current_setting('app.current_facility_id', true)
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""

REVERSE_SQL = """
DROP POLICY IF EXISTS facility_isolation ON core_attachment;
CREATE POLICY facility_isolation ON core_attachment
    USING (
        event_id IN (
            SELECT id FROM core_event
            WHERE facility_id::text = current_setting('app.current_facility_id', true)
        )
        OR current_setting('app.is_super_admin', true) = 'true'
    );
"""


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0130_attachment_owner_facility"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ENABLE_SQL,
            reverse_sql=REVERSE_SQL,
        ),
    ]
