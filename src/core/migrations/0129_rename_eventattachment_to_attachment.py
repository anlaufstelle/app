"""RenameModel ``EventAttachment`` → ``Attachment`` (M24, Refs #1738).

Reines Rename (Tabelle ``core_eventattachment`` → ``core_attachment``) — der
erste Schritt der dreistufigen Kette aus [ADR-039](../../../docs/adr/039-owner-generische-dateianhaenge.md)
§11 (0129 Rename → 0130 Owner/Facility → 0131 RLS-Swap).

Bewusst KEIN Policy-Handling hier: RLS-Flags, Policies und GRANTs haengen an
der Tabellen-OID und **ueberleben das Rename** — die Join-Policy aus 0047/0085
bleibt bis 0131 aktiv (lueckenlos fail-closed). Der Self-FK ``superseded_by``
wird von Postgres mit umgezogen (Test-Assertion in
``tests/migrations/test_0129_rename_eventattachment_to_attachment.py``).
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0128_backfill_snapshot_person_counts"),
    ]

    operations = [
        migrations.RenameModel(old_name="EventAttachment", new_name="Attachment"),
    ]
