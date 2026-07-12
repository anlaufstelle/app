"""E2E: Offline-V2- — kalt-offline Erfassung ueber die "+"-Create-Shells.

Refs #1524 (#1499, SI-7). ersetzt die alte Sackgasse (Kalt-Navigation
auf /events/new/ bzw. /workitems/new/ landete auf dem Offline-Arbeitsplatz mit
einem "geht nur im Dossier"-Wegweiser, #1483/#1485) durch **echte pk-lose
Create-Shells**, die der Service Worker offline IN-PLACE an den kanonischen URLs
serviert (SI-6). Diese Journeys fahren den kompletten Feldpfad nach:

* Kalt-offline ueber das mobile "+"-Menue (``mobile-nav-create`` →
  ``mobile-create-event`` aus ``_navigation_mobile.html``) zur Event-Create-
  Shell → (a) anonymer Kontakt ohne Person, (b) Kontakt an eine zuvor "offline
  mitgenommene" Person; Reconnect + Startup-Drain (KEIN ``online``-Event) →
  Server-Verifikation (``is_anonymous``/``client IS NULL`` bzw. Event am Klienten).
* Kalt-offline Standalone-Aufgabe ueber die WorkItem-Create-Shell
  (``mobile-create-workitem``) → Server-Verifikation ``client_id IS NULL``.
* Assistenz-Gate (Risiko #7 der): Assistenz erreicht die
  WorkItem-Shell offline zwar per Direkt-URL (der SW serviert sie rollenlos),
  bekommt aber statt der Form das Staff+-Gate — ihr assignable_users-loses
  Facility-Bundle blendet die Erfassung aus, damit kein Assistant-WorkItem
  gequeuet wird, das beim Replay gegen den StaffRequired-Create-View zu 403
  ("revoked") stranden wuerde.

Harness-Grenze (bewusst, wie die uebrige Offline-Suite): Playwrights
``set_offline`` gilt NICHT fuer die fetch()-Aufrufe des Service Workers selbst
(SWR fuellt den Cache trotz Context-Offline). Diese Journeys beweisen daher das
reale Offline-*Verhalten* der Shells, NICHT die Precache-Vollstaendigkeit — die
leistet deterministisch der Cache-Inhalts-Guard
``test_sw_robustness.py::test_precache_includes_offline_sync_core_assets``
(inspiziert den echten Install-Precache) plus ``test_pwa_views.py``
(APP_SHELL-Quelle + CACHE_NAME-Pin).

Muster (Login/Bundle/Offline-Helfer, manage.py-Shell-Verifikation) bewusst
dateilokal aus ``test_offline_android_journeys.py`` kopiert — etablierte
Konvention dieser Suite (keine geteilten Fixtures ueber Dateien hinweg).

WICHTIG: E2E seriell ausfuehren (RAM-Limit der Container) — nicht
parallelisieren.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import suppress

import pytest

pytestmark = pytest.mark.e2e

ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
)


# ---------------------------------------------------------------------------
# Server-seitiges Seed/Inspektion ueber manage.py shell (Worker-DB via e2e_env)


def _python():
    return ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable


def _shell(e2e_env, script):
    out = subprocess.run(
        [_python(), "src/manage.py", "shell", "--no-imports", "-c", script],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return [ln for ln in out.stdout.strip().splitlines() if ln.strip()]


def _seed_client(e2e_env):
    """Frischen Klienten in Hauptstelle anlegen (von miriam, Staff)."""
    suffix = uuid.uuid4().hex[:8]
    script = (
        "from core.models import Client, Facility;"
        " from core.models.user import User;"
        " f = Facility.objects.get(name='Hauptstelle');"
        " u = User.objects.get(username='miriam');"
        " c = Client.objects.create(facility=f, contact_stage='identified',"
        f"  pseudonym='E2E-SHELL-{suffix}', created_by=u);"
        " print(c.pk)"
    )
    return _shell(e2e_env, script)[-1]


def _server_anon_event_count(e2e_env):
    """Anzahl anonymer Events ohne Personenbezug (Delta-Vergleich im Test)."""
    return int(
        _shell(
            e2e_env,
            "from core.models import Event;"
            " print(Event.objects.filter(is_anonymous=True, client__isnull=True).count())",
        )[-1]
    )


def _server_event_count_for_client(e2e_env, client_pk):
    """Anzahl (nicht geloeschter) Events, die serverseitig am Klienten haengen."""
    return int(
        _shell(
            e2e_env,
            "from core.models import Event;"
            f" print(Event.objects.filter(client_id='{client_pk}', is_deleted=False).count())",
        )[-1]
    )


def _server_workitem_state(e2e_env, title):
    """'STANDALONE' | 'HASCLIENT' | 'MISSING' fuer die Aufgabe mit dem Titel."""
    return _shell(
        e2e_env,
        "from core.models import WorkItem;"
        f" w = WorkItem.objects.filter(title='{title}', is_deleted=False).first();"
        " print('MISSING' if w is None else ('STANDALONE' if w.client_id is None else 'HASCLIENT'))",
    )[-1]


def _wait_until(fn, timeout_s=25.0, interval_s=0.5, desc=""):
    """Python-seitiges Polling (kein wait_for_function mit async-Praedikat —
    das liefert False-Positives, siehe Suite-Konvention)."""
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval_s)
    raise AssertionError(f"Timeout ({timeout_s}s): {desc} — zuletzt: {last!r}")


# ---------------------------------------------------------------------------
# Browser-Helfer (dateilokal aus test_offline_android_journeys.py kopiert)


def _android_context(browser, **extra):
    """Android-Chrome-Emulation: Pixel-7-artiger Viewport, Touch, Mobile-UA.

    Das mobile "+"-Menue (``_navigation_mobile.html``) ist nur unterhalb der
    ``md``-Breakpoint sichtbar — die Journeys brauchen den Mobile-Viewport.
    Bewusst KEIN storage_state-Restore: crypto_session leitet den Session-Key
    nur beim echten Login-POST ab.
    """
    return browser.new_context(
        locale="de-DE",
        viewport={"width": 412, "height": 915},
        device_scale_factor=2.625,
        is_mobile=True,
        has_touch=True,
        user_agent=ANDROID_UA,
        **extra,
    )


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    page.evaluate("async () => { await window.crypto_session.ready(); }")


def _cache_facility_bundle(page):
    """Personenloses Facility-Meta-Bundle (SI-1) online holen und verschluesselt
    in IndexedDB ablegen (Analog ``_cache_bundle`` fuer Klient-Bundles). Speist
    getOfflineFacility() der Create-Shells: DocumentType-Katalog + (nur Staff+)
    assignable_users. Ohne dieses Bundle zeigen die Shells den Edge-Fallback."""
    return page.evaluate(
        """async () => {
            const resp = await fetch('/api/v1/offline/bundle/facility/', {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveFacilityBundle(bundle);
            return {
                ok: true,
                docTypes: (bundle.document_types || []).length,
                assignable: (bundle.assignable_users || []).length,
            };
        }"""
    )


def _cache_client_bundle(page, client_pk):
    """Klient-Bundle online holen + speichern — macht die Person "offline
    mitgenommen" (erscheint im Person-Picker via listOfflineClientsDetailed)."""
    return page.evaluate(
        """async (pk) => {
            const resp = await fetch(`/api/v1/offline/bundle/client/${pk}/`, {
                headers: {Accept: 'application/json'},
            });
            if (!resp.ok) return {ok: false, status: resp.status};
            const bundle = await resp.json();
            await window.offlineStore.saveClientBundle(bundle);
            return {ok: true};
        }""",
        client_pk,
    )


def _wait_for_active_service_worker(page):
    """SW registriert + aktiviert + kontrolliert die Seite (Muster
    test_pwa_offline.py)."""
    page.evaluate(
        """
        async () => {
            const reg = await navigator.serviceWorker.getRegistration('/');
            if (!reg) return;
            const sw = reg.active || reg.installing || reg.waiting;
            if (!sw || sw.state === 'activated') return;
            return new Promise((resolve) => {
                sw.addEventListener('statechange', () => {
                    if (sw.state === 'activated') resolve();
                });
                setTimeout(resolve, 5000);
            });
        }
        """
    )
    if not page.evaluate("() => !!navigator.serviceWorker.controller"):
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=5000)


def _go_offline(page):
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")


def _open_plus_menu_and_navigate(page, target_testid):
    """Das mobile "+"-Menue oeffnen und den gewuenschten Create-Eintrag klicken.

    Der Klick navigiert an die kanonische Create-URL (/events/new/ bzw.
    /workitems/new/); offline serviert der SW dort die pk-lose Create-Shell
    IN-PLACE. Scoped data-testids (Selector-Guard-konform: kein .first/.nth)."""
    page.locator("[data-testid='mobile-nav-create']").click()
    page.locator("[data-testid='mobile-create-dropdown']").wait_for(state="visible", timeout=5000)
    page.locator(f"[data-testid='{target_testid}']").click()


def _purge_offline(page):
    with suppress(Exception):
        page.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")


# ---------------------------------------------------------------------------


class TestColdOfflinePlusMenuCreate:
    """Refs #1524/#1499 (SI-7): kalt-offline Erfassung ueber die "+"-Shells,
    inkl. serverseitiger Verifikation nach dem Startup-Drain."""

    def test_plus_menu_anonymous_event_syncs_via_startup_drain(self, browser, base_url, e2e_env):
        """Anonymer Kontakt (ohne Person) offline ueber die Event-Create-Shell
        erfasst → Reconnect + Seitenstart MIT Netz (KEIN ``online``-Event)
        drain die Queue → serverseitig ``is_anonymous=True`` + ``client IS
        NULL``. Deckt zugleich den weichen Kontaktstufen-Vorfilter ab: fuer den
        stufenlosen Typ "Kontakt" ist "ohne Person" erlaubt (kein Stage-Hint)."""
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_facility_bundle(page)
            assert cached["ok"], f"Facility-Bundle-Cache fehlgeschlagen: {cached!r}"
            assert cached["docTypes"] > 0, "Facility-Bundle traegt keine DocumentTypes"
            count_before = _server_anon_event_count(e2e_env)

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            # Kalt-offline: /events/new/ war in dieser Session nie online offen —
            # der SW muss die Shell aus dem Precache servieren.
            _open_plus_menu_and_navigate(page, "mobile-create-event")
            page.locator("[data-testid='offline-event-create']").wait_for(state="attached", timeout=15000)
            page.locator("[data-testid='offline-event-create-form']").wait_for(state="visible", timeout=15000)

            page.locator("[data-testid='offline-event-create-doctype']").select_option(label="Kontakt")
            # Kein min_contact_stage bei "Kontakt" → "ohne Person" bleibt waehlbar.
            assert not page.locator("[data-testid='offline-event-create-stage-hint']").is_visible(), (
                "Stage-Hint darf fuer den stufenlosen Typ 'Kontakt' nicht erscheinen"
            )
            page.locator("[data-testid='offline-event-create-save']").click()
            page.locator("[data-testid='offline-event-create-saved']").wait_for(state="visible", timeout=10000)
            assert _server_anon_event_count(e2e_env) == count_before, (
                "Der anonyme Kontakt darf VOR dem Reconnect nicht serverseitig existieren"
            )

            # Reconnect: bewusst KEIN dispatch von 'online' — der naechste
            # Seitenstart MIT Netz muss selbst drainen (Startup-Drain, #1484).
            page.context.set_offline(False)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_until(
                lambda: _server_anon_event_count(e2e_env) == count_before + 1,
                desc="anonymer Kontakt aus der Shell nach Startup-Drain serverseitig angelegt",
            )
        finally:
            _purge_offline(page)
            context.close()

    def test_plus_menu_event_for_taken_offline_person(self, browser, base_url, e2e_env):
        """Kontakt an eine zuvor "offline mitgenommene" Person: Person im Picker
        waehlen → Shell speichert → Reconnect/Drain → serverseitig ein Event am
        Klienten (``client_id`` gesetzt, nicht anonym)."""
        client_pk = _seed_client(e2e_env)
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            assert _cache_facility_bundle(page)["ok"], "Facility-Bundle-Cache fehlgeschlagen"
            assert _cache_client_bundle(page, client_pk)["ok"], "Klient-Bundle-Cache fehlgeschlagen"
            count_before = _server_event_count_for_client(e2e_env, client_pk)

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            _open_plus_menu_and_navigate(page, "mobile-create-event")
            page.locator("[data-testid='offline-event-create-form']").wait_for(state="visible", timeout=15000)

            page.locator("[data-testid='offline-event-create-doctype']").select_option(label="Kontakt")
            # Die mitgenommene Person waehlen (genau ein Bundle gecacht → value=pk).
            page.locator("[data-testid='offline-event-create-client']").select_option(value=client_pk)
            page.locator("[data-testid='offline-event-create-save']").click()
            page.locator("[data-testid='offline-event-create-saved']").wait_for(state="visible", timeout=10000)

            page.context.set_offline(False)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_until(
                lambda: _server_event_count_for_client(e2e_env, client_pk) == count_before + 1,
                desc=f"Kontakt aus der Shell am Klienten {client_pk} serverseitig angelegt",
            )
        finally:
            _purge_offline(page)
            context.close()

    def test_plus_menu_standalone_workitem(self, browser, base_url, e2e_env):
        """Standalone-Aufgabe (ohne Person) offline ueber die WorkItem-Create-
        Shell → Reconnect/Drain → serverseitig angelegt mit ``client_id IS
        NULL``. miriam ist Staff → assignable_users befuellt → Form sichtbar."""
        title = f"Shell-Standalone-Aufgabe {uuid.uuid4().hex[:6]}"
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            _do_real_login(page, base_url)
            cached = _cache_facility_bundle(page)
            assert cached["ok"], "Facility-Bundle-Cache fehlgeschlagen"
            assert cached["assignable"] > 0, "Staff-Bundle muss assignable_users tragen (Form-Gate)"

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            _open_plus_menu_and_navigate(page, "mobile-create-workitem")
            page.locator("[data-testid='offline-workitem-create']").wait_for(state="attached", timeout=15000)
            page.locator("[data-testid='offline-workitem-create-form']").wait_for(state="visible", timeout=15000)

            page.locator("[data-testid='offline-wi-input-title']").fill(title)
            page.locator("[data-testid='offline-workitem-create-save']").click()
            page.locator("[data-testid='offline-workitem-create-saved']").wait_for(state="visible", timeout=10000)
            assert _server_workitem_state(e2e_env, title) == "MISSING", (
                "Die Aufgabe darf VOR dem Reconnect nicht serverseitig existieren"
            )

            page.context.set_offline(False)
            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_until(
                lambda: _server_workitem_state(e2e_env, title) == "STANDALONE",
                desc=f"Standalone-Aufgabe '{title}' aus der Shell serverseitig ohne Klientenbezug",
            )
        finally:
            _purge_offline(page)
            context.close()


class TestOfflineWorkItemShellAssistantGate:
    """Refs #1524/#1499 (Risiko #7): Assistenz erreicht die WorkItem-Shell
    offline (der SW serviert sie rollenlos), sieht aber statt der Form das
    Staff+-Gate — ihr Facility-Bundle traegt ein leeres assignable_users, das
    die Erfassung ausblendet (kein gequeuetes Assistant-WorkItem, das beim
    Replay gegen den StaffRequired-Create-View zu 403 straende)."""

    def test_assistant_sees_gate_not_form(self, browser, base_url):
        context = _android_context(browser)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            # lena = Assistenz (core/seed/constants.py).
            _do_real_login(page, base_url, username="lena")
            cached = _cache_facility_bundle(page)
            assert cached["ok"], "Facility-Bundle-Cache (Assistenz) fehlgeschlagen"
            assert cached["assignable"] == 0, (
                "Assistenz-Bundle darf keinen assignable_users-Roster tragen (Staff+-Marker)"
            )

            page.goto(f"{base_url}/", wait_until="domcontentloaded")
            _wait_for_active_service_worker(page)

            _go_offline(page)
            # Assistenz hat keinen "+"-Menue-Eintrag fuer Aufgaben — der SW
            # serviert die Shell aber an der kanonischen URL (rollenlos), das
            # Gate greift im Shell-UI.
            page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
            page.locator("[data-testid='offline-workitem-create']").wait_for(state="attached", timeout=15000)
            page.locator("[data-testid='offline-workitem-create-gate']").wait_for(state="visible", timeout=15000)
            assert not page.locator("[data-testid='offline-workitem-create-form']").is_visible(), (
                "Assistenz darf die WorkItem-Erfassungs-Form offline nicht sehen (Staff+-Gate)"
            )
        finally:
            _purge_offline(page)
            context.close()
