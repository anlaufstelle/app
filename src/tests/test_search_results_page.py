"""Die Ergebnisseite ``/search/`` (Refs #1803, Konzept §3.4–§3.7, Etappe E2).

Bis hierher war ``/search/`` eine gekappte Vorschau, die sich als vollständige
Liste ausgab: ``search_clients_and_events`` lief in die Defaults 20/20/10, die
Gruppenüberschrift zeigte mit ``{{ clients|length }}`` die **Kappe** als wäre
sie die Gesamtzahl, es gab weder Paginierung noch einen Trunkierungshinweis.
Bei 21 Personen sagte die Seite „Personen (20)" und verschwieg den Rest.

Vier Aussagen hält dieses Modul fest:

* **Zahlenwahrheit (L7)** — die Zahl in der Gruppenüberschrift ist die
  Gesamtzahl, nie die Kappe. Das Format ``Personen (N)`` bleibt (E2E-Guard
  ``e2e/test_fuzzy_search.py``), nur seine Bedeutung ändert sich.
* **Adressierbarkeit (L5)** — ``?q``/``?type``/``?page`` stehen vollständig in
  der URL und werden serverseitig ausgewertet.
* **Kein-Sackgassen-Regel (L2)** — jeder Zustand (kein ``q``, zu kurz, null
  Treffer) hat einen sichtbaren Ausgang.
* **Konstanz-Regel (L4)** — der Hinweis „Was die Suche nicht findet" ist in
  JEDEM Zustand wortgleich da. Ein bedingt eingeblendeter Hinweis wäre ein
  Orakel: seine bloße Anwesenheit verriete, dass es zu genau diesem Suchbegriff
  höher eingestufte Daten gibt.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Client as ClientModel
from core.models import Event

SEARCH = "core:search"
FOOTNOTE = "Was die Suche nicht findet"


def _make_clients(facility, staff_user, count, prefix="Wolke"):
    return [
        ClientModel.objects.create(facility=facility, pseudonym=f"{prefix}-{i:03d}", created_by=staff_user)
        for i in range(count)
    ]


def _make_events(facility, staff_user, doc_type, client_obj, count):
    return [
        Event.objects.create(
            facility=facility,
            client=client_obj,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"notiz": f"Wolke-Notiz {i}"},
            created_by=staff_user,
        )
        for i in range(count)
    ]


@pytest.mark.django_db
class TestGroupCountsTellTheTruth:
    """L7 — die Zahl in der Überschrift ist die Gesamtzahl, nicht die Kappe."""

    def test_client_group_shows_the_total_not_the_display_cap(self, client, staff_user, facility):
        _make_clients(facility, staff_user, 23)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke"}).content.decode()

        assert "Personen (23)" in content, "Gruppenzaehler zeigt die Kappe statt der Gesamtzahl"

    def test_overview_still_caps_the_rendered_rows(self, client, staff_user, facility):
        """Die Übersicht bleibt eine Übersicht — zehn Zeilen, dann ein Sprung."""
        _make_clients(facility, staff_user, 23)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke"}).content.decode()

        assert content.count('data-testid="search-result-client"') == 10
        assert "Alle 23 Personen anzeigen" in content

    def test_no_jump_link_when_everything_fits(self, client, staff_user, facility):
        _make_clients(facility, staff_user, 3)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke"}).content.decode()

        assert "Personen (3)" in content
        assert "Personen anzeigen" not in content

    def test_event_group_shows_the_total(self, client, staff_user, facility, doc_type_contact, client_identified):
        _make_events(facility, staff_user, doc_type_contact, client_identified, 12)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke-Notiz"}).content.decode()

        assert "Ereignisse (12)" in content
        assert "Alle 12 Ereignisse anzeigen" in content


@pytest.mark.django_db
class TestTypeFilterAndPagination:
    """L5 — der Suchzustand steht vollständig in der Adresse."""

    def test_type_person_lists_only_people(self, client, staff_user, facility, doc_type_contact, client_identified):
        _make_clients(facility, staff_user, 3)
        _make_events(facility, staff_user, doc_type_contact, client_identified, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke", "type": "person"}).content.decode()

        assert "Personen (3)" in content
        # Die Chip-Reihe nennt weiterhin BEIDE Gruppen (sonst gaebe es keinen
        # Weg zurueck) — die Ergebnisliste darf nur eine davon zeigen.
        assert 'data-testid="search-group-clients"' in content
        assert 'data-testid="search-group-events"' not in content

    def test_type_event_lists_only_events(self, client, staff_user, facility, doc_type_contact, client_identified):
        _make_clients(facility, staff_user, 3)
        _make_events(facility, staff_user, doc_type_contact, client_identified, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke", "type": "event"}).content.decode()

        assert "Ereignisse (2)" in content
        assert 'data-testid="search-group-events"' in content
        assert 'data-testid="search-group-clients"' not in content

    def test_type_person_paginates_beyond_the_page_size(self, client, staff_user, facility):
        _make_clients(facility, staff_user, 30)
        client.force_login(staff_user)

        first = client.get(reverse(SEARCH), {"q": "Wolke", "type": "person"}).content.decode()
        second = client.get(reverse(SEARCH), {"q": "Wolke", "type": "person", "page": "2"}).content.decode()

        assert first.count('data-testid="search-result-client"') == 25
        assert second.count('data-testid="search-result-client"') == 5
        assert "type=person" in first, "Paginierungs-Links muessen den Typ mittragen"

    def test_unknown_type_falls_back_to_the_overview(self, client, staff_user, facility):
        """Fail-closed auf den harmlosen Zustand statt auf einen Fehler."""
        _make_clients(facility, staff_user, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke", "type": "attachment"}).content.decode()

        assert "Personen (2)" in content

    def test_fuzzy_block_only_lives_in_the_overview(self, client, staff_user, facility):
        """Ähnliche Pseudonyme sind ein Hinweis, kein Ergebnisraum — in
        ``type=person`` wird sauber über exakte Treffer paginiert."""
        ClientModel.objects.create(facility=facility, pseudonym="Schmidt", created_by=staff_user)
        client.force_login(staff_user)

        overview = client.get(reverse(SEARCH), {"q": "Schmitt"}).content.decode()
        typed = client.get(reverse(SEARCH), {"q": "Schmitt", "type": "person"}).content.decode()

        assert "Ähnliche Pseudonyme" in overview
        assert "Ähnliche Pseudonyme" not in typed


@pytest.mark.django_db
class TestTheAddressFollowsTheTyping:
    def test_the_search_page_field_pushes_its_url(self, client, staff_user):
        """L5: Tippen aenderte bisher die Ergebnisse, aber nicht die Adresse —
        Reload, Bookmark, Weitergabe und Browser-Zurueck verloren ``?q=``."""
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH)).content.decode()
        idx = content.index('data-testid="search-page-input"')
        field = content[content.rindex("<input", 0, idx) : content.index(">", idx)]

        assert 'hx-push-url="true"' in field, field

    def test_the_dropdown_fields_do_not_push_a_url(self, client, staff_user):
        """Das Dropdown ist ein Sprung, kein Ort (Regel L1) — es darf die
        Adresse der Seite, auf der man gerade arbeitet, nicht umschreiben."""
        client.force_login(staff_user)

        content = client.get(reverse("core:client_list")).content.decode()
        idx = content.index('data-testid="global-search-input"')
        field = content[content.rindex("<input", 0, idx) : content.index(">", idx)]

        assert "hx-push-url" not in field, field


@pytest.mark.django_db
class TestFilterChips:
    def test_chips_appear_for_existing_groups_only(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        _make_clients(facility, staff_user, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke"}).content.decode()

        assert 'data-testid="search-chip-all"' in content
        assert 'data-testid="search-chip-person"' in content
        assert 'data-testid="search-chip-event"' not in content, "Ein Chip ohne Gruppe waere eine Luege (L6)"

    def test_active_chip_is_marked_for_screen_readers(self, client, staff_user, facility):
        _make_clients(facility, staff_user, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke", "type": "person"}).content.decode()
        idx = content.index('data-testid="search-chip-person"')
        chip = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert 'aria-current="page"' in chip, chip
        assert "min-h-[44px]" in chip, chip

    def test_no_chips_without_a_query(self, client, staff_user):
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH)).content.decode()

        assert 'data-testid="search-chip-all"' not in content


@pytest.mark.django_db
class TestThresholdAndEmptyStates:
    """L2 — kein Zustand ohne Ausgang."""

    def test_single_character_runs_no_query_and_explains_itself(self, client, staff_user, facility):
        _make_clients(facility, staff_user, 1, prefix="W")
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "W"}).content.decode()

        assert "Bitte mindestens 2 Zeichen eingeben." in content
        assert "W-000" not in content, "Unter der Schwelle darf keine Query laufen"

    def test_no_query_keeps_the_existing_invitation(self, client, staff_user):
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH)).content.decode()

        assert "Suchbegriff eingeben, um Personen und Ereignisse zu finden." in content

    def test_zero_hits_keeps_the_existing_headline(self, client, staff_user):
        """Refs ``test_boundary_empty.py`` — der Kopftext bleibt bewusst stehen."""
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ-GIBT-ES-NICHT"}).content.decode()

        assert "Keine Ergebnisse" in content
        assert "Geprüft wurden Pseudonyme und die offen gespeicherten Inhalte von Ereignissen." in content

    def test_zero_hits_offers_a_way_out(self, client, staff_user):
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ-GIBT-ES-NICHT"}).content.decode()

        assert 'data-testid="search-empty-create-client"' in content
        assert 'data-testid="search-empty-create-event"' in content
        assert 'data-testid="search-empty-client-list"' in content

    def test_the_client_list_link_carries_the_query(self, client, staff_user):
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ&NICHT"}).content.decode()
        idx = content.index('data-testid="search-empty-client-list"')
        link = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert f'href="{reverse("core:client_list")}?q=ZZZ%26NICHT"' in link, link

    def test_create_client_button_does_not_preseed_the_pseudonym(self, client, staff_user):
        """Entscheid 6.4: ein Tippfehler in der Suche darf nicht zum dauerhaften,
        einrichtungsweit eindeutigen Pseudonym werden."""
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Tipfehlar"}).content.decode()
        idx = content.index('data-testid="search-empty-create-client"')
        link = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert f'href="{reverse("core:client_create")}"' in link, link
        assert "pseudonym=" not in link, link


@pytest.mark.django_db
class TestEmptyStateIsRoleAware:
    """Ein Angebot, das in einem 403 endet, ist keine Hilfe."""

    def test_assistant_gets_no_create_client_offer(self, client, assistant_user):
        client.force_login(assistant_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ-GIBT-ES-NICHT"}).content.decode()

        assert 'data-testid="search-empty-create-client"' not in content
        assert "Neue Personen legt eine Fachkraft an." in content

    def test_assistant_still_gets_the_documentation_offer(self, client, assistant_user):
        client.force_login(assistant_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ-GIBT-ES-NICHT"}).content.decode()

        assert 'data-testid="search-empty-create-event"' in content
        assert 'data-testid="search-empty-client-list"' in content

    def test_staff_does_not_see_the_assistant_explanation(self, client, staff_user):
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "ZZZ-GIBT-ES-NICHT"}).content.decode()

        assert "Neue Personen legt eine Fachkraft an." not in content


@pytest.mark.django_db
class TestConstantHonestyFootnote:
    """L4 — der Hinweis darf nie aus einer Query abgeleitet werden."""

    @pytest.mark.parametrize(
        "params",
        [{}, {"q": "W"}, {"q": "ZZZ-GIBT-ES-NICHT"}, {"q": "Wolke"}, {"q": "Wolke", "type": "person"}],
        ids=["ohne-q", "zu-kurz", "null-treffer", "mit-treffern", "typ-gefiltert"],
    )
    def test_footnote_is_present_in_every_state(self, client, staff_user, facility, params):
        _make_clients(facility, staff_user, 2)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), params).content.decode()

        assert FOOTNOTE in content, f"Fussnote fehlt im Zustand {params}"

    def test_footnote_names_the_structural_gaps(self, client, staff_user):
        """Entscheid 6.2: Dateien bleiben unsuchbar — und das wird gesagt."""
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke"}).content.decode()

        assert "Angaben in geschützten Feldern" in content
        assert "Dateien" in content

    def test_footnote_wording_does_not_depend_on_the_result_set(self, client, staff_user, facility):
        """Wortgleich bei 0 wie bei vielen Treffern — sonst wäre die Anwesenheit
        des Satzes selbst das Orakel."""
        _make_clients(facility, staff_user, 5)
        client.force_login(staff_user)

        def _footnote(term):
            content = client.get(reverse(SEARCH), {"q": term}).content.decode()
            start = content.index(FOOTNOTE)
            return content[start : content.index("</div>", start)]

        assert _footnote("Wolke") == _footnote("ZZZ-GIBT-ES-NICHT")


@pytest.mark.django_db
class TestSearchResultCountsService:
    def test_counts_are_totals_and_ignore_display_caps(self, facility, staff_user):
        from core.services.dashboard import search_result_counts

        _make_clients(facility, staff_user, 42)

        assert search_result_counts(facility, staff_user, "Wolke")["clients"] == 42

    def test_counts_respect_the_trash(self, facility, staff_user):
        from core.services.dashboard import search_result_counts

        clients = _make_clients(facility, staff_user, 3)
        clients[0].soft_delete(user=staff_user)

        assert search_result_counts(facility, staff_user, "Wolke")["clients"] == 2

    def test_counts_are_empty_below_the_threshold(self, facility, staff_user):
        from core.services.dashboard import search_result_counts

        _make_clients(facility, staff_user, 3, prefix="W")

        assert search_result_counts(facility, staff_user, "W") == {"clients": 0, "events": 0}

    def test_counts_respect_sensitivity(self, facility, staff_user, assistant_user, client_identified):
        """Der Zähler darf nie mehr versprechen, als die Rolle sehen darf."""
        from core.models import DocumentType
        from core.services.dashboard import search_result_counts

        elevated = DocumentType.objects.create(
            facility=facility,
            sensitivity=DocumentType.Sensitivity.ELEVATED,
            name="Erhoehter Typ",
        )
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=elevated,
            occurred_at=timezone.now(),
            created_by=staff_user,
        )

        assert search_result_counts(facility, staff_user, "ID-01")["events"] == 1
        assert search_result_counts(facility, assistant_user, "ID-01")["events"] == 0


@pytest.mark.django_db
class TestRatelimitStaysAtThirtyPerMinute:
    """Entscheid 6.5 — die Grenze bleibt; der Test hält fest, WAS bei 31 passiert.

    ``ratelimit_enabled`` (conftest) nagelt das Zaehlfenster fest. Hier ist das
    besonders wichtig: das Limit laeuft pro **Minute**, die Fenstergrenze faellt
    also sechzigmal haeufiger in die Schleife als bei den Stundenlimits
    (Refs #1900).
    """

    @pytest.fixture(autouse=True)
    def _enable_ratelimit(self, ratelimit_enabled):
        return ratelimit_enabled

    def test_thirty_first_search_in_a_minute_is_refused_with_429(self, client, staff_user):
        client.force_login(staff_user)

        for _ in range(30):
            assert client.get(reverse(SEARCH), {"q": "Wolke"}).status_code == 200

        assert client.get(reverse(SEARCH), {"q": "Wolke"}).status_code == 429

    def test_the_dropdown_endpoint_has_its_own_budget(self, client, staff_user):
        """Getrennte Eimer: die Vollseite verbraucht das Dropdown-Budget nicht."""
        client.force_login(staff_user)

        for _ in range(30):
            client.get(reverse(SEARCH), {"q": "Wolke"})

        assert client.get(reverse("core:global_search"), {"q": "Wolke"}).status_code == 200


class TestEnglishRenderingLock:
    """Refs ADR-027: DE ist Quellsprache, EN die einzige Uebersetzung.

    Analog ``test_i18n_offline_create.py``: ``test_i18n_catalog`` sichert nur,
    dass kein EN-Eintrag leer oder fuzzy ist. Dieser Lock friert die konkreten
    Renderings der neuen Such-Texte ein, damit ein spaeterer
    ``makemessages``/``msgmerge``-Lauf sie nicht unbemerkt auf einen
    Fuzzy-Fallback zurueckdreht.
    """

    EN = [
        ("Bitte mindestens 2 Zeichen eingeben.", "Please enter at least 2 characters."),
        (
            "Geprüft wurden Pseudonyme und die offen gespeicherten Inhalte von Ereignissen.",
            "Pseudonyms and unprotected event contents were checked.",
        ),
        ("Neue Personen legt eine Fachkraft an.", "New people are created by a staff member."),
        ("In der Personenliste weitersuchen", "Keep searching in the people list"),
        ("Alle Typen", "All types"),
        ("Nur Personen", "People only"),
        ("Nur Ereignisse", "Events only"),
        ("Gesucht wird im Pseudonym.", "Searches the pseudonym."),
        (
            "Gesucht wird in den offen gespeicherten Feldinhalten der Ereignisse.",
            "Searches the unprotected field contents of events.",
        ),
        ("Alle %(count)s Personen anzeigen", "Show all %(count)s people"),
        ("Alle %(count)s Ereignisse anzeigen", "Show all %(count)s events"),
        ("Was die Suche nicht findet", "What search cannot find"),
        (
            "Angaben in geschützten Feldern und Einträge, die deine Rolle nicht öffnen darf, "
            "erscheinen hier nie. Auch dann nicht, wenn es sie gibt.",
            "Entries in protected fields, and entries your role may not open, never appear here "
            "— not even if they exist.",
        ),
        (
            "Dateien sind ebenfalls nicht durchsuchbar: Dateinamen liegen verschlüsselt im Dateitresor.",
            "Files are not searchable either: file names are stored encrypted in the file vault.",
        ),
        ("Suche schließen", "Close search"),
    ]

    @pytest.mark.parametrize(("source", "english"), EN, ids=[s[:32] for s, _ in EN])
    def test_english_catalog_renders_the_expected_string(self, source, english):
        from django.utils.translation import gettext, override

        with override("en"):
            assert gettext(source) == english


@pytest.mark.django_db
class TestOneObjectClassOneWord:
    """Konzept §3.12: Dieselbe Objektklasse hiess im Dropdown „Kontakte" und auf
    der Vollseite „Ereignisse" — zwei Woerter fuer eine Sache, direkt
    nebeneinander sichtbar, weil das Dropdown ueber der Vollseite aufgeht.

    Die msgid „Kontakte" bleibt in Gebrauch (Fall-Detail, Uebergabe,
    Sachbericht-PDF) und wird NICHT aus dem Katalog entfernt — sie ist dort die
    richtige Vokabel, nur nicht als Gruppenname im Suchergebnis.
    """

    def test_dropdown_group_is_called_ereignisse(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        _make_events(facility, staff_user, doc_type_contact, client_identified, 1)
        client.force_login(staff_user)

        content = client.get(reverse("core:global_search"), {"q": "Wolke-Notiz"}).content.decode()

        assert "Ereignisse" in content
        assert "Kontakte" not in content

    def test_full_page_group_uses_the_same_word(
        self, client, staff_user, facility, doc_type_contact, client_identified
    ):
        _make_events(facility, staff_user, doc_type_contact, client_identified, 1)
        client.force_login(staff_user)

        content = client.get(reverse(SEARCH), {"q": "Wolke-Notiz"}).content.decode()

        assert "Ereignisse (1)" in content


@pytest.mark.django_db
class TestSearchIsNotPartOfTheOfflineShell:
    """Offene Frage 6.6, am Code beantwortet (Refs #1803).

    Die Suche laeuft rein serverseitig gegen PostgreSQL und ist offline
    strukturell wertlos. Waere ``/search/`` im ``APP_SHELL`` vorgehalten oder
    haette sie einen eigenen Shell-Zweig wie ``/clients/``, ``/workitems/`` und
    ``/events/new/``, muesste sie eine eigene Offline-Aussage bekommen — sonst
    laege dort offline eine leere Ergebnisliste, die wie „nichts gefunden"
    aussieht. Sie hat beides nicht: eine Navigation faellt generisch auf
    ``/offline/`` zurueck, ein Live-Request auf das Offline-Partial-Banner.

    Der Guard haelt den Befund fest — wer ``/search/`` kuenftig precacht,
    braucht diese Aussage.
    """

    def test_search_is_neither_precached_nor_shell_routed(self, client):
        body = client.get(reverse("service_worker")).content.decode()

        assert '"/search/"' not in body, "/search/ im Service-Worker — dann braucht es eine eigene Offline-Aussage"
        assert "/partials/search/" not in body
