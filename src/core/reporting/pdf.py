"""Default PDF layout for an already protected reporting dataset."""

from django.template.loader import render_to_string

from core.reporting.base import DatasetBackend, DatasetResult
from core.reporting.data_source import LIVE, table_note
from core.services.facility import resolve_facility_display_name


def render_dataset_pdf(
    backend: DatasetBackend, facility, date_from, date_to, result: DatasetResult, generated_at
) -> bytes:
    """Render only the protected field projection, never the raw summary.

    The records form one table with the backend's declared population and live
    source, just like CSV and the preview. Completeness messages are prepared
    by the backend; their internal counts are deliberately excluded.
    """
    import weasyprint

    from core.services.system.export import restricted_url_fetcher

    field_specs = backend.get_field_specs()
    html = render_to_string(
        "core/export/dataset_pdf.html",
        {
            "facility_name": resolve_facility_display_name(facility),
            "report_name": backend.name,
            "date_from": date_from,
            "date_to": date_to,
            "generated_at": generated_at,
            "threshold": result.summary["k_anonymity_threshold"],
            "field_specs": field_specs,
            "rows": [[record.get(spec.key) for spec in field_specs] for record in result.records],
            "population_note": table_note(backend.population, LIVE),
            "completeness_messages": [issue.message for issue in result.completeness],
        },
    )
    return weasyprint.HTML(string=html, url_fetcher=restricted_url_fetcher).write_pdf()
