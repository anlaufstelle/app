"""Generate DSGVO documentation package for a facility."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import Facility
from core.services.dsgvo_package import DOCUMENTS, render_document


class Command(BaseCommand):
    help = "Generate DSGVO documentation package (Markdown files) for a facility."

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True, help="Facility name")
        parser.add_argument("--output-dir", default=".", help="Output directory (default: current dir)")

    def handle(self, *args, **options):
        facility_name = options["facility"]
        output_dir = Path(options["output_dir"])

        try:
            facility = Facility.objects.select_related("settings").get(name=facility_name)
        except Facility.DoesNotExist as exc:
            raise CommandError(f'Facility "{facility_name}" not found.') from exc

        output_dir.mkdir(parents=True, exist_ok=True)

        for slug in DOCUMENTS:
            content, filename = render_document(slug, facility)
            filepath = output_dir / filename
            filepath.write_text(content, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"  {filename}"))

        self.stdout.write(self.style.SUCCESS(f"\n{len(DOCUMENTS)} documents generated in {output_dir}"))
