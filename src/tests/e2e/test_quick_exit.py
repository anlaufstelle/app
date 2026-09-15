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
from ._selectors import open_mobile_search_overlay

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


def _stub_exit_target_recording(page, kopfzeilen):
    """Wie ``_stub_exit_target``, haelt aber die Kopfzeilen der Zielanfrage fest.

    ``all_headers()`` liefert die tatsaechlich gesendeten Kopfzeilen in
    Kleinschreibung — inklusive derer, die der Browser selbst ergaenzt.
    ``referer`` ist genau so eine.
    """

    def handler(route):
        kopfzeilen.append(route.request.all_headers())
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=STUB_HTML)

    page.route(EXIT_TARGET_GLOB, handler)


# Rohzugriff auf die Krypto-IndexedDB (``crypto.js``: DB ``anlaufstelle-crypto``,
# Store ``meta``, Zeile ``sessionKey``). Bewusst OHNE ``window.crypto_session``:
# Die Pruefung laeuft nach dem Verlassen auf der Zielseite, und die muss das
# Krypto-Modul nicht geladen haben. ``indexedDB.open`` legt eine fehlende
# Datenbank leer an — dann existiert der Store nicht und die Antwort ist
# ``null``, was hier genau das gewuenschte Ergebnis ist.
_READ_PERSISTED_KEY = """
    async () => {
        return await new Promise((resolve) => {
            const req = indexedDB.open("anlaufstelle-crypto", 1);
            req.onerror = () => resolve("db-error");
            req.onsuccess = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains("meta")) {
                    db.close();
                    resolve(null);
                    return;
                }
                const tx = db.transaction("meta", "readonly");
                const get = tx.objectStore("meta").get("sessionKey");
                get.onsuccess = () => {
                    const row = get.result;
                    db.close();
                    resolve(row && row.cryptoKey ? "present" : null);
                };
                get.onerror = () => {
                    db.close();
                    resolve("get-error");
                };
            };
        });
    }
"""


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

    def test_sprung_verraet_dem_sprungziel_die_herkunft_nicht(self, browser, base_url, e2e_env, default_quick_exit):
        """Die Anfrage an das Sprungziel traegt keine ``Referer``-Kopfzeile.

        Refs https://github.com/anlaufstelle/app/issues/2179.

        **Warum das Dokument hier erst eine leckende Politik bekommt:** Der
        E2E-Server erbt von ``settings/dev.py`` und liefert damit Djangos
        Default ``Referrer-Policy: same-origin``. Darunter laesst der Browser
        den Cross-Origin-Referer ohnehin weg — die Zusage waere ohne Zutun
        gruen, auch OHNE den Fix, und der Fall belegte nichts. Produktion
        liefert dagegen ``strict-origin-when-cross-origin``
        (``settings/prod.py``), und http->https ist kein Downgrade: dort geht
        die Origin mit.

        Statt die Server-Kopfzeile umzubiegen (was jede App-Antwort durch
        einen Route-Handler zwaenge) setzt der Test die Politik im Dokument
        selbst — auf ``unsafe-url``, den schaerfsten Fall: Origin UND Pfad,
        also strikt mehr, als Produktion je sendet. Genau diese Politik muss
        der Sprung ueberschreiben.

        Der Fall ist eine **Positivkontrolle nur zusammen mit einem Rot-Lauf**:
        Dass er ohne die Unterdrueckung wirklich scheitert, zeigt erst ein Lauf
        gegen ein ``quick-exit.js`` ohne ``_suppressReferrer()`` (Anleitung im
        Lane-Bericht). Eine DOM-Assertion allein ersetzt diesen Nachweis nicht.
        """
        kopfzeilen = []
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            _stub_exit_target_recording(page, kopfzeilen)

            page.evaluate(
                """() => {
                    const meta = document.createElement("meta");
                    meta.setAttribute("name", "referrer");
                    meta.setAttribute("content", "unsafe-url");
                    document.head.appendChild(meta);
                }"""
            )
            # Vorcheck auf die WIRKSAME Politik, nicht nur auf DOM-Inhalt: nach
            # dem HTML-Verarbeitungsmodell gilt das ZULETZT eingefuegte
            # ``meta[name=referrer]``. Ein blosses „irgendwo steht unsafe-url"
            # waere auch dann erfuellt, wenn danach noch ein strengeres Meta
            # folgte — der Fall belegte dann nichts.
            assert page.evaluate(
                """() => Array.from(document.head.querySelectorAll('meta[name="referrer"]')).map(
                    (meta) => meta.getAttribute("content")
                )"""
            ) == ["unsafe-url"], (
                "Die leckende Ausgangspolitik ist nicht die wirksame: erwartet wird GENAU EIN "
                "meta[name=referrer], und zwar mit 'unsafe-url'."
            )

            with page.expect_navigation(url=re.compile(r"wikipedia\.org"), timeout=15000):
                page.locator("[data-testid='quick-exit-button']").click()

            assert STUB_MARKER in page.content(), "Die App-Seite wurde nicht durch die Zielseite ersetzt."
            assert kopfzeilen, "Die Anfrage an das Sprungziel wurde nicht abgefangen."
            referer = kopfzeilen[0].get("referer", "")
            assert not referer, (
                "Der Sprung hat dem Sprungziel die Herkunft verraten: "
                f"Referer={referer!r} — erwartet war gar keine Referer-Kopfzeile."
            )
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


class TestSchichtungMobil:
    """Die Leiste darf das mobile Such-Overlay nicht abfangen (Nachzug zu #2136).

    Die Leiste liegt ``sticky top-0`` im Wurzel-Stapelkontext. Das mobile
    Such-Overlay liegt ``fixed inset-x-0 top-0`` — aber INNERHALB der
    Bottom-Nav, und die spannt mit ``z-40`` einen eigenen Stapelkontext auf.
    Das ``z-[60]`` des Overlays wirkt deshalb nur nav-intern; nach aussen
    zaehlt die ``40`` der Nav. Beide belegen denselben Streifen am oberen
    Rand (Leiste 0–44 px, Suchfeld ca. 16–58 px), und mit der Leiste auf
    ``z-50`` lag sie darueber: jeder Tap ins Suchfeld landete auf ihr
    (Playwright: „<div id=quick-exit-bar …> subtree intercepts pointer
    events"). Der Streifen ist mobil nicht abwaehlbar — vor #2136 war der
    gemeinsame Wrapper leer und damit 0 px hoch, weshalb die Kollision erst
    mit der Leiste sichtbar wurde.

    Der zweite Fall haelt die Gegenrichtung fest: Ist das Overlay zu, gehoert
    der Streifen wieder der Leiste. Ein Fix per ``pointer-events-none`` haette
    den ersten Fall geheilt und den Knopf dabei stillgelegt.
    """

    def test_offenes_overlay_bekommt_den_tap_ins_suchfeld(
        self, mobile_authenticated_page, base_url, default_quick_exit
    ):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        page.locator("[data-testid='quick-exit-button']").wait_for(state="visible", timeout=15000)

        open_mobile_search_overlay(page)
        feld = page.locator("[data-testid='mobile-search-input']")

        # Der echte Tap: Playwrights Aktionierbarkeitspruefung wartet die
        # ``x-transition`` ab und benennt im Fehlerfall das abfangende
        # Element. Das kurze Zeitlimit haelt eine Regression bei ~10 s statt
        # bei den 30 s der Vorgabe.
        feld.click(timeout=10000)

        # Und die Schichtung explizit: am oberen Rand des Feldes, also genau
        # dort, wo die 44 px hohe Leiste liegt.
        box = feld.bounding_box()
        assert box is not None, "Das Suchfeld hat keine Box."
        treffer = page.evaluate(
            "([x, y]) => { const el = document.elementFromPoint(x, y); return el && "
            "{ id: el.id, testid: el.getAttribute('data-testid'), "
            "imOverlay: !!el.closest('#mobile-search-overlay') }; }",
            [box["x"] + box["width"] / 2, box["y"] + 2],
        )
        assert treffer and treffer["imOverlay"], (
            f"Ueber dem oberen Rand des Suchfeldes liegt ein fremdes Element: {treffer}"
        )

    def test_geschlossenes_overlay_laesst_den_knopf_erreichbar(
        self, mobile_authenticated_page, base_url, default_quick_exit
    ):
        page = mobile_authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        knopf = page.locator("[data-testid='quick-exit-button']")
        knopf.wait_for(state="visible", timeout=15000)

        box = knopf.bounding_box()
        assert box is not None, "Der Knopf hat keine Box."
        treffer = page.evaluate(
            "([x, y]) => { const el = document.elementFromPoint(x, y); return el && "
            "{ id: el.id, imKnopf: !!el.closest('#quick-exit-button') }; }",
            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
        )
        assert treffer and treffer["imKnopf"], f"Der Knopf nimmt den Tap nicht mehr an: {treffer}"


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

    Zusaetzlich (R-L3-9) der eigentliche Offline-Riegel: Ein Server-Logout gibt
    es hier nicht, der einzige wirksame Schritt ist das Loeschen des
    persistenten Sitzungsschluessels. Der wird NACH dem Dokumentwechsel
    gelesen — nur so ist belegt, dass die asynchrone IndexedDB-Loeschung die
    Navigation ueberlebt hat und nicht mit dem alten Kontext gestorben ist.
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

            # Vorbedingung: ohne einen tatsaechlich abgelegten Schluessel waere
            # die Loeschzusicherung unten leer gruen.
            page.evaluate("async () => { await window.crypto_session.ready(); }")
            assert page.evaluate("() => window.crypto_session.hasSessionKey()") is True, (
                "Der Login hat keinen Sitzungsschluessel abgelegt — die Loeschpruefung waere wertlos."
            )
            assert page.evaluate(_READ_PERSISTED_KEY) == "present", (
                "Der Sitzungsschluessel liegt nicht persistent in der IndexedDB."
            )

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

            # R-L3-9: Der persistente Schluessel muss nach dem Dokumentwechsel
            # weg sein. Bis zur Fix-Runde 1 startete ``clearSessionKey`` nur und
            # ``location.replace`` riss den Kontext mitten in der
            # IndexedDB-Transaktion weg — die naechste Offline-Shell haette den
            # Schluessel wieder geladen und die Bestaende erneut entschluesselt.
            assert page.evaluate(_READ_PERSISTED_KEY) is None, (
                "Der persistente Sitzungsschluessel liegt nach dem Verlassen weiterhin in der "
                "IndexedDB — offline ist er der einzige Riegel."
            )
        finally:
            context.set_offline(False)
            context.close()
            _set_facility_settings(e2e_env, quick_exit_url=EXIT_TARGET, privacy_screen=False)
