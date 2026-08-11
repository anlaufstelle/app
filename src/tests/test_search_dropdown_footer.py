"""Fußzeile des Such-Dropdowns (Refs #1813, Konzept #1801 §7.1).

``/search/`` bekommt **keinen** Navigations-Eintrag: das Suchfeld steht bereits
als zweites Element in der Sidebar und mobil als Overlay — ein Nav-Eintrag
würde ein dauerhaft sichtbares Bedienelement verdoppeln. Stattdessen wird die
Fußzeile des Ergebnis-Dropdowns der verlässliche Einstieg: sie erscheint,
sobald etwas eingegeben ist, **auch bei null Treffern**.

Genau dieser Fall war die Lücke. Die Fußzeile lag innerhalb des Treffer-``{%
if %}``; wer sich vertippte, sah „Keine Ergebnisse" und danach nichts mehr —
keinen Weg zur Volltextsuche, obwohl die dort mit anderen Feldern und ohne
Ergebnis-Deckelung noch etwas finden kann.

Geprüft wird über den echten HTMX-Endpunkt (``core:global_search``), nicht über
ein isoliertes Template-Rendering: der Endpunkt ist es, den das Dropdown
aufruft, und nur er belegt, dass ``q`` überhaupt im Kontext ankommt.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

FOOTER = 'data-testid="search-all-results-link"'


def _dropdown(client, user, query: str) -> str:
    client.force_login(user)
    return client.get(reverse("core:global_search"), {"q": query}).content.decode()


@pytest.mark.django_db
class TestFooterAlwaysVisible:
    def test_footer_appears_with_hits(self, client, staff_user, client_identified):
        content = _dropdown(client, staff_user, "ID-01")

        assert "Test-ID-01" in content, "Sanity: die Probe findet die Person"
        assert FOOTER in content
        assert "Alle Ergebnisse anzeigen" in content

    def test_footer_appears_without_hits(self, client, staff_user):
        """Der eigentliche Fix — hier endete das Dropdown bisher als Sackgasse."""
        content = _dropdown(client, staff_user, "zzz-gibt-es-nicht")

        assert "Keine Ergebnisse" in content
        assert FOOTER in content
        assert "Erweiterte Suche öffnen" in content

    def test_no_footer_without_a_query(self, client, staff_user):
        """Ohne Eingabe ist das Dropdown leer — eine Fußzeile allein wäre Lärm."""
        assert FOOTER not in _dropdown(client, staff_user, "")

    def test_single_character_shows_hint_instead_of_link(self, client, staff_user):
        """Konzept #1803 §3.1: unter 2 Zeichen ist die Fußzeile ein Hinweis, kein Link.

        Die Vollseite hat eine 2-Zeichen-Schwelle — ein Link dorthin wäre bei
        einem einzelnen Zeichen ein Klick ins Leere.
        """
        content = _dropdown(client, staff_user, "a")

        assert FOOTER not in content
        assert "Bitte mindestens 2 Zeichen eingeben." in content

    @pytest.mark.parametrize("query", ["zzz-gibt-es-nicht", "ID-01"])
    def test_footer_links_to_the_full_search_with_the_query(self, client, staff_user, client_identified, query):
        content = _dropdown(client, staff_user, query)
        idx = content.index(FOOTER)
        tag = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert f'href="{reverse("core:search")}?q={query}"' in tag, tag

    @pytest.mark.parametrize("query", ["zzz-gibt-es-nicht", "ID-01"])
    def test_footer_meets_the_44px_touch_target(self, client, staff_user, client_identified, query):
        """Refs #810: die Fußzeile ist mobil das Ziel eines Daumens, nicht eines Mauszeigers."""
        content = _dropdown(client, staff_user, query)
        idx = content.index(FOOTER)
        tag = content[content.rindex("<a", 0, idx) : content.index(">", idx)]

        assert "min-h-[44px]" in tag, tag

    def test_single_character_shows_no_empty_state_either(self, client, staff_user):
        """Refs #1803: unter der Schwelle wurde nicht gesucht — also auch nichts gefunden.

        „Keine Ergebnisse" ist eine Aussage ueber ein Suchergebnis. Bei einem
        einzelnen Zeichen gibt es keins; die Zeile daneben sagt gleichzeitig
        „Bitte mindestens 2 Zeichen eingeben." — beides zusammen widerspricht
        sich.
        """
        content = _dropdown(client, staff_user, "a")

        assert "Keine Ergebnisse" not in content
        assert "Bitte mindestens 2 Zeichen eingeben." in content

    def test_query_is_url_encoded(self, client, staff_user):
        """``|urlencode`` bleibt: ein ``&`` in der Eingabe darf die URL nicht zerlegen."""
        content = _dropdown(client, staff_user, "a&b")

        assert 'href="/search/?q=a%26b"' in content


@pytest.mark.django_db
class TestBelowTheMinimumQueryLength:
    """Refs #1803: die Schwelle galt fuer die Fusszeile, nicht fuer die Treffer.

    ``MIN_QUERY_LENGTH`` deckelte bisher nur ``/search/`` und den Fuzzy-Zweig.
    Das Dropdown lief bei einem einzelnen Zeichen weiter in den vollen
    ``icontains``-Scan — und zeigte dessen Treffer an, waehrend direkt darunter
    „Bitte mindestens 2 Zeichen eingeben." stand. Unterhalb der Schwelle wird
    jetzt gar nicht gesucht: kein Tabellenscan, kein Ratelimit-Budget fuer
    Ergebnisse, die die Vollseite ohnehin nicht wiederholen wuerde.
    """

    def test_no_client_hits_below_the_threshold(self, client, staff_user, client_identified):
        content = _dropdown(client, staff_user, "T")

        assert "Test-ID-01" not in content
        assert "Bitte mindestens 2 Zeichen eingeben." in content

    def test_no_event_hits_below_the_threshold(self, client, staff_user, facility, doc_type_contact, client_identified):
        from django.utils import timezone

        from core.models import Event

        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Tagesstruktur"},
            created_by=staff_user,
        )

        content = _dropdown(client, staff_user, "T")

        assert reverse("core:event_detail", args=[event.pk]) not in content

    def test_two_characters_still_search(self, client, staff_user, client_identified):
        """Die Gegenprobe: genau ein Zeichen mehr, und es wird gesucht."""
        content = _dropdown(client, staff_user, "Te")

        assert "Test-ID-01" in content
