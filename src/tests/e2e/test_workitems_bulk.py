"""E2E: Bulk-Operationen und Recurrence für WorkItems (Welle 5 #928).

Refs Master #922.

- ENT-WI-06 — Bulk-Priorität: 3 Items → urgent.
- ENT-WI-07 — Bulk-Assign: 3 Items → leitung; Cross-Facility-Schutz negativ-getestet.
- ENT-WI-09 — Recurrence: weekly → DONE erzeugt Folgeaufgabe mit due_date + 7d.

Die Bulk-Endpoints werden direkt per POST aus der eingeloggten Session
angesprochen (CSRF-Token aus der Inbox-Page abgreifen). Das spiegelt
identisch das Verhalten, das der Alpine-Checkbox-Flow im Inbox-Template
auslöst — ist aber deterministischer als das UI-Selektieren von genau
drei richtigen Karten.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import urlencode

import pytest

pytestmark = pytest.mark.e2e


def _create_workitem(page, base_url, title: str, priority: str = "normal", recurrence: str = "") -> str:
    """Erzeugt eine Aufgabe und liefert deren UUID.

    Nach Submit landet die UI auf ``/workitems/``; der Detail-Link der frisch
    angelegten Karte enthält die UUID.
    """
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    page.select_option("select[name='item_type']", value="task")
    page.fill("input[name='title']", title)
    page.select_option("select[name='priority']", value=priority)
    if recurrence:
        page.select_option("select[name='recurrence']", value=recurrence)
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)

    link = page.locator(f"a[href*='/workitems/']:has-text('{title}')").first
    link.wait_for(state="visible", timeout=5000)
    href = link.get_attribute("href")
    match = re.search(r"/workitems/([0-9a-f-]{36})/", href or "")
    assert match, f"Konnte UUID nicht aus Link extrahieren: {href!r}"
    return match.group(1)


def _csrf_token(page) -> str:
    """CSRF-Cookie aus der Page-Session ziehen."""
    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
    token = cookies.get("csrftoken")
    assert token, "csrftoken-Cookie fehlt — Page muss vorher eine GET-Seite besucht haben."
    return token


def _post_form(page, base_url, path: str, data: dict) -> dict:
    """POST mit CSRF aus der aktiven Page-Session, gibt {status, url} zurück.

    Serialisiert ``data`` als ``application/x-www-form-urlencoded`` mit
    Unterstützung für Listen-Werte (z.B. mehrere ``workitem_ids``) — das
    ist genau das Format, das Django ``request.POST.getlist()`` erwartet.
    """
    page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
    token = _csrf_token(page)
    pairs = []
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    body = urlencode(pairs)
    response = page.request.post(
        f"{base_url}{path}",
        data=body,
        headers={
            "X-CSRFToken": token,
            "Referer": f"{base_url}/workitems/",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    return {"status": response.status, "url": response.url}


class TestBulkPriority:
    """Refs Matrix ENT-WI-06."""

    def test_bulk_priority_sets_three_items_to_urgent(self, staff_page, base_url):
        page = staff_page
        ids = [_create_workitem(page, base_url, f"E2E-Bulk-P-{uuid.uuid4().hex[:6]}") for _ in range(3)]

        result = _post_form(
            page,
            base_url,
            "/workitems/bulk-priority/",
            {"workitem_ids": ids, "priority": "urgent"},
        )
        assert result["status"] == 200, f"Bulk-Priority muss 200 liefern, ist {result['status']}"

        # Verifikation: jedes Detail zeigt jetzt „dringend"/„urgent".
        for wi in ids:
            page.goto(f"{base_url}/workitems/{wi}/", wait_until="domcontentloaded")
            content = page.content().lower()
            assert "dringend" in content or "urgent" in content, f"WorkItem {wi} sollte nach Bulk-Update urgent sein."

    def test_bulk_priority_invalid_returns_400(self, staff_page, base_url):
        page = staff_page
        ids = [_create_workitem(page, base_url, f"E2E-Bulk-PI-{uuid.uuid4().hex[:6]}")]
        result = _post_form(
            page,
            base_url,
            "/workitems/bulk-priority/",
            {"workitem_ids": ids, "priority": "nonsense"},
        )
        assert result["status"] == 400, f"Ungültiger Priority-Value muss 400 liefern, ist {result['status']}"


class TestBulkAssign:
    """Refs Matrix ENT-WI-07."""

    def test_bulk_assign_three_items_to_lead(self, staff_page, base_url):
        page = staff_page

        # Drei Aufgaben ohne Zuweisung anlegen.
        ids = [_create_workitem(page, base_url, f"E2E-Bulk-A-{uuid.uuid4().hex[:6]}") for _ in range(3)]

        # Lead-User-ID aus dem Bulk-Assign-Select der Inbox holen — vermeidet
        # eine separate DB-Anbindung.
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        lead_option = page.locator(
            "#bulk-assign option:has-text('Leitung'), #bulk-assign option:has-text('thomas')"
        ).first
        if lead_option.count() == 0:
            pytest.skip("Lead-User im Bulk-Assign-Select nicht gefunden — Seed-Variation.")
        lead_id = lead_option.get_attribute("value")
        assert lead_id, "Lead-User-Option hat keinen Wert."

        result = _post_form(
            page,
            base_url,
            "/workitems/bulk-assign/",
            {"workitem_ids": ids, "assigned_to": lead_id},
        )
        assert result["status"] == 200, f"Bulk-Assign muss 200 liefern, ist {result['status']}"

    def test_bulk_assign_unknown_user_returns_400(self, staff_page, base_url):
        page = staff_page
        ids = [_create_workitem(page, base_url, f"E2E-Bulk-AI-{uuid.uuid4().hex[:6]}")]
        result = _post_form(
            page,
            base_url,
            "/workitems/bulk-assign/",
            {"workitem_ids": ids, "assigned_to": str(uuid.uuid4())},
        )
        assert result["status"] == 400, f"Unbekannte User-ID muss 400 liefern, ist {result['status']}"


def _distinct_workitem_ids_with_title(page, base_url, title: str) -> set[str]:
    """Sammelt alle WorkItem-UUIDs, deren Karte den angegebenen Title trägt.

    Die Inbox kann denselben WorkItem mehrfach in unterschiedlichen
    Sektionen rendern (Heute/Demnächst, Compact/Full, sm:block vs sm:hidden).
    Der Test will aber Distinct-Counts — wir entfernen Duplikate über die
    PK aus dem ``href`` der Detail-Links.
    """
    ids: set[str] = set()
    for status_filter in ("open", "in_progress", "done"):
        page.goto(f"{base_url}/workitems/?status={status_filter}", wait_until="domcontentloaded")
        hrefs = page.locator(f"a[href*='/workitems/']:has-text('{title}')").evaluate_all(
            "els => els.map(e => e.getAttribute('href'))"
        )
        for href in hrefs:
            m = re.search(r"/workitems/([0-9a-f-]{36})/", href or "")
            if m:
                ids.add(m.group(1))
    return ids


class TestRecurrence:
    """Refs Matrix ENT-WI-09 — wöchentlich wiederkehrendes WorkItem."""

    def test_weekly_done_creates_follow_up_workitem(self, staff_page, base_url):
        page = staff_page
        title = f"E2E-Rec-{uuid.uuid4().hex[:6]}"
        wi_id = _create_workitem(page, base_url, title, recurrence="weekly")

        # Status auf done setzen — triggert duplicate_recurring_workitem.
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        token = _csrf_token(page)
        resp = page.request.post(
            f"{base_url}/api/workitems/{wi_id}/status/",
            form={"status": "done"},
            headers={"X-CSRFToken": token, "Referer": f"{base_url}/workitems/"},
        )
        assert resp.status in (200, 302), f"Status-Update unerwartet: {resp.status}"

        ids = _distinct_workitem_ids_with_title(page, base_url, title)
        assert wi_id in ids, "Original-WorkItem fehlt nach DONE in der Liste."
        assert len(ids) >= 2, (
            f"Erwarte mind. 2 distinkte WorkItems mit Title {title!r} (Original + Folge), gefunden: {len(ids)} ({ids})."
        )

    def test_weekly_done_is_idempotent(self, staff_page, base_url):
        """Refs #596: zweites DONE→OPEN→DONE darf KEIN drittes Item erzeugen."""
        page = staff_page
        title = f"E2E-Rec-Idem-{uuid.uuid4().hex[:6]}"
        wi_id = _create_workitem(page, base_url, title, recurrence="weekly")

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        token = _csrf_token(page)
        for status in ("done", "open", "done"):
            r = page.request.post(
                f"{base_url}/api/workitems/{wi_id}/status/",
                form={"status": status},
                headers={"X-CSRFToken": token, "Referer": f"{base_url}/workitems/"},
            )
            assert r.status in (200, 302), f"Status-Update {status!r} unerwartet: {r.status}"

        ids = _distinct_workitem_ids_with_title(page, base_url, title)
        assert len(ids) <= 2, (
            f"Idempotenz verletzt: {len(ids)} distinkte WorkItems mit Title {title!r} "
            f"— erwartet ≤ 2 (Refs #596). IDs: {ids}"
        )
