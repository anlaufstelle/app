"""Management command: versionierter JSON-Facility-Export (M4 Strang B, Refs #1060).

Duenner Wrapper um :func:`core.services.system.facility_export.build_facility_export`
— alle Business-Logik (Serialisierung, Entschluesselung, Attachment-Hashing)
liegt im Service; der Command uebernimmt nur Argument-Parsing, Facility-
Aufloesung (UUID oder Name, Delta 4), Datei-Schreiben, Audit und
``stdout``-Zusammenfassung.

    python manage.py export_facility --facility <uuid-oder-name> --output facility.json
    python manage.py export_facility --facility "Teststelle" --output facility.json --indent 2
    python manage.py export_facility --facility <uuid> --output facility.json --no-file-hashes

Wie die uebrigen Wartungskommandos MUSS der Lauf mit der Admin-DB-Rolle
(BYPASSRLS) erfolgen — siehe docs/ops-runbook.md § 3.5 / § 9. Ohne Bypass-
Kontext bricht der Command fail-loud ab (E15, Muster ``enforce_retention``),
statt einen leeren, aber scheinbar vollstaendigen Export zu schreiben.
"""

import json
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.models import AuditLog, Facility
from core.services.audit import audit_event

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen
# (Muster ``enforce_retention``/``reencrypt_fields``).
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context
from core.services.system.facility_export import build_facility_export


class Command(BaseCommand):
    help = "Exportiert Konfiguration und fachliches Datenmodell einer Einrichtung als versioniertes JSON (Art. 20)."

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True, help="Facility-UUID oder -Name.")
        parser.add_argument("--output", required=True, help="Zielpfad der JSON-Datei.")
        parser.add_argument(
            "--indent",
            type=int,
            default=None,
            help="JSON-Einrückung (Pretty-Print). Default: kompakt.",
        )
        parser.add_argument(
            "--no-file-hashes",
            action="store_true",
            help="Überspringt das On-the-fly-SHA-256-Hashing der Anhang-Inhalte (schneller bei großen Beständen).",
        )

    def handle(self, *args, **options):
        # E15 (Refs #1060-Plan): Der Command laeuft ausserhalb des Request-
        # Kontexts (kein RLS-scopetes ``app.current_facility_id``) — ohne
        # Bypass-Rolle/GUC liefert jede Query 0 Zeilen und der Export waere
        # eine leere, aber scheinbar vollstaendige Datei. Fail-loud statt
        # stillem Falsch-Erfolg (Muster ``enforce_retention.py``).
        if not _has_rls_bypass_context():
            raise CommandError(
                "export_facility läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch "
                "ohne Export-Datei — sonst würde eine leere, aber scheinbar vollständige "
                "JSON-Datei geschrieben (Refs #1060)."
            )

        facility = self._resolve_facility(options["facility"])
        with_file_hashes = not options["no_file_hashes"]

        export = build_facility_export(facility, with_file_hashes=with_file_hashes)

        output_path = Path(options["output"])
        if output_path.parent != Path():
            output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(export, indent=options["indent"], ensure_ascii=False),
            encoding="utf-8",
        )

        counters = {key: len(value) for key, value in export["entities"].items()}
        # E15: Action.EXPORT (Bestandswert) statt eines neuen Action-Werts
        # (YAGNI, Plan-Delta) — target_type unterscheidet den Facility-Export
        # von anderen EXPORT-Zeilen. detail traegt ausschliesslich Zaehler,
        # keine Pseudonyme/Inhalte (PII-frei, Muster RETENTION_RUN_COMPLETED).
        audit_event(
            AuditLog.Action.EXPORT,
            user=None,
            facility=facility,
            target_type="FacilityExport",
            detail=counters,
        )

        self.stdout.write(self.style.SUCCESS(f"Facility-Export für '{facility.name}' nach {output_path} geschrieben."))
        for key, count in counters.items():
            self.stdout.write(f"  {key}: {count}")

    def _resolve_facility(self, raw: str) -> Facility:
        """Facility ueber UUID-Parse-Versuch, sonst Namens-Lookup (Delta 4).

        Mehrdeutige Namenstreffer (Facility-Namen sind nicht unique constraint-
        gepinnt) sind ein ``CommandError`` statt eines stillen ``.first()``.
        """
        try:
            facility_uuid = uuid.UUID(raw)
        except ValueError:
            facility_uuid = None

        if facility_uuid is not None:
            try:
                return Facility.objects.select_related("organization").get(pk=facility_uuid)
            except Facility.DoesNotExist as exc:
                raise CommandError(f"Facility '{raw}' nicht gefunden.") from exc

        matches = list(Facility.objects.select_related("organization").filter(name=raw)[:2])
        if not matches:
            raise CommandError(f"Facility '{raw}' nicht gefunden.")
        if len(matches) > 1:
            raise CommandError(f"Facility-Name '{raw}' ist mehrdeutig — UUID verwenden.")
        return matches[0]
