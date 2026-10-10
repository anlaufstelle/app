"""Service for building unified activity feeds."""

from datetime import UTC, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.constants import FEED_MAX_PER_TYPE
from core.models import Activity, Case, Event, WorkItem
from core.services.compliance import user_can_see_field
from core.services.config import iter_fields_in_block_order
from core.services.dynamic_data import option_label_map
from core.services.file_vault import is_encrypted_value, safe_decrypt


def _is_imaginary(naive_dt, tzinfo):
    """True, wenn diese Wanduhrzeit in dieser Zone nicht existiert (Refs #2057).

    Am Vorstelltag springt die Uhr (Europe/Berlin: 2026-03-29 02:00 -> 03:00);
    die uebersprungene Stunde ist eine Luecke. Der kanonische Test dafuer
    (PEP 495) ist die Rundreise ueber UTC: existiert die Wanduhrzeit nicht,
    kommt eine andere zurueck.
    """
    aware = naive_dt.replace(tzinfo=tzinfo)
    return aware.astimezone(UTC).astimezone(tzinfo).replace(tzinfo=None) != naive_dt


def _is_ambiguous(naive_dt, tzinfo):
    """True, wenn diese Wanduhrzeit in dieser Zone zweimal auftritt (Refs #2057).

    Am Rueckfalltag laeuft die Uhr zurueck (Europe/Berlin: 2026-10-25
    03:00 -> 02:00); dieselbe Wanduhrzeit steht dann fuer zwei Instants, die
    sich im UTC-Offset unterscheiden. Nur fuer solche Zeiten hat ``fold``
    ueberhaupt eine Wirkung. Voraussetzung: ``naive_dt`` ist nicht imaginaer
    (in der Luecke unterscheiden sich die Offsets ebenfalls) — die Aufrufer
    pruefen ``_is_imaginary`` zuerst.
    """
    return naive_dt.replace(tzinfo=tzinfo, fold=0).utcoffset() != naive_dt.replace(tzinfo=tzinfo, fold=1).utcoffset()


def _resolve_window_end(naive_start, naive_end, start_dt):
    """Fensterende nach der DST-Semantik aus #2057 auf einen Instant aufloesen."""
    tzinfo = timezone.get_current_timezone()
    if _is_imaginary(naive_end, tzinfo):
        # Vorstelltag: die Wanduhrzeit gibt es nicht -> Dauer-ab-Start. Die
        # nominale Dauer ist die Differenz der beiden naiven Wanduhrzeiten,
        # also die Dauer an einem DST-freien Referenztag.
        #
        # Addiert wird auf der UTC-Achse und danach zurueck normalisiert:
        # ``aware + timedelta`` rechnet in lokaler Wanduhrarithmetik und
        # lieferte sonst ``02:45+01:00`` — den richtigen Instant unter einer
        # lokalen Zeit, die an diesem Tag nicht existiert. ``case.handover``
        # rendert ``end_dt`` direkt per ``strftime``; die Fachkraft saehe
        # dann eine Uhrzeit, die die Uhr nie angezeigt hat.
        return (start_dt.astimezone(UTC) + (naive_end - naive_start)).astimezone(tzinfo)
    if _is_ambiguous(naive_end, tzinfo):
        # Rueckfalltag: ``fold=1`` waehlt die ZWEITE Okkurrenz der doppelten
        # Stunde.
        return timezone.make_aware(naive_end.replace(fold=1))
    # Eindeutige Zeit: unveraendert wie vor #2057. ``fold=1`` waere zwar
    # instantgleich, aendert aber die Objektdarstellung (Pickle, ``==``
    # gegen einen frisch gebauten Wert) an jedem normalen Tag.
    return timezone.make_aware(naive_end)


def shift_window_covers(start_dt, end_dt, moment):
    """True, wenn ``moment`` im Fenster ``[start_dt, end_dt]`` liegt.

    Refs #2057. Der Vergleich laeuft ausdruecklich ueber UTC: tragen zwei
    aware Datetimes dasselbe ``tzinfo``-Objekt, vergleicht Python ihre
    Wanduhrfelder und ignoriert die UTC-Offsets (CPython-Doku zu
    ``datetime``). Genau die doppelte Stunde des Rueckfalltags faellt damit
    unter den Tisch — 02:45 der zweiten Okkurrenz gilt dann als "vor"
    02:50 der ersten.

    Wer die laufende oder zuletzt abgeschlossene Schicht bestimmt, muss
    diesen Weg nehmen statt Wanduhrzeiten zu vergleichen; sonst weicht die
    Auswahl von dem Fenster ab, das ``get_time_range`` liefert.

    Das Fenster ist an **beiden** Enden geschlossen — wie das abgeloeste
    ``TimeFilter.covers_time`` (``t >= start or t <= end``). Der Grenzfall ist
    festgeschrieben (Refs #1910,
    ``test_tally_live_context.py::test_auto_selection_uses_the_same_tie_breaker_as_the_live_tally_context``):
    Treffen um Punkt 08:00 ein endender Nachtdienst 22:00-08:00 und ein
    beginnender Fruehdienst 08:00-16:00 aufeinander, gewinnt der **endende**
    Dienst. Ein halboffenes ``[start, end)`` wuerde diese Zuordnung kippen —
    die Erfassung spraenge am Schichtwechsel eine Sekunde zu frueh in die
    neue Schicht.
    """
    moment_utc = moment.astimezone(UTC)
    return start_dt.astimezone(UTC) <= moment_utc <= end_dt.astimezone(UTC)


def get_time_range(target_date, time_filter=None):
    """Return (start_dt, end_dt) for a given date, optionally scoped by a TimeFilter.

    Without time_filter: full day (00:00:00 to 23:59:59.999999).
    With time_filter:
      - Normal range (start <= end): same day from start_time to end_time.
      - Midnight overlap (start > end, e.g. 22:00-08:00): start on target_date,
        end on target_date + 1 day.

    DST-Semantik des Fensterendes (Refs #2057, Maintainer-Entscheid Welle E)
    =======================================================================
    Zwei Tage im Jahr ist eine Wanduhrzeit nicht eindeutig. Die Regel dafuer
    steht hier zentral, weil fuenf Aufrufer sie teilen: Zeitstrom-Feed,
    Live-Watch (``zeitstrom_watch``), Strichliste (``events.tally``),
    Schichtuebergabe (``case.handover``) und die Schichtfilter-Tabs
    (``zeitstrom_filters``).

    * **Rueckfalltag** (Herbst, z. B. 2026-10-25): die Stunde 02:00-03:00
      laeuft zweimal. Das Fensterende nimmt die **zweite** Okkurrenz
      (``fold=1``). Eine Nachtschicht 20:00-02:45 dauert an diesem Tag real
      **7 h 45** und enthaelt beide 02:xx-Stunden — genau das, was die
      Fachkraft erlebt, die eine Stunde laenger im Dienst ist. Wuerde das
      Fenster auf der ersten Okkurrenz enden, fielen alle in der zweiten
      Stunde dokumentierten Ereignisse aus Feed, Uebergabe und Strichliste.
    * **Vorstelltag** (Fruehjahr, z. B. 2026-03-29): die Stunde 02:00-03:00
      existiert nicht. Eine zweite Okkurrenz gibt es hier nicht, also gilt
      **Dauer-ab-Start**: ``end = start + nominale Dauer``, auf der UTC-Achse
      addiert und in die lokale Zone zurueck normalisiert. Die Schicht dauert
      damit real genauso lang wie an jedem anderen Tag (20:00-02:45 = 6 h 45);
      die **Wanduhr** am Schichtende steht eine Stunde spaeter, weil sie
      waehrend der Schicht vorgesprungen ist — aus 02:45 wird 03:45 CEST.
      Erfunden wird dabei keine Uhrzeit: 02:45 hat es an diesem Tag nie
      gegeben, 03:45 schon. Der UTC-Instant ist derselbe, den die fruehere
      ``fold=0``-Aufloesung traf (gleicher Pre-Gap-Offset wie am
      Schichtbeginn) — die Regel steht trotzdem ausdruecklich da, statt sich
      auf diesen Zufall zu verlassen.
    * Der **Schichtbeginn** bleibt auf der ersten Okkurrenz (``fold=0``).
      Zusammen mit dem Ende auf der zweiten ist das Fenster die weiteste
      wanduhrtreue Auslegung — es verliert nie ein dokumentiertes Ereignis.
    * Wer die **laufende** oder **zuletzt abgeschlossene** Schicht bestimmt
      (``events.tally.current_tally_context``,
      ``zeitstrom_filters._handover_filter``), muss dafuer die Instants
      dieses Fensters heranziehen — ``shift_window_covers`` unten. Ein
      Vergleich von Wanduhrzeiten (``TimeFilter.covers_time``) verlaesst die
      hier festgelegte Semantik wieder.
    * **Ganztags-Fenster** aendern sich nicht: 00:00 und 23:59:59.999999 sind
      an keiner EU-Transition mehrdeutig. Der Rueckfalltag umfasst weiterhin
      25 Stunden, der Vorstelltag 23.

    Nicht Teil dieser Regel ist die **Eingabe** von ``occurred_at``: Django
    weist eine mehrdeutige oder nicht existente Eingabezeit im Formular mit
    einer ``ValidationError`` ab (``forms.utils.from_current_timezone``), sie
    wird also nirgends still auf eine Okkurrenz gepinnt.
    """
    if time_filter is not None:
        naive_start = datetime.combine(target_date, time_filter.start_time)
        if time_filter.start_time <= time_filter.end_time:
            naive_end = datetime.combine(target_date, time_filter.end_time)
        else:
            naive_end = datetime.combine(target_date + timedelta(days=1), time_filter.end_time)
    else:
        naive_start = datetime.combine(target_date, time.min)
        naive_end = datetime.combine(target_date, time.max)

    start_dt = timezone.make_aware(naive_start)
    return start_dt, _resolve_window_end(naive_start, naive_end, start_dt)


def _feed_events(facility, start_dt, end_dt, user, include_all):
    """Event-Zweig des Feeds (Refs #1892, Extraktion aus ``build_feed_items``).

    Exclude bans when loading all types to avoid duplicates with bans feed.
    """
    events_qs = Event.objects.visible_to(user).filter(
        facility=facility,
        is_deleted=False,
        occurred_at__gte=start_dt,
        occurred_at__lte=end_dt,
    )
    if include_all:
        events_qs = events_qs.exclude(document_type__system_type="ban")
    events = events_qs.select_related("document_type", "client", "created_by").order_by("-occurred_at")[
        :FEED_MAX_PER_TYPE
    ]
    return [{"type": "event", "occurred_at": e.occurred_at, "object": e} for e in events]


def _feed_activities(facility, start_dt, end_dt, user, include_all):
    """Activity-Zweig des Feeds (Refs #1892, Extraktion aus ``build_feed_items``)."""
    from core.services.dashboard.activity import activity_target_prefetch

    activities_qs = Activity.objects.for_facility(facility).filter(occurred_at__gte=start_dt, occurred_at__lte=end_dt)
    # In mixed feed: exclude 'created' activities (redundant with first-class object cards)
    if include_all:
        activities_qs = activities_qs.exclude(verb=Activity.Verb.CREATED)

    # Filter out activities whose target is an Event the user may not see (#562)
    if user is not None:
        from django.contrib.contenttypes.models import ContentType

        event_ct = ContentType.objects.get_for_model(Event)
        # ``facility`` gehoert mit in den Subselect (Refs #2028): ohne das
        # Praedikat laeuft der NOT-IN ueber ``core_event`` aller Einrichtungen
        # und waechst damit mit der Gesamtinstallation statt mit der eigenen
        # Einrichtung. Das Ergebnis aendert sich nicht — Event-PKs sind global
        # eindeutig, ein ``target_id`` dieser Facility zeigt nie auf ein Event
        # einer anderen; es ist reine Queryform (Muster wie ``_feed_events``).
        visible_event_ids = Event.objects.visible_to(user).filter(facility=facility).values_list("pk", flat=True)
        activities_qs = activities_qs.exclude(
            Q(target_type=event_ct) & ~Q(target_id__in=visible_event_ids),
        )

    # Faelle im Papierkorb aus dem Zeitstrom nehmen (Refs #1818, ADR-044 §7).
    # ``close_case``/``reopen_case`` rendern den Falltitel aus dem Ziel. Der
    # Papierkorb nimmt den Fall aus
    # Liste, Detail, Episoden, Zielen und Ereignis-Select — der Zeitstrom
    # bliebe sonst der eine Ort, an dem sein Titel weiterlaeuft, und zwar
    # bis zur Frist (``retention_activities_days``, Default 365 gegen 30 Tage;
    # der Befund aus #1751). Rollenunabhaengig, weil „geloescht" keine
    # Sichtbarkeitsfrage ist.
    #
    # Auch die ``deleted``-Zeile („Fall in den Papierkorb verschoben",
    # titelfrei) faellt mit raus — die Rechenschaft ueber die Loeschung
    # traegt die ``CASE_SOFT_DELETED``-Zeile im append-only AuditLog, nicht
    # der Zeitstrom. Sie ist trotzdem nicht umsonst geschrieben: nach
    # ``restore_case`` erscheint der Fall wieder, und mit ihm die Spur, dass
    # er zwischenzeitlich im Papierkorb lag.
    from django.contrib.contenttypes.models import ContentType

    case_ct = ContentType.objects.get_for_model(Case)
    trashed_case_ids = Case.objects.for_facility(facility).filter(is_deleted=True).values_list("pk", flat=True)
    activities_qs = activities_qs.exclude(Q(target_type=case_ct) & Q(target_id__in=trashed_case_ids))

    activities = (
        activities_qs.select_related("actor", "target_type")
        .prefetch_related(activity_target_prefetch())
        .order_by("-occurred_at")[:FEED_MAX_PER_TYPE]
    )
    return [{"type": "activity", "occurred_at": a.occurred_at, "object": a} for a in activities]


def _feed_workitems(facility, start_dt, end_dt):
    """WorkItem-Zweig des Feeds (Refs #1892, Extraktion aus ``build_feed_items``)."""
    workitems = (
        WorkItem.objects.for_facility(facility)
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .select_related("client", "assigned_to")
        .order_by("-created_at")[:FEED_MAX_PER_TYPE]
    )
    return [{"type": "workitem", "occurred_at": wi.created_at, "object": wi} for wi in workitems]


def _feed_bans(facility, start_dt, end_dt, user):
    """Ban-Zweig des Feeds (Refs #1892, Extraktion aus ``build_feed_items``)."""
    bans = (
        Event.objects.visible_to(user)
        .filter(
            facility=facility,
            is_deleted=False,
            document_type__system_type="ban",
            occurred_at__gte=start_dt,
            occurred_at__lte=end_dt,
        )
        .select_related("document_type", "client", "created_by")
        .order_by("-occurred_at")[:FEED_MAX_PER_TYPE]
    )
    return [{"type": "ban", "occurred_at": e.occurred_at, "object": e} for e in bans]


def build_feed_items(facility, target_date, feed_type, time_filter=None, user=None):
    """Build a unified feed of events, activities, work items, and bans for a given date.

    *user* is required for sensitivity-based event visibility — without it the
    feed includes events whose existence the caller is not allowed to know
    about (#522). Callers must always pass ``request.user``.

    Refs #1892: je Feed-Sorte ein eigener ``_feed_<sorte>``-Builder; hier
    bleiben nur Zweig-Auswahl, Merge und Sortierung.
    """
    start_dt, end_dt = get_time_range(target_date, time_filter)
    include_all = feed_type == "" or feed_type == "all"

    events_list = (
        _feed_events(facility, start_dt, end_dt, user, include_all) if feed_type == "events" or include_all else []
    )
    activities_list = (
        _feed_activities(facility, start_dt, end_dt, user, include_all)
        if feed_type == "activities" or include_all
        else []
    )
    workitems_list = _feed_workitems(facility, start_dt, end_dt) if feed_type == "workitems" or include_all else []
    bans_list = _feed_bans(facility, start_dt, end_dt, user) if feed_type == "bans" or include_all else []

    return sorted(
        events_list + activities_list + workitems_list + bans_list,
        key=lambda x: x["occurred_at"],
        reverse=True,
    )[:FEED_MAX_PER_TYPE]


def _format_preview_value(value, ft):
    """Format a data_json value for preview display."""
    if ft.field_type == "boolean":
        return "Ja" if value else "Nein"

    # Encrypted selections are stored as an envelope; unwrap before mapping labels.
    if ft.field_type in ("select", "multi_select") and ft.options_json and is_encrypted_value(value):
        value = safe_decrypt(value)

    if ft.field_type == "select" and ft.options_json and isinstance(value, str):
        return option_label_map(ft.options_json).get(value, value)

    if ft.field_type == "multi_select" and ft.options_json and isinstance(value, list):
        label_map = option_label_map(ft.options_json)
        return ", ".join(label_map.get(v, str(v)) for v in value)

    if isinstance(value, dict):
        # File-Marker (Stufe A: einzelnes Attachment, Stufe B: Liste mit Versionen)
        # treten in `data_json` fuer File-Felder auf (siehe services/event.py:250-267).
        # Sie wuerden sonst als rohes Dict-Repr im Preview landen — Privacy-Leak
        # (UUIDs sichtbar) und UX-Bug (Refs #670).
        if value.get("__file__"):
            return _("[Datei]")
        if value.get("__files__"):
            count = len(value.get("entries") or [])
            return ngettext("[%(n)d Datei]", "[%(n)d Dateien]", count) % {"n": count}
        return safe_decrypt(value, default="[verschlüsselt]")

    return str(value)


def _build_event_preview_fields(event, field_templates, user):
    """Refs #1160: ``(preview_fields, expanded_fields)`` fuer ein Event bauen.

    Aus :func:`enrich_events_with_preview` herausgezogen. Wirkungs-identisch:

    - Felder, die der User nicht sehen darf, sowie leere/None/leere-Liste- und
      falsy-BOOLEAN-Werte werden uebersprungen.
    - ``expanded_fields`` enthaelt alle uebrigen Felder (inkl. textarea),
      ``preview_fields`` bleibt kompakt (max. 3, keine textarea) — Refs #707.
    """
    doc_sensitivity = event.document_type.sensitivity
    data = event.data_json or {}
    preview_fields = []
    expanded_fields = []

    for ft in field_templates:
        if not user_can_see_field(user, doc_sensitivity, ft.sensitivity):
            continue
        value = data.get(ft.slug)
        if value is None or value == "":
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if ft.field_type == "boolean" and not value:
            continue
        formatted = _format_preview_value(value, ft)
        entry = {"label": ft.name, "value": formatted, "is_textarea": ft.field_type == "textarea"}
        expanded_fields.append(entry)
        # Preview bleibt kompakt (max 3 Felder, keine textareas);
        # Expanded zeigt alles inkl. textarea, damit Notizen ohne
        # Detail-Klick lesbar sind (Refs #707).
        if ft.field_type != "textarea" and len(preview_fields) < 3:
            preview_fields.append(entry)

    return preview_fields, expanded_fields


def enrich_events_with_preview(feed_items, user):
    """Attach preview_fields to each event/ban in feed_items."""
    dt_ids = {item["object"].document_type_id for item in feed_items if item["type"] in ("event", "ban")}
    if not dt_ids:
        return

    # #1713: Vorschau-Feldreihenfolge = globale Block-Sequenz je DocumentType
    # (identisch zur Formular-Reihenfolge), damit die Feed-Vorschau nicht anders
    # sortiert als das Event-Formular. Ein flaches ``order_by("sort_order")``
    # würde gruppierte Felder unter neuer Semantik scrambeln.
    dt_fields = {dt_id: [dtf.field_template for dtf in iter_fields_in_block_order(dt_id)] for dt_id in dt_ids}

    for item in feed_items:
        if item["type"] not in ("event", "ban"):
            continue

        event = item["object"]
        preview_fields, expanded_fields = _build_event_preview_fields(
            event, dt_fields.get(event.document_type_id, []), user
        )
        event.preview_fields = preview_fields
        event.expanded_fields = expanded_fields
