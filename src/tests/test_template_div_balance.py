"""Die gerenderten Formular-Seiten haben eine ausgeglichene ``<div>``-Bilanz (Refs #1836).

Die Bugklasse: ein verwaistes ``</div>``. In [fd9dbd6d] wanderte das oeffnende
``<div>`` des Personen-Autocomplete in ``components/_client_autocomplete.html``
(wo es sich selbst schliesst), sein Closer blieb aber in
``core/events/create.html`` stehen — 11 offene gegen 12 schliessende ``<div>``.

Warum das niemand sah: Browser reparieren so etwas still. Kein Fehler, keine
Konsolenmeldung, HTTP 200, alle Felder vorhanden — nur der DOM-Baum ist ein
anderer. Konkret schloss der verwaiste Closer die Karte „Zuordnung und
Zeitpunkt" vorzeitig, deren Closer dann den Seiten-Wrapper ``max-w-2xl``: das
Dynamische-Felder-Fieldset und die Submit-Zeile verloren ihre
Breitenbegrenzung (gemessen 1076px statt 672px). Genau daran greift keine der
bestehenden Testarten — Markup-Tests suchen Teilstrings (``assert 'data-testid=…'
in content``), und die sind alle noch da; E2E klickt Felder an, und die sind
klickbar. Diese Datei schliesst die Luecke.

Geprueft wird auf der **gerenderten Antwort**, nicht am Template-Text: ein
statischer Scan muesste raten, welcher ``{% if %}``-Zweig laeuft und was ein
``{% include %}`` beisteuert. Auf dem fertigen HTML gibt es diese Ausrede nicht
— was der Browser bekommt, ist genau das, was hier gezaehlt wird.

Bewusst nur ``<div>``: das ist der Container, der die Layout-Struktur traegt
(``card``, Grid-Spalten, ``max-w-*``-Wrapper), und der einzige, bei dem eine
schiefe Bilanz sichtbar wird, ohne irgendetwas kaputtzumachen.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from django.urls import reverse

# Die Formulare mit den geteilten Layout-Partials aus #1836 — Kontakterfassung,
# Personen- und Fall-Maske, Aufgaben-Maske. Anlage und Bearbeitung getrennt,
# weil sie verschiedene Template-Zweige nehmen (``is_edit``, gepinnte Person).
CREATE_ROUTES = [
    "core:event_create",
    "core:client_create",
    "core:case_create",
    "core:workitem_create",
]

# (Route, Fixture-Name des Objekts, dessen ``pk`` in die URL geht)
UPDATE_ROUTES = [
    ("core:client_update", "client_identified"),
    ("core:case_update", "case_open"),
    ("core:workitem_update", "sample_workitem"),
]


class _DivBalance(HTMLParser):
    """Zaehlt ``<div>``/``</div>`` und merkt sich den ersten Unterlauf.

    Zwei Eigenheiten von ``HTMLParser``, auf die sich der Zaehler stuetzt:

    * ``<script>``/``<style>`` liest der Parser im CDATA-Modus — ein ``</div>``
      in einem JS-String zaehlt nicht mit. Kommentare laufen ueber
      ``handle_comment``, nicht ueber die Tag-Handler.
    * ``<div/>`` meldet der Parser ueber ``handle_startendtag``, dessen
      Default-Implementierung Start UND Ende feuert. Das ist NICHT, was ein
      Browser tut: HTML5 kennt kein selbstschliessendes ``<div>``, der Browser
      liest schlicht ein offenes ``<div>``. Die Default-Fassung wuerde eine
      echte Schieflage also zudecken — deshalb wird hier browsertreu nur der
      Start gezaehlt.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.opened = 0
        self.closed = 0
        self.depth = 0
        self.underflow: tuple[int, int] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "div":
            self.opened += 1
            self.depth += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self.closed += 1
            self.depth -= 1
            if self.depth < 0 and self.underflow is None:
                self.underflow = self.getpos()


def _assert_divs_are_balanced(response, where: str) -> None:
    assert response.status_code == 200, f"{where}: HTTP {response.status_code} statt 200"

    parser = _DivBalance()
    parser.feed(response.content.decode())
    parser.close()

    assert parser.underflow is None, (
        f"{where}: verwaistes </div> — es schliesst ein <div>, das nie geoeffnet wurde "
        f"(gerendertes HTML, Zeile {parser.underflow[0]}, Spalte {parser.underflow[1]}). "
        "Der Browser repariert das still, aber der DOM-Baum ist ab dort ein anderer: "
        "Der Closer schliesst den naechsten UMSCHLIESSENDEN Container zu frueh (Refs #1836)."
    )
    assert parser.opened == parser.closed, (
        f"{where}: <div>-Bilanz schief — {parser.opened} offene gegen {parser.closed} "
        f"schliessende Tags (Differenz {parser.opened - parser.closed}). Ein Template oder "
        "ein eingebundenes Partial oeffnet oder schliesst einen Container zu viel; der "
        "Browser verschiebt daraufhin still die Verschachtelung (Refs #1836)."
    )


@pytest.mark.django_db
class TestFormPagesKeepTheirDivBalance:
    @pytest.mark.parametrize("route", CREATE_ROUTES)
    def test_create_forms_are_balanced(self, client, staff_user, route):
        client.force_login(staff_user)

        _assert_divs_are_balanced(client.get(reverse(route)), route)

    @pytest.mark.parametrize(("route", "fixture_name"), UPDATE_ROUTES, ids=[route for route, _ in UPDATE_ROUTES])
    def test_update_forms_are_balanced(self, request, client, staff_user, route, fixture_name):
        obj = request.getfixturevalue(fixture_name)
        client.force_login(staff_user)

        _assert_divs_are_balanced(client.get(reverse(route, args=[obj.pk])), route)


class TestTheParserItselfCatchesTheBug:
    """Positiv-Kontrolle: der Zaehler ist kein Placebo.

    Ohne diese Faelle koennte ``_DivBalance`` still nichts mehr zaehlen (etwa
    weil ein Handler-Name nicht mehr passt) und alle Routen blieben gruen.
    """

    def test_balanced_markup_passes(self):
        parser = _DivBalance()
        parser.feed('<div class="card"><div><p>x</p></div></div>')
        parser.close()

        assert (parser.opened, parser.closed, parser.underflow) == (2, 2, None)

    def test_a_self_closing_div_counts_as_open_like_in_a_browser(self):
        """HTML5 kennt kein ``<div/>`` — der Browser laesst es offen stehen.

        Die Default-Fassung von ``handle_startendtag`` feuert Start UND Ende
        und wuerde die Schieflage damit zudecken.
        """
        parser = _DivBalance()
        parser.feed("<div/>")
        parser.close()

        assert (parser.opened, parser.closed) == (1, 0)

    def test_the_orphan_closer_from_1836_is_seen(self):
        """Der Bilanz-Fehler aus [fd9dbd6d]: ein ``</div>`` zu viel."""
        parser = _DivBalance()
        parser.feed('<div class="card"><p>x</p></div></div>')
        parser.close()

        assert parser.underflow is not None
        assert parser.opened == 1
        assert parser.closed == 2

    def test_closers_inside_script_do_not_count(self):
        parser = _DivBalance()
        parser.feed('<div><script>var s = "</div>";</script></div>')
        parser.close()

        assert (parser.opened, parser.closed, parser.underflow) == (1, 1, None)
