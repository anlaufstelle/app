"""Management-Command: Dokumentationspaket in eine Einrichtung einspielen.

M2 P11 (Refs #1058) — CLI-Fassade um das P10-Paar
:func:`core.packages.schema.parse_package` /
:func:`core.packages.loader.load_package`. Ablauf: Datei lesen → YAML parsen
→ Schema validieren → Loader anwenden → Report ausgeben. ``yaml`` wird
bewusst **nur hier** importiert — Schema und Loader bleiben dict-basiert
(siehe deren Docstrings).

Läuft wie ``seed``/``reencrypt_fields`` auf der Superuser-Verbindung — kein
RLS-Kontext nötig.

Report-Ausgabe: Bezeichner sind **nicht** als eindeutige Schlüssel zu
behandeln. Bei Teiländerungen an Bedingungen (Wholesale-Replace, siehe
``core.packages.loader``) können mehrere Einträge desselben Bezeichners im
selben Bucket auftauchen. Die Ausgabe zählt und listet daher roh
(``len()``/Join über die Report-Listen), ohne über ein Set/Dict zu
deduplizieren.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from django.core.management.base import BaseCommand, CommandError, CommandParser

from core.models.organization import Facility
from core.packages.loader import PackageLoadError, PackageReport, load_package
from core.packages.schema import PackageV1, PackageValidationError, parse_package

# Anwendungsreihenfolge wie ``PackageReport.sections()`` — deutsche Labels
# macht laut P10-Report-Vertrag bewusst die CLI, nicht der Loader.
_SECTION_LABELS: dict[str, str] = {
    "field_templates": "Feldvorlagen",
    "document_types": "Dokumentationstypen",
    "groups": "Feldgruppen",
    "field_refs": "Feldzuordnungen",
    "conditions": "Bedingungen",
}


class Command(BaseCommand):
    help = (
        "Spielt ein Dokumentationspaket (YAML, Schema v1) idempotent in eine "
        "Einrichtung ein. Legt Feldvorlagen, Dokumentationstypen, Feldgruppen, "
        "Feldzuordnungen und Bedingungen an bzw. aktualisiert sie."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", required=True, help="Pfad zur Paket-YAML-Datei.")
        parser.add_argument("--facility", required=True, help="UUID der Ziel-Einrichtung.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Nur simulieren — es wird nichts geschrieben (identischer Code-Pfad, Rollback am Ende).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        facility = self._resolve_facility(options["facility"])
        raw = self._read_package_file(options["file"])
        package = self._parse_package(raw)

        dry_run: bool = bool(options["dry_run"])
        try:
            report = load_package(facility, package, dry_run=dry_run)
        except PackageLoadError as exc:
            # Trotz Rollback: der (verworfene) Report zeigt, was ohne die
            # Fehler angelegt/aktualisiert worden wäre — für die Fehlersuche
            # hilfreich. Gilt unverändert im Dry-Run (der raist ebenfalls).
            self._write_report(exc.report)
            raise CommandError(
                f"Import fehlgeschlagen — {len(exc.report.errors)} Fehler, nichts wurde übernommen."
            ) from exc

        self._write_report(report)

    # ------------------------------------------------------------------
    # Datei / Facility / Schema
    # ------------------------------------------------------------------

    def _resolve_facility(self, raw_uuid: str) -> Facility:
        try:
            facility_id = uuid.UUID(raw_uuid)
        except (ValueError, AttributeError, TypeError) as exc:
            raise CommandError(f"Ungültige Facility-UUID: {raw_uuid!r}.") from exc
        try:
            return Facility.objects.get(id=facility_id)
        except Facility.DoesNotExist as exc:
            raise CommandError(f"Einrichtung mit UUID {raw_uuid!r} nicht gefunden.") from exc

    def _read_package_file(self, path_str: str) -> dict[str, Any]:
        path = Path(path_str)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CommandError(f"Datei {path_str!r} kann nicht gelesen werden: {exc}.") from exc

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise CommandError(f"YAML-Fehler in {path_str!r}:\n{exc}") from exc

        if not isinstance(data, dict):
            raise CommandError(
                f"{path_str!r} enthält kein Mapping auf oberster Ebene (erhalten: {type(data).__name__})."
            )
        return data

    def _parse_package(self, raw: dict[str, Any]) -> PackageV1:
        try:
            return parse_package(raw)
        except PackageValidationError as exc:
            raise CommandError(f"Paket ungültig:\n{exc}") from exc

    # ------------------------------------------------------------------
    # Ausgabe
    # ------------------------------------------------------------------

    def _write_report(self, report: PackageReport) -> None:
        if report.dry_run:
            self.stdout.write(self.style.WARNING("Dry-Run — es wird nichts geschrieben.\n"))

        for name, section in report.sections():
            self.stdout.write(self.style.MIGRATE_HEADING(f"{_SECTION_LABELS[name]}:"))
            self._write_bucket("angelegt", section.created)
            self._write_bucket("aktualisiert", section.updated)
            self._write_bucket("unverändert", section.unchanged)

        self._write_messages("Warnungen", report.warnings, self.style.WARNING)
        self._write_messages("Fehler", report.errors, self.style.ERROR)

        if not report.errors:
            action = "Dry-Run abgeschlossen" if report.dry_run else "Import abgeschlossen"
            self.stdout.write(self.style.SUCCESS(f"\n{action}."))

    def _write_bucket(self, label: str, identifiers: list[str]) -> None:
        # Bewusst KEIN Set/Dict: Bezeichner sind nicht garantiert eindeutig
        # (siehe Moduldocstring) — Zählung (``len``) und Liste (Join) bleiben
        # roh über die Report-Liste, ohne Deduplizierung.
        rendered = ", ".join(identifiers) if identifiers else "–"
        self.stdout.write(f"  {label} ({len(identifiers)}): {rendered}")

    def _write_messages(self, label: str, messages: list[tuple[str, str]], style: Any) -> None:
        if not messages:
            return
        self.stdout.write(style(f"\n{label} ({len(messages)}):"))
        for identifier, message in messages:
            self.stdout.write(f"  {identifier}: {message}")
