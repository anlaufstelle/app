"""Management command to re-encrypt all encrypted Event fields with the current key."""

from django.core.management.base import BaseCommand

from core.models import Event
from core.services.encryption import encrypt_field, is_encrypted_value, safe_decrypt


class Command(BaseCommand):
    help = "Re-encrypt all encrypted fields with the current primary encryption key."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show what would be re-encrypted without saving.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        updated = 0

        for event in Event.objects.filter(is_deleted=False).iterator(chunk_size=500):
            if not event.data_json:
                continue
            changed = False
            for key, value in event.data_json.items():
                if is_encrypted_value(value):
                    decrypted = safe_decrypt(value)
                    if decrypted != "[verschlüsselt]":
                        event.data_json[key] = encrypt_field(decrypted)
                        changed = True
            if changed:
                updated += 1
                if not dry_run:
                    event.save(update_fields=["data_json", "updated_at"])

        action = "Would re-encrypt" if dry_run else "Re-encrypted"
        self.stdout.write(self.style.SUCCESS(f"{action} {updated} events."))
