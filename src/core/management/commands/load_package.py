"""Management-Command: Dokumentationspaket in eine Einrichtung einspielen.

M2 P11 (Refs #1058) — CLI-Fassade um das P10-Paar
:func:`core.packages.schema.parse_package` /
:func:`core.packages.loader.load_package`. Ablauf: Datei lesen → YAML parsen
→ Schema validieren → Loader anwenden → Report ausgeben. ``yaml`` wird
bewusst **nur hier** importiert — Schema und Loader bleiben dict-basiert
(siehe deren Docstrings).

Rollen-Voraussetzung (Final-Review A, Refs #1058): der Command schreibt in
fünf FORCE-RLS-Tabellen und MUSS deshalb mit einer Rolle laufen, die RLS
umgehen darf (``POSTGRES_ADMIN_USER``/BYPASSRLS — siehe
``docs/ops-runbook.md`` § 9.5). Als reine App-Rolle (NOBYPASSRLS, kein
``app.current_facility_id``) sähe der Loader 0 Bestandszeilen und stürbe beim
ersten INSERT an einer ``ProgrammingError``. :func:`has_rls_bypass_context`
prüft das vorab und bricht fail-loud ab — Muster der Schwester-Commands
(``enforce_retention`` Refs #1016, ``detect_breaches`` Refs #1512).

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
from core.packages.yaml_io import safe_load_unique

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen.
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context

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
        # Final-Review A (Refs #1058): fail-loud, statt RLS-blind gegen 0
        # Bestandszeilen zu laufen und beim ersten INSERT mit einem rohen
        # ProgrammingError-Traceback zu sterben (die Policies auf
        # core_documenttype/-fieldtemplate/-documenttypefield/-fieldgroup/
        # -fieldcondition haben kein eigenes WITH CHECK, die USING-Klausel
        # greift also auch beim INSERT). Gilt auch im Dry-Run: der liest
        # denselben Bestand und meldete sonst jedes Objekt als „angelegt".
        if not _has_rls_bypass_context():
            raise CommandError(
                "Paket-Import läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — "
                "sonst blieben alle Bestands-Lookups leer und der erste Schreibzugriff "
                "scheiterte an der Row-Level-Security. Command mit der Admin-Rolle "
                "aufrufen: docker compose exec -e POSTGRES_USER=$POSTGRES_ADMIN_USER "
                "-e POSTGRES_PASSWORD=$POSTGRES_ADMIN_PASSWORD web python manage.py load_package … "
                "(siehe docs/ops-runbook.md § 9.5)."
            )
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
            # Härtung Refs #1727: Duplikat-Keys sind ein Fehler (kein stiller
            # Last-Write-Wins). ``DuplicateKeyError`` ist eine ``yaml.YAMLError``-
            # Subklasse und wird von derselben except-Klausel als CommandError
            # gemeldet.
            data = safe_load_unique(text)
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
