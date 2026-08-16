"""Read-only inventory of stored values outside configured numeric ranges (Refs #1914)."""

from __future__ import annotations

import math
import uuid
from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from core.models import Case, Client, DocumentTypeField, Event, Facility, FieldTemplate
from core.services.file_vault import safe_decrypt
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


def _normalize_number_value(value):
    """Normalisiere JSON- und entschlüsselte Zahlen auf ``int``/``float``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        try:
            return int(raw)
        except ValueError:
            try:
                parsed = float(raw)
            except ValueError:
                return None
    else:
        return None
    if isinstance(parsed, float) and not math.isfinite(parsed):
        return None
    return int(parsed) if isinstance(parsed, float) and parsed.is_integer() else parsed


def _range_violations(entity, templates_by_document_type):
    """Yield ``(field_slug, reason)`` without exposing individual field values."""
    if entity.document_type_id is None or not entity.data_json:
        return
    templates = templates_by_document_type.get(entity.document_type_id, {})
    for slug, stored_value in entity.data_json.items():
        template = templates.get(slug)
        if template is None or template.field_type != FieldTemplate.FieldType.NUMBER:
            continue
        if template.min_value is None and template.max_value is None:
            continue
        value = _normalize_number_value(safe_decrypt(stored_value, default=None))
        if value is None:
            yield slug, "keine Zahl"
        elif template.min_value is not None and value < template.min_value:
            yield slug, f"unter Minimalwert {template.min_value}"
        elif template.max_value is not None and value > template.max_value:
            yield slug, f"über Maximalwert {template.max_value}"


class Command(BaseCommand):
    help = (
        "Inventarisiert gespeicherte dynamische Zahlenwerte außerhalb der konfigurierten "
        "Minimal-/Maximalwerte, ohne Werte zu verändern (Refs #1914)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True, help="Facility-UUID oder -Name.")

    def handle(self, *args, **options):
        if not _has_rls_bypass_context():
            raise CommandError(
                "inventory_number_ranges läuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext. "
                "Abbruch — sonst wäre das Inventar unvollständig. Command mit der Admin-Rolle aufrufen."
            )
        facility = self._resolve_facility(options["facility"])
        findings: Counter[tuple[str, str, str, str]] = Counter()
        templates_by_document_type: dict = {}
        for assignment in DocumentTypeField.objects.filter(
            document_type__facility=facility,
            field_template__field_type=FieldTemplate.FieldType.NUMBER,
        ).select_related("field_template"):
            template = assignment.field_template
            if template.min_value is None and template.max_value is None:
                continue
            templates_by_document_type.setdefault(assignment.document_type_id, {})[template.slug] = template
        for label, model in (("Personen", Client), ("Fälle", Case), ("Ereignisse", Event)):
            queryset = model.objects.filter(
                facility=facility,
                document_type__isnull=False,
            ).select_related("document_type")
            for entity in queryset.iterator(chunk_size=500):
                for slug, reason in _range_violations(entity, templates_by_document_type) or ():
                    findings[(label, entity.document_type.name, slug, reason)] += 1

        if not findings:
            self.stdout.write(self.style.SUCCESS("Keine Werte außerhalb konfigurierter Zahlenbereiche gefunden."))
            return
        self.stdout.write(self.style.WARNING("Gefundene Werte außerhalb konfigurierter Zahlenbereiche:"))
        for (label, document_type, slug, reason), count in sorted(findings.items()):
            self.stdout.write(f"- {label} · {document_type} · {slug}: {count} ({reason})")

    @staticmethod
    def _resolve_facility(raw: str) -> Facility:
        try:
            facility_id = uuid.UUID(raw)
        except ValueError:
            facility_id = None
        if facility_id is not None:
            try:
                return Facility.objects.get(pk=facility_id)
            except Facility.DoesNotExist as exc:
                raise CommandError(f"Facility '{raw}' nicht gefunden.") from exc
        facilities = list(Facility.objects.filter(name=raw)[:2])
        if not facilities:
            raise CommandError(f"Facility '{raw}' nicht gefunden.")
        if len(facilities) > 1:
            raise CommandError(f"Facility-Name '{raw}' ist mehrdeutig — UUID verwenden.")
        return facilities[0]
