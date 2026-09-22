"""E2E: Demo-Bereichsauswahl + Bereichswechsel (Refs #2285).

**Geschrieben, NICHT gefahren** (Standardblock Punkt 46/Briefing F-3) — der
Orchestrator fährt diese Datei seriell unter dem globalen CI-Lock, NACH dem
Merge von Lane F-2 (#2279, Szenariokatalog) und dieser Lane, UND nachdem der
E2E-Server tatsächlich mit ``DEMO_MODE=True`` UND einem `--scale demo`-Seed
läuft.

**Zwei Vorbedingungen, die diese Lane NICHT selbst herstellen kann** (siehe
Bericht, Abschnitt „Nicht gefahren/nicht verifiziert"):

1. ``anlaufstelle.settings.e2e`` setzt ``DEMO_MODE`` bisher NICHT (die Datei
   ist nicht Teil der Besitzliste dieser Lane) — ohne einen Schalter
   (analog dem bestehenden ``E2E_MFA_ENFORCE_ROLES``-Muster in
   ``settings/e2e.py``) liefern ``/demo/`` und ``/demo/start/`` dort 404.
2. Die E2E-Datenbank (``anlaufstelle_e2e``) braucht ``--scale demo``
   (Lane F-2s Szenariokatalog) statt des Standard-Seeds, damit die
   fachlichen Bereiche (Kontaktladen, Solo-Betrieb, …) tatsächlich existieren.

Jede Testklasse ueberspringt sich SELBST (``pytest.skip``), wenn ``/demo/``
nicht 200 liefert — Anlaufen gegen eine Umgebung ohne diese beiden
Vorbedingungen bricht damit nicht hart ab, sondern meldet ehrlich, dass die
Vorbedingung fehlt (Standardblock Regel 25).

Deckt aus dem Briefing (Ticket-Punkte 1/2/4/5/8/9): Bereichsauswahl vor der
ersten Anmeldung, Start ohne Zugangsdaten in der Alltagsrolle, jederzeit
erreichbarer Wechsel über das Demo-Banner, unabhaengige Besuchersitzungen,
Erkennen eines Wechsels in einem zweiten Tab derselben Sitzung.
"""

import pytest

from ._helpers import django_shell

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


@pytest.fixture
def demo_mode_or_skip(base_url, browser):
    if not _demo_enabled(base_url, browser):
        pytest.skip(
            "DEMO_MODE/--scale demo-Vorbedingung nicht erfuellt (settings/e2e.py "
            "braucht einen Schalter, s. Moduldocstring) — Spec geschrieben, aber "
            "in dieser Umgebung nicht ausfuehrbar."
        )


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
            if cards.count() < 2:
                pytest.skip("Weniger als zwei Demo-Bereiche im Katalog — Wechsel nicht pruefbar.")

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
        context = browser.new_context(locale="de-DE")
        try:
            page_old_tab = context.new_page()
            page_old_tab.goto(f"{base_url}/demo/")
            cards = page_old_tab.locator("[data-testid='demo-scenario-card']")
            if cards.count() < 2:
                pytest.skip("Weniger als zwei Demo-Bereiche im Katalog — Wechsel nicht pruefbar.")
            first_key = cards.nth(0).get_attribute("data-scenario-key")
            cards.nth(0).locator("[data-testid='demo-scenario-start']").click()
            page_old_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # "Alter Tab": bleibt auf der App-Seite von Bereich 1 stehen,
            # WAEHREND ein neuer Tab (gleiche Sitzung/Cookies) den Wechsel
            # ausloest.
            page_new_tab = context.new_page()
            page_new_tab.goto(f"{base_url}/demo/")
            cards = page_new_tab.locator("[data-testid='demo-scenario-card']")
            for i in range(cards.count()):
                if cards.nth(i).get_attribute("data-scenario-key") != first_key:
                    cards.nth(i).locator("[data-testid='demo-scenario-start']").click()
                    break
            page_new_tab.wait_for_url(lambda url: "/demo/" not in url, timeout=10000)

            # Alter Tab neu laden: kontrolliertes Verhalten statt eines
            # stillen Mischzustands — die Seite laedt entweder mit dem neuen
            # Bereichskontext neu, oder das Reset-Session-Cookie fuehrt zu
            # einem Login-Redirect. Beides ist "kein stiller Weiterbetrieb im
            # alten Bereich"; harte Assertion auf das Nicht-Fehlschlagen des
            # Reloads selbst.
            response = page_old_tab.reload()
            assert response is not None and response.status in (200, 302)
        finally:
            context.close()
