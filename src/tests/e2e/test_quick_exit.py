"""E2E: Mitschauen-Schutz — schnell verlassen, Kuerzel, Sichtblende, offline.

Refs https://github.com/anlaufstelle/app/issues/2136 (Befund N-2).

Was hier — und nur hier — belegt wird:

* Der Knopf beendet die Sitzung **serverseitig**. Das ist das eigentliche
  Akzeptanzkriterium und im Unit-/JS-Test grundsaetzlich nicht pruefbar: Der
  Logout laeuft als ``sendBeacon``-POST, auf dessen Antwort niemand wartet,
  waehrend die Seite schon ersetzt wird. Ob dieser Beacon wirklich ankommt,
  entscheidet der echte Browser.
* Die Kuerzel wirken aus einem **fokussierten Eingabefeld** heraus.
* Die Sichtblende nimmt den Fokus und gibt ihn beim Freigeben an das
  Ausgangselement zurueck (WCAG 2.4.3) — Fokusverhalten ist in jsdom nur
  simuliert.
* Offline: Die precachte Shell traegt den Knopf, und er navigiert dort
  tatsaechlich weg.

**Harness-Grenzen, bewusst:**

1. Die Zieladresse wird per ``page.route`` lokal beantwortet — der Lauf darf
   das Netz nicht verlassen.
2. Das *Verdecken* des Inhalts beim Verlassen ist hier nicht direkt
   beobachtbar: Zwischen "Blende an" und ``location.replace`` liegt kein
   Zeitfenster, in dem der Testthread den DOM lesen koennte, und ein
   kuenstlich verzoegerter Route-Handler blockiert im Sync-API genau diesen
   Thread. Belegt wird das Verdecken deshalb in
   ``tests/js/quick-exit.test.mjs`` (Blende sichtbar, ``<main>``
   ``visibility:hidden``) — hier stattdessen das nach aussen sichtbare
   Ergebnis: die App-Seite ist weg. Der Sichtschutz-Pfad, der NICHT navigiert,
   wird unten vollstaendig im Browser geprueft.
3. Die Tests, die tatsaechlich ausloggen, melden sich per Formular an
   (``do_real_login``) statt den gecachten Storage-State zu nutzen: Ein Logout
   wuerde die geteilte Admin-Sitzung der ``authenticated_page``-Fixture
   entwerten und die uebrigen Dateien des seriellen Laufs mitreissen.

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container).
"""

from __future__ import annotations

import re
import subprocess
import time

import pytest

from ._helpers import do_real_login as _do_real_login
from ._helpers import python_bin as _python
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker

pytestmark = pytest.mark.e2e

EXIT_TARGET = "https://www.wikipedia.org/"
EXIT_TARGET_GLOB = "https://www.wikipedia.org/**"
STUB_MARKER = "E2E-NEUTRALE-ZIELSEITE"
STUB_HTML = f"<!doctype html><html lang='de'><head><title>Neutral</title><body><h1>{STUB_MARKER}</h1>"


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _set_facility_settings(e2e_env, *, quick_exit_url, privacy_screen):
    """Einstellungen der Einrichtung „Hauptstelle" setzen.

    Ueber den Namen und nicht ueber ``.first()``: Der Primaerschluessel ist eine
    UUID, die Reihenfolge der Seed-Einrichtungen also nicht bestimmt
    (Lehre aus #2166).
    """
    _shell(
        e2e_env,
        "from core.models import Facility, Settings;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " s, _ = Settings.objects.get_or_create(facility=f);"
        f" s.quick_exit_url = '{quick_exit_url}';"
        f" s.privacy_screen_on_blur = {bool(privacy_screen)};"
        " s.save(update_fields=['quick_exit_url', 'privacy_screen_on_blur']);"
        " print(s.quick_exit_url, s.privacy_screen_on_blur)",
    )


@pytest.fixture
def default_quick_exit(e2e_env):
    """Vorgabeziel, Sichtschutz aus — und derselbe Zustand danach wieder."""
    _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=False)
    yield
    _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=False)


@pytest.fixture
def privacy_screen_on(e2e_env):
    """Sichtschutz an — und danach wieder aus, damit die uebrigen Dateien des
    seriellen Laufs ihn nicht ungewollt erben."""
    _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=True)
    yield
    _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=False)


def _stub_exit_target(page):
    """Die Zieladresse lokal beantworten — der Lauf geht nicht ins Netz."""
    page.route(
        EXIT_TARGET_GLOB,
        lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=STUB_HTML),
    )


def _assert_session_ended(page, base_url, deadline_s=15.0):
    """Ein Folge-Request auf eine App-Seite landet auf ``/login/``.

    Der Logout-Beacon fliegt ohne Antwort-Wartezeit los; er kann beim ersten
    Folge-Request theoretisch noch unterwegs sein. Deshalb mit Deadline
    pollen statt einmal zu schauen — und am Ende hart zusichern.
    """
    ende = time.monotonic() + deadline_s
    letzte_url = ""
    while time.monotonic() < ende:
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        letzte_url = page.url
        if "/login/" in letzte_url:
            return
        time.sleep(0.3)
    raise AssertionError(
        f"Die Sitzung lebt nach dem Schnell-Verlassen weiter — /clients/ antwortete mit {letzte_url!r} "
        "statt einer Weiterleitung auf /login/."
    )


class TestSchnellVerlassen:
    def test_knopf_navigiert_zur_zielseite_und_beendet_die_sitzung(
        self, browser, base_url, e2e_env, default_quick_exit
    ):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            _stub_exit_target(page)

            with page.expect_navigation(url=re.compile(r"wikipedia\.org"), timeout=15000):
                page.locator("[data-testid='quick-exit-button']").click()

            assert STUB_MARKER in page.content(), "Die App-Seite wurde nicht durch die Zielseite ersetzt."
            _assert_session_ended(page, base_url)
        finally:
            context.close()

    def test_kuerzel_wirkt_aus_einem_eingabefeld_heraus(self, browser, base_url, e2e_env, default_quick_exit):
        """Strg+Umschalt+X mit dem Fokus IM Suchfeld.

        Genau das ist der Feldfall: Wer gerade tippt, wenn jemand hereinkommt,
        soll die Hand nicht erst zur Maus nehmen muessen.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            _stub_exit_target(page)
            page.locator("[data-testid='global-search-input']").focus()

            with page.expect_navigation(url=re.compile(r"wikipedia\.org"), timeout=15000):
                page.keyboard.press("Control+Shift+X")

            assert STUB_MARKER in page.content()
            _assert_session_ended(page, base_url)
        finally:
            context.close()

    def test_alternatives_kuerzel_umschalt_esc(self, browser, base_url, e2e_env, default_quick_exit):
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            _stub_exit_target(page)

            with page.expect_navigation(url=re.compile(r"wikipedia\.org"), timeout=15000):
                page.keyboard.press("Shift+Escape")

            assert STUB_MARKER in page.content()
        finally:
            context.close()


class TestTastaturpfad:
    """WCAG 2.1.1: Der Knopf ist das erste Tab-Ziel nach dem Skip-Link."""

    def test_zweites_tab_landet_auf_dem_knopf(self, authenticated_page, base_url, default_quick_exit):
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.locator("[data-testid='quick-exit-button']").wait_for(state="visible", timeout=15000)

        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement && document.activeElement.getAttribute('href')") == (
            "#main-content"
        ), "Erstes Tab-Ziel ist nicht mehr der Skip-Link."

        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "quick-exit-button", (
            "Zweites Tab-Ziel ist nicht der Knopf „Schnell verlassen“: "
            + str(page.evaluate("() => document.activeElement && document.activeElement.outerHTML"))
        )


class TestSichtschutz:
    """Der Pfad ohne Navigation — hier ist der DOM vollstaendig beobachtbar."""

    def test_blur_verdeckt_und_gibt_den_fokus_zurueck(self, authenticated_page, base_url, privacy_screen_on):
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        page.locator("#main-content h1:has-text('Personen')").wait_for(state="visible", timeout=15000)

        # Ausgangsfokus auf den Skip-Link: Er liegt ausserhalb von <main> und
        # Navigation, wird von der Blende also nicht stillgelegt — genau
        # deshalb ist er ein stabiler Beleg fuer die Rueckgabe.
        page.locator("a[href='#main-content']").focus()

        page.evaluate("() => window.dispatchEvent(new Event('blur'))")

        page.locator("[data-testid='privacy-screen']").wait_for(state="visible", timeout=10000)
        assert not page.locator("#main-content").is_visible(), "Der Inhalt liegt sichtbar unter der Blende."
        assert page.evaluate("() => document.activeElement && document.activeElement.id") == "privacy-screen-resume"

        page.locator("[data-testid='privacy-screen-resume']").click()

        page.locator("[data-testid='privacy-screen']").wait_for(state="hidden", timeout=10000)
        assert page.locator("#main-content").is_visible(), "Der Inhalt bleibt nach der Freigabe verdeckt."
        assert page.evaluate("() => document.activeElement && document.activeElement.getAttribute('href')") == (
            "#main-content"
        ), "Der Fokus kehrt nicht an sein Ausgangselement zurueck (WCAG 2.4.3)."

    def test_bleibt_aus_solange_die_einrichtung_ihn_nicht_aktiviert(
        self, authenticated_page, base_url, default_quick_exit
    ):
        page = authenticated_page
        page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
        page.locator("#main-content h1:has-text('Personen')").wait_for(state="visible", timeout=15000)

        page.evaluate("() => window.dispatchEvent(new Event('blur'))")

        assert not page.locator("[data-testid='privacy-screen']").is_visible()


class TestOfflineSmoke:
    """Die precachte Shell traegt den Schutz — dort wird er am meisten gebraucht.

    Das Ziel ist fuer diesen Test bewusst **same-origin** (``/offline/``, selbst
    im Precache): Eine externe Adresse ist ohne Netz nicht erreichbar, und ein
    ``page.route``-Stub wuerde nur die Harness testen, nicht den Offline-Pfad.
    Belegt wird damit genau die Zusage aus dem Ticket — der Knopf ist offline
    da und fuehrt aus der Anwendung heraus.
    """

    def test_shell_traegt_den_knopf_und_verlaesst_die_anwendung(self, browser, base_url, e2e_env):
        offline_ziel = f"{base_url}/offline/"
        _set_facility_settings(e2e_env, quick_exit_url=offline_ziel, privacy_screen=False)
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            # Der Spiegel entsteht beim Laden einer Online-Seite; die Shell
            # liest ihn spaeter aus dem localStorage.
            page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)
            assert page.evaluate("() => JSON.parse(localStorage.getItem('anlaufstelle.quickExit')).url") == (
                offline_ziel
            ), "Die Online-Seite hat die Konfiguration nicht gespiegelt."

            context.set_offline(True)
            page.evaluate("() => window.dispatchEvent(new Event('offline'))")
            page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")

            knopf = page.locator("[data-testid='quick-exit-button']")
            knopf.wait_for(state="visible", timeout=15000)

            with page.expect_navigation(url=re.compile(r"/offline/$"), timeout=15000):
                knopf.click()

            assert page.url.endswith("/offline/"), (
                f"Der Knopf hat die Anwendung offline nicht verlassen (URL: {page.url!r})."
            )
        finally:
            context.set_offline(False)
            context.close()
            _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=False)
