"""Strichlisten-/Tally-Aggregation für anonyme Massenkontakte (Refs #1349, Stufe 2).

Zählt anonyme Kontakte (``Event.is_anonymous=True``) je Dokumentationstyp im
aktuellen Zeitfenster — bewusst OHNE eigenes Zähler-Model, als reine
Aggregation über die bestehenden Events (Vorbild: die Dienst-Übersicht der
laufenden Schicht, ``ZeitstromView._build_current_shift_summary``). Das
Zeitfenster ist die gerade laufende Schicht (falls ein ``TimeFilter`` den
aktuellen Zeitpunkt abdeckt), sonst der komplette heutige Tag.
"""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from core.models import DocumentType, Event, TimeFilter
from core.services.compliance import allowed_sensitivities_for_user
from core.services.events.feed import get_time_range


def current_tally_window(facility):
    """Aktuelles Zeitfenster für die Strichliste als ``(start_dt, end_dt)``.

    Deckt ein aktiver ``TimeFilter`` den jetzigen Zeitpunkt ab, wird dessen
    Schicht-Fenster verwendet (inklusive Mitternachts-Überlappung: in den
    frühen Morgenstunden gehört der Zeitpunkt zum Nachtdienst des Vortags).
    Läuft gerade keine Schicht, ist das Fenster der komplette heutige Tag.
    """
    now = timezone.localtime()
    for tf in TimeFilter.objects.for_facility(facility).filter(is_active=True):
        if not tf.covers_time(now):
            continue
        shift_date = timezone.localdate()
        if tf.start_time > tf.end_time and now.time() <= tf.end_time:
            shift_date = shift_date - timedelta(days=1)
        return get_time_range(shift_date, tf)
    return get_time_range(timezone.localdate(), None)


def tally_document_types(facility, user):
    """Für die Strichliste anbietbare Dokumentationstypen.

    Nur aktive Typen der Facility, die (a) der User seiner Rolle nach sehen
    darf (Sensitivity-Guard) und (b) anonymfähig sind — Typen mit einer
    Mindest-Kontaktstufe (``min_contact_stage``) sind NIE anonym
    (``create_event`` erzwingt das) und dürfen daher nicht per „+1" erfassbar
    sein.
    """
    return (
        DocumentType.objects.for_facility(facility)
        .filter(
            Q(min_contact_stage__isnull=True) | Q(min_contact_stage=""),
            is_active=True,
            sensitivity__in=allowed_sensitivities_for_user(user),
        )
        .order_by("name")
    )


def build_tally_summary(facility, user):
    """Strichlisten-Aggregation für die Zeitstrom-Startseite.

    Liefert ein Dict ``{"window_start", "window_end", "rows"}`` — ``rows`` ist
    je anonymfähigem, sichtbarem Dokumentationstyp ein
    ``{"document_type", "count"}`` mit der Anzahl anonymer Kontakte im
    aktuellen Zeitfenster. Gezählt werden ausschließlich für den User sichtbare
    (``Event.objects.visible_to``), nicht gelöschte, anonyme Events.
    """
    start_dt, end_dt = current_tally_window(facility)
    doc_types = list(tally_document_types(facility, user))

    counts = dict(
        Event.objects.visible_to(user)
        .filter(
            facility=facility,
            is_deleted=False,
            is_anonymous=True,
            occurred_at__range=(start_dt, end_dt),
        )
        .values_list("document_type_id")
        .annotate(n=Count("id"))
    )

    rows = [{"document_type": dt, "count": counts.get(dt.pk, 0)} for dt in doc_types]
    return {"window_start": start_dt, "window_end": end_dt, "rows": rows}
