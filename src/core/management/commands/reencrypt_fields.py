"""Re-encrypt all encrypted Event/EventHistory/EventAttachment fields with the current primary key.

A4.4 (Refs #1024): Für ``Event.data_json`` werden zusätzlich Plaintext-PII-Felder
(laut document_type ``is_encrypted=True``) nachverschlüsselt — Heal von Altbestand
vor Einführung der Feldverschlüsselung.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Event, EventHistory
from core.models.attachment import EventAttachment
from core.services.file_vault import encrypt_event_data, encrypt_field, is_encrypted_value, safe_decrypt
from core.services.system import bypass_replication_triggers


def _reencrypt_data_json(payload):
    """Re-encrypt every encrypted value in a ``data_json``-style dict.

    Returns ``(new_payload_or_None_if_unchanged, changed_flag)``. Operates on
    a copy, so the caller decides whether to persist.
    """
    if not payload or not isinstance(payload, dict):
        return payload, False
    changed = False
    new_payload = dict(payload)
    for key, value in payload.items():
        if is_encrypted_value(value):
            decrypted = safe_decrypt(value)
            if decrypted != "[verschlüsselt]":
                new_payload[key] = encrypt_field(decrypted)
                changed = True
    return new_payload, changed


class Command(BaseCommand):
    help = (
        "Re-encrypt all encrypted fields with the current primary encryption key — "
        "Event.data_json, EventHistory.data_before/data_after, "
        "EventAttachment.original_filename_encrypted (Refs #783). Heilt zusätzlich "
        "Plaintext-PII in Event.data_json (Refs #1024 A4.4)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be re-encrypted without saving.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        events_updated = self._reencrypt_events(dry_run)
        history_updated = self._reencrypt_event_history(dry_run)
        attachments_updated = self._reencrypt_attachments(dry_run)

        action = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {events_updated} events, {history_updated} history entries, "
                f"{attachments_updated} attachments."
            )
        )

    def _reencrypt_events(self, dry_run: bool) -> int:
        updated = 0
        for event in Event.objects.filter(is_deleted=False).iterator(chunk_size=500):
            new_data, changed = _reencrypt_data_json(event.data_json)
            # A4.4 (Refs #1024): zusätzlich Plaintext-PII-Felder (laut
            # document_type ``is_encrypted=True``) nachverschlüsseln — heilt
            # Altbestand, der vor Einführung der Feldverschlüsselung als Klartext
            # gespeichert wurde. ``encrypt_event_data`` lässt bereits verschlüsselte
            # Werte unangetastet.
            healed = encrypt_event_data(event.document_type, new_data) if event.document_type_id else new_data
            if changed or healed != event.data_json:
                updated += 1
                if not dry_run:
                    event.data_json = healed
                    event.save(update_fields=["data_json", "updated_at"])
        return updated

    def _reencrypt_event_history(self, dry_run: bool) -> int:
        """Refs #783 — EventHistory ist append-only (Trigger ``prevent_eventhistory_update``).

        UPDATE wird per ``bypass_replication_triggers`` umgangen — derselbe
        Pfad, den ``services/clients.anonymize_client`` fuer redaktierte
        History-Eintraege nutzt.
        """
        updated = 0
        for hist in EventHistory.objects.iterator(chunk_size=500):
            before, before_changed = _reencrypt_data_json(hist.data_before)
            after, after_changed = _reencrypt_data_json(hist.data_after)
            if not (before_changed or after_changed):
                continue
            updated += 1
            if dry_run:
                continue
            with transaction.atomic(), bypass_replication_triggers():
                EventHistory.objects.filter(pk=hist.pk).update(
                    data_before=before,
                    data_after=after,
                )
        return updated

    def _reencrypt_attachments(self, dry_run: bool) -> int:
        """``EventAttachment.original_filename_encrypted`` ist ein Fernet-
        verschluesseltes JSON-Marker-Dict. Hier reicht ein normales Save —
        keine append-only-Trigger schuetzen die Tabelle.
        """
        updated = 0
        for att in EventAttachment.objects.iterator(chunk_size=500):
            value = att.original_filename_encrypted
            if not is_encrypted_value(value):
                continue
            decrypted = safe_decrypt(value)
            if decrypted == "[verschlüsselt]":
                continue
            updated += 1
            if dry_run:
                continue
            att.original_filename_encrypted = encrypt_field(decrypted)
            att.save(update_fields=["original_filename_encrypted"])
        return updated
