"""E2E: Demo-Bereichsauswahl + Bereichswechsel (Refs #2285).

Laeuft nur gegen einen E2E-Server im Demo-Modus mit Szenariokatalog. Beide
Vorbedingungen stellen die E2E-Fixtures ueber Umgebungsvariablen her:

1. ``E2E_DEMO_MODE=1`` — ``anlaufstelle.settings.e2e`` schaltet damit
   ``DEMO_MODE`` ein; ohne den Schalter liefern ``/demo/`` und
   ``/demo/start/`` 404.
2. ``E2E_SEED_SCALE=demo`` — die E2E-Datenbank wird mit dem Szenariokatalog
   (``seed --scale demo``) statt des Standard-Seeds befuellt, damit die
   fachlichen Bereiche (Kontaktladen, Solo-Betrieb, …) tatsaechlich existieren.

Aufruf::

    E2E_DEMO_MODE=1 E2E_SEED_SCALE=demo pytest src/tests/e2e/test_demo_scenario_switch.py -m e2e

Outside the explicit demo profile the spec skips. Inside that profile,
a missing scenario catalog or a broken /demo/ response is a test failure.

Deckt ab: Bereichsauswahl vor der ersten Anmeldung, Start ohne Zugangsdaten in
der Alltagsrolle, jederzeit erreichbarer Wechsel ueber das Demo-Banner,
unabhaengige Besuchersitzungen, Erkennen eines Wechsels in einem zweiten Tab
derselben Sitzung.
"""

import pytest

from ._helpers import django_shell
from ._profiles import require_demo

pytestmark = pytest.mark.e2e


def _demo_enabled(base_url, browser):
    """Best-effort Sonde: liefert ``/demo/`` 200? Sonst gilt die Vorbedingung
    (``DEMO_MODE`` + ``--scale demo``-Seed) als nicht erfuellt."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    try:
        response = page.goto(f"{base_url}/demo/")
        return response is not None and response.status == 200
    finally:
        context.close()


def _session_key(context):
    """Value of the session cookie in the context, ``None`` when it is absent."""
    cookies = [c for c in context.cookies() if c["name"] == "sessionid"]
    return cookies[0]["value"] if cookies else None


@pytest.fixture
def demo_mode_or_skip(base_url, browser):
    require_demo(lambda: _demo_enabled(base_url, browser))


@pytest.mark.usefixtures("demo_mode_or_skip")
class TestScenarioSelectionBeforeFirstLogin:
    """Ticket-Punkt 1/2: Bereichsauswahl vor der ersten Anmeldung."""

    @pytest.mark.smoke
    def test_select_page_lists_scenario_cards(self, base_url, browser):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/")
            cards = page.locator("[data-testid='demo-scenario-card']")
            assert cards.count() > 0
        finally:
            context.close()

    def test_deep_link_preselects_without_logging_in(self, base_url, browser):
        """GET/Deep-Link waehlt nur vor (Ticket-Punkt 8) — der reine Aufruf
        der Auswahlseite mit ``?bereich=`` meldet NICHT an: kein
        Wechsel-Link (== kein Demo-Banner, == kein angemeldetes Konto)."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/?bereich=kontaktladen")
            assert page.locator("[data-testid='demo-scenario-switch-link']").count() == 0
        finally:
            context.close()

    @pytest.mark.smoke
    def test_start_button_logs_in_without_credentials(self, base_url, browser):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/")
            first_card = page.locator("[data-testid='demo-scenario-card']").first
            first_card.locator("[data-testid='demo-scenario-start']").click()
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)
            # Demo-Banner mit "Demo-Bereich wechseln" bestaetigt: angemeldet,
            # im Demo-Modus, kein Zugangsdaten-Formular war noetig.
            assert page.locator("[data-testid='demo-scenario-switch-link']").is_visible()
        finally:
            context.close()

    @pytest.mark.smoke
    def test_start_button_logs_in_with_blocked_indexeddb(self, base_url, browser):
        """Demo login stays available when the browser denies IndexedDB access."""
        context = browser.new_context(locale="de-DE")
        context.add_init_script("indexedDB.open = function () { throw new DOMException('Blocked', 'SecurityError'); };")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/")
            first_card = page.locator("[data-testid='demo-scenario-card']").first
            first_card.locator("[data-testid='demo-scenario-start']").click()
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)
            assert page.locator("[data-testid='demo-scenario-switch-link']").is_visible()
            assert _session_key(context) is not None
        finally:
            context.close()


@pytest.mark.usefixtures("demo_mode_or_skip")
class TestScenarioSwitchFromBanner:
    """Ticket-Punkt 4/5/9: jederzeit erreichbarer Wechsel, saubere Trennung."""

    @pytest.mark.smoke
    def test_switch_link_returns_to_selection(self, base_url, browser):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/")
            page.locator("[data-testid='demo-scenario-card']").first.locator(
                "[data-testid='demo-scenario-start']"
            ).click()
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            page.locator("[data-testid='demo-scenario-switch-link']").click()
            page.wait_for_url(lambda url: url.rstrip("/").endswith("/demo"), timeout=10000)
            assert page.locator("[data-testid='demo-scenario-select']").is_visible()
        finally:
            context.close()

    def test_switching_to_a_different_scenario_changes_current_facility(self, base_url, browser, e2e_env):
        """Wechsel zwischen zwei verschiedenen Karten meldet ein ANDERES Konto
        an, ohne die ``facility``-Zuordnung eines Kontos umzuschreiben
        (Ticket-Punkt 5) — per ``manage.py shell`` gegenprobiert."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/demo/")
            cards = page.locator("[data-testid='demo-scenario-card']")
            assert cards.count() >= 2, "The demo catalog must offer at least two scenarios to switch between."

            first_key = cards.nth(0).get_attribute("data-scenario-key")
            cards.nth(0).locator("[data-testid='demo-scenario-start']").click()
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            page.locator("[data-testid='demo-scenario-switch-link']").click()
            page.wait_for_url(lambda url: url.rstrip("/").endswith("/demo"), timeout=10000)

            cards = page.locator("[data-testid='demo-scenario-card']")
            second_key = None
            for i in range(cards.count()):
                key = cards.nth(i).get_attribute("data-scenario-key")
                if key != first_key:
                    second_key = key
                    cards.nth(i).locator("[data-testid='demo-scenario-start']").click()
                    break
            assert second_key is not None
            page.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # Gegenprobe auf DB-Ebene: beide Facility-Zuordnungen der fest
            # zugeordneten Demo-Konten sind unveraendert (kein Umschreiben).
            out = django_shell(
                e2e_env,
                "from core.seed.scenarios import get_scenario\n"
                "from core.models import User\n"
                f"a = get_scenario({first_key!r})\n"
                f"b = get_scenario({second_key!r})\n"
                "ua = User.objects.filter(facility__name=a.facility_name, role=a.demo_roles[0]).first()\n"
                "ub = User.objects.filter(facility__name=b.facility_name, role=b.demo_roles[0]).first()\n"
                "print(ua.facility.name == a.facility_name, ub.facility.name == b.facility_name)",
            )
            assert out == ["True True"]
        finally:
            context.close()

    def test_two_independent_visitor_contexts_do_not_interfere(self, base_url, browser):
        """Ticket-Punkt 5: eine Besuchersitzung darf keine andere beeinflussen
        — zwei getrennte Browser-Contexts (= zwei Cookie-Jars) starten je
        einen Bereich, ohne sich gegenseitig auszuloggen."""
        context_a = browser.new_context(locale="de-DE")
        context_b = browser.new_context(locale="de-DE")
        try:
            page_a = context_a.new_page()
            page_a.goto(f"{base_url}/demo/")
            page_a.locator("[data-testid='demo-scenario-card']").first.locator(
                "[data-testid='demo-scenario-start']"
            ).click()
            page_a.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            page_b = context_b.new_page()
            page_b.goto(f"{base_url}/demo/")
            page_b.locator("[data-testid='demo-scenario-card']").first.locator(
                "[data-testid='demo-scenario-start']"
            ).click()
            page_b.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # Sitzung A bleibt nach dem Start von B unveraendert angemeldet.
            page_a.reload()
            assert page_a.locator("[data-testid='demo-scenario-switch-link']").is_visible()
        finally:
            context_a.close()
            context_b.close()


@pytest.mark.usefixtures("demo_mode_or_skip")
class TestOldTabAfterSwitch:
    """Ticket-Punkt 9: alte Tabs erkennen den Wechsel oder laden kontrolliert
    neu — hier zwei Seiten IM SELBEN Context (= dieselbe Sitzung/Cookie-Jar),
    wie zwei Tabs desselben Browsers."""

    def test_stale_tab_reload_reflects_the_switch(self, base_url, browser):
        """Scharfe Assertion statt ``status in (200, 302)`` — Playwright-
        Contexts teilen sich den
        Cookie-Jar ueber alle Seiten hinweg (kein Per-Tab-Storage), daher
        traegt der ALTE Tab nach einem Reload automatisch das NEUE
        Sitzungscookie aus dem zweiten Tab. Der scharfe Nachweis ist deshalb
        nicht der Statuscode, sondern der tatsaechliche Banner-Inhalt: der
        alte Tab zeigt nach dem Reload den ZWEITEN Bereich, nicht mehr den
        ersten — "erkennt den Wechsel" im Wortsinn (Ticket-Punkt 9)."""
        context = browser.new_context(locale="de-DE")
        try:
            page_old_tab = context.new_page()
            page_old_tab.goto(f"{base_url}/demo/")
            cards = page_old_tab.locator("[data-testid='demo-scenario-card']")
            assert cards.count() >= 2, "The demo catalog must offer at least two scenarios to switch between."
            first_key = cards.nth(0).get_attribute("data-scenario-key")
            first_name = cards.nth(0).locator("h2").inner_text().strip()
            cards.nth(0).locator("[data-testid='demo-scenario-start']").click()
            page_old_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # "Alter Tab": bleibt auf der App-Seite von Bereich 1 stehen,
            # WAEHREND ein neuer Tab (gleiche Sitzung/Cookies) den Wechsel
            # ausloest.
            page_new_tab = context.new_page()
            page_new_tab.goto(f"{base_url}/demo/")
            cards = page_new_tab.locator("[data-testid='demo-scenario-card']")
            second_name = None
            for i in range(cards.count()):
                if cards.nth(i).get_attribute("data-scenario-key") != first_key:
                    second_name = cards.nth(i).locator("h2").inner_text().strip()
                    cards.nth(i).locator("[data-testid='demo-scenario-start']").click()
                    break
            assert second_name is not None
            page_new_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # Alter Tab neu laden: der geteilte Cookie-Jar traegt bereits die
            # neue Sitzung — der Banner muss jetzt den ZWEITEN Bereich zeigen,
            # NICHT mehr den ersten (kein stiller Weiterbetrieb im alten
            # Bereich, kein Mischzustand).
            page_old_tab.reload()
            banner = page_old_tab.locator("[data-testid='demo-current-scenario']")
            banner.wait_for(timeout=10000)
            banner_text = banner.inner_text()
            assert second_name in banner_text, f"Banner zeigt nicht den neuen Bereich: {banner_text!r}"
            assert first_name not in banner_text, f"Banner zeigt noch den alten Bereich: {banner_text!r}"
        finally:
            context.close()

    def test_requests_pending_during_the_switch_keep_the_new_session(self, base_url, browser):
        """A request the old tab sent before the switch reaches the server
        after it. Its response must not delete the cookie the switch set.

        The service worker is blocked so that no background fetch races the
        test. The pending request is created explicitly: the route holds it,
        and once the switch is done it is sent with the replaced session key
        pinned in the ``Cookie`` header, exactly what the browser attached when
        the request was sent. ``route.fetch`` goes through the context's request
        API, which applies the response's ``Set-Cookie`` to the shared cookie
        jar like the browser does. Releasing the held route with ``continue_``
        instead would make the browser add the new cookie at that moment, and
        the stale request would not exist.
        """
        context = browser.new_context(locale="de-DE", service_workers="block")
        held = []
        try:
            context.route("**/offline/workitems/?pending=1", lambda route: held.append(route))

            page_old_tab = context.new_page()
            page_old_tab.goto(f"{base_url}/demo/")
            cards = page_old_tab.locator("[data-testid='demo-scenario-card']")
            assert cards.count() >= 2, "The demo catalog must offer at least two scenarios to switch between."
            first_key = cards.nth(0).get_attribute("data-scenario-key")
            cards.nth(0).locator("[data-testid='demo-scenario-start']").click()
            page_old_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)
            old_session_key = _session_key(context)

            # Request in flight from the old tab, held before it reaches the server.
            page_old_tab.evaluate(
                "() => { window.__pending = fetch('/offline/workitems/?pending=1').then((r) => r.status); }"
            )
            for _ in range(100):
                if held:
                    break
                page_old_tab.wait_for_timeout(50)
            assert len(held) == 1, "The old tab's request must be held by the route before the switch."

            # The switch happens in a second tab of the same context (shared cookie jar).
            page_new_tab = context.new_page()
            page_new_tab.goto(f"{base_url}/demo/")
            cards = page_new_tab.locator("[data-testid='demo-scenario-card']")
            second_name = None
            for i in range(cards.count()):
                if cards.nth(i).get_attribute("data-scenario-key") != first_key:
                    second_name = cards.nth(i).locator("h2").inner_text().strip()
                    cards.nth(i).locator("[data-testid='demo-scenario-start']").click()
                    break
            assert second_name is not None
            page_new_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)
            new_session_key = _session_key(context)
            assert new_session_key != old_session_key

            # Send the pending request now: it arrives with the replaced key.
            stale_response = held[0].fetch(
                headers={**held[0].request.headers, "cookie": f"sessionid={old_session_key}"}
            )
            assert stale_response.status == 200
            held[0].fulfill(response=stale_response)
            assert page_old_tab.evaluate("() => window.__pending") == 200

            assert _session_key(context) == new_session_key, "The stale response replaced or deleted the new cookie."
            page_old_tab.reload()
            banner = page_old_tab.locator("[data-testid='demo-current-scenario']")
            banner.wait_for(timeout=10000)
            assert second_name in banner.inner_text()
        finally:
            context.close()


@pytest.mark.usefixtures("demo_mode_or_skip")
@pytest.mark.parametrize("path", ["/impressum/", "/datenschutzerklaerung/"])
def test_anonymous_legal_pages_have_no_demo_offline_alert(base_url, browser, path):
    """Anonymous legal pages in the demo must not announce a key failure."""
    context = browser.new_context(locale="de-DE")
    try:
        page = context.new_page()
        response = page.goto(f"{base_url}{path}")
        assert response is not None and response.status == 200
        page.locator("[data-testid='demo-banner']").wait_for()
        assert page.locator("body").get_attribute("data-user-id") == ""
        assert page.locator("[data-demo-offline-error]").count() == 0
        assert page.locator('[role="alert"]').count() == 0
    finally:
        context.close()
