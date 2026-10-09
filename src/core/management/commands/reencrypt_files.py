"""Rotate attachment ciphertext through the privileged maintenance process."""

import os
from argparse import ArgumentTypeError
from time import monotonic
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection

from core.models import AuditLog
from core.services.audit import audit_event
from core.services.file_vault.encryption import EncryptionError, EncryptionKeyMissing
from core.services.file_vault.file_rotation import (
    FileRotationLockedError,
    FileRotationManifestError,
    rotate_files,
    verify_files,
)
from core.utils.migration_guards import connection_bypasses_rls


def _positive_batch_size(value: str) -> int:
    try:
        size = int(value)
    except ValueError as exc:
        raise ArgumentTypeError("batch-size must be an integer") from exc
    if size < 1:
        raise ArgumentTypeError("batch-size must be at least 1")
    return size


class Command(BaseCommand):
    help = "Re-encrypt attachment files with the primary encryption key."

    def add_arguments(self, parser):
        parser.add_argument("--facility", type=UUID, help="Restrict processing to one facility UUID.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Check rotation without changing ciphertext or progress."
        )
        parser.add_argument(
            "--batch-size", type=_positive_batch_size, default=200, help="Database iterator batch size."
        )
        parser.add_argument(
            "--verify", action="store_true", help="Read all referenced versions using only the primary key."
        )

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
        facility_id = str(options["facility"]) if options["facility"] is not None else None
        if facility_id is not None:
            self.stderr.write(
                "A --facility run does not prove installation-wide key removability. "
                "Run --verify without --facility before removing an old key."
            )
        started = monotonic()
        result = None
        try:
            if options["verify"]:
                result = verify_files(facility_id=facility_id, batch_size=options["batch_size"])
            else:
                result = rotate_files(
                    facility_id=facility_id, batch_size=options["batch_size"], dry_run=options["dry_run"]
                )
        except EncryptionKeyMissing as exc:
            raise CommandError("No encryption key configured.") from exc
        except (FileRotationLockedError, FileRotationManifestError, EncryptionError, ValueError) as exc:
            raise CommandError(f"File re-encryption stopped: {exc}") from exc
        except OSError as exc:
            raise CommandError(f"File re-encryption stopped: {type(exc).__name__}") from exc
        finally:
            self._record_run(options, facility_id, started, result)
        self.stdout.write(
            f"checked={result.checked}, rotated={result.rotated}, already_primary={result.already_primary}, "
            f"deleted={result.deleted}, would_rotate={result.would_rotate}, errors={len(result.errors)}"
        )
        if result.errors:
            for attachment_id, reason in result.errors.items():
                self.stderr.write(f"{attachment_id}: {reason}")
            operation = "Primary-key verification" if options["verify"] else "File re-encryption"
            raise CommandError(f"{operation} failed for {len(result.errors)} attachment(s).")

    def _record_run(self, options, facility_id, started, result):
        summary = {
            "duration_seconds": round(monotonic() - started, 6),
            "checked": result.checked if result is not None else None,
            "rotated": result.rotated if result is not None else None,
            "already_primary": result.already_primary if result is not None else None,
            "deleted": result.deleted if result is not None else None,
            "would_rotate": result.would_rotate if result is not None else None,
            "error_count": len(result.errors) if result is not None else 1,
            "facility_id": facility_id,
            "dry_run": options["dry_run"],
            "verify": options["verify"],
            "batch_size": options["batch_size"],
            "status": "completed" if result is not None and not result.errors else "failed",
        }
        try:
            audit_event(
                AuditLog.Action.FILE_REENCRYPTION_COMPLETED,
                user=None,
                facility=None,
                target_type="Attachment",
                detail={"file_reencryption": summary},
            )
        except DatabaseError as exc:
            raise CommandError(
                "File re-encryption audit could not be written; keep old keys and repeat verification."
            ) from exc
