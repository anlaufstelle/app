"""E2E-Tests für Mobile Views (iPhone SE Viewport 375x812).

Prüft Layout, Navigation und Overflow auf mobilen Viewports.
Refs #382, #418.
"""

import pytest
from django.utils import timezone

from ._selectors import action_bar, open_mobile_overflow, wait_for_settled

pytestmark = pytest.mark.e2e

# Deutscher Monatsname für Datums-Asserts (locale-frei, Refs #761).
_MONTHS_DE = {
    1: "Januar",
    2: "Februar",
    3: "März",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}


@pytest.fixture
def mobile_page(browser, base_url, _login_storage_state):
    """Playwright-Page mit iPhone-SE-Viewport (375x812) und Admin-Login."""
    context = browser.new_context(
        storage_state=_login_storage_state,
        viewport={"width": 375, "height": 812},
        device_scale_factor=2,
        locale="de-DE",
    )
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    yield page
    context.close()


def _assert_no_horizontal_overflow(page):
    """Prüft, dass die Seite keinen horizontalen Scrollbar hat (body overflow-x: hidden erlaubt)."""
    can_scroll = page.evaluate("window.innerWidth > document.documentElement.clientWidth")
    assert not can_scroll, "Horizontaler Scrollbar sichtbar"


class TestMobileNavMore:
    """Mobile Bottom-Nav mit Mehr-Dropdown (Refs #1813: Selektoren auf testids).

    Vorher haingen diese Tests an Label-Treffern (``a:has-text('Aufgaben')``).
    Das war doppelt unscharf: „Aufgaben" ist ein eigener Bottom-Bar-Slot, kein
    Menue-Eintrag — der Treffer kam also gar nicht aus dem Dropdown, und mit
    einer englischen Sitzung waere er weggefallen. Jetzt greifen die Tests den
    Container ``mobile-more-items`` und die Ziele darin.
    """

    def _open_more(self, page):
        page.locator("[data-testid='mobile-nav-more']").click()
        menu = page.locator("[data-testid='mobile-more-items']")
        menu.wait_for(state="visible", timeout=10000)
        return menu

    def test_more_dropdown(self, mobile_page, base_url):
        """Anwendungsbetreuung: Ziel-IA des Menues (Konzept #1801 §5.2)."""
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        menu = self._open_more(page)

        assert menu.locator("[data-testid='mobile-nav-search']").is_visible()
        assert menu.locator("a[href='/cases/']").is_visible()
        assert menu.locator("a[href='/attachments/']").is_visible()
        assert menu.locator("a[href='/statistics/']").is_visible()
        assert menu.locator("[data-testid='mobile-nav-datenschutz']").is_visible()
        assert menu.locator("[data-testid='mobile-nav-verwaltung']").is_visible()
        # Die vier Bottom-Bar-Slots stehen NICHT zusaetzlich im Menue.
        assert menu.locator("a[href='/clients/']").count() == 0
        assert menu.locator("a[href='/workitems/']").count() == 0

    def test_staff_no_statistik_in_more(self, base_url, browser):
        """Fachkraft: kein Statistik- und kein Verwaltungs-Eintrag im Mehr-Menue."""
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{base_url}/")

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/")

        menu = self._open_more(page)

        assert menu.locator("a[href='/cases/']").is_visible(), "Faelle fehlen — Menue leer geladen?"
        assert menu.locator("a[href='/statistics/']").count() == 0
        assert menu.locator("[data-testid='mobile-nav-verwaltung']").count() == 0
        context.close()

    def test_language_switch_is_reachable_on_mobile(self, mobile_page, base_url):
        """Refs #1813: mobil liess sich die Sprache bisher gar nicht wechseln.

        Bewusst nur die Erreichbarkeit: ein Klick wuerde ueber
        ``set_user_language`` die Praeferenz am Seed-User **persistieren** und
        damit alle folgenden Specs dieses Laufs auf Englisch stellen.
        """
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        menu = self._open_more(page)

        assert menu.locator("[data-testid='mobile-nav-language-de']").is_visible()
        assert menu.locator("[data-testid='mobile-nav-language-en']").is_visible()
        # Groesse erst messen, wenn die Alpine-Einblendung durch ist: waehrend
        # ``x-transition`` skaliert das Panel (scale .95 -> 1) und
        # ``bounding_box()`` liefert die skalierte Groesse — ein 44-px-Ziel
        # maesse dann ~42 px (Refs #1813).
        wait_for_settled(menu)
        for testid in ("mobile-nav-language-de", "mobile-nav-language-en"):
            box = menu.locator(f"[data-testid='{testid}']").bounding_box()
            assert box is not None and box["height"] >= 44, f"{testid}: Touch-Target zu klein"


class TestMobileCards:
    """Card-Layout auf Mobile."""

    def test_clients_card_layout_mobile(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Table should be hidden on mobile
        assert not page.locator(".hidden.sm\\:block table").is_visible()
        # Card layout should be visible
        assert page.locator(".sm\\:hidden").first.is_visible()


class TestMobileSidebarAndNav:
    """Sidebar versteckt, Bottom-Nav sichtbar auf Mobile."""

    def test_mobile_sidebar_hidden(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Desktop-Sidebar muss unsichtbar sein (hidden below md)
        sidebar = page.locator("nav[aria-label='Hauptnavigation']")
        assert not sidebar.is_visible(), "Desktop-Sidebar sollte auf Mobile versteckt sein"

    def test_mobile_bottom_nav_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Bottom-Nav muss sichtbar sein
        bottom_nav = page.locator("nav[aria-label='Mobile Navigation']")
        assert bottom_nav.is_visible(), "Mobile Bottom-Nav sollte sichtbar sein"

    def test_mobile_bottom_nav_has_all_slots(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Alle 5 Slots: Neu, Zeitstrom, Klientel, Aufgaben, Mehr (Suche im Mehr-Dropdown)
        assert page.locator("[data-testid='mobile-nav-create']").is_visible()
        assert page.locator("[data-testid='mobile-nav-zeitstrom']").is_visible()
        assert page.locator("[data-testid='mobile-nav-clients']").is_visible()
        assert page.locator("[data-testid='mobile-nav-workitems']").is_visible()
        assert page.locator("[data-testid='mobile-nav-more']").is_visible()

    def test_mobile_touch_targets_min_size(self, mobile_page, base_url):
        """Bottom-Nav-Buttons haben mindestens 44px Touch-Target."""
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        for testid in [
            "mobile-nav-create",
            "mobile-nav-zeitstrom",
            "mobile-nav-clients",
            "mobile-nav-workitems",
            "mobile-nav-more",
        ]:
            box = page.locator(f"[data-testid='{testid}']").bounding_box()
            assert box is not None, f"Touch-Target {testid} nicht gefunden"
            assert box["width"] >= 44, f"{testid}: Breite {box['width']}px < 44px"
            assert box["height"] >= 44, f"{testid}: Höhe {box['height']}px < 44px"


class TestMobileNavigation:
    """Bottom-Nav-Links navigieren korrekt zwischen Views."""

    def test_mobile_nav_zeitstrom(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-zeitstrom']").click()
        page.wait_for_url("**/")
        assert page.locator("h1").inner_text() == "Zeitstrom"

    def test_mobile_nav_more_opens_menu(self, mobile_page, base_url):
        # Personen/Aufgaben sind eigene Bottom-Nav-Slots — Mehr enthält Dateien/Suche/Profil etc.
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-more']").click()
        profil_link = page.locator("[data-testid='mobile-more-items'] a[href='/account/']")
        profil_link.wait_for(state="visible", timeout=10000)
        assert profil_link.is_visible(), "Profil-Link im Mehr-Menue nicht sichtbar"

    def test_mobile_nav_clients_navigates(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-clients']").click()
        page.wait_for_url("**/clients/")
        assert page.locator("h1").inner_text() == "Personen"

    def test_mobile_nav_workitems_navigates(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-workitems']").click()
        page.wait_for_url("**/workitems/")
        assert page.locator("h1").inner_text() == "Aufgaben"

    def test_mobile_nav_create_opens_dropdown(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-create']").click()
        dropdown = page.locator("[data-testid='mobile-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=10000)
        assert dropdown.is_visible(), "Create-Dropdown sollte sichtbar sein"
        assert dropdown.locator("a:has-text('Kontakt')").is_visible()

    def test_mobile_nav_search_opens_overlay(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Suche liegt jetzt im Mehr-Dropdown (Design-Refresh: 5 Bottom-Nav-Slots).
        page.locator("[data-testid='mobile-nav-more']").click()
        page.locator("[data-testid='mobile-nav-search']").click()
        overlay = page.locator("[data-testid='mobile-search-overlay']")
        overlay.wait_for(state="visible", timeout=10000)
        assert overlay.is_visible(), "Such-Overlay sollte sichtbar sein"


class TestMobileZeitstrom:
    """Zeitstrom-Seite auf Mobile."""

    def test_mobile_zeitstrom_loads(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Zeitstrom"
        assert page.locator("#feed-list").is_visible()

    def test_mobile_zeitstrom_no_overflow(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_mobile_zeitstrom_date_navigation_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Datums-Navigation sollte sichtbar und nutzbar sein (Heute-Datum als h2-aehnliches Element).
        # Monatsnamen aus der aktuellen Server-Zeit ableiten — sonst wird der Test
        # am 1. eines Monats rot, sobald sich der Server-Monat dreht (Refs #761).
        current_month = _MONTHS_DE[timezone.localdate().month]
        date_nav = page.locator(f"text={current_month}")
        assert date_nav.first.is_visible()

    def test_mobile_zeitstrom_filter_selects_visible(self, mobile_page, base_url):
        # Type-Filter ist seit Visual-Refresh (#663) als Pills (Alle/Kontakte/Aktivitaeten/...) statt Select.
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Mindestens eine Filter-Pill muss sichtbar sein (z.B. "Alle")
        alle_pill = page.locator("a:has-text('Alle')").first
        assert alle_pill.is_visible(), "Type-Filter-Pill sollte auf Mobile sichtbar sein"


class TestMobileClientList:
    """Klientel-Liste auf Mobile."""

    def test_mobile_client_list_loads(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Personen"

    def test_mobile_client_list_no_overflow(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_mobile_client_list_search_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Use placeholder to distinguish from global search inputs
        search_input = page.get_by_placeholder("Pseudonym suchen")
        assert search_input.is_visible(), "Suchfeld sollte auf Mobile sichtbar sein"

    def test_mobile_client_list_filters_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        stage_filter = page.locator("select[name='stage']")
        assert stage_filter.is_visible(), "Stufen-Filter sollte auf Mobile sichtbar sein"

        age_filter = page.locator("select[name='age']")
        assert age_filter.is_visible(), "Altersgruppen-Filter sollte auf Mobile sichtbar sein"


class TestMobileInbox:
    """Aufgaben-Inbox auf Mobile."""

    def test_mobile_inbox_loads(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Aufgaben"

    def test_mobile_inbox_no_overflow(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_mobile_inbox_filters_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("#filter-item-type").is_visible(), "Typ-Filter sichtbar"
        assert page.locator("#filter-priority").is_visible(), "Priorität-Filter sichtbar"


class TestMobileEventCreate:
    """Event-Erstellung auf Mobile."""

    def test_mobile_event_create_loads(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Neuer Kontakt"

    def test_mobile_event_create_no_overflow(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")
        _assert_no_horizontal_overflow(page)

    def test_mobile_event_create_form_fields_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        # Dokumentationstyp und Zeitpunkt sollten sichtbar sein
        assert page.locator("#id_document_type").is_visible()
        assert page.locator("#id_occurred_at").is_visible()

    def test_mobile_event_create_submit_button_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/events/new/")
        page.wait_for_load_state("domcontentloaded")

        submit_btn = page.locator("#event-submit-btn")
        assert submit_btn.is_visible(), "Submit-Button sollte sichtbar sein"

        # Button muss im sichtbaren Bereich sein (scrollbar)
        submit_btn.scroll_into_view_if_needed()
        box = submit_btn.bounding_box()
        assert box is not None, "Submit-Button hat keine Bounding Box"
        assert box["width"] >= 44, f"Submit-Button zu schmal: {box['width']}px"


class TestMobileActionButtonsHidden:
    """Auf Mobile sind Inline-Create-Buttons versteckt (Bottom-Nav übernimmt). Refs #448."""

    def test_zeitstrom_create_buttons_hidden(self, mobile_page, base_url):
        """Zeitstrom: 3 Create-Buttons sind auf Mobile unsichtbar."""
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Der Wrapper-Div mit den 3 Desktop-Buttons (hidden md:flex)
        buttons_wrapper = page.locator("h1:has-text('Zeitstrom') + div")
        assert not buttons_wrapper.is_visible(), "Create-Buttons sollten auf Mobile versteckt sein"

    def test_client_list_create_button_hidden(self, mobile_page, base_url):
        """Personenliste: 'Neue Person'-Button auf Mobile unsichtbar."""
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Direkt am Ziel-Link statt am Wrapper: die Kopfzeile der Personenliste
        # trägt heute ``hidden md:flex`` (nicht mehr ``md:block``) — der alte
        # Klassen-Selektor traf NICHTS und war damit tautologisch wahr.
        create_link = page.locator("main a[href$='/clients/new/']")
        assert not create_link.first.is_visible(), "Neue-Person-Button sollte auf Mobile versteckt sein"

    def test_case_list_create_button_hidden(self, mobile_page, base_url):
        """Fälle-Liste: 'Neuer Fall'-Button auf Mobile unsichtbar."""
        page = mobile_page
        page.goto(f"{base_url}/cases/")
        page.wait_for_load_state("domcontentloaded")

        main = page.locator("main")
        create_wrapper = main.locator(".hidden.md\\:block").first
        assert not create_wrapper.is_visible(), "Neuer-Fall-Button sollte auf Mobile versteckt sein"

    def test_workitem_list_create_button_hidden(self, mobile_page, base_url):
        """Aufgaben-Inbox: 'Neue Aufgabe'-Button auf Mobile unsichtbar."""
        page = mobile_page
        page.goto(f"{base_url}/workitems/")
        page.wait_for_load_state("domcontentloaded")

        main = page.locator("main")
        create_wrapper = main.locator(".hidden.md\\:block").first
        assert not create_wrapper.is_visible(), "Neue-Aufgabe-Button sollte auf Mobile versteckt sein"


class TestMobileDetailOverflowMenu:
    """Detail-Seiten haben auf Mobile ein Overflow-Menü statt vieler Buttons. Refs #448."""

    def test_client_detail_shows_edit_icon_and_overflow(self, mobile_page, base_url):
        """Klienteldetail: Bearbeiten-Icon + ⋯-Menü sichtbar, Desktop-Buttons versteckt."""
        page = mobile_page
        # Klientelliste laden und erste Klientel-PK aus dem HTML extrahieren
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")
        pk = page.evaluate("""
            () => {
                const m = document.body.innerHTML.match(/\\/clients\\/([0-9a-f-]{36})\\//);
                return m ? m[1] : null;
            }
        """)
        assert pk, "Kein Klientel in der Datenbank gefunden"
        page.goto(f"{base_url}/clients/{pk}/")
        page.wait_for_load_state("domcontentloaded")

        # Desktop-Aktionsleiste ist unter md ausgeblendet (Refs #1813: der
        # Container traegt jetzt ein data-testid statt nur Tailwind-Klassen —
        # der alte Klassen-Selektor traf nach dem Umbau NICHTS und war damit
        # tautologisch wahr).
        assert not action_bar(page, "client", "desktop").is_visible(), (
            "Desktop-Aktionsleiste sollte auf Mobile versteckt sein"
        )
        assert action_bar(page, "client", "mobile").is_visible(), "Mobile Aktionsleiste sollte sichtbar sein"

        # Overflow-Menü-Button sichtbar
        overflow_btn = page.locator("[data-testid='mobile-overflow-menu']")
        assert overflow_btn.is_visible(), "Overflow-Menü-Button sollte sichtbar sein"

    def test_client_detail_overflow_menu_has_actions(self, mobile_page, base_url):
        """Klienteldetail mobil: Kontakt dokumentieren, Aufgabe erstellen und Fall anlegen erreichbar.

        Refs #1813 (§6.4 Paritaetsregel): „Kontakt dokumentieren" und „Aufgabe
        erstellen" sind mobil PRIMAER (Icon + aria-label, kein Text) statt
        Menue-Eintraege; nur „Fall anlegen" liegt im ⋮-Menue. Die Aussage des
        Tests bleibt: alle drei Anlage-Aktionen sind mobil ohne Umweg da.
        """
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")
        pk = page.evaluate("""
            () => {
                const m = document.body.innerHTML.match(/\\/clients\\/([0-9a-f-]{36})\\//);
                return m ? m[1] : null;
            }
        """)
        assert pk, "Kein Klientel in der Datenbank gefunden"
        page.goto(f"{base_url}/clients/{pk}/")
        page.wait_for_load_state("domcontentloaded")

        # Primaeraktionen stehen direkt in der mobilen Leiste.
        bar = action_bar(page, "client", "mobile")
        assert bar.locator("[data-testid='client-action-contact']").is_visible()
        assert bar.locator("[data-testid='client-action-task']").is_visible()

        # Overflow-Menü öffnen
        menu = open_mobile_overflow(page, "client")

        # Refs #1734: die Fall-Anlage war auf Mobil praktisch unauffindbar
        # (Textlink unterhalb der kompletten Kontakt-Chronik).
        assert menu.locator("[data-testid='mobile-client-new-case-btn']").is_visible()


class TestMobileNoHorizontalScroll:
    """Kein horizontaler Overflow auf den wichtigsten Seiten."""

    @pytest.mark.parametrize(
        "path,title",
        [
            ("/", "Zeitstrom"),
            ("/clients/", "Personen"),
            ("/workitems/", "Aufgaben"),
            ("/events/new/", "Neuer Kontakt"),
            ("/?view=uebergabe", "Zeitstrom"),
        ],
    )
    def test_mobile_no_horizontal_scroll(self, mobile_page, base_url, path, title):
        page = mobile_page
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("domcontentloaded")

        assert title in page.locator("h1").inner_text()
        _assert_no_horizontal_overflow(page)
