"""Rebuild event search indexes through the privileged maintenance context."""

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.models import Facility
from core.services.events.fields import rebuild_event_search_text
from core.services.system import has_rls_bypass_context


class Command(BaseCommand):
    help = "Rebuild event search indexes using effective Normal sensitivity."

    def add_arguments(self, parser):
        parser.add_argument("--facility", type=UUID, help="Restrict processing to one facility UUID.")
        parser.add_argument("--dry-run", action="store_true", help="Report index changes without writing them.")

    def handle(self, *args, **options):
        if not has_rls_bypass_context():
            raise CommandError(
                "Search index rebuilding requires an RLS bypass context. "
                "Run with the admin database role; refusing an RLS-filtered zero-row run."
            )
        # Installation-wide enumeration is intentional for this maintenance command.
        facilities = Facility.objects.all().order_by("pk")
        if options["facility"] is not None:
            facilities = facilities.filter(pk=options["facility"])
            if not facilities.exists():
                raise CommandError(f"Facility {options['facility']} not found.")
        checked = changed = 0
        for facility in facilities.iterator(chunk_size=100):
            counts = rebuild_event_search_text(facility, dry_run=options["dry_run"])
            checked += counts[0]
            changed += counts[1]
        prefix = "Would rebuild" if options["dry_run"] else "Rebuilt"
        self.stdout.write(f"{prefix} event search indexes: checked={checked}, changed={changed}.")
