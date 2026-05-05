"""DSGVO documentation package service."""

import hashlib
import json
import logging
from datetime import date
from pathlib import Path

from django.conf import settings as django_settings
from django.utils import timezone

from core.models import Settings

logger = logging.getLogger(__name__)

# Refs #784 — Templates leben jetzt im App-Paket (``core/dsgvo_templates/``),
# damit sie im Docker-Image enthalten sind. Frueher zeigte TEMPLATE_DIR
# auf ``<repo>/docs/dsgvo-templates``, das aber per ``.dockerignore``
# nicht ins Image kopiert wurde — DSGVO-Paket-Download warf
# ``FileNotFoundError`` in Produktion.
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "dsgvo_templates"

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


def _settings_hash(settings_obj) -> str:
    """Refs #840 (C-73): kurzer Hash der retention-relevanten Settings.

    Macht im Footer sichtbar, ob das Paket auf einem geaenderten Settings-
    Stand basiert — Aufsichts-Pruefungen koennen damit Aktualitaet verifizieren.
    """
    payload = {
        "retention_anonymous_days": getattr(settings_obj, "retention_anonymous_days", 90),
        "retention_identified_days": getattr(settings_obj, "retention_identified_days", 365),
        "retention_qualified_days": getattr(settings_obj, "retention_qualified_days", 3650),
        "facility_full_name": getattr(settings_obj, "facility_full_name", "") or "",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:8]


def _build_footer(settings_obj) -> str:
    """Refs #840 (C-73): Versionsstempel-Footer fuer jedes gerenderte Dokument.

    Datum + Settings-Hash + Source-Code-Version (Commit-SHA, sofern gesetzt
    via #835). Aufsichts-Pruefungen erkennen so, ob das Paket aktuell ist.
    """
    commit = getattr(django_settings, "SOURCE_CODE_VERSION", "") or "n/a"
    settings_hash = _settings_hash(settings_obj)
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

    facility_name = getattr(settings_obj, "facility_full_name", "") or facility.name

    replacements = {
        "{{ facility_name }}": facility_name,
        "{{ date }}": date.today().strftime("%d.%m.%Y"),
        "{{ retention_anonymous_days }}": str(getattr(settings_obj, "retention_anonymous_days", 90)),
        "{{ retention_identified_days }}": str(getattr(settings_obj, "retention_identified_days", 365)),
        "{{ retention_qualified_days }}": str(getattr(settings_obj, "retention_qualified_days", 3650)),
    }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    # Refs #840 (C-73): Versionsstempel anhaengen — Datum + Settings-Hash +
    # Software-Version. Aufsichts-Pruefungen erkennen Aktualitaet.
    content += _build_footer(settings_obj)

    return content, doc["filename"]


def get_document_list():
    """Return list of available documents for display."""
    return [{"slug": slug, "name": doc["name"], "article": doc["article"]} for slug, doc in DOCUMENTS.items()]
