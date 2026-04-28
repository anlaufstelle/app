"""E2E-Tests für Mobile Views (iPhone SE Viewport 375x812).

Prüft Layout, Navigation und Overflow auf mobilen Viewports.
Refs #382, #418.
"""

import pytest

pytestmark = pytest.mark.e2e


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
    """Mobile Bottom-Nav mit Mehr-Dropdown."""

    def test_more_dropdown(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        # Mehr-Button should be visible
        more_btn = page.locator("button:has-text('Mehr')")
        assert more_btn.is_visible()

        # Click opens dropdown with Aufgaben, Klientel
        more_btn.click()
        aufgaben_link = page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Aufgaben')")
        aufgaben_link.wait_for(state="visible", timeout=3000)
        assert aufgaben_link.is_visible()
        assert page.locator("nav[aria-label='Mobile Navigation'] a[href='/clients/']").is_visible()

    def test_staff_no_statistik_in_more(self, base_url, browser):
        """Staff-User sieht kein Statistik im Mehr-Menü."""
        context = browser.new_context()
        page = context.new_page()
        page.goto(f"{base_url}/login/")
        page.fill('input[name="username"]', "miriam")
        page.fill('input[name="password"]', "anlaufstelle2026")
        page.click('button[type="submit"]')
        page.wait_for_url(f"{base_url}/")

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{base_url}/")

        more_btn = page.locator("button:has-text('Mehr')")
        more_btn.click()
        # Menü geöffnet — auf ein immer-vorhandenes Element warten.
        page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Aufgaben')").wait_for(
            state="visible", timeout=3000
        )

        assert page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Statistik')").count() == 0
        context.close()


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

        # Alle 4 Slots: Neu, Zeitstrom, Suche, Mehr
        assert page.locator("[data-testid='mobile-nav-create']").is_visible()
        assert page.locator("[data-testid='mobile-nav-zeitstrom']").is_visible()
        assert page.locator("[data-testid='mobile-nav-search']").is_visible()
        assert page.locator("[data-testid='mobile-nav-more']").is_visible()

    def test_mobile_touch_targets_min_size(self, mobile_page, base_url):
        """Bottom-Nav-Buttons haben mindestens 44px Touch-Target."""
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        for testid in [
            "mobile-nav-create",
            "mobile-nav-zeitstrom",
            "mobile-nav-search",
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
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-more']").click()
        klienten_link = page.locator("nav[aria-label='Mobile Navigation'] a[href='/clients/']")
        klienten_link.wait_for(state="visible", timeout=3000)
        assert klienten_link.is_visible(), "Klientel-Link im Mehr-Menü nicht sichtbar"

    def test_mobile_nav_more_navigate_to_clients(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-more']").click()
        clients_link = page.locator("nav[aria-label='Mobile Navigation'] a[href='/clients/']")
        clients_link.wait_for(state="visible", timeout=3000)
        clients_link.click()
        page.wait_for_url("**/clients/")
        assert page.locator("h1").inner_text() == "Klientel"

    def test_mobile_nav_more_navigate_to_workitems(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-more']").click()
        aufgaben_link = page.locator("nav[aria-label='Mobile Navigation'] a:has-text('Aufgaben')")
        aufgaben_link.wait_for(state="visible", timeout=3000)
        aufgaben_link.click()
        page.wait_for_url("**/workitems/")
        assert page.locator("h1").inner_text() == "Aufgaben"

    def test_mobile_nav_create_opens_dropdown(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-create']").click()
        dropdown = page.locator("[data-testid='mobile-create-dropdown']")
        dropdown.wait_for(state="visible", timeout=3000)
        assert dropdown.is_visible(), "Create-Dropdown sollte sichtbar sein"
        assert dropdown.locator("a:has-text('Kontakt')").is_visible()

    def test_mobile_nav_search_opens_overlay(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("[data-testid='mobile-nav-search']").click()
        overlay = page.locator("[data-testid='mobile-search-overlay']")
        overlay.wait_for(state="visible", timeout=3000)
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

        # Datums-Navigation sollte sichtbar und nutzbar sein
        date_nav = page.locator(".flex.items-center.justify-center.space-x-4")
        assert date_nav.is_visible()

    def test_mobile_zeitstrom_filter_selects_visible(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        filter_type = page.locator("#filter-type")
        assert filter_type.is_visible(), "Type-Filter sollte auf Mobile sichtbar sein"


class TestMobileClientList:
    """Klientel-Liste auf Mobile."""

    def test_mobile_client_list_loads(self, mobile_page, base_url):
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1").inner_text() == "Klientel"

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
        """Klientelliste: 'Neues Klientel'-Button auf Mobile unsichtbar."""
        page = mobile_page
        page.goto(f"{base_url}/clients/")
        page.wait_for_load_state("domcontentloaded")

        # Der Wrapper-Div (hidden md:block) im Main-Bereich
        main = page.locator("main")
        create_wrapper = main.locator(".hidden.md\\:block").first
        assert not create_wrapper.is_visible(), "Neues-Klientel-Button sollte auf Mobile versteckt sein"

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

        # Desktop-Buttons sollten versteckt sein
        desktop_wrapper = page.locator("main .hidden.md\\:flex.md\\:space-x-2").first
        assert not desktop_wrapper.is_visible(), "Desktop-Buttons sollten auf Mobile versteckt sein"

        # Overflow-Menü-Button sichtbar
        overflow_btn = page.locator("[data-testid='mobile-overflow-menu']")
        assert overflow_btn.is_visible(), "Overflow-Menü-Button sollte sichtbar sein"

    def test_client_detail_overflow_menu_has_actions(self, mobile_page, base_url):
        """Klienteldetail: Overflow-Menü enthält Neue Aufgabe, Neuer Kontakt."""
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

        # Overflow-Menü öffnen
        page.locator("[data-testid='mobile-overflow-menu']").click()

        # Aktionen im Menü prüfen — auf Menu-Panel warten.
        menu = page.locator("[data-testid='mobile-overflow-menu'] + div")
        menu.locator("a:has-text('Neue Aufgabe')").wait_for(state="visible", timeout=3000)
        assert menu.locator("a:has-text('Neue Aufgabe')").is_visible()
        assert menu.locator("a:has-text('Neuer Kontakt')").is_visible()


class TestMobileNoHorizontalScroll:
    """Kein horizontaler Overflow auf den wichtigsten Seiten."""

    @pytest.mark.parametrize(
        "path,title",
        [
            ("/", "Zeitstrom"),
            ("/clients/", "Klientel"),
            ("/workitems/", "Aufgaben"),
            ("/events/new/", "Neuer Kontakt"),
            ("/uebergabe/", "Übergabe"),
        ],
    )
    def test_mobile_no_horizontal_scroll(self, mobile_page, base_url, path, title):
        page = mobile_page
        page.goto(f"{base_url}{path}")
        page.wait_for_load_state("domcontentloaded")

        assert title in page.locator("h1").inner_text()
        _assert_no_horizontal_overflow(page)
