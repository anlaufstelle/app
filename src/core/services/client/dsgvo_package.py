"""DSGVO documentation package service."""

import hashlib
import json
import logging
from datetime import date
from pathlib import Path

from django.conf import settings as django_settings
from django.utils import timezone

from core.models import DocumentType, Settings
from core.services.facility import resolve_facility_display_name

logger = logging.getLogger(__name__)

# Refs #784 — Templates leben jetzt im App-Paket (``core/dsgvo_templates/``),
# damit sie im Docker-Image enthalten sind. Frueher zeigte TEMPLATE_DIR
# auf ``<repo>/docs/dsgvo-templates``, das aber per ``.dockerignore``
# nicht ins Image kopiert wurde — DSGVO-Paket-Download warf
# ``FileNotFoundError`` in Produktion.
# Refs #959: dieses Modul ist jetzt unter ``services/client/`` — TEMPLATE_DIR
# muss daher ``../../dsgvo_templates`` aufloesen, nicht ``../dsgvo_templates``.
TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "dsgvo_templates"

DOCUMENTS = {
    "verarbeitungsverzeichnis": {
        "name": "Verzeichnis von Verarbeitungstätigkeiten",
        "article": "Art. 30 DSGVO",
        "filename": "verarbeitungsverzeichnis.md",
    },
    "dsfa": {
        "name": "Datenschutz-Folgenabschätzung",
        "article": "Art. 35 DSGVO",
        "filename": "dsfa.md",
    },
    "av-vertrag": {
        "name": "Auftragsverarbeitungsvertrag",
        "article": "Art. 28 DSGVO",
        "filename": "av-vertrag.md",
    },
    "toms": {
        "name": "Technische und Organisatorische Maßnahmen",
        "article": "Art. 32 DSGVO",
        "filename": "toms.md",
    },
    "informationspflichten": {
        "name": "Informationspflichten",
        "article": "Art. 13/14 DSGVO",
        "filename": "informationspflichten.md",
    },
}


def _settings_hash(settings_obj, document_type_overrides=()) -> str:
    """Refs #840 (C-73), erweitert Refs #1762: kurzer Hash der retention-
    relevanten Settings plus der abgeleiteten Dokumentationstyp-Ausnahmen.

    Macht im Footer sichtbar, ob das Paket auf einem geaenderten Settings-
    Stand basiert — Aufsichts-Pruefungen koennen damit Aktualitaet verifizieren.

    Refs #1762: dsfa.md § 2.3 rendert inzwischen alle sieben Aufbewahrungs-
    fristen als Ist-Wert (vorher nur die ersten drei; die restlichen vier
    standen als "Standardwert mit Pruefauftrag"-Prosa). Ohne die drei
    zusaetzlichen Settings-Felder sowie ``document_type_overrides`` im Hash
    wuerde eine inhaltlich geaenderte DSFA denselben Settings-Stempel tragen
    wie zuvor — der Aktualitaetsnachweis fuer Aufsichtspruefungen waere unwahr.

    ``document_type_overrides``: sortierte Sequenz von (Name, Tage)-Paaren je
    DocumentType mit eigener ``retention_days`` (Zeile 4 in dsfa.md § 2.3 —
    kein einzelnes Settings-Feld, deshalb als separates Argument statt als
    ``settings_obj``-Attribut; der Aufrufer berechnet die Liste aus der DB,
    siehe :func:`_document_type_retention_overrides`).
    """
    payload = {
        "retention_anonymous_days": getattr(settings_obj, "retention_anonymous_days", 90),
        "retention_identified_days": getattr(settings_obj, "retention_identified_days", 365),
        "retention_qualified_days": getattr(settings_obj, "retention_qualified_days", 3650),
        "retention_activities_days": getattr(settings_obj, "retention_activities_days", 365),
        "client_trash_days": getattr(settings_obj, "client_trash_days", 30),
        "auditlog_retention_months": getattr(settings_obj, "auditlog_retention_months", 24),
        "facility_full_name": getattr(settings_obj, "facility_full_name", "") or "",
        "document_type_overrides": [list(pair) for pair in document_type_overrides],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def _document_type_retention_overrides(facility) -> list[tuple[str, int]]:
    """Refs #1762: Ist-Wert fuer Zeile 4 (dsfa.md § 2.3, "Abweichende Frist
    einzelner Dokumentationstypen").

    ``DocumentType.retention_days`` ist kein einzelnes Settings-Feld, sondern
    pro Dokumentationstyp gesetzt — der Ist-Wert muss deshalb ueber alle
    DocumentTypes der Facility abgeleitet (berechnet) werden. Gleiche
    Filterung wie ``core.retention.enforcement.enforce_document_type_retention``
    (``entity_type=EVENT`` und ``retention_days`` gesetzt; ``is_active`` spielt
    keine Rolle), damit die DSFA exakt beschreibt, was der Aufbewahrungs-Job
    tatsaechlich durchsetzt. Liefert eine Liste von (Name, Tage)-Paaren in
    Modell-Standardreihenfolge (``sort_order``, ``name``); leer, wenn kein Typ
    eine eigene Frist hat.

    Refs #1738 (M24/G4): Der ``entity_type``-Filter kam mit der Event-only-Regel
    fuer ``retention_days`` (ADR-039) dazu. Ohne ihn wies Zeile 4 die Frist
    eines Personen- oder Fall-Typs als **durchgesetzte** Frist aus, obwohl sie
    nirgends durchgesetzt wird — eine Falschaussage in genau dem Dokument, das
    Aufsichtspruefungen als Ist-Beschreibung dient. Fuer Personen- und
    Fall-Daten gilt der Owner-Lebenszyklus.
    """
    overrides: list[tuple[str, int]] = []
    for dt in DocumentType.objects.filter(
        facility=facility,
        entity_type=DocumentType.EntityType.EVENT,
        retention_days__isnull=False,
    ):
        if dt.retention_days is None:
            continue
        overrides.append((dt.name, dt.retention_days))
    return overrides


def _format_document_type_overrides(overrides) -> str:
    """Rendert die (Name, Tage)-Paare aus :func:`_document_type_retention_overrides`
    als Ist-Wert-Text fuer die DSFA-Tabellenzelle. ``"keine"``, wenn kein
    Dokumentationstyp eine eigene Frist hat."""
    if not overrides:
        return "keine"
    return "; ".join(f"{name}: {days} Tage" for name, days in overrides)


def _build_footer(settings_obj, document_type_overrides=()) -> str:
    """Refs #840 (C-73): Versionsstempel-Footer fuer jedes gerenderte Dokument.

    Datum + Settings-Hash + Source-Code-Version (Commit-SHA, sofern gesetzt
    via #835). Aufsichts-Pruefungen erkennen so, ob das Paket aktuell ist.
    """
    commit = getattr(django_settings, "SOURCE_CODE_VERSION", "") or "n/a"
    settings_hash = _settings_hash(settings_obj, document_type_overrides)
    return (
        "\n\n---\n\n"
        f"<sub>Generiert: {timezone.now().isoformat(timespec='seconds')} · "
        f"Software-Version: {commit[:8]} · "
        f"Settings-Hash: {settings_hash}</sub>\n"
    )


def render_document(slug, facility):
    """Render a DSGVO template with facility data. Returns (content_str, filename)."""
    if slug not in DOCUMENTS:
        raise ValueError(f"Unknown document: {slug}")

    doc = DOCUMENTS[slug]
    template_path = TEMPLATE_DIR / doc["filename"]
    content = template_path.read_text(encoding="utf-8")

    try:
        settings_obj = facility.settings
    except Settings.DoesNotExist:
        settings_obj = None

    facility_name = resolve_facility_display_name(facility)
    document_type_overrides = _document_type_retention_overrides(facility)

    replacements = {
        "{{ facility_name }}": facility_name,
        "{{ date }}": date.today().strftime("%d.%m.%Y"),
        "{{ retention_anonymous_days }}": str(getattr(settings_obj, "retention_anonymous_days", 90)),
        "{{ retention_identified_days }}": str(getattr(settings_obj, "retention_identified_days", 365)),
        "{{ retention_qualified_days }}": str(getattr(settings_obj, "retention_qualified_days", 3650)),
        # Refs #1762: vormals "Standardwert mit Pruefauftrag"-Prosa in
        # dsfa.md § 2.3 Zeilen 4-7, jetzt echte Ist-Wert-Platzhalter.
        "{{ retention_activities_days }}": str(getattr(settings_obj, "retention_activities_days", 365)),
        "{{ client_trash_days }}": str(getattr(settings_obj, "client_trash_days", 30)),
        "{{ auditlog_retention_months }}": str(getattr(settings_obj, "auditlog_retention_months", 24)),
        "{{ retention_document_type_overrides }}": _format_document_type_overrides(document_type_overrides),
    }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    # Refs #840 (C-73): Versionsstempel anhaengen — Datum + Settings-Hash +
    # Software-Version. Aufsichts-Pruefungen erkennen Aktualitaet.
    content += _build_footer(settings_obj, document_type_overrides)

    return content, doc["filename"]


def get_document_list():
    """Return list of available documents for display."""
    return [{"slug": slug, "name": doc["name"], "article": doc["article"]} for slug, doc in DOCUMENTS.items()]
