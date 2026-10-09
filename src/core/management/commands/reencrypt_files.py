"""Rotate attachment ciphertext through the privileged maintenance process."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from core.services.file_vault.file_rotation import rotate_files
from core.utils.migration_guards import connection_bypasses_rls


class Command(BaseCommand):
    help = "Re-encrypt attachment files with the primary encryption key."

    def handle(self, *args, **options):
        if os.environ.get("ANLAUFSTELLE_PROCESS_ROLE", "runtime").strip().lower() != "maintenance":
            raise CommandError(
                "File re-encryption requires the maintenance process. "
                "Use docker compose -f docker-compose.prod.yml run --rm maintenance reencrypt_files."
            )
        if connection.vendor != "postgresql" or not connection_bypasses_rls(connection):
            raise CommandError(
                "File re-encryption requires a SUPERUSER or BYPASSRLS database role in maintenance. "
                "app.is_super_admin does not authorize this operation; refusing an RLS-filtered zero-row run."
            )
        result = rotate_files()
        self.stdout.write(
            f"checked={result.checked}, rotated={result.rotated}, already_primary={result.already_primary}, "
            f"deleted={result.deleted}, would_rotate={result.would_rotate}, errors={len(result.errors)}"
        )
