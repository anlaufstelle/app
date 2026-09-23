"""E2E-Tests für gruppierte Event-Felder im „Kontakt dokumentieren"-Formular (M0 P4, Refs #1056).

Prüft, dass die Feldgruppen des Seed-Dokumentationstyps „Kontakt" im
Ereignis-Formular korrekt gerendert werden: benannte Gruppen-Header, die
„Weitere Angaben"-Sektion zuletzt und die einklappbare Gruppe „Leistungen &
Notizen" (Alpine ``collapsibleGroup``, CSP-kompatibel).

Es werden **keine** dauerhaften Ereignisse angelegt — die Sichtbarkeits-/
Toggle-Checks kommen ohne Formular-Submit aus, sodass weder Seed-Daten noch
die geteilte E2E-DB verändert werden.
"""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors

pytestmark = pytest.mark.e2e

COLLAPSIBLE_TOGGLE = '#dynamic-fields button:has(span:has-text("Leistungen & Notizen"))'

# Genau die Karte, die den obigen Toggle traegt. ``#dynamic-fields [x-data]``
# waere der Partial-Wurzelknoten ``conditionEvaluator`` — dessen Initialisierung
# sagt nichts ueber die spaeter angeklickte ``collapsibleGroup``-Karte aus
# (Review, Refs #1855).
COLLAPSIBLE_CARD = (
    "#dynamic-fields [x-data='collapsibleGroup']:has(button:has(span:has-text(\"Leistungen & Notizen\")))"
)

# Der Chevron im Toggle traegt ``:class="rotateClass"``. Server-seitig steht dort
# nur ``w-4 h-4 shrink-0 text-ink-muted transition-transform``; ``rotate-180``
# kommt ausschliesslich von Alpine (``rotateClass`` liefert es bei ``open``).
COLLAPSIBLE_CHEVRON = f"{COLLAPSIBLE_TOGGLE} svg"


def _open_kontakt_form(page, base_url):
    """`/events/new/` öffnen, „Kontakt" wählen und auf die dynamischen Felder warten."""
    page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
    page.select_option("select[name='document_type']", label="Kontakt")
    # HTMX swap-t #dynamic-fields mit den gruppierten Feldern (Basisdaten enthält
    # „dauer") — auf ein konkretes Feld warten, nicht auf networkidle.
    page.locator('#dynamic-fields input[name="dauer"]').wait_for(state="attached", timeout=10000)
    # Refs #1855 / R-A-6: Statt einer festen Pause auf ein DOM-beobachtbares
    # ERGEBNIS der Alpine-Verarbeitung genau dieser Karte warten — vor der
    # Bindung verpufft ein Klick auf den Toggle wirkungslos (dasselbe Muster wie
    # ``wait_until_htmx_bound`` fuer htmx).
    #
    # Warum der Chevron und nicht ``aria-expanded``: Das Template rendert
    # ``aria-expanded="true"`` als STATISCHES Attribut neben der
    # ``:aria-expanded``-Bindung (``core/partials/dynamic_fields.html``) — es
    # steht also schon vor Alpine im DOM und ist kein Signal. Auch der
    # Komponenten-State taugt nicht: ``open`` ist in der Alpine-Data-Factory
    # bereits mit ``true`` vorbelegt (``static/js/alpine/widgets.js``), ein
    # ``typeof ... === 'boolean'`` waere schon vor ``init()`` wahr (Befund der
    # Re-Review).
    #
    # Der Chevron dagegen traegt ``:class="rotateClass"``; ``rotate-180`` steht
    # NICHT in der server-gerenderten Klassenliste und erscheint erst, wenn
    # Alpine die Karte samt ihrer Direktiven verarbeitet hat. Weil Alpine
    # ``x-data`` (inkl. ``init()``) vor den Direktiven im Teilbaum abarbeitet,
    # belegt das Attribut zugleich die abgeschlossene Initialisierung genau
    # dieser Karte.
    page.locator(COLLAPSIBLE_CARD).wait_for(state="attached", timeout=10000)
    expect(page.locator(COLLAPSIBLE_CHEVRON)).to_have_class(re.compile(r"(?:^|\s)rotate-180(?:\s|$)"), timeout=10000)


class TestKontaktFieldGroups:
    """Gruppen-Header, „Weitere Angaben"-Reihenfolge und Collapsible-Verhalten."""

    def test_group_headers_and_ungrouped_section(self, authenticated_page, base_url):
        """Header „Basisdaten" + „Leistungen & Notizen" sichtbar, „Weitere Angaben" zuletzt."""
        page = authenticated_page
        _open_kontakt_form(page, base_url)

        # Benannte Gruppen-Header.
        expect(page.locator('#dynamic-fields h2:has-text("Basisdaten")')).to_be_visible()
        expect(page.locator(COLLAPSIBLE_TOGGLE)).to_be_visible()

        # „Weitere Angaben" ist die letzte Sektion und enthält „Straßenkontakt".
        # Seit M1 wrappt der conditionEvaluator-Wurzelknoten die Gruppen-Cards
        # (eine Ebene unter #dynamic-fields, Refs #1057). Seit #1867 zieht ein
        # optionaler conditionalGroup-Wrapper (nur bei all-konditionalen
        # Blöcken) eine WEITERE Ebene ein — der Selektor bleibt daher
        # tiefen-unabhängig statt auf eine feste Verschachtelung zu pinnen.
        cards = page.locator("#dynamic-fields .card")
        expect(cards).to_have_count(3, timeout=10000)
        last_card = cards.last
        expect(last_card.locator("h2")).to_contain_text("Weitere Angaben")
        expect(last_card.locator('input[name="strassenkontakt"]')).to_have_count(1)

    def test_collapsible_group_toggle(self, authenticated_page, base_url):
        """Toggle der „Leistungen & Notizen"-Gruppe wechselt aria-expanded + Sichtbarkeit."""
        page = authenticated_page
        _open_kontakt_form(page, base_url)

        toggle = page.locator(COLLAPSIBLE_TOGGLE)
        notiz = page.locator('#dynamic-fields textarea[name="notiz"]')

        # Startzustand: offen (data-start-open="true").
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(notiz).to_be_visible()

        # Einklappen: aria-expanded="false", Felder via x-show ausgeblendet.
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(notiz).to_be_hidden()

        # Zwischen den beiden Toggle-Klicks bewusst entkoppeln: Pointer wegbewegen
        # und > Doppelklick-Fenster warten, damit zwei aufeinanderfolgende
        # Real-Klicks am selben Punkt nicht als Doppelklick koaleszieren (genau ein
        # @click-Toggle pro Klick).
        page.mouse.move(0, 0)
        page.wait_for_timeout(600)

        # Wieder aufklappen: Feld sichtbar und ausfüllbar.
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(notiz).to_be_visible()
        notiz.fill("E2E-CFG collapse check")
        expect(notiz).to_have_value("E2E-CFG collapse check")

    def test_event_form_has_no_console_errors(self, authenticated_page, base_url):
        """Das gruppierte „Neuer Kontakt"-Formular wirft keine Konsolen-/CSP-Fehler."""
        page = authenticated_page
        errors = track_console_errors(page)

        _open_kontakt_form(page, base_url)
        # Collapsible einmal betätigen — deckt den Alpine-Pfad mit ab.
        toggle = page.locator(COLLAPSIBLE_TOGGLE)
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        page.mouse.move(0, 0)
        page.wait_for_timeout(600)
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")

        assert errors == [], f"Konsolen-Errors/CSP-Verstöße auf dem Event-Formular: {errors}"
