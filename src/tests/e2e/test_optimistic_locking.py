"""E2E: Optimistic-Locking auf WorkItem via Two-Browser-Context.

Refs Welle 4 (#927), Master #922.

Szenario:
1. Admin-Session A öffnet WorkItem-Edit (``expected_updated_at`` = v1).
2. Admin-Session B öffnet dasselbe WorkItem, ändert Titel und speichert
   erfolgreich (``updated_at`` rückt auf v2).
3. Session A submittet das Formular mit dem alten v1-Timestamp →
   ``ValidationError`` aus ``check_version_conflict``; der View hängt die
   Message an die Session und redirected zurück auf die Edit-URL.

Der Test verifiziert das End-to-End-Verhalten in der UI: die Konflikt-
Message ist sichtbar, die Änderung von Session A ist *nicht* persistiert,
die Änderung von Session B bleibt erhalten.
"""

from __future__ import annotations

import re
import uuid

import pytest


def _create_workitem(page, base_url, title: str) -> str:
    """Erzeugt ein WorkItem und liefert dessen Edit-URL.

    Nach der Erzeugung landet die UI auf ``/workitems/`` (Inbox). Der Link
    zum neu erzeugten Item enthält ``/workitems/<uuid>/`` als Detail-Pfad —
    daraus konstruieren wir die Edit-URL.
    """
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    page.select_option("select[name='item_type']", value="task")
    page.fill("input[name='title']", title)
    page.select_option("select[name='priority']", value="normal")
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)

    detail_link = page.locator(f"a[href*='/workitems/']:has-text('{title}')").first
    detail_link.wait_for(state="visible", timeout=5000)
    href = detail_link.get_attribute("href")
    assert href is not None and "/workitems/" in href
    pk_match = re.search(r"/workitems/([0-9a-f-]{36})/", href)
    assert pk_match, f"Konnte WorkItem-PK nicht aus URL extrahieren: {href}"
    pk = pk_match.group(1)
    return f"{base_url}/workitems/{pk}/edit/"


@pytest.mark.e2e
class TestOptimisticLockingTwoSessions:
    """Konflikt sichtbar machen via zwei parallele Browser-Sessions."""

    def test_concurrent_edit_triggers_conflict_message(
        self, authenticated_page, base_url, browser, _login_storage_state
    ):
        page_a = authenticated_page
        # Eindeutiger Titel, damit der Link in der Inbox eindeutig ist.
        original_title = f"Lock-Test-{uuid.uuid4().hex[:8]}"
        edit_url = _create_workitem(page_a, base_url, original_title)

        # Session A: Edit-Seite laden (snapshot v1 in hidden expected_updated_at).
        page_a.goto(edit_url, wait_until="domcontentloaded")
        v1 = page_a.locator("input[name='expected_updated_at']").get_attribute("value")
        assert v1, "Hidden expected_updated_at muss in Session A gesetzt sein."

        # Session B: zweiter Browser-Kontext mit derselben Login-Session.
        context_b = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
        page_b = context_b.new_page()
        page_b.set_default_timeout(30000)
        try:
            page_b.goto(edit_url, wait_until="domcontentloaded")
            v1_b = page_b.locator("input[name='expected_updated_at']").get_attribute("value")
            assert v1_b == v1, "Beide Sessions müssen denselben v1-Timestamp sehen."

            title_b = f"{original_title}-B"
            page_b.fill("input[name='title']", title_b)
            page_b.click("button:has-text('Speichern')")
            page_b.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)

            # Session A: jetzt mit dem alten v1-Snapshot submitten → Konflikt.
            title_a = f"{original_title}-A"
            page_a.fill("input[name='title']", title_a)
            page_a.click("button:has-text('Speichern')")

            # Der View redirected bei Konflikt zurück auf workitem_update.
            # Wir prüfen, dass die URL die Edit-Seite bleibt UND die Message rendert.
            page_a.wait_for_url(re.compile(r"/workitems/[0-9a-f-]{36}/edit/"), timeout=10000)
            page_a.wait_for_load_state("domcontentloaded")

            conflict_banner = page_a.locator("[role='alert']:has-text('zwischenzeitlich bearbeitet')").first
            conflict_banner.wait_for(state="visible", timeout=10000)

            # Verifikation: Session-B-Änderung bleibt persistiert, A-Änderung nicht.
            page_a.goto(edit_url, wait_until="domcontentloaded")
            persisted = page_a.locator("input[name='title']").get_attribute("value")
            assert persisted == title_b, f"Erwartet Session-B-Titel persistiert ({title_b!r}), gefunden: {persisted!r}"
        finally:
            context_b.close()
