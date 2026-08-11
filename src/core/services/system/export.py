"""Export services: CSV, PDF, sample activity report."""

import csv
import io
import logging

import weasyprint
import weasyprint.urls
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import DocumentType, Event, FieldTemplate
from core.services.compliance import user_can_see_field
from core.services.facility import resolve_facility_display_name
from core.services.file_vault import safe_decrypt

logger = logging.getLogger(__name__)


# Restriktiver Fetcher fuer inline ``data:``-URIs (Refs #1610). WeasyPrint 69.0
# deprecatet ``default_url_fetcher`` (Removal in 69.0 angekuendigt) zugunsten von
# ``URLFetcher``; die neue API bringt die ``data:``-Whitelist nativ mit
# (``allowed_protocols``). ``allow_redirects=False`` bewahrt die restriktive
# Vorgabe (``URLFetcher``-Default ist ``True``). Einmalige Modul-Instanz statt
# einer Neuallokation pro Aufruf.
_DATA_URI_FETCHER = weasyprint.urls.URLFetcher(allowed_protocols={"data"}, allow_redirects=False)


def restricted_url_fetcher(url, *args, **kwargs):
    """Restriktiver WeasyPrint-``url_fetcher`` (Refs #1272, T8).

    Defense-in-Depth gegen ``file://``-SSRF / Local-File-Read: Die PDF-Templates
    referenzieren ausschliesslich Inline-Styles und keine externen Ressourcen.
    WeasyPrints Default-Fetcher liest ``file://``-URLs jedoch vom lokalen
    Dateisystem und ruft externe ``http(s)://``-Ressourcen ab. Dieser Fetcher
    lehnt daher jeden ``file://``- oder Netzwerk-Fetch hart ab und reicht nur
    inline ``data:``-URIs an den restriktiven ``URLFetcher`` (Whitelist
    ``allowed_protocols={"data"}``) durch.

    Bisher griff der Schutz nur, weil die Templates kein ``|safe`` enthalten
    (injizierte Tags rendern inert) — fragil. Da die legitimen Templates keine
    externen Ressourcen referenzieren, wird der Fetcher fuer regulaere Eingaben
    nie aufgerufen → byte-gleiche PDF-Ausgabe.
    """
    if url.startswith("data:"):
        # ``URLFetcher.fetch(url, headers=None)`` weicht in der Signatur von
        # ``default_url_fetcher`` ab — WeasyPrint (und unsere Tests) rufen den
        # Fetcher nur mit ``url`` auf, daher werden ``*args/**kwargs`` bewusst
        # nicht durchgereicht.
        return _DATA_URI_FETCHER.fetch(url)
    raise ValueError(f"Externe Ressourcen sind in PDF-Exporten nicht erlaubt: {url!r}")


def _get_events_queryset(facility, date_from, date_to, user=None, document_type=None):
    """Events in the given period, ordered descending by occurrence.

    Refs #779 (C-11): wenn ``user`` gesetzt ist, wird der QuerySet ueber
    :meth:`EventQuerySet.visible_to` auf die Sensitivity-Stufen
    eingeschraenkt, die die Rolle sehen darf. Vorher filterte der Service
    erst pro Feld und liess Zeilen hoch sensitiver DocumentTypes durch —
    bei Wiederverwendung des Service durch Staff/Assistant ein Leak.

    ``user=None`` bleibt der Systemmodus (z.B. Cron-Reports). Aufrufer im
    Systemmodus tragen das Privileg-Risiko bewusst.

    ``document_type`` (Refs #1804, R2) verengt den Export auf denselben
    Dokumentationstyp, den die Uebersicht am Bildschirm zeigt. Der Parameter
    ist die Voraussetzung dafuer, dass die Export-Schaltflaeche neben einer
    gefilterten Tabelle nicht mehr Zeilen ausgibt, als darueber stehen.
    """
    qs = Event.objects.filter(
        facility=facility,
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )
    if document_type is not None:
        qs = qs.filter(document_type=document_type)
    if user is not None:
        qs = qs & Event.objects.visible_to(user)
    return qs.select_related("document_type", "client").order_by("-occurred_at")


def _collect_field_templates(facility, user):
    """Return (all_field_templates, visible_slugs) for a facility.

    Visible slugs are pre-filtered by the user's role using ``NORMAL`` as the
    baseline document sensitivity; the caller must still run the per-event
    check against the actual document type's sensitivity.
    """
    all_field_templates = {}
    for ft in FieldTemplate.objects.for_facility(facility).order_by("name"):
        all_field_templates[ft.slug] = ft
    field_slugs = list(all_field_templates.keys())

    if user is not None:
        visible_field_slugs = []
        for slug in field_slugs:
            ft = all_field_templates[slug]
            if user_can_see_field(user, DocumentType.Sensitivity.NORMAL, ft.sensitivity):
                visible_field_slugs.append(slug)
        field_slugs = visible_field_slugs

    return all_field_templates, field_slugs


def _build_header(all_field_templates, field_slugs):
    """Static CSV columns followed by the visible dynamic field names."""
    return [
        _("Datum"),
        _("Uhrzeit"),
        _("Dokumentationstyp"),
        _("Person"),
        _("Kontaktstufe"),
        _("Altersgruppe"),
    ] + [
        # Security R13: FieldTemplate.name ist admin-kontrollierter Freitext —
        # Header-Zellen genauso gegen Formel-Injection neutralisieren wie
        # Werte-Zellen (Refs #719).
        _sanitize_csv_cell(all_field_templates[s].name)
        for s in field_slugs
    ]


def _resolve_field_value(value, ft):
    """Convert a raw data_json value into a CSV-ready string.

    Handles encrypted dicts, multi/single-select option lookups and lists.
    """
    if isinstance(value, dict):
        return safe_decrypt(value)
    if isinstance(value, list):
        if ft and ft.options_json:
            label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
            return ", ".join(label_map.get(str(v), str(v)) for v in value)
        return ", ".join(str(v) for v in value)
    if isinstance(value, str) and ft and ft.options_json:
        label_map = {o["slug"]: o["label"] for o in ft.options_json if isinstance(o, dict)}
        return label_map.get(value, value)
    return value


# OWASP CSV-Injection-Pattern (Refs #719):
# Werte, die mit einem dieser Zeichen beginnen, werden in Excel/LibreOffice
# als Formel ausgewertet (``=cmd|'/c calc'!A1``, ``@SUM(...)``, ``-1234``,
# ``+1234`` oder als Zellseparator-Smuggling via Tab/CR/LF). Das ``'`` als
# Praefix neutralisiert das — die Zelle zeigt den Originalwert ohne das
# fuehrende ``'`` an, fuehrt ihn aber nicht aus.
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _sanitize_csv_cell(value):
    """Neutralisiere Formula-Injection in einer einzelnen CSV-Zelle.

    ``None`` → ``""``, alles andere wird zu str konvertiert. Beginnt der
    Wert mit einem der OWASP-Praefixe, wird ein Leading-``'`` vorangestellt.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_CSV_INJECTION_PREFIXES):
        return "'" + text
    return text


def _build_event_row(event, all_field_templates, field_slugs, user):
    """Build a single CSV row for an event, applying per-event sensitivity checks.

    Refs #719: alle dynamischen Zellen laufen durch ``_sanitize_csv_cell``
    gegen Formula-Injection beim Oeffnen in Excel/LibreOffice.
    """
    client_name = event.client.pseudonym if event.client else (_("Anonym") if event.is_anonymous else "–")
    contact_stage = ""
    age_cluster = ""
    if event.client:
        contact_stage = event.client.get_contact_stage_display()
        age_cluster = event.client.get_age_cluster_display()

    row = [
        event.occurred_at.strftime("%d.%m.%Y"),
        event.occurred_at.strftime("%H:%M"),
        _sanitize_csv_cell(event.document_type.name),
        _sanitize_csv_cell(client_name),
        _sanitize_csv_cell(contact_stage),
        _sanitize_csv_cell(age_cluster),
    ]

    doc_sensitivity = event.document_type.sensitivity
    data = event.data_json or {}
    for field_slug in field_slugs:
        ft = all_field_templates.get(field_slug)
        field_sensitivity = ft.sensitivity if ft else ""

        if user is not None and not user_can_see_field(user, doc_sensitivity, field_sensitivity):
            row.append(_("[Eingeschränkt]"))
            continue

        value = data.get(field_slug, "")
        row.append(_sanitize_csv_cell(_resolve_field_value(value, ft)))

    return row


def _stream_row(writer, output, row):
    """Write one row to the buffered writer and flush it as a streamed chunk."""
    writer.writerow(row)
    chunk = output.getvalue()
    output.seek(0)
    output.truncate(0)
    return chunk


def export_events_csv(facility, date_from, date_to, user=None, document_type=None):
    """Generate CSV data for events in the given period. Yields CSV chunks.

    When *user* is provided, fields are filtered by the user's role and the
    field's sensitivity level (same logic as EventDetailView).

    ``document_type`` (Refs #1804, R2) ist der Dokumentationstyp-Filter der
    Uebersicht. Er gehoert hierher und nicht in die View: der Export ist die
    Datei-Fassung derselben Zahlen, und beide muessen ueber dieselbe
    Grundgesamtheit laufen.
    """
    events = _get_events_queryset(facility, date_from, date_to, user=user, document_type=document_type)
    all_field_templates, field_slugs = _collect_field_templates(facility, user)
    header = _build_header(all_field_templates, field_slugs)

    output = io.StringIO()
    writer = csv.writer(output, dialect="excel")

    user_name = user.get_full_name() if user else "System"
    # Metadaten-Zeile: facility.name + user_name fliessen aus User-Eingabe
    # in die CSV — auch hier Formula-Injection-Praefix abfangen.
    metadata = f"# Export: {facility.name} | {user_name} | {timezone.now().isoformat()}"
    yield _stream_row(writer, output, [_sanitize_csv_cell(metadata)])
    yield _stream_row(writer, output, header)

    for event in events:
        row = _build_event_row(event, all_field_templates, field_slugs, user)
        yield _stream_row(writer, output, row)


def generate_report_pdf(facility, date_from, date_to, stats, *, internal_mode=False, document_type=None):
    """Generate a PDF semi-annual report. Returns bytes.

    Refs #792 (C-24): Standard-PDF (``internal_mode=False``) rendert keine
    ``Top 5 Personen``-Tabelle — Pseudonym-Rankings sind ein Re-Identifizierungs-
    risiko bei Traeger-/Foerderberichten. Im Internal-Mode (Lead/Admin-Toggle)
    wird das Ranking gerendert und der Report mit einem ``INTERN``-Banner
    markiert.

    ``document_type`` (Refs #1804, R2) ist der Dokumentationstyp-Filter der
    Uebersicht. Er aendert die Zahlen nicht — die stehen bereits in ``stats``
    —, sondern zwei Angaben, ohne die das PDF im Umlauf nicht mehr von einem
    vollstaendigen Bericht zu unterscheiden waere: den Filterhinweis auf dem
    Deckblatt und den Quellenausweis. Bei gesetztem Filter rechnet der
    Aufrufer live (Monats-Aggregate kennen keine Zerlegung nach Typ); ein
    fester ``SNAPSHOT_HYBRID``-Ausweis waere dann eine Falschangabe.
    """
    from core.reporting.data_source import (
        ALL_EVENTS,
        DATA_SOURCE_EXPLANATIONS,
        DATA_SOURCE_NOTES,
        EVENTS_WITH_PERSON,
        LIVE,
        SNAPSHOT_HYBRID,
        table_note,
    )

    data_source = LIVE if document_type is not None else SNAPSHOT_HYBRID

    # Security R4: Standard-Modus ist das externe Artefakt (Traeger/Foerderer)
    # — Kleinstfallzahlen unterdruecken wie Sachbericht-PDF/On-Screen-Bericht.
    # Der Internal-Mode (Lead/Admin, INTERN-Banner, Refs #792) bleibt roh.
    if not internal_mode:
        from core.services.dashboard import suppress_report_stats

        stats = suppress_report_stats(facility, stats)

    html = render_to_string(
        "core/export/report_pdf.html",
        {
            "facility_name": resolve_facility_display_name(facility),
            "date_from": date_from,
            "date_to": date_to,
            "stats": stats,
            "internal_mode": internal_mode,
            "generated_at": timezone.now(),
            # Refs #1804 (R2): Der Filter steht auf dem Deckblatt. Eine Datei,
            # die ihren Ausschnitt verschweigt, wandert als „der Bericht" durch
            # Traegerpostfaecher.
            "document_type": document_type,
            # Quellenausweis (Refs #1743): Der Aufrufer ``PDFExportView`` liest
            # ueber ``get_statistics_hybrid`` aus den Monats-Snapshots — derselbe
            # hybride Pfad wie das Sachbericht-PDF. Auch dieses extern
            # zirkulierende PDF weist seine Quelle daher in der Fusszeile aus.
            # Bei gesetztem Doktyp-Filter ist es der Live-Pfad (Refs #1804).
            "data_source_note": DATA_SOURCE_NOTES[data_source],
            "data_source_explanation": DATA_SOURCE_EXPLANATIONS[data_source],
            # K3-A3 (Refs #1770): Jede Tabelle benennt ihre Grundgesamtheit.
            # Die Alterstabelle zaehlt **alle** Ereignisse des Zeitraums, aber
            # erst seit die Restzeile "ohne Personenbezug" mitlaeuft (K3-A1) —
            # und die entsteht in der Suppression, die der Internal-Mode oben
            # gerade ueberspringt. Dort zaehlt sie also weiter nur Ereignisse
            # MIT Personenbezug, und genau das muss dastehen: ein Ausweis
            # "alle Ereignisse" waere hier die Sorte Falschangabe, die K3-A3
            # beseitigen sollte. Die Dokumenttyp-Tabelle ist in beiden Modi
            # dieselbe Menge.
            "population_notes": {
                "by_document_type": table_note(ALL_EVENTS, data_source),
                "by_age_cluster": table_note(EVENTS_WITH_PERSON if internal_mode else ALL_EVENTS, data_source),
            },
        },
    )
    return weasyprint.HTML(string=html, url_fetcher=restricted_url_fetcher).write_pdf()


def generate_sample_report_1_pdf(facility, date_from, date_to):
    """Generate the sample activity report as PDF.

    Refs #1278 (T1): Das Sachbericht-PDF ist das am ehesten externe Artefakt und
    wird daher durch dieselbe k-Anon-Kleinstfallzahl-Suppression geleitet wie
    der externe On-Screen-Bericht — Zellen < Schwelle werden unterdrueckt.
    """
    from core.reporting.data_source import (
        DATA_SOURCE_EXPLANATIONS,
        DATA_SOURCE_NOTES,
        EVENTS_WITH_PERSON,
        MAPPED_EVENTS,
        SNAPSHOT_HYBRID,
        table_note,
    )
    from core.services.dashboard import get_sample_report_1_statistics_hybrid, suppress_sample_report_1_stats

    stats = get_sample_report_1_statistics_hybrid(facility, date_from, date_to)
    stats = suppress_sample_report_1_stats(facility, stats)
    html = render_to_string(
        "core/export/sample_report_1_pdf.html",
        {
            "facility_name": resolve_facility_display_name(facility),
            "date_from": date_from,
            "date_to": date_to,
            "stats": stats,
            "generated_at": timezone.now(),
            # Quellenausweis (Refs #1743): dieses PDF ist das einzige hybrid
            # gelesene Artefakt — die Zahlen kommen aus den Monats-Snapshots
            # und ueberdauern die Loeschfristen.
            "data_source_note": DATA_SOURCE_NOTES[SNAPSHOT_HYBRID],
            "data_source_explanation": DATA_SOURCE_EXPLANATIONS[SNAPSHOT_HYBRID],
            # K3-A3 (Refs #1770): Hier stehen wirklich zwei verschiedene
            # Grundgesamtheiten nebeneinander — "total"/by_category zaehlen nur
            # die gemappten Ereignisse, die Altersverteilung nur die mit
            # Personenbezug. Genau diese Differenz ist K3, und ohne den Ausweis
            # ist der Bericht nicht nur undicht, sondern irrefuehrend.
            "population_notes": {
                "by_category": table_note(MAPPED_EVENTS, SNAPSHOT_HYBRID),
                "by_age_cluster": table_note(EVENTS_WITH_PERSON, SNAPSHOT_HYBRID),
            },
        },
    )
    return weasyprint.HTML(string=html, url_fetcher=restricted_url_fetcher).write_pdf()
