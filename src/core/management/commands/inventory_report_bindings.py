"""Management command: Registry-Auskunft & Bindungs-Inventar (E2, Refs #1848).

Wertet die Reporting-Registry aus: welcher Berichtsschlüssel steckt in
welchem Backend (Typ- UND Feld-Ebene), und welche Dokumentationstypen/
Feldvorlagen einer Einrichtung tragen ihn — bzw. welche keinen. Dieselbe
Registry-Auskunft, die die Konfigurations-UI unter „Erscheint in …" zeigt
(:func:`core.reporting.registry.describe_type_bindings`/
:func:`~core.reporting.registry.describe_field_bindings`), hier als
Facility-weites Inventar statt Einzelblick.

**Zugleich das Prüfwerkzeug für Etappe E3 Schritt 7** (Konzept #1847): bevor
der Slug-Zweitschlüssel entfernt werden darf, muss das Inventar zeigen, dass
kein Dokumentationstyp mehr AUSSCHLIESSLICH über ihn gebunden ist (Erststufe
— heute ``system_type`` — leer, Treffer nur über den Slug). Genau diese
Menge steht unter ``document_types.slug_only`` (JSON) bzw. als eigene Zeile
im Text-Report; das Gate ist erfüllt, sobald sie leer ist.

**Achtung beim Lesen des Text-/JSON-Reports:** Die Backend-*Verfügbarkeit*
selbst ist NICHT durchgängig auf ``entity_type=event`` beschränkt — nur
``monthly_trend`` prüft das in seiner ``Anforderung`` explizit;
``counseling_hours``/``needle_exchange_balance``/``service_distribution``
zählen jede Entität. Beschränkt auf ``entity_type=event`` sind ausschließlich
diese Inventar-Sektion (``document_types``) und damit das Slug-Zweitschlüssel-
Gate — nicht die Backend-Verfügbarkeit an sich.

    python manage.py inventory_report_bindings --facility <uuid-oder-name>
    python manage.py inventory_report_bindings --facility <uuid> --format json --output inventar.json

Wie die übrigen Wartungskommandos (``export_facility``, ``load_package``,
``purge_orphan_field_data``, ``detect_breaches``) MUSS der Lauf mit der
Admin-DB-Rolle (BYPASSRLS) erfolgen — siehe docs/ops-runbook.md § 3.5 / § 9.
Ohne Bypass-Kontext bricht der Command fail-loud ab (Muster
``enforce_retention``), statt einen scheinbar vollständigen, aber tatsächlich
leeren Report zu schreiben: der Report ist eine POSITIVE Gate-Aussage (E3
Schritt 7) — 0 Dokumentationstypen/Feldvorlagen unter der App-Rolle ohne
``app.current_facility_id``-GUC sähen aus wie ein erfülltes Gate
(``slug_only`` leer), obwohl in Wahrheit gar nichts geprüft wurde. Genau
dieses Falsch-Grün wäre schlimmer als ein Absturz.
"""

import json
import uuid
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from core.models import DocumentType, Facility, FieldTemplate
from core.reporting.registry import (
    describe_field_bindings,
    describe_type_bindings,
    feld_bindungen,
    get_backends,
    typ_bindungen,
)

# Refs #1016/#1070/#1512: zentrale Fail-Loud-Pruefung in services/system/_db_admin —
# als Modul-Name re-exportiert, damit Tests sie auf Command-Ebene patchen koennen
# (Muster ``enforce_retention``/``purge_orphan_field_data``).
from core.services.system import has_rls_bypass_context as _has_rls_bypass_context


def _backend_catalog() -> list[dict[str, Any]]:
    """Typ-/Feld-Berichtsschlüssel + Labels je registriertem Backend.

    Baut auf der E1-API auf (:func:`typ_bindungen`/:func:`feld_bindungen`/
    ``vokabular()``) — keine eigene Auflösungslogik.
    """
    catalog: list[dict[str, Any]] = []
    for backend in get_backends():
        type_keys: dict[str, str] = {}
        for typ_bindung in typ_bindungen(backend):
            type_keys.update({key: str(label) for key, label in typ_bindung.vokabular().items()})
        field_keys: dict[str, str] = {}
        for feld_bindung in feld_bindungen(backend):
            field_keys.update({key: str(label) for key, label in feld_bindung.vokabular().items()})
        catalog.append(
            {
                "slug": backend.slug,
                "name": str(backend.name),
                "type_keys": [{"key": key, "label": label} for key, label in type_keys.items()],
                "field_keys": [{"key": key, "label": label} for key, label in field_keys.items()],
            }
        )
    return catalog


def _document_type_entries(facility: Facility) -> dict[str, list[dict[str, Any]]]:
    """Dokumentationstypen der Einrichtung, gebunden/ungebunden/slug_only.

    NUR ``entity_type=event`` (Plan §3, konsistent mit der Konfig-UI aus E2):
    an Personen-/Fall-Typen ist die Berichtsfrage gegenstandslos.
    """
    bound: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    slug_only: list[dict[str, Any]] = []
    doc_types = (
        DocumentType.objects.for_facility(facility)
        .filter(entity_type=DocumentType.EntityType.EVENT)
        .order_by("sort_order", "name")
    )
    for doc_type in doc_types:
        appears_in = describe_type_bindings(doc_type, facility)
        entry: dict[str, Any] = {
            "id": str(doc_type.id),
            "name": doc_type.name,
            "slug": doc_type.slug,
            "system_type": doc_type.system_type or None,
            "appears_in": appears_in,
        }
        if not appears_in:
            unbound.append(entry)
            continue
        bound.append(entry)
        # E3-Gate (Schritt 7): Erststufe (system_type) leer UND trotzdem ein
        # Treffer -> die Zuordnung kommt AUSSCHLIESSLICH über den
        # Slug-Zweitschlüssel zustande — dieselbe Auflösungsordnung wie
        # TypBindung._eintrag()/resolve_key (E1-API): die Erststufe gewinnt
        # immer, wenn sie gesetzt ist; der Treffer trotz leerer Erststufe
        # kann also nur über den Slug entstanden sein.
        if not doc_type.system_type:
            slug_only.append(entry)
    return {"bound": bound, "unbound": unbound, "slug_only": slug_only}


def _field_template_entries(facility: Facility) -> dict[str, list[dict[str, Any]]]:
    """Feldvorlagen der Einrichtung, gebunden/ungebunden — OHNE entity_type-Filter.

    ``FieldTemplate`` hat kein ``entity_type``-Feld (facility-weit, M:N über
    ``DocumentTypeField``) — die Ereignis-Einschränkung von ``DocumentType``
    gilt hier nicht (dieselbe Abweichung wie in der Konfig-UI, E2).
    """
    bound: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    for field_template in FieldTemplate.objects.for_facility(facility).order_by("name"):
        appears_in = describe_field_bindings(field_template, facility)
        entry: dict[str, Any] = {
            "id": str(field_template.id),
            "name": field_template.name,
            "slug": field_template.slug,
            "appears_in": appears_in,
        }
        (bound if appears_in else unbound).append(entry)
    return {"bound": bound, "unbound": unbound}


def _render_text(data: dict[str, Any]) -> str:
    lines: list[str] = [
        f"Einrichtung: {data['facility']['name']} ({data['facility']['id']})",
        "",
        "Hinweis: Backend-Verfuegbarkeit ist NICHT durchgaengig auf entity_type=event "
        'beschraenkt (nur "Monatsverlauf" prueft das explizit) — nur diese Dokumentationstyp-Sektion '
        "und das Slug-Zweitschluessel-Gate unten sind es.",
        "",
        "Backends:",
    ]
    for backend in data["backends"]:
        lines.append(f"  {backend['slug']} ({backend['name']})")
        if backend["type_keys"]:
            keys = ", ".join(f"{k['key']} ({k['label']})" for k in backend["type_keys"])
            lines.append(f"    Typ-Schlüssel: {keys}")
        if backend["field_keys"]:
            keys = ", ".join(f"{k['key']} ({k['label']})" for k in backend["field_keys"])
            lines.append(f"    Feld-Schlüssel: {keys}")

    doc_types = data["document_types"]
    lines.append("")
    lines.append(
        f"Dokumentationstypen (entity_type=event) — {len(doc_types['bound'])} gebunden, "
        f"{len(doc_types['unbound'])} ungebunden:"
    )
    for entry in doc_types["bound"]:
        lines.append(f"  + {entry['name']} (slug={entry['slug']}) -> {' · '.join(entry['appears_in'])}")
    for entry in doc_types["unbound"]:
        lines.append(f"  - {entry['name']} (slug={entry['slug']})")
    lines.append(f"  davon ausschließlich über den Slug-Zweitschlüssel gebunden: {len(doc_types['slug_only'])}")
    for entry in doc_types["slug_only"]:
        lines.append(f"    * {entry['name']} (slug={entry['slug']})")

    field_templates = data["field_templates"]
    lines.append("")
    lines.append(
        f"Feldvorlagen — {len(field_templates['bound'])} gebunden, {len(field_templates['unbound'])} ungebunden:"
    )
    for entry in field_templates["bound"]:
        lines.append(f"  + {entry['name']} (slug={entry['slug']}) -> {' · '.join(entry['appears_in'])}")
    for entry in field_templates["unbound"]:
        lines.append(f"  - {entry['name']} (slug={entry['slug']})")

    return "\n".join(lines)


class Command(BaseCommand):
    help = (
        "Wertet die Reporting-Registry aus: Backend-Katalog sowie je Einrichtung "
        "gebundene/ungebundene Dokumentationstypen und Feldvorlagen (E2, Refs #1848). "
        "Zugleich Gate-Check für E3 Schritt 7 (Slug-Zweitschlüssel-Entfernung)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--facility", required=True, help="Facility-UUID oder -Name.")
        parser.add_argument(
            "--format",
            choices=["text", "json"],
            default="text",
            help="Ausgabeformat (Default: text).",
        )
        parser.add_argument(
            "--output",
            default=None,
            help="Zielpfad für die Ausgabe. Ohne Angabe wird auf stdout geschrieben.",
        )

    def handle(self, *args, **options):
        # Review-Befund #1848: core_facility ist nicht RLS-gescoped, ``_resolve_facility``
        # gelingt also auch ohne Bypass — aber DocumentType/FieldTemplate stehen unter
        # FORCE RLS. Ohne Bypass-Kontext saehe der Lauf 0 Zeilen und meldete faelschlich
        # ein erfuelltes E3-Gate (slug_only leer). Fail-loud wie enforce_retention/
        # export_facility/purge_orphan_field_data/detect_breaches.
        if not _has_rls_bypass_context():
            raise CommandError(
                "inventory_report_bindings laeuft als RLS-gefilterte App-Rolle ohne Bypass-Kontext "
                "(weder SUPERUSER/BYPASSRLS-Rolle noch app.is_super_admin-GUC). Abbruch — sonst saehe "
                "der Lauf 0 Dokumentationstypen/Feldvorlagen und meldete faelschlich ein erfuelltes "
                "E3-Gate (slug_only leer, obwohl ungeprueft) statt eines tatsaechlich vollstaendigen "
                "Inventars. Command mit der Admin-Rolle aufrufen (docs/ops-runbook.md § 3.5 / § 9 "
                "DB-Rollenmodell)."
            )
        facility = self._resolve_facility(options["facility"])
        data: dict[str, Any] = {
            "facility": {"id": str(facility.id), "name": facility.name},
            "backends": _backend_catalog(),
            "document_types": _document_type_entries(facility),
            "field_templates": _field_template_entries(facility),
        }

        is_json = options["format"] == "json"
        rendered = json.dumps(data, indent=2, ensure_ascii=False) if is_json else _render_text(data)

        output_path = options["output"]
        if output_path:
            Path(output_path).write_text(rendered + "\n", encoding="utf-8")
        else:
            self.stdout.write(rendered)

        # Zaehlerzeile (Muster detect_breaches) NUR im Text-Format: stdout im
        # JSON-Format muss stabil/parsebar bleiben (E3 nutzt --format json als
        # Vorbedingungs-Check, Schritt 7) — die Kennzahl selbst steht dort
        # bereits maschinenlesbar unter ``document_types.slug_only``.
        if not is_json:
            slug_only_count = len(data["document_types"]["slug_only"])
            if slug_only_count:
                self.stdout.write(
                    self.style.WARNING(
                        f"{slug_only_count} Typ(en) ausschließlich über den Slug-Zweitschlüssel gebunden — "
                        "Gate für E3 Schritt 7 (Slug-Zweitschlüssel entfernen) noch nicht erfüllt."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Kein Typ ausschließlich über den Slug-Zweitschlüssel gebunden — Gate für E3 Schritt 7 erfüllt."
                    )
                )

    def _resolve_facility(self, raw: str) -> Facility:
        """Facility über UUID-Parse-Versuch, sonst Namens-Lookup (Muster ``export_facility``).

        Mehrdeutige Namenstreffer (Facility-Namen sind nicht unique constraint-
        gepinnt) sind ein ``CommandError`` statt eines stillen ``.first()``.
        """
        try:
            facility_uuid: uuid.UUID | None = uuid.UUID(raw)
        except ValueError:
            facility_uuid = None

        if facility_uuid is not None:
            try:
                return Facility.objects.get(pk=facility_uuid)
            except Facility.DoesNotExist as exc:
                raise CommandError(f"Facility '{raw}' nicht gefunden.") from exc

        matches = list(Facility.objects.filter(name=raw)[:2])
        if not matches:
            raise CommandError(f"Facility '{raw}' nicht gefunden.")
        if len(matches) > 1:
            raise CommandError(f"Facility-Name '{raw}' ist mehrdeutig — UUID verwenden.")
        return matches[0]
