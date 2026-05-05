"""DSGVO documentation package service."""

import logging
from datetime import date
from pathlib import Path

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


def render_document(slug, facility):
    """Render a DSGVO template with facility data. Returns (content_str, filename)."""
    if slug not in DOCUMENTS:
        raise ValueError(f"Unknown document: {slug}")

    doc = DOCUMENTS[slug]
    template_path = TEMPLATE_DIR / doc["filename"]
    content = template_path.read_text(encoding="utf-8")

    try:
        settings = facility.settings
    except Settings.DoesNotExist:
        settings = None

    facility_name = getattr(settings, "facility_full_name", "") or facility.name

    replacements = {
        "{{ facility_name }}": facility_name,
        "{{ date }}": date.today().strftime("%d.%m.%Y"),
        "{{ retention_anonymous_days }}": str(getattr(settings, "retention_anonymous_days", 90)),
        "{{ retention_identified_days }}": str(getattr(settings, "retention_identified_days", 365)),
        "{{ retention_qualified_days }}": str(getattr(settings, "retention_qualified_days", 3650)),
    }

    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)

    return content, doc["filename"]


def get_document_list():
    """Return list of available documents for display."""
    return [{"slug": slug, "name": doc["name"], "article": doc["article"]} for slug, doc in DOCUMENTS.items()]
