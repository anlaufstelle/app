"""Service layer for search logic."""

import logging

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from core.models import Client, Event
from core.services.compliance import allowed_sensitivities_for_user
from core.services.workitem_visibility import readable_person_filter

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
    """Search events by pseudonym or indexed values within role and facility scope.

    The write path indexes only unencrypted fields with effective Normal
    sensitivity (ADR-016/043, Refs #2525). The read filter additionally limits
    document types to the user's allowed levels. Pseudonym matches remain
    available for those document types even when their field index is empty.

    Events belonging to deleted or inactive people are excluded, including
    matches found solely through the index. Events without a person remain
    searchable, matching the chronological feed and ordinary event readers.
    """
    allowed_sens = allowed_sensitivities_for_user(user)
    return (
        Event.objects.for_facility(facility)
        .filter(is_deleted=False, document_type__sensitivity__in=allowed_sens)
        .filter(readable_person_filter())
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
    """Search clients and events within a facility, returning two result lists.

    Full-text matches use Event.search_text and its GIN trigram index rather
    than raw data_json (Refs #827). At write time, the index excludes encrypted
    fields and every field whose effective sensitivity is above Normal,
    including inherited levels (ADR-016/043, Refs #2525). At read time, a
    document-type filter additionally enforces role visibility. Pseudonym
    search is independent of the field index and retains that role filter.

    Deleted clients and their events are excluded, matching the client list
    and detail visibility rules (Refs #1803).
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
