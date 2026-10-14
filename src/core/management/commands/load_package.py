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

``--default`` (Refs #1883) ersetzt ``--file`` durch den mitgelieferten
Standardkatalog ``core/packages/default.yaml``. Damit bekommt eine
**Bestands**-Einrichtung den Auslieferungsstand, ohne dass der Operator den
Container-Pfad der YAML kennen muss — ``setup_facility`` stattet nur neu
angelegte Einrichtungen aus.

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
from core.packages.catalog import UnknownPackageError, load_package_data
from core.packages.default_catalog import DEFAULT_PACKAGE_PATH
from core.packages.loader import PackageLoadError, PackageReport, load_package
from core.packages.schema import PackageV1, PackageValidationError, parse_package
from core.packages.yaml_io import safe_load_unique
from core.services.facility_bootstrap import foreign_catalog_slugs

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
        parser.add_argument("--file", help="Pfad zur Paket-YAML-Datei.")
        parser.add_argument(
            "--default",
            action="store_true",
            help=(
                "Statt --file den mitgelieferten Standardkatalog "
                f"({DEFAULT_PACKAGE_PATH}) einspielen — der Weg, um eine "
                "Bestands-Einrichtung nachträglich auf den Auslieferungsstand zu bringen."
            ),
        )
        parser.add_argument(
            "--package",
            help="Mitgeliefertes Katalogpaket nach Name einspielen.",
        )
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
        raw, package_name = self._read_source(options)
        package = self._parse_package(raw)

        if package_name is not None and package_name != "default":
            foreign_slugs = foreign_catalog_slugs(facility, package)
            if foreign_slugs:
                rendered = ", ".join(sorted(foreign_slugs))
                raise CommandError(
                    "Domänen-Pakete sind Ersatz, kein Zusatz: die Einrichtung trägt bereits "
                    f"einen anderen Katalog (fremde Slugs: {rendered}). Gegen eine frische "
                    f"Einrichtung laden (setup_facility --package {package_name})."
                )

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

    def _read_source(self, options: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Paketquelle bestimmen: genau eines von Datei, Alias oder Katalogname.

        Refs #1883: ``--default`` schliesst die Bestandsluecke des
        Kaltstart-Tickets — ``setup_facility`` stattet nur NEUE Einrichtungen
        aus, eine bereits vorhandene bekommt den Standardkatalog hierueber,
        ohne dass der Operator den Container-Pfad der ausgelieferten YAML
        kennen muss. Fachlich identisch zu ``--file <…>/default.yaml``:
        derselbe strikte Reader, derselbe Loader, derselbe RLS-Guard oben.
        """
        path_str: str | None = options.get("file")
        use_default: bool = bool(options.get("default"))
        package_name: str | None = options.get("package")
        selected = int(bool(path_str)) + int(use_default) + int(package_name is not None)
        if selected > 1:
            raise CommandError("--file, --default und --package schließen sich aus — genau eine Paketquelle angeben.")
        if use_default:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Standardkatalog: {DEFAULT_PACKAGE_PATH}\n"))
            return load_package_data("default"), "default"
        if package_name is not None:
            try:
                data = load_package_data(package_name)
            except UnknownPackageError as exc:
                raise CommandError(str(exc)) from exc
            except yaml.YAMLError as exc:
                package_path = f"core/packages/{package_name}.yaml"
                raise CommandError(f"YAML-Fehler in {package_path!r}:\n{exc}") from exc
            self.stdout.write(self.style.MIGRATE_HEADING(f"Katalogpaket: core/packages/{package_name}.yaml\n"))
            return data, package_name
        # Reihenfolge ist bewusst: erst der ``--default``-Zweig, dann die
        # Leerpruefung. Ein zusammengefasstes ``if not path_str and not
        # use_default`` narrowt ``path_str`` fuer mypy NICHT auf ``str``
        # (``str | None`` bleibt stehen) — der Aufruf unten waere ein
        # ``arg-type``-Fehler.
        if not path_str:
            raise CommandError(
                f"Keine Paketquelle angegeben — entweder --file <pfad>, --package <name> oder --default "
                f"(mitgelieferter Standardkatalog {DEFAULT_PACKAGE_PATH})."
            )
        return self._read_package_file(path_str), None

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

        # Refs #1934: eigener, neutral
        # eingefaerbter Abschnitt statt einer weiteren Warnung — sonst wuerde
        # das im Normalfall (Paket verwaltet keine Grenze) jeden Lauf
        # zuspammen. Kein ``style``-Callable: die Zeile ist Information, kein
        # Alarm.
        self._write_messages("Unangetastete Bestandsgrenzen", report.preserved, None)
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

    def _write_messages(self, label: str, messages: list[tuple[str, str]], style: Any | None) -> None:
        if not messages:
            return
        header = f"\n{label} ({len(messages)}):"
        self.stdout.write(style(header) if style else header)
        for identifier, message in messages:
            self.stdout.write(f"  {identifier}: {message}")
