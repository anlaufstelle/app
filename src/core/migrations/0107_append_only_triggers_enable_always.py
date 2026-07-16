"""A1.4 (Refs #1026): Append-Only-Trigger auf ``ENABLE ALWAYS`` haerten.

Die Append-Only-Trigger (``auditlog_immutable`` aus 0024, ``eventhistory_no_update``/
``eventhistory_no_delete`` aus 0012) waren im Default-Modus ``ENABLE`` (``tgenabled
= 'O'``). Ein Trigger im Origin-Modus feuert NICHT, wenn eine Session
``session_replication_role = replica`` setzt — was eine ausreichend privilegierte
Rolle (SET-PARAMETER-GRANT) als Bypass-Vektor nutzen koennte.

``ENABLE ALWAYS`` (``tgenabled = 'A'``) laesst den Trigger auch im ``replica``-Modus
feuern und schliesst diesen Vektor (Defense-in-Depth). Der legitime Wartungspfad
umgeht die Trigger nicht mehr ueber ``replica``, sondern transaktional per
Owner-DDL ``ALTER TABLE ... DISABLE/ENABLE ALWAYS TRIGGER`` (siehe
``core/services/system/_db_admin.py``) — und die Runtime-App-Rolle ist kein Owner
mehr (``deploy/postgres-init/01-app-role.sh``), kann den Trigger also weder
DISABLE-n noch per ``replica`` umgehen.

Deterministisch (reines ``ALTER TABLE``, keine Datenabhaengigkeit); ``reverse_sql``
setzt die Trigger zurueck auf den Default ``ENABLE`` (Origin).
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0106_mfa_setup_deadline")]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE core_auditlog     ENABLE ALWAYS TRIGGER auditlog_immutable;
                ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_update;
                ALTER TABLE core_eventhistory ENABLE ALWAYS TRIGGER eventhistory_no_delete;
            """,
            reverse_sql="""
                ALTER TABLE core_auditlog     ENABLE TRIGGER auditlog_immutable;
                ALTER TABLE core_eventhistory ENABLE TRIGGER eventhistory_no_update;
                ALTER TABLE core_eventhistory ENABLE TRIGGER eventhistory_no_delete;
            """,
        ),
    ]
