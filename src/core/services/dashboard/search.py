"""Service layer for search logic."""

import logging

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from core.models import Client, Event
from core.services.compliance import allowed_sensitivities_for_user

logger = logging.getLogger(__name__)

DEFAULT_TRIGRAM_THRESHOLD = 0.3

#: Refs #1803 (Konzept §3.4): unter zwei Zeichen wird keine Query ausgefuehrt.
#: Spart den ``icontains``-Scan ueber die ganze Tabelle bei einem einzelnen
#: Zeichen, haelt das 30/m-Ratelimit-Budget frei (ADR-026) und deckt sich mit
#: der Grenze, die ``search_similar_clients`` schon immer hatte.
MIN_QUERY_LENGTH = 2


def client_search_queryset(facility, query):
    """Exakte (Teilstring-)Treffer im Pseudonym — als Queryset, nicht als Liste.

    Refs #1803: Zaehlen (``search_result_counts``) und Paginieren
    (``?type=person``) brauchen dasselbe Queryset, das auch die Uebersicht
    kappt. Eine zweite, handgepflegte Filterkette waere genau die Stelle, an
    der der Papierkorb-Filter beim naechsten Mal wieder fehlt.
    """
    return Client.objects.for_facility(facility).filter(pseudonym__ilike=query, is_active=True, is_deleted=False)


def event_search_queryset(facility, user, query):
    """Ereignisse ueber Pseudonym ODER Suchindex — ein Queryset statt zwei Listen.

    Vorher liefen die beiden Zweige getrennt und wurden in Python
    dedupliziert; damit gab es nichts, worauf ``COUNT(*)`` oder ein Paginator
    haetten aufsetzen koennen. Das ``Q``-ODER liefert dieselbe Menge (der
    ``client``-Join ist n:1, vervielfacht also keine Zeilen) und traegt die
    Modell-Sortierung ``-occurred_at``.

    Der Sensitivity-Filter bleibt, wo er ist: ``Event.search_text`` enthaelt
    schreibseitig nur unverschluesselte, nicht erhoehte Feldwerte (ADR-016);
    der Lesefilter deckelt zusaetzlich ueber den Dokumentationstyp.

    Refs #1803: ``exclude(client__is_deleted=True)`` — Ereignisse einer Person
    im Papierkorb fallen KOMPLETT aus der Suche, auch die, die nur ueber
    ``search_text`` matchen. Ohne den Filter blieb das Pseudonym einer
    geloeschten Person ueber ihre Ereignis-Trefferkarten lesbar und ihre
    Dokumentation von dort aus aufblaetterbar — derselbe Leak wie im
    Client-Zweig, nur eine Flaeche weiter.

    Das ist eine BEWUSSTE Nicht-Paritaet zum Zeitstrom (``build_feed_items``),
    der diese Ereignisse weiter zeigt: Die Suche ist ein Nachschlagewerkzeug —
    was sie nicht findet, ist praktisch weg, und genau das soll der Papierkorb
    bewirken. Der Zeitstrom ist ein chronologisches Protokoll — eine Luecke
    darin waere eine Faelschung des Verlaufs. Ereignisse ohne ``client``
    (``SET_NULL``) haengen an keinem Papierkorb und bleiben suchbar; das
    ``exclude`` ueber die nullable Relation laesst sie stehen (Django haengt
    den ``IS NOT NULL``-Vorbehalt selbst an).
    """
    allowed_sens = allowed_sensitivities_for_user(user)
    return (
        Event.objects.for_facility(facility)
        .filter(is_deleted=False, document_type__sensitivity__in=allowed_sens)
        .exclude(client__is_deleted=True)
        .filter(Q(client__pseudonym__ilike=query) | Q(search_text__ilike=query))
        .select_related("document_type", "client")
    )


def search_result_counts(facility, user, query):
    """Gesamtzahlen je Gruppe — ``{"clients": n, "events": m}``.

    Refs #1803 (Konzept §3.5, Regel L7): Eine Zahl in einer Gruppenueberschrift
    ist die Gesamtzahl, nie die Anzeige-Kappe. Vorher zeigte ``/search/`` bei 21
    Personen „Personen (20)" und verschwieg den Rest.

    Zwei ``COUNT(*)`` auf denselben Querysets, die auch die Listen liefern. Der
    Dropdown-Pfad ruft das bewusst NICHT auf und bleibt unveraendert schnell.
    """
    if not query or len(query) < MIN_QUERY_LENGTH:
        return {"clients": 0, "events": 0}
    return {
        "clients": client_search_queryset(facility, query).count(),
        "events": event_search_queryset(facility, user, query).count(),
    }


def search_clients_and_events(facility, user, query, max_clients=20, max_events=20):
    """Search clients and events within a facility.

    Refs #827 (C-60): Volltextsuche laeuft gegen die ``Event.search_text``-
    Spalte (im create/update-Pfad gepflegt, GIN-trgm-Index), nicht mehr
    gegen ``data_json__icontains``. Verschluesselte und ELEVATED/HIGH-
    Felder sind im Suchindex bewusst nicht enthalten — der Sensitivity-
    Filter geschieht damit beim Schreiben, nicht beim Lesen.

    Refs #1803: Der Client-Zweig filtert ``is_deleted=False`` — identisch zu
    ``ClientListView``/``ClientDetailView``. ``soft_delete()`` setzt
    ``is_active`` nicht mit; ohne den Filter blieb eine Person im Papierkorb
    auffindbar und ihr Treffer verlinkte auf einen 404.

    Returns (clients, events) tuple.
    """
    if not query:
        return [], []

    clients = list(client_search_queryset(facility, query)[:max_clients])
    events = list(event_search_queryset(facility, user, query)[:max_events])

    return clients, events


def search_similar_clients(facility, query, exclude_pks=None, max_results=10, threshold=None):
    """Return active clients whose pseudonym is similar to ``query`` (pg_trgm).

    Uses ``TrigramSimilarity`` on ``Client.pseudonym`` — tolerant to typos
    and phonetic variants ("Schmidt" vs. "Schmitt").

    Exact substring matches (``pseudonym__ilike=query``) are always excluded,
    including those that overflow the caller's display cap — otherwise excess
    icontains hits would be silently relabeled as fuzzy and displace real
    similar results (Refs #580). ``exclude_pks`` is still honored for
    additional caller-specific dedup.

    ``threshold`` defaults to ``facility.settings.search_trigram_threshold``.

    Refs #1803: ``is_deleted=False`` — der Papierkorb ist auch fuer den
    Fuzzy-Zweig unsichtbar (siehe ``search_clients_and_events``).
    """
    if not query or len(query) < 2:
        return []

    if threshold is None:
        settings_obj = getattr(facility, "settings", None)
        threshold = getattr(settings_obj, "search_trigram_threshold", DEFAULT_TRIGRAM_THRESHOLD)

    qs = (
        Client.objects.for_facility(facility)
        .filter(is_active=True, is_deleted=False)
        .annotate(similarity=TrigramSimilarity("pseudonym", query))
        .filter(similarity__gte=threshold)
        .exclude(pseudonym__ilike=query)
        .order_by("-similarity")
    )
    if exclude_pks:
        qs = qs.exclude(pk__in=exclude_pks)
    return list(qs[:max_results])
