"""Statistics service: aggregations over events."""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.utils.formatting import parse_date

logger = logging.getLogger(__name__)

STATISTICS_MV_NAME = "core_statistics_event_flat"

# Refs #1589: Obergrenze fuer den ``period=custom``-Zeitraum. Ohne Cap laeuft
# ``_split_into_segments`` den Bereich Monat fuer Monat ohne Ende ab und loest
# pro Segment ~6 Live-Aggregat-Queries aus — ein einziger Request mit
# ``date_from=0001-01-01&date_to=9999-12-31`` ergibt ~10^5 Segmente und wird zum
# synchronen DB-DoS. 1096 Tage (~3 Jahre inkl. Schaltjahr) deckt jeden
# plausiblen Traeger-/Foerderberichtszeitraum ab; der Wert ist eine
# Produktentscheidung und kann bei Bedarf angehoben werden.
STATISTICS_MAX_CUSTOM_RANGE_DAYS = 1096


class InvalidStatisticsPeriodError(ValueError):
    """Refs #1589: Ungueltiger custom-Zeitraum (Bereich zu gross oder invertiert).

    Die Views fangen diese Exception und antworten mit HTTP 400 statt einen
    unbegrenzten Live-Aggregationslauf auszuloesen.
    """


def validate_statistics_range(date_from: date, date_to: date) -> None:
    """Refs #1589: erzwingt ``date_from <= date_to`` und die Maximalspanne.

    Single Source of Truth fuer die Bereichsvalidierung — genutzt vom
    Period-Parsing (Dashboard/Chart/External-Report) und den Export-Views, die
    ``date_from``/``date_to`` direkt aus der Query lesen.
    """
    if date_from > date_to:
        raise InvalidStatisticsPeriodError(f"date_from ({date_from}) darf nicht nach date_to ({date_to}) liegen.")
    if (date_to - date_from).days > STATISTICS_MAX_CUSTOM_RANGE_DAYS:
        raise InvalidStatisticsPeriodError(
            f"Der Zeitraum ueberschreitet das Maximum von {STATISTICS_MAX_CUSTOM_RANGE_DAYS} Tagen."
        )


# ---------------------------------------------------------------------------
# Zeitraum-Schluessel (Refs #1804, Konzept §3.6 / Entscheide F1+F5)
# ---------------------------------------------------------------------------
#
# Kanonisch sind deutsche Schluessel, und rollierende Fenster heissen nach ihrer
# Laenge (Z2): „letzte30" statt „month". Vier Kalenderwoerter in einem
# Pill-Streifen, von denen drei rollierende Fenster meinten, waren keine
# Ungenauigkeit, sondern eine falsche Zusage — „Monat" hiess ``today-30 …
# today`` und traf nie ein Kalendersegment.
#
# ``jahr`` ist der einzige kalendarische Schluessel (mit Stepper), ``frei`` der
# freie Zeitraum. Kalendermonat/-quartal sind bewusst NICHT gebaut (Entscheid
# F2) — sie liessen sich hier ohne Umbau nachruesten.
PERIOD_LETZTE_30 = "letzte30"
PERIOD_LETZTE_90 = "letzte90"
PERIOD_LETZTE_180 = "letzte180"
PERIOD_JAHR = "jahr"
PERIOD_FREI = "frei"

DEFAULT_PERIOD = PERIOD_LETZTE_30

# Fensterlaenge in Tagen — der Name IST die Zusage (Z2/Z4). Beide Enden sind
# inklusiv, deshalb beginnt das Fenster ``laenge - 1`` Tage vor heute.
ROLLING_WINDOW_DAYS: dict[str, int] = {
    PERIOD_LETZTE_30: 30,
    PERIOD_LETZTE_90: 90,
    PERIOD_LETZTE_180: 180,
}

CANONICAL_PERIODS: frozenset[str] = frozenset(
    {PERIOD_LETZTE_30, PERIOD_LETZTE_90, PERIOD_LETZTE_180, PERIOD_JAHR, PERIOD_FREI}
)

# Die Werte des Bestands bleiben DAUERHAFT stille Aliasse (Entscheid F5): sie
# stehen in Bookmarks, in geteilten Deep-Links und im ``sessionStorage`` der
# Filter-Persistenz. Sie werden nirgends mehr angeboten, aber immer akzeptiert —
# das kostet diese eine Tabelle und schuetzt jeden gespeicherten Link.
PERIOD_ALIASES: dict[str, str] = {
    "month": PERIOD_LETZTE_30,
    "quarter": PERIOD_LETZTE_90,
    "half": PERIOD_LETZTE_180,
    "year": PERIOD_JAHR,
    "custom": PERIOD_FREI,
}


def normalize_period(value: str | None) -> str:
    """Kanonischer Zeitraum-Schluessel zu einem beliebigen Eingabewert.

    Unbekanntes faellt auf die Vorgabe zurueck — dieselbe Gutmuetigkeit wie beim
    Modus-Parameter (``?ansicht=quatsch`` → Uebersicht): ein Tippfehler in einem
    Deep-Link soll eine bekannte Seite zeigen, keinen Zustand ohne aktiven Pill.
    """
    if not value:
        return DEFAULT_PERIOD
    if value in CANONICAL_PERIODS:
        return value
    return PERIOD_ALIASES.get(value, DEFAULT_PERIOD)


def _first_present(query_params, *keys):
    """Erster Schluessel mit nicht-leerem Wert — Zweitschluessel nur bei LEEREM Erstem.

    Ein ``or``-Kurzschluss ueber ``.get()`` waere hier subtil falsch: ein
    ausdruecklich leeres ``?zeitraum=`` wuerde den Alias gewinnen lassen, ohne
    dass irgendwo sichtbar waere, welcher Parameter entschieden hat.
    """
    for key in keys:
        value = query_params.get(key)
        if value:
            return value
    return None


# Modus-Parameter des Statistik-Bereichs (Refs #1804, F1/F5). Kanonisch
# ``?ansicht=datensaetze``; ``?view=berichte`` bleibt dauerhaft Alias — es steht
# seit #1813 in Bookmarks und in der Reiterzeile ausgelieferter Staende.
VIEW_DATENSAETZE = "datensaetze"
VIEW_ALIASES: dict[str, str] = {"berichte": VIEW_DATENSAETZE}


def parse_statistics_view(query_params) -> str:
    """Kanonischer Modus-Schluessel oder ``""`` fuer die Uebersicht.

    Der Rueckgabewert ist bewusst der leere String statt ``None``: er ist im
    Template direkt als Wahrheitswert und als Attributwert verwendbar.
    """
    raw = _first_present(query_params, "ansicht", "view") or ""
    if raw == VIEW_DATENSAETZE:
        return VIEW_DATENSAETZE
    return VIEW_ALIASES.get(raw, "")


@dataclass(frozen=True)
class PeriodState:
    """Refs #816 (C-49): Aufgeloeste Period-Konfiguration fuer Statistik-Sichten.

    ``period`` ist der **kanonische** Schluessel (``letzte30`` | ``letzte90`` |
    ``letzte180`` | ``jahr`` | ``frei``) — nie der eingehende Alias, damit
    Templates und Tests genau einen Wertebereich kennen. ``selected_year`` ist
    gesetzt, wenn ``period == "jahr"``, sonst ``None``.
    """

    period: str
    date_from: date
    date_to: date
    selected_year: int | None = None


def _parse_year(value, default: int) -> int:
    if not value:
        return default
    try:
        year = int(value)
    except (ValueError, TypeError):
        return default
    return max(1, min(year, default))


def parse_statistics_period(query_params, today: date) -> PeriodState:
    """Refs #816 (C-49): Single Source of Truth fuer Period-Parsing.

    Frueher interpretierten ``StatisticsView`` und ``ChartDataView`` dieselben
    Query-Parameter separat — Drift-Risiko zwischen Dashboard und Chart-API.

    Refs #1804 (F1/F5): Hier — und nur hier — wird die Alt-Schreibweise
    (``?period=``, ``?date_from=``/``?date_to=``, ``?year=``) auf die kanonische
    deutsche normalisiert. Ein zweiter Normalisierungsort waere die naechste
    Drift-Quelle; deshalb liest jede Statistik-Sicht ihren Zeitraum von hier.
    """
    period = normalize_period(_first_present(query_params, "zeitraum", "period"))
    selected_year: int | None = None

    if period == PERIOD_FREI:
        # parse_date faellt auf den Default zurueck — das ist hier ein
        # konkretes date, also ist None nicht erreichbar.
        custom_default_from = today - timedelta(days=ROLLING_WINDOW_DAYS[PERIOD_LETZTE_30] - 1)
        raw_from = _first_present(query_params, "von", "date_from")
        raw_to = _first_present(query_params, "bis", "date_to")
        date_from = parse_date(raw_from, custom_default_from) or custom_default_from
        date_to = parse_date(raw_to, today) or today
        # Refs #1589: unbegrenzte custom-Spanne = Query-Amplification-DoS.
        validate_statistics_range(date_from, date_to)
    elif period == PERIOD_JAHR:
        selected_year = _parse_year(_first_present(query_params, "jahr", "year"), today.year)
        date_from = date(selected_year, 1, 1)
        date_to = today if selected_year == today.year else date(selected_year, 12, 31)
    else:
        # Z4 (Refs #1804): beide Enden inklusiv, Laenge == Name. „Letzte 30 Tage"
        # ist ``heute-29 … heute``; der Bestand rechnete ``heute-30 … heute`` und
        # lieferte damit 31 Tage unter einem 30er-Etikett (bzw. 183 unter „Halbjahr").
        date_from = today - timedelta(days=ROLLING_WINDOW_DAYS[period] - 1)
        date_to = today

    return PeriodState(period=period, date_from=date_from, date_to=date_to, selected_year=selected_year)


# K0-b (Refs #1770): Schluesselname der DISTINCT-Personenzahl in
# personenabgeleiteten Aufschluesselungszeilen (``by_age_cluster``). Der
# Suppressionspfad erkennt personenabgeleitete Zeilen an genau diesem Feld —
# siehe ``core.services.dashboard.external_report``.
PERSONS_KEY = "persons"

# Filterbedingungen der drei Kontaktstufen — als Modulkonstanten, damit
# Ereignis- und Personenzaehlung garantiert dieselbe Grundgesamtheit treffen
# (K0-b, Refs #1770). Anonym ist bewusst „is_anonymous ODER client IS NULL".
_STAGE_ANONYM_Q = Q(is_anonymous=True) | Q(client__isnull=True)
_STAGE_IDENTIFIED_Q = ~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="identified")
_STAGE_QUALIFIED_Q = ~Q(is_anonymous=True) & Q(client__isnull=False) & Q(client__contact_stage="qualified")


def event_queryset_for_period(facility, date_from, date_to, document_type=None):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum — Basis aller Aggregate.

    Einziger Ort, an dem der Zeitraumfilter der Statistik-/Berichtspfade
    entsteht: Ereigniszahlen und Personenzahlen muessen ueber **dieselbe**
    Grundgesamtheit laufen, sonst vergleicht die k-Schwelle Aepfel mit Birnen
    (K0-b, Refs #1770).

    ``document_type`` (Refs #1804, Entscheid F3) verengt dieselbe Grundgesamtheit
    fuer ALLE Aggregate gemeinsam. Der Parameter gehoert genau hierher: ein
    Filter, der nur in einzelnen Aggregaten landet, erzeugt Kennzahl und Tabelle
    ueber verschiedenen Mengen — die Divergenzklasse, die #1804 schliesst.
    """
    qs = Event.objects.for_facility(facility).filter(
        is_deleted=False, occurred_at__date__gte=date_from, occurred_at__date__lte=date_to
    )
    if document_type is not None:
        qs = qs.filter(document_type=document_type)
    return qs


def count_unique_clients(facility, date_from, date_to, document_type=None) -> int:
    """DISTINCT-Personenzahl der Einrichtung **im Zeitraum** (K0-b/K4b, Refs #1770).

    Bewusst **nicht** ``core.services.compliance.count_clients_in_bucket``: jene
    Funktion gehoert dem Retention-/Anonymisierungspfad, zaehlt **alle** Klienten
    der Einrichtung und kennt keinen Zeitraumfilter. Als Suppressionskriterium
    waere sie *fail-open* — sie meldet einen Bucket gross, dessen Personenmenge
    im Berichtszeitraum aus einer einzigen Person besteht. Fuer den
    Berichtspfad gilt deshalb ausschliesslich diese zeitraum-skopierte Variante.

    Zaehlt Personen, nicht Ereignisse, und ist damit **nicht additiv**: die
    Zahl darf nie ueber Monatssegmente summiert werden (K4b) — der Hybrid-Pfad
    ruft sie stattdessen einmal ueber den Gesamtzeitraum auf.
    """
    return (
        event_queryset_for_period(facility, date_from, date_to, document_type)
        .exclude(client__isnull=True)
        .values("client")
        .distinct()
        .count()
    )


def _aggregate_contact_stage(base_qs) -> tuple[dict, dict]:
    """``(by_contact_stage, persons_by_contact_stage)`` — Refs #1827, aus
    ``get_statistics`` extrahiert (einer von vier Aggregat-Helfern).

    Contact stages: anonymous = is_anonymous OR client IS NULL. Ereignis- und
    Personenzahlen in EINER Aggregation — dieselben Filter, damit Zaehler und
    k-Kriterium nicht auseinanderlaufen koennen.
    """
    stage_qs = base_qs.aggregate(
        anonym=Count("id", filter=_STAGE_ANONYM_Q),
        identifiziert=Count("id", filter=_STAGE_IDENTIFIED_Q),
        qualifiziert=Count("id", filter=_STAGE_QUALIFIED_Q),
        anonym_persons=Count("client", filter=_STAGE_ANONYM_Q, distinct=True),
        anonym_ohne_person=Count("id", filter=Q(client__isnull=True)),
        identifiziert_persons=Count("client", filter=_STAGE_IDENTIFIED_Q, distinct=True),
        qualifiziert_persons=Count("client", filter=_STAGE_QUALIFIED_Q, distinct=True),
    )
    by_contact_stage = {
        "anonym": stage_qs["anonym"],
        "identifiziert": stage_qs["identifiziert"],
        "qualifiziert": stage_qs["qualifiziert"],
    }
    # Ereignisse ohne Klienten-FK tragen ueberhaupt keinen Personenbezug: sie
    # sind untereinander nicht verknuepfbar, also zaehlt jedes als eigenes
    # Individuum. Wuerde man sie mit 0 Personen bewerten, waere die Stufe
    # „anonym" in einer niedrigschwelligen Einrichtung dauerhaft unterdrueckt,
    # ohne dass irgendjemand geschuetzt wuerde — es gibt dort keine
    # Aequivalenzklasse, die eine Person heraussingularisieren koennte.
    persons_by_contact_stage = {
        "anonym": stage_qs["anonym_persons"] + stage_qs["anonym_ohne_person"],
        "identifiziert": stage_qs["identifiziert_persons"],
        "qualifiziert": stage_qs["qualifiziert_persons"],
    }
    return by_contact_stage, persons_by_contact_stage


def _aggregate_by_document_type(base_qs) -> list[dict]:
    """``by_document_type`` — Refs #1827, aus ``get_statistics`` extrahiert.

    Refs #1749 (ADR-040): Gruppierung allein ueber ``name`` — seit dem Wegfall
    von ``category`` ist der Name je Einrichtung eindeutig (UniqueConstraint
    ``unique_facility_documenttype_name``), das Paar war nur dessen
    Zweitschluessel.
    """
    rows = base_qs.values("document_type__name").annotate(count=Count("id")).order_by("-count")
    return [{"name": row["document_type__name"], "count": row["count"]} for row in rows]


def _aggregate_by_age_cluster(base_qs) -> list[dict]:
    """``by_age_cluster`` (nur Ereignisse mit Klientenbezug) — Refs #1827, aus
    ``get_statistics`` extrahiert."""
    age_labels = {
        "u18": _("Unter 18"),
        "18_26": _("18–26"),
        "27_plus": _("27+"),
        "unknown": _("Unbekannt"),
    }
    rows = (
        base_qs.exclude(client__isnull=True)
        .values("client__age_cluster")
        .annotate(count=Count("id"), persons=Count("client", distinct=True))
        .order_by("-count")
    )
    return [
        {
            "cluster": row["client__age_cluster"],
            "label": age_labels.get(row["client__age_cluster"], row["client__age_cluster"]),
            "count": row["count"],
            # K0-b (Refs #1770): DISTINCT-Personenzahl des Buckets im Zeitraum.
            PERSONS_KEY: row["persons"],
        }
        for row in rows
    ]


def _aggregate_top_clients(base_qs) -> list[dict]:
    """Top-5-Klient:innen nach Ereigniszahl — Refs #1827, aus
    ``get_statistics`` extrahiert."""
    rows = (
        base_qs.exclude(client__isnull=True)
        .values("client__pseudonym")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )
    return [{"pseudonym": row["client__pseudonym"], "count": row["count"]} for row in rows]


def get_statistics(facility, date_from, date_to, document_type=None):
    """Statistics data for a facility in the given period.

    Returns dict with: total_contacts, by_contact_stage, persons_by_contact_stage,
    by_document_type, by_age_cluster (inkl. ``persons``), top_clients,
    unique_clients.

    K0-b (Refs #1770): Jede personenabgeleitete Aufschluesselung traegt
    zusaetzlich zur Ereigniszahl die **DISTINCT-Personenzahl** ihres Buckets —
    die Groesse, gegen die die k-Schwelle im Export-/PDF-Pfad prueft. Sie ist
    Suppressions-*Eingabe*, kein publizierter Wert: ``_suppress_small``
    entfernt sie, bevor eine Zeile in ein Artefakt geht.

    Refs #1827: die vier unabhaengigen Aggregationen (Kontaktstufen,
    Dokumentationstyp, Altersgruppe, Top-Klient:innen) sind in eigene private
    Helfer ausgelagert — jede liest denselben ``base_qs``, schreibt aber einen
    eigenen, unabhaengigen Teil des Rueckgabe-Dicts.
    """
    base_qs = event_queryset_for_period(facility, date_from, date_to, document_type)

    total_contacts = base_qs.count()
    by_contact_stage, persons_by_contact_stage = _aggregate_contact_stage(base_qs)
    by_document_type = _aggregate_by_document_type(base_qs)
    by_age_cluster = _aggregate_by_age_cluster(base_qs)
    top_clients = _aggregate_top_clients(base_qs)

    # Unique clients
    # Refs #1804 (F3): eigener Query-Zweig — ohne den Filter zaehlte er weiter
    # ueber alle Dokumentationstypen und widerspraeche der Kontaktzahl daneben.
    unique_clients = count_unique_clients(facility, date_from, date_to, document_type)

    return {
        "total_contacts": total_contacts,
        "by_contact_stage": by_contact_stage,
        "persons_by_contact_stage": persons_by_contact_stage,
        "by_document_type": by_document_type,
        "by_age_cluster": by_age_cluster,
        "top_clients": top_clients,
        "unique_clients": unique_clients,
    }


def get_statistics_for_events(events):
    """Snapshot-shaped statistics over an explicit event set (Refs #2425).

    Counts the events a retention run has just deleted, for the monthly
    snapshot's carry-over. They no longer pass ``event_queryset_for_period``
    (``is_deleted=False``), so the caller hands them over directly; the
    aggregates only read document type, person and date, which the retention
    run leaves in place. No ``top_clients`` (never stored in a snapshot) and no
    ``unique_clients`` (not additive across sets).
    """
    by_contact_stage, persons_by_contact_stage = _aggregate_contact_stage(events)
    return {
        "total_contacts": events.count(),
        "by_contact_stage": by_contact_stage,
        "persons_by_contact_stage": persons_by_contact_stage,
        "by_document_type": _aggregate_by_document_type(events),
        "by_age_cluster": _aggregate_by_age_cluster(events),
    }


# ---------------------------------------------------------------------------
# Materialized View Pfad (Refs #544)
# ---------------------------------------------------------------------------


def _flat_view_enabled() -> bool:
    """Return True wenn die Materialized View genutzt werden darf.

    Aktiviert wird der Pfad über ``settings.STATISTICS_USE_FLAT_VIEW = True``.
    Zusätzlich muss das Backend Postgres sein — SQLite hat keine
    Materialized Views (siehe Migration 0049).
    """
    return bool(getattr(settings, "STATISTICS_USE_FLAT_VIEW", False)) and connection.vendor == "postgresql"


def _query_statistics_mv(
    facility,
    *,
    select: str,
    where_extra: str = "",
    extra_params=(),
    group_by: str = "",
    order_by: str = "",
) -> list[tuple]:
    """Einziger erlaubter Lesezugriff auf die Statistik-Matview (Refs #1659).

    Die Matview ``core_statistics_event_flat`` hat keine RLS (Migration
    0049) — Zugriffsschutz ist reine Service-Schicht. Damit ein künftiger
    zweiter Zugriffspfad den ``facility_id``-Filter nicht vergessen kann,
    läuft JEDER lesende Zugriff auf die Matview durch diese Funktion:

    - ``facility`` ist ein Pflichtparameter ohne Default — ein Aufruf ohne
      Facility ist ein ``TypeError``, kein stiller Vollzugriff.
    - Der ``WHERE facility_id = %s``-Filter ist fest verdrahtet und steht
      vor jeder Zusatzbedingung; ``where_extra`` kann ihn per ``AND`` nur
      ERGÄNZEN, nie ersetzen.

    Ein Architecture-Guard
    (``src/tests/test_architecture_guards_statistics_mv.py``) stellt sicher,
    dass kein anderer Code in ``src/core`` den Matview-Namen referenziert —
    jeder künftige Zugriffspfad muss durch diese Funktion.
    """
    if facility is None:
        raise TypeError("facility ist Pflicht fuer einen Statistik-Matview-Zugriff (Refs #1659).")

    # STATISTICS_MV_NAME ist eine Modul-Konstante, kein User-Input.
    sql = f"SELECT {select} FROM {STATISTICS_MV_NAME} WHERE facility_id = %s"  # noqa: S608 — kein User-Input
    params = [facility.pk]
    if where_extra:
        sql += f" AND {where_extra}"
        params.extend(extra_params)
    if group_by:
        sql += f" GROUP BY {group_by}"
    if order_by:
        sql += f" ORDER BY {order_by}"

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def get_event_counts_by_month(facility, year):
    """Gesamte Event-Anzahl pro Monat für ein Kalenderjahr.

    Wählt je nach Feature-Flag den Pfad:
    - ``STATISTICS_USE_FLAT_VIEW=True`` + Postgres → Materialized View
      ``core_statistics_event_flat`` über ``_query_statistics_mv`` (dramatisch
      schneller bei vielen Events).
    - sonst → klassisches ``Event``-Queryset als Fallback.

    Rückgabe: Liste von Dicts ``{"month": 1..12, "count": int}``, lückenlos
    von Januar bis Dezember.
    """
    counts = {month: 0 for month in range(1, 13)}

    if _flat_view_enabled():
        rows = _query_statistics_mv(
            facility,
            select="EXTRACT(MONTH FROM month)::int AS m, COUNT(*)::bigint",
            where_extra="EXTRACT(YEAR FROM month)::int = %s",
            extra_params=[year],
            group_by="m",
            order_by="m",
        )
        for month, count in rows:
            counts[int(month)] = int(count)
    else:
        rows = (
            Event.objects.for_facility(facility)
            .filter(is_deleted=False, occurred_at__year=year)
            .values_list("occurred_at__month")
            .annotate(count=Count("id"))
        )
        for month, count in rows:
            counts[int(month)] = int(count)

    return [{"month": month, "count": counts[month]} for month in range(1, 13)]
