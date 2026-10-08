"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.dashboard.search``.

Quelle: ``src/core/services/dashboard/search.py``; Mutantenbaum
``mutants/src/core/services/dashboard/search.py``. Herkunft der Triage: der
``mutation-delta``-Lauf des 0.92-Checkpoints (Feld ``herkunft: "092"``), aus
dem der Meilenstein release-0.94 die noch offenen, als killbar bestaetigten
Eintraege des Modul-Batches ``search`` uebernommen hat — Vorgabe ist die
Survivor-Triage release-0.94 (Ledger des Meilensteins) (9 Mutanten). Vier Bloecke:

* :func:`~core.services.dashboard.search.event_search_queryset` — die beiden
  ``select_related``-Argumente. Sie aendern die Ergebnismenge nicht, nur die
  Zahl der Queries; im ganzen Suchmodul gab es bislang keine einzige
  Query-Zaehl-Zusicherung, weshalb ein N+1 auf der Trefferkarte (Typname und
  Pseudonym) unbemerkt zurueckkehren konnte.
* :func:`~core.services.dashboard.search.search_result_counts` — die
  Untergrenze ``MIN_QUERY_LENGTH``. Der Bestand kennt nur „zu kurz" (1
  Zeichen) und „lang genug" (5 Zeichen); die Grenze SELBST (exakt 2 Zeichen
  MIT Treffern) war ungeprueft, obwohl genau dort die Gruppenzahl von der
  tatsaechlichen Trefferliste abweichen wuerde.
* :func:`~core.services.dashboard.search.search_clients_and_events` — die
  Default-Kappen ``max_clients``/``max_events``. Alle Produktions-Aufrufer
  uebergeben die Werte explizit, der Default war damit in Tests wie in
  Produktion tot.
* :func:`~core.services.dashboard.search.search_similar_clients` — die
  Default-Kappe ``max_results`` und der Kurzschluss der Mindestlaenge
  (``or`` statt ``and``, sowie die beiden Off-by-one-Varianten der Grenze).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); KEINE Imports
aus ``test_rls_functional`` (its fixture ``rls_test_role`` skips itself under
``MUTANT_UNDER_TEST``; a pin requesting it never runs in a mutation run); Fixtures
kommen aus ``src/tests/conftest.py``
bzw. werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt
seine Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Client, Event
from core.services.dashboard.search import (
    client_search_queryset,
    event_search_queryset,
    search_clients_and_events,
    search_result_counts,
    search_similar_clients,
)

# ============================================================================
# core.services.dashboard.search.event_search_queryset
# ============================================================================


@pytest.mark.django_db
class TestEventSearchQuerysetLoadsTheCardFieldsInOneQuery:
    """Die Trefferkarte eines Ereignisses zeigt Dokumentationstyp UND Pseudonym.

    Beide Werte liegen hinter je einem ForeignKey; ``select_related`` holt sie
    im selben ``SELECT``. Faellt eines der Argumente weg, bleibt die
    Ergebnismenge identisch — die Suchliste laedt aber pro Treffer eine
    weitere Zeile nach (N+1). Genau deshalb ist die Zusicherung hier eine
    Query-ZAHL und keine Wertgleichheit: Wert-Assertions koennen diese
    Mutation prinzipiell nicht sehen.

    Der Aufbau des Querysets (inkl. ``allowed_sensitivities_for_user``, rein
    rechnend) steht bewusst VOR der Zaehlklammer; gezaehlt wird nur die
    Auswertung samt Zugriff auf beide Relationen.
    """

    @pytest.fixture
    def two_hits(self, facility, staff_user, doc_type_contact):
        """Zwei Ereignisse an zwei Personen — eine Karte allein zeigt kein N+1."""
        now = timezone.now()
        rows = []
        for idx, offset in ((1, 2), (2, 1)):
            person = Client.objects.create(
                facility=facility,
                pseudonym=f"Nplus-{idx:02d}",
                created_by=staff_user,
            )
            rows.append(
                Event.objects.create(
                    facility=facility,
                    client=person,
                    document_type=doc_type_contact,
                    occurred_at=now - timedelta(hours=offset),
                    data_json={"notiz": "Kartenfelder"},
                    created_by=staff_user,
                )
            )
        return rows

    def test_document_type_and_client_come_with_the_row(
        self, facility, staff_user, two_hits, django_assert_num_queries
    ):
        """Pin: ``x_event_search_queryset__mutmut_5`` (``select_related("client")``,
        ``"document_type"`` entfernt) und ``x_event_search_queryset__mutmut_6``
        (``select_related("document_type", )``, ``"client"`` entfernt).

        Original: genau 1 Query fuer beide Treffer inklusive beider
        Relationen. Mutant 5: 3 Queries (je ein Nachladen von
        ``document_type`` pro Treffer). Mutant 6: 3 Queries (je ein Nachladen
        von ``client``). Die Werte selbst sind bei allen drei Varianten
        gleich — die Abweichung ist ausschliesslich die Query-Zahl.
        """
        qs = event_search_queryset(facility, staff_user, "Nplus-")

        with django_assert_num_queries(1):
            karten = [(event.document_type.name, event.client.pseudonym) for event in qs]

        assert karten == [("Kontakt", "Nplus-02"), ("Kontakt", "Nplus-01")]


# ============================================================================
# core.services.dashboard.search.search_result_counts
# ============================================================================


@pytest.mark.django_db
class TestSearchResultCountsAtTheMinimumQueryLength:
    """Zwei Zeichen sind lang genug — die Gruppenzahl muss den Treffer nennen.

    ``MIN_QUERY_LENGTH = 2`` ist im Modul als „ab zwei Zeichen wird gesucht"
    dokumentiert, und ``client_search_queryset``/``event_search_queryset``
    haben gar keine Laengengrenze. Verschiebt sich der Vergleich auf ``<=``,
    zaehlt die Ueberschrift bei exakt zwei Zeichen ``0``, waehrend die
    darunter gerenderte Liste ihre Treffer weiter zeigt (Refs #1803, L7 —
    dieselbe Luege wie „Personen (20)" bei 21 Personen, nur andersherum).
    """

    @pytest.fixture
    def two_char_hit(self, facility, staff_user, doc_type_contact):
        """Person mit dem Zweizeichen-Substring ``Ab`` plus ein Ereignis dazu."""
        person = Client.objects.create(
            facility=facility,
            pseudonym="Pin-Ab-01",
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            client=person,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Zweizeichengrenze"},
            created_by=staff_user,
        )
        return person

    def test_a_two_character_query_still_counts_its_hits(self, facility, staff_user, two_char_hit):
        """Pin: ``x_search_result_counts__mutmut_3`` — ``len(query) < MIN_QUERY_LENGTH``
        → ``len(query) <= MIN_QUERY_LENGTH``.

        Original: ``{"clients": 1, "events": 1}``; Mutant:
        ``{"clients": 0, "events": 0}``.
        """
        assert search_result_counts(facility, staff_user, "ab") == {"clients": 1, "events": 1}

    def test_the_listing_querysets_agree_with_that_count(self, facility, staff_user, two_char_hit):
        """Gegenprobe zum Mutanten: die Listen kennen keine Laengengrenze.

        Ohne diese Zusicherung liesse sich der Mutant als „nur strenger"
        verteidigen; sie zeigt, dass er die Ueberschrift von der Liste
        entkoppelt. Original wie hier: beide Querysets liefern je 1 Zeile,
        unter dem Mutanten von ``search_result_counts`` unveraendert — die
        Ueberschrift daneben aber ``0``.
        """
        assert client_search_queryset(facility, "ab").count() == 1
        assert event_search_queryset(facility, staff_user, "ab").count() == 1


# ============================================================================
# core.services.dashboard.search.search_clients_and_events
# ============================================================================


@pytest.mark.django_db
class TestSearchClientsAndEventsDefaultCaps:
    """Die Default-Kappen der Suche liegen bei 20 — beide.

    Jeder Produktions-Aufrufer (``core/views/search.py``) uebergibt
    ``max_clients``/``max_events`` explizit, der Default war damit nirgends
    beobachtet. Er ist trotzdem die Zusage der Signatur: ein Aufruf ohne
    Kappen darf nicht mehr als 20 Zeilen je Gruppe materialisieren.
    """

    @pytest.fixture
    def twentyone_hits(self, facility, staff_user, doc_type_contact):
        """21 Personen mit je einem Ereignis — eine Zeile ueber der Kappe."""
        now = timezone.now()
        for idx in range(1, 22):
            person = Client.objects.create(
                facility=facility,
                pseudonym=f"Kappe-{idx:02d}",
                created_by=staff_user,
            )
            Event.objects.create(
                facility=facility,
                client=person,
                document_type=doc_type_contact,
                occurred_at=now - timedelta(minutes=idx),
                data_json={"notiz": "Kappentest"},
                created_by=staff_user,
            )

    def test_without_explicit_caps_each_group_stops_at_twenty(self, facility, staff_user, twentyone_hits):
        """Pin: ``x_search_clients_and_events__mutmut_1`` (``max_clients=21``) und
        ``x_search_clients_and_events__mutmut_2`` (``max_events=21``).

        Original: ``(20, 20)``. Mutant 1: ``(21, 20)``. Mutant 2: ``(20, 21)``.
        """
        clients, events = search_clients_and_events(facility, staff_user, "Kappe-")

        assert (len(clients), len(events)) == (20, 20)

    def test_the_group_counts_still_name_all_twentyone(self, facility, staff_user, twentyone_hits):
        """Gegenprobe (Refs #1803, L7): die Kappe kappt die Liste, nicht die Zahl.

        Ohne diesen Zwilling koennte ein spaeterer „Fix" die Kappe einfach
        anheben, statt die Ueberschrift zu bedienen. Original wie Mutanten:
        ``{"clients": 21, "events": 21}`` — die Zahl ist die Gesamtzahl.
        """
        assert search_result_counts(facility, staff_user, "Kappe-") == {"clients": 21, "events": 21}


# ============================================================================
# core.services.dashboard.search.search_similar_clients
# ============================================================================


@pytest.mark.django_db
class TestSearchSimilarClientsDefaultCap:
    """Der Fuzzy-Zweig liefert ohne Angabe hoechstens 10 Vorschlaege.

    ``threshold=0.0`` wird explizit uebergeben (das ist NICHT der gepinnte
    Default) — nur so ist die Kandidatenmenge deterministisch groesser als
    die Kappe, ohne von pg_trgm-Aehnlichkeitswerten abzuhaengen.
    """

    def test_eleven_candidates_are_cut_to_ten(self, facility, staff_user):
        """Pin: ``x_search_similar_clients__mutmut_1`` — ``max_results=10`` →
        ``max_results=11``.

        11 Kandidaten, keiner davon ein ``ilike``-Treffer auf ``"Zx"``.
        Original: ``10``; Mutant: ``11``.
        """
        for idx in range(1, 12):
            Client.objects.create(
                facility=facility,
                pseudonym=f"Anton-{idx:02d}",
                created_by=staff_user,
            )

        assert len(search_similar_clients(facility, "Zx", threshold=0.0)) == 10


@pytest.mark.django_db
class TestSearchSimilarClientsMinimumLength:
    """Die Mindestlaenge ist ein Kurzschluss VOR der Trigram-Query.

    Die Grenze schuetzt zwei Dinge zugleich (Modul-Docstring zu
    ``MIN_QUERY_LENGTH``): den Tabellenscan bei einem einzelnen Zeichen und
    das 30/m-Ratelimit-Budget (ADR-026). Beobachtbar ist sie deshalb nicht
    allein am Rueckgabewert — bei einem Zeichen ist das Trigram-Ergebnis
    ohnehin meist leer —, sondern daran, dass ueberhaupt KEINE Query laeuft.
    """

    def test_a_single_character_never_reaches_the_database(
        self, facility, staff_user, client_identified, django_assert_num_queries
    ):
        """Pin: ``x_search_similar_clients__mutmut_2`` — ``not query or
        len(query) < 2`` → ``not query and len(query) < 2``.

        Bei ``"S"`` ist ``not query`` falsch; mit ``and`` bricht der
        Kurzschluss, der zweite Operand blockt nicht mehr und die Funktion
        laeuft in ``getattr(facility, "settings", ...)`` plus die volle
        ``TrigramSimilarity``-Query. Original: 0 Queries und ``[]``; Mutant:
        mindestens 2 Queries.

        Der zweite Zweig faellt beim Mutanten sogar hart: bei ``None`` ist
        ``not query`` wahr, ``and`` wertet dann ``len(None)`` aus. Original:
        ``[]``; Mutant: ``TypeError: object of type 'NoneType' has no len()``.
        """
        with django_assert_num_queries(0):
            assert search_similar_clients(facility, "S") == []
            assert search_similar_clients(facility, "") == []
            assert search_similar_clients(facility, None) == []

    def test_exactly_two_characters_are_let_through_with_a_real_hit(self, facility, staff_user):
        """Pin: ``x_search_similar_clients__mutmut_4`` (``len(query) <= 2``) und
        ``x_search_similar_clients__mutmut_5`` (``len(query) < 3``).

        Beide blocken die Laenge 2 zusaetzlich. Der Bestand prueft an dieser
        Grenze nur ``isinstance(result, list)`` — das erfuellt ``[]`` genauso.
        Hier faellt bei ``threshold=0.0`` genau eine Person an, die kein
        ``ilike``-Treffer auf ``"Zx"`` ist. Original: ``["Anton"]``;
        Mutant 4 wie Mutant 5: ``[]``.
        """
        Client.objects.create(facility=facility, pseudonym="Anton", created_by=staff_user)

        result = search_similar_clients(facility, "Zx", threshold=0.0)

        assert [person.pseudonym for person in result] == ["Anton"]
