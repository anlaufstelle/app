"""E2E: Art.-18-Knopf „Verarbeitung einschränken" auf dem Telefon erreichbar (Refs #2332).

Handtest-Befund ``ZUSATZ-ART18`` (Meilenstein handtest #38, P1): auf
``/clients/<uuid>/`` liegt das Formular des Art.-16/18-Kastens
(``templates/core/clients/detail.html``, Art.-16-Kasten Z. 149ff.) bei
390×844 und 375×812 vollständig außerhalb des Sichtfensters — kein
Seitenscroll (``overflow-x: hidden`` auf ``main``/``body``), der Knopf
„Verarbeitung einschränken" ist weder sicht- noch antippbar. Ursache: der
äußere Kasten trägt bereits ``flex flex-wrap`` (Z. 149), das eingebettete
``<form>`` (Z. 164) dagegen NICHT — dessen eigene Kinder (Label, Select,
Knopf) laufen deshalb als ungebrochene Reihe über die Kartenbreite hinaus.

Dieser Lauf ist der Rot-Nachweis (Bounding-Box-Messung vor dem Fix) UND
(nach dem Fix) der Gruen-Nachweis + axe-/Tastatur-Pfad-Beleg in einem Spec —
das Ticket selbst schreibt „E2E-Guard zuerst (Rot-Nachweis...), dann Fix" vor.
"""

from __future__ import annotations

import re

import pytest

from ._axe import AXE_TAGS, run_axe

pytestmark = pytest.mark.e2e

_IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

_SUBMIT_TESTID = "client-processing-restrict-submit"
_MIN_HIT_AREA_PX = 44


def _client_detail_url(page, base_url) -> str:
    """Erste geeignete Person suchen (staff+ Kasten braucht keinen bestimmten
    Klienten — Stern-42 ist unrestricted per Seed)."""
    page.goto(f"{base_url}/clients/?q=Stern-42", wait_until="domcontentloaded")
    page.click("a:has-text('Stern-42')")
    page.wait_for_url(re.compile(r"/clients/[0-9a-f-]+/$"))
    return page.url


def _submit_button_box(page):
    return page.locator(f"[data-testid='{_SUBMIT_TESTID}']").bounding_box()


class TestArt18ButtonReachableOnMobile:
    @pytest.fixture
    def mobile_context(self, browser, _login_storage_state):
        """Context mit iPhone-UA + Touch, Viewport erst je Test gesetzt."""
        context = browser.new_context(
            storage_state=_login_storage_state,
            locale="de-DE",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=2,
            user_agent=_IPHONE_UA,
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            yield page
        finally:
            context.close()

    @pytest.mark.parametrize("width,height", [(390, 844), (375, 812)])
    def test_button_is_fully_inside_the_viewport_and_tappable(self, mobile_context, base_url, width, height):
        page = mobile_context
        page.set_viewport_size({"width": width, "height": height})
        url = _client_detail_url(page, base_url)
        page.goto(url, wait_until="domcontentloaded")

        # Kein Seitenscroll — deckungsgleich mit dem Ticket-Befund
        # (``document.scrollWidth == clientWidth``).
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width == client_width, (
            f"Seite scrollt horizontal ({scroll_width}px Inhalt bei {client_width}px Sichtfenster) — "
            "der Ticket-Befund nennt genau dieses Fehlen als Ursache dafuer, dass der abgeschnittene "
            "Knopf nicht per Wischen erreichbar ist."
        )

        box = _submit_button_box(page)
        assert box is not None, "Knopf 'Verarbeitung einschraenken' nicht im DOM gefunden."

        assert box["x"] >= 0, f"Knopf beginnt links ausserhalb des Sichtfensters (x={box['x']})."
        assert box["x"] + box["width"] <= width, (
            f"Knopf liegt (teilweise) ausserhalb des {width}px-Sichtfensters: "
            f"x={box['x']}, width={box['width']}, x+width={box['x'] + box['width']}."
        )
        assert box["height"] >= _MIN_HIT_AREA_PX, (
            f"Trefferflaeche zu niedrig: {box['height']}px < {_MIN_HIT_AREA_PX}px (WCAG 2.5.5/2.5.8-Zielgroesse)."
        )
        assert box["width"] >= _MIN_HIT_AREA_PX, f"Trefferflaeche zu schmal: {box['width']}px < {_MIN_HIT_AREA_PX}px."

        # Der Knopf muss tatsaechlich antippbar sein (kein anderes Element
        # ueberlagert ihn) — ``click`` mit dem Default-Aktionsraum schlaegt
        # fehl, wenn Playwright das Ziel fuer nicht getroffen haelt.
        page.locator(f"[data-testid='{_SUBMIT_TESTID}']").click(trial=True)

    def test_landscape_counter_probe_still_works(self, mobile_context, base_url):
        """Gegenprobe: in der Querlage (844x390) war der Knopf laut Befund immer da."""
        page = mobile_context
        page.set_viewport_size({"width": 844, "height": 390})
        url = _client_detail_url(page, base_url)
        page.goto(url, wait_until="domcontentloaded")

        box = _submit_button_box(page)
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= 844

    def test_keyboard_path_reaches_the_button_with_visible_focus(self, mobile_context, base_url):
        """Tab-Reihenfolge (WCAG 2.1.1/2.4.7): der Knopf ist per Tastatur erreichbar,
        der Fokus dabei sichtbar (nicht per CSS ausgeblendet)."""
        page = mobile_context
        page.set_viewport_size({"width": 390, "height": 844})
        url = _client_detail_url(page, base_url)
        page.goto(url, wait_until="domcontentloaded")

        target = page.locator(f"[data-testid='{_SUBMIT_TESTID}']")
        target.scroll_into_view_if_needed()
        reached = False
        for _ in range(40):
            page.keyboard.press("Tab")
            is_target = page.evaluate(
                "(testid) => document.activeElement && document.activeElement.dataset.testid === testid",
                _SUBMIT_TESTID,
            )
            if is_target:
                reached = True
                break
        assert reached, "Der Knopf ist per Tab-Reihenfolge nicht erreichbar."

        outline = page.evaluate(
            "() => { const s = getComputedStyle(document.activeElement); "
            "return {outline: s.outlineStyle, outlineWidth: s.outlineWidth, boxShadow: s.boxShadow}; }"
        )
        assert outline["outline"] != "none" or outline["boxShadow"] != "none", (
            f"Kein sichtbarer Fokusindikator auf dem Knopf: {outline}"
        )

    @pytest.mark.parametrize("width,height", [(390, 844), (375, 812)])
    def test_axe_smoke_stays_within_the_baseline(self, mobile_context, base_url, width, height):
        """a11y-Nachweis: axe ueber die Seite in beiden Breiten.

        ``a11y_baseline.json`` ist fuer diese Seite leer ({}) — jeder
        critical/serious-Fund waere hier bereits ein Fehlschlag, nicht erst
        eine Regression gegen eine erlaubte Zahl.
        """
        page = mobile_context
        page.set_viewport_size({"width": width, "height": height})
        url = _client_detail_url(page, base_url)
        page.goto(url, wait_until="domcontentloaded")

        result = run_axe(page, base_url, tags=AXE_TAGS)
        blocking = result.blocking()
        assert not blocking, f"axe critical/serious auf {url} ({width}x{height}): {blocking}"
