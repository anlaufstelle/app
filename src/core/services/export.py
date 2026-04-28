"""Export services: CSV, PDF, youth welfare office report."""

import csv
import io
import logging
from collections import OrderedDict

import weasyprint
from django.db.models import Count
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event, FieldTemplate
from core.services.encryption import safe_decrypt
from core.services.sensitivity import user_can_see_field

logger = logging.getLogger(__name__)


def export_events_csv(facility, date_from, date_to, user=None):
    """Generate CSV data for events in the given period. Returns (header, rows).

    When *user* is provided, fields are filtered by the user's role and the
    field's sensitivity level (same logic as EventDetailView).
    """
    events = (
        Event.objects.filter(
            facility=facility,
            is_deleted=False,
            occurred_at__date__gte=date_from,
            occurred_at__date__lte=date_to,
        )
        .select_related("document_type", "client")
        .order_by("-occurred_at")
    )

    # Dynamic columns: collect all FieldTemplate slugs for the facility
    all_field_templates = {}
    for ft in FieldTemplate.objects.for_facility(facility).order_by("name"):
        all_field_templates[ft.slug] = ft
    field_slugs = list(all_field_templates.keys())

    # If a user is given, determine which field slugs the user may see.
    # Fields whose effective sensitivity exceeds the user's role are excluded.
    if user is not None:
        visible_field_slugs = []
        for slug in field_slugs:
            ft = all_field_templates[slug]
            # Without a concrete doc-type we use NORMAL as baseline; the
            # per-event loop below will do the precise check.
            if user_can_see_field(user, DocumentType.Sensitivity.NORMAL, ft.sensitivity):
                visible_field_slugs.append(slug)
        field_slugs = visible_field_slugs

    header = [
        _("Datum"),
        _("Uhrzeit"),
        _("Dokumentationstyp"),
        _("Klientel"),
        _("Kontaktstufe"),
        _("Altersgruppe"),
    ] + [all_field_templates[s].name for s in field_slugs]

    output = io.StringIO()
    writer = csv.writer(output, dialect="excel")

    # Metadata header row with facility, user, and export timestamp
    user_name = user.get_full_name() if user else "System"
    writer.writerow([f"# Export: {facility.name} | {user_name} | {timezone.now().isoformat()}"])
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    writer.writerow(header)
    yield output.getvalue()
    output.seek(0)
    output.truncate(0)

    for event in events:
        client_name = event.client.pseudonym if event.client else (_("Anonym") if event.is_anonymous else "–")
        contact_stage = ""
        age_cluster = ""
        if event.client:
            contact_stage = event.client.get_contact_stage_display()
            age_cluster = event.client.get_age_cluster_display()

        row = [
            event.occurred_at.strftime("%d.%m.%Y"),
            event.occurred_at.strftime("%H:%M"),
            event.document_type.name,
            client_name,
            contact_stage,
            age_cluster,
        ]

        doc_sensitivity = event.document_type.sensitivity
        data = event.data_json or {}
        for field_slug in field_slugs:
            ft = all_field_templates.get(field_slug)
            field_sensitivity = ft.sensitivity if ft else ""

            # Per-event sensitivity check using the actual document type
            if user is not None and not user_can_see_field(user, doc_sensitivity, field_sensitivity):
                row.append(_("[Eingeschränkt]"))
                continue

            value = data.get(field_slug, "")
            if isinstance(value, dict):
                value = safe_decrypt(value)
            elif isinstance(value, list):
                # Resolve option slugs to labels
                if ft and ft.options_json:
                    label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
                    value = ", ".join(label_map.get(str(v), str(v)) for v in value)
                else:
                    value = ", ".join(str(v) for v in value)
            elif isinstance(value, str) and ft and ft.options_json:
                # Single select values: resolve slug to label
                label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
                value = label_map.get(value, value)
            row.append(value)

        writer.writerow(row)
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)


def generate_report_pdf(facility, date_from, date_to, stats):
    """Generate a PDF semi-annual report. Returns bytes."""
    html = render_to_string(
        "core/export/report_pdf.html",
        {
            "facility_name": getattr(getattr(facility, "settings", None), "facility_full_name", facility.name),
            "date_from": date_from,
            "date_to": date_to,
            "stats": stats,
            "generated_at": timezone.now(),
        },
    )
    return weasyprint.HTML(string=html).write_pdf()


# Mapping: DocumentType.system_type -> Jugendamt category (missing = excluded)
JUGENDAMT_CATEGORY_MAP = {
    "contact": "Kontakte",
    "crisis": "Beratung",
    "medical": "Versorgung",
    "needle_exchange": "Versorgung",
    "accompaniment": "Vermittlung",
    "counseling": "Beratung",
    "referral": "Vermittlung",
}


def get_jugendamt_statistics(facility, date_from, date_to):
    """Statistics aggregated by youth welfare office categories."""
    base_qs = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )

    # Aggregate by DocumentType system_type
    by_dt = (
        base_qs.values("document_type__system_type").annotate(count=Count("id")).order_by("document_type__system_type")
    )

    # Consolidate into Jugendamt categories
    category_counts = OrderedDict()
    total = 0
    for row in by_dt:
        system_type = row["document_type__system_type"]
        cat = JUGENDAMT_CATEGORY_MAP.get(system_type)
        if cat is None:
            continue
        category_counts[cat] = category_counts.get(cat, 0) + row["count"]
        total += row["count"]

    by_category = [(name, count) for name, count in category_counts.items()]

    # Age clusters
    age_labels = {
        "u18": _("Unter 18"),
        "18_26": _("18–26"),
        "27_plus": _("27+"),
        "unknown": _("Unbekannt"),
    }
    by_age_cluster = list(
        base_qs.exclude(client__isnull=True)
        .values("client__age_cluster")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    by_age_cluster = [
        {
            "cluster": row["client__age_cluster"],
            "label": age_labels.get(row["client__age_cluster"], ""),
            "count": row["count"],
        }
        for row in by_age_cluster
    ]

    unique_clients = base_qs.exclude(client__isnull=True).values("client").distinct().count()

    return {
        "total": total,
        "by_category": by_category,
        "by_age_cluster": by_age_cluster,
        "unique_clients": unique_clients,
    }


def generate_jugendamt_pdf(facility, date_from, date_to):
    """Generate the youth welfare office report as PDF."""
    from core.services.snapshot import get_jugendamt_statistics_hybrid

    stats = get_jugendamt_statistics_hybrid(facility, date_from, date_to)
    html = render_to_string(
        "core/export/jugendamt_pdf.html",
        {
            "facility_name": getattr(getattr(facility, "settings", None), "facility_full_name", facility.name),
            "date_from": date_from,
            "date_to": date_to,
            "stats": stats,
            "generated_at": timezone.now(),
        },
    )
    return weasyprint.HTML(string=html).write_pdf()
