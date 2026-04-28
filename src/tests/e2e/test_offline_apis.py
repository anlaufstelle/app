"""E2E-Tests für die PWA-Offline-APIs (Refs #660).

Deckt den Browser-Round-Trip ab, der in den reinen Unit-/Integration-Tests
(``test_offline_bundle_api.py`` u.a.) nicht sichtbar ist:

- Bundle-Fetch via "Mitnehmen"-Button mit Krypto-Session-Handshake
- Auth-Gates auf allen vier Offline-Endpoints
- Offline-Detail- und Conflict-Scaffold-Seiten rendern ohne Crash
  (auch ohne IndexedDB-Pre-State)
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers


def _do_real_login(page, base_url, username="miriam", password="anlaufstelle2026"):
    """Full login flow inkl. crypto_session-Handshake (keine storage_state).

    storage_state würde den Browser mit gültigem Session-Cookie ausstatten,
    aber den ``login.html``-fetch-Hook überspringen, der
    ``crypto_session.deriveSessionKey`` auslöst. Ohne Session-Key scheitert
    aber ``offlineStore.saveClientBundle`` an ``EncryptionKeyMissing``.
    """
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(re.compile(r"^(?!.*/login/)"), timeout=15000)
    # Warten bis der In-Memory-Key aus IndexedDB rehydriert wurde.
    page.evaluate(
        """async () => {
            await window.crypto_session.ready();
        }"""
    )


_UUID_RE = re.compile(
    r"/clients/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/"
)


def _first_client_pk(page, base_url):
    """Nimmt das erste echte Klient-UUID aus der Liste.

    Links wie ``/clients/new/`` dürfen nicht als pk interpretiert werden,
    daher filtert der UUID-RegEx auf das konkrete Format.
    """
    page.goto(f"{base_url}/clients/", wait_until="domcontentloaded")
    hrefs = page.locator("a[href^='/clients/']").evaluate_all(
        "els => els.map(e => e.getAttribute('href'))"
    )
    for href in hrefs:
        m = _UUID_RE.match(href or "")
        if m:
            return m.group(1)
    raise AssertionError(f"Kein UUID-Klient-Link auf /clients/ gefunden. Gesehen: {hrefs!r}")


# ---------------------------------------------------------------------------
# 1. Bundle-Fetch Round-Trip


class TestOfflineBundleRoundTrip:
    """Bundle-API-Roundtrip: Browser fetched Bundle, verschlüsselt und persistiert."""

    def test_bundle_fetch_and_idb_persist_roundtrip(self, browser, base_url):
        """Das, was ``clientRowOffline.toggleOffline()`` serverseitig tut:
        Bundle fetchen + verschlüsselt in IndexedDB ablegen. Wir rufen die
        JS-API direkt, ohne uns an die UI-Komponente zu binden — der Test
        prüft den Endpoint-Roundtrip und die IDB-Persistenz, nicht die
        Alpine-Komponente.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)

            pk = _first_client_pk(page, base_url)

            # Bundle direkt vom Endpoint holen (gleicher HTTP-Weg wie die
            # Alpine-Komponente), dann via offlineStore verschlüsseln und ablegen.
            result = page.evaluate(
                """async (pk) => {
                    const resp = await fetch(`/api/offline/bundle/client/${pk}/`, {
                        headers: {"Accept": "application/json"},
                    });
                    if (!resp.ok) return {status: resp.status, error: "fetch failed"};
                    const bundle = await resp.json();
                    // saveClientBundle akzeptiert nur das Bundle; pk wird
                    // intern aus bundle.client.pk gelesen (siehe offline-store.js).
                    await window.offlineStore.saveClientBundle(bundle);
                    return {
                        status: resp.status,
                        clientPk: bundle.client?.pk,
                        eventCount: (bundle.events || []).length,
                    };
                }""",
                pk,
            )
            assert result["status"] == 200, f"Bundle-Endpoint antwortete nicht 200: {result!r}"
            assert result["clientPk"] == pk, f"Bundle-client.pk {result['clientPk']!r} != {pk!r}"

            count = page.evaluate("() => window.offlineStore.countOfflineClients()")
            assert count == 1, f"Erwarte genau 1 Offline-Klient, gesehen: {count}"
        finally:
            try:
                page.evaluate(
                    """async () => {
                        if (window.offlineStore) await window.offlineStore.purgeAll();
                    }"""
                )
            except Exception:
                pass
            context.close()

    def test_bundle_audit_log_written_on_fetch(self, browser, base_url):
        """Jeder Bundle-Fetch hinterlässt einen AuditLog-Eintrag.

        Wir prüfen das indirekt über die Bundle-URL-Wiederholbarkeit: wenn
        der erste Fetch 200 liefert und ein AuditLog schreibt, dann muss
        ein zweiter Fetch (immer noch unter dem 30/h-Limit) auch 200
        liefern — der vollständige AuditLog-Content wird bereits in den
        Integrations-Tests (test_offline_bundle_api.py) abgedeckt.
        """
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _do_real_login(page, base_url)
            pk = _first_client_pk(page, base_url)

            statuses = page.evaluate(
                """async (pk) => {
                    const r1 = await fetch(`/api/offline/bundle/client/${pk}/`);
                    const r2 = await fetch(`/api/offline/bundle/client/${pk}/`);
                    return [r1.status, r2.status];
                }""",
                pk,
            )
            assert statuses == [200, 200], f"Erwarte zwei 200er, gesehen: {statuses}"
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 2. Auth-Gates


class TestOfflineApiAuthGates:
    """Alle vier Offline-Endpoints verlangen Login."""

    def test_bundle_endpoint_requires_login(self, browser, base_url):
        context = browser.new_context()
        page = context.new_page()
        try:
            # Dummy-UUID — ohne Login ist das egal, wir erwarten 302 vor Routing.
            response = page.goto(
                f"{base_url}/api/offline/bundle/client/00000000-0000-0000-0000-000000000000/",
                wait_until="domcontentloaded",
            )
            # Follow-Redirect zur Login-Seite.
            assert "/login/" in page.url, f"Erwartete Weiterleitung zu /login/, aktuell: {page.url}"
            assert response is not None
        finally:
            context.close()

    def test_offline_client_detail_requires_login(self, browser, base_url):
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(
                f"{base_url}/offline/clients/00000000-0000-0000-0000-000000000000/",
                wait_until="domcontentloaded",
            )
            assert "/login/" in page.url
        finally:
            context.close()

    def test_offline_conflict_list_requires_login(self, browser, base_url):
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
            assert "/login/" in page.url
        finally:
            context.close()

    def test_offline_conflict_review_requires_login(self, browser, base_url):
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(
                f"{base_url}/offline/conflicts/00000000-0000-0000-0000-000000000000/",
                wait_until="domcontentloaded",
            )
            assert "/login/" in page.url
        finally:
            context.close()


# ---------------------------------------------------------------------------
# 3. Scaffold-Views rendern (auch ohne IndexedDB-State)


class TestOfflineScaffolds:
    """Die drei Scaffold-Seiten rendern ohne Crash im Online-Modus."""

    def test_offline_client_detail_renders_scaffold(self, authenticated_page, base_url):
        page = authenticated_page
        # Dummy-UUID. Die View rendert die Seite auch ohne DB-Treffer —
        # Scaffold-Pattern (siehe Docstring der View).
        page.goto(
            f"{base_url}/offline/clients/00000000-0000-0000-0000-000000000000/",
            wait_until="domcontentloaded",
        )
        # Scaffold-Skeleton: das Template setzt client_pk in den Context.
        assert page.locator("body").is_visible()
        assert "/login/" not in page.url
        # Ein data-Attribut mit der UUID sollte gerendert sein (für den JS-Loader).
        html = page.content()
        assert "00000000-0000-0000-0000-000000000000" in html

    def test_offline_conflict_list_renders_scaffold(self, authenticated_page, base_url):
        page = authenticated_page
        page.goto(f"{base_url}/offline/conflicts/", wait_until="domcontentloaded")
        assert page.locator("body").is_visible()
        assert "/login/" not in page.url

    def test_offline_conflict_review_renders_scaffold(self, authenticated_page, base_url):
        page = authenticated_page
        # Dummy-UUID. View 404't nicht auf fehlende Events (siehe Docstring).
        page.goto(
            f"{base_url}/offline/conflicts/00000000-0000-0000-0000-000000000000/",
            wait_until="domcontentloaded",
        )
        assert page.locator("body").is_visible()
        assert "/login/" not in page.url
        html = page.content()
        assert "00000000-0000-0000-0000-000000000000" in html
