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

    def test_query_is_url_encoded(self, client, staff_user):
        """``|urlencode`` bleibt: ein ``&`` in der Eingabe darf die URL nicht zerlegen."""
        content = _dropdown(client, staff_user, "a&b")

        assert 'href="/search/?q=a%26b"' in content
