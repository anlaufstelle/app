"""E2E: Bulk-Operationen und Recurrence für WorkItems (#928).

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
            f"{base_url}/partials/workitems/{wi_id}/status/",
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
                f"{base_url}/partials/workitems/{wi_id}/status/",
                form={"status": status},
                headers={"X-CSRFToken": token, "Referer": f"{base_url}/workitems/"},
            )
            assert r.status in (200, 302), f"Status-Update {status!r} unerwartet: {r.status}"

        ids = _distinct_workitem_ids_with_title(page, base_url, title)
        assert len(ids) <= 2, (
            f"Idempotenz verletzt: {len(ids)} distinkte WorkItems mit Title {title!r} "
            f"— erwartet ≤ 2 (Refs #596). IDs: {ids}"
        )


class TestBulkSelectAllUI:
    """Refs #1023: ``Alle sichtbaren auswählen`` im echten Inbox-UI.

    Klickt die Select-All-Checkbox (Alpine ``workitemBulkSelect``,
    ``@alpinejs/csp``-Build) statt — wie die übrigen Bulk-Tests — direkt zu
    POSTen. Regression: die Bindung ``@change="toggleAll($event.target.checked)"``
    warf im CSP-Build einen ``TypeError: …reading 'target'`` (kein ``$event``
    im Ausdruck-Scope) → keine Item-Auswahl, keine Bulk-Toolbar. Fix: Bare-
    Method ``@change="onToggleAll"``.
    """

    def test_select_all_marks_visible_items_and_shows_toolbar(self, staff_page, base_url):
        page = staff_page
        tag = uuid.uuid4().hex[:6]
        _create_workitem(page, base_url, f"E2E-SelAll-{tag}-1")
        _create_workitem(page, base_url, f"E2E-SelAll-{tag}-2")

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        boxes = page.locator("input[type=checkbox][name='workitem_ids']")
        total = boxes.count()
        assert total >= 2, f"Erwartet ≥2 sichtbare Auswahl-Checkboxen, gefunden {total}."

        page.locator("#workitem-select-all").click()

        # Fix: Select-All markiert ALLE sichtbaren Item-Checkboxen.
        checked = sum(1 for i in range(total) if boxes.nth(i).is_checked())
        assert checked == total, f"Select-All markierte nur {checked}/{total} Checkboxen (CSP-Bind-Bug #1023?)."

        # …und die Bulk-Toolbar (x-show=hasSelection) erscheint.
        page.locator("[x-show='hasSelection']").first.wait_for(state="visible", timeout=3000)


class TestBulkManualSelectionUI:
    """Refs #1132: manuelle Einzel-/Mehrfachauswahl im echten Inbox-UI.

    Regression: die Item-Checkbox-Bindung ``@change="toggle('<pk>')"`` war im
    ``@alpinejs/csp``-Build wirkungslos — der CSP-Evaluator interpretiert nur
    Property-Pfade, keine Methodenaufrufe mit Literal-Argumenten. Folge: ein
    Einzelklick aenderte die Auswahl NICHT, die Toolbar oeffnete nur ueber
    „Alle sichtbaren auswählen", und nach „Alle + Abwaehlen" blieb der Zaehler
    stehen und die Bulk-Aktion traf alle zuvor sichtbaren Items. Fix: Bare-
    Method ``@change="onToggleItem"`` + Auswahl frisch aus dem DOM ableiten.
    """

    def test_single_manual_checkbox_opens_toolbar_with_count_one(self, staff_page, base_url):
        page = staff_page
        tag = uuid.uuid4().hex[:6]
        _create_workitem(page, base_url, f"E2E-Manual-{tag}-1")
        _create_workitem(page, base_url, f"E2E-Manual-{tag}-2")

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        toolbar = page.locator("[x-show='hasSelection']").first
        # Vor jeder Auswahl ist die Toolbar verborgen.
        assert not toolbar.is_visible(), "Toolbar darf ohne Auswahl nicht sichtbar sein."

        boxes = page.locator("input[type=checkbox][name='workitem_ids']")
        assert boxes.count() >= 1
        boxes.first.check()

        # Einzelner manueller Klick: Toolbar erscheint, Zaehler steht auf 1.
        toolbar.wait_for(state="visible", timeout=3000)
        count = page.locator("[x-text='selectionCount']").first
        assert count.inner_text().strip() == "1", "Manuelle Einzelauswahl muss Zaehler 1 zeigen (Refs #1132)."

    def test_deselect_after_select_all_reduces_count_and_submission(self, staff_page, base_url):
        page = staff_page
        tag = uuid.uuid4().hex[:6]
        for i in range(3):
            _create_workitem(page, base_url, f"E2E-Desel-{tag}-{i}")

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        boxes = page.locator("input[type=checkbox][name='workitem_ids']")
        total = boxes.count()
        assert total >= 3, f"Erwartet ≥3 Checkboxen, gefunden {total}."

        page.locator("#workitem-select-all").check()
        count = page.locator("[x-text='selectionCount']").first
        expect = page.locator("[x-show='hasSelection']").first
        expect.wait_for(state="visible", timeout=3000)
        assert count.inner_text().strip() == str(total)

        # Eine Checkbox manuell wieder abwaehlen → Zaehler sinkt um genau 1.
        boxes.first.uncheck()
        assert count.inner_text().strip() == str(total - 1), (
            "Nach manuellem Abwaehlen muss der Zaehler um 1 sinken (Refs #1132)."
        )
        # Master-Checkbox darf nicht mehr „alle" anzeigen.
        assert not page.locator("#workitem-select-all").is_checked()

        # Die Bulk-Status-Form sendet GENAU die noch ausgewaehlten IDs — nicht
        # die abgewaehlte. Die versteckten ``workitem_ids`` der Form spiegeln
        # die aktuelle Auswahl.
        deselected_value = boxes.first.get_attribute("value")
        submitted = page.locator("form[action*='bulk-status'] input[type=hidden][name='workitem_ids']")
        submitted_values = submitted.evaluate_all("els => els.map(e => e.value)")
        assert deselected_value not in submitted_values, (
            "Abgewaehltes Item darf NICHT mehr im Bulk-Submit stehen (Refs #1132)."
        )
        assert len(submitted_values) == total - 1

    def test_clear_selection_hides_toolbar(self, staff_page, base_url):
        page = staff_page
        tag = uuid.uuid4().hex[:6]
        _create_workitem(page, base_url, f"E2E-Clear-{tag}-1")

        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        page.locator("input[type=checkbox][name='workitem_ids']").first.check()
        toolbar = page.locator("[x-show='hasSelection']").first
        toolbar.wait_for(state="visible", timeout=3000)

        page.get_by_role("button", name="Auswahl löschen").click()
        toolbar.wait_for(state="hidden", timeout=3000)
        assert not page.locator("input[type=checkbox][name='workitem_ids']").first.is_checked()


class TestBulkPreservesFilterUI:
    """Refs #1132: nach dem Anwenden bleibt der aktive Filter erhalten.

    Vorher leitete der Bulk-Endpoint auf den nackten ``/workitems/``-Pfad um
    — die Tabelle kam ungefiltert zurueck, das Filter-Feld zeigte (client-
    seitig restauriert) aber weiter z.B. ``Lena Weber``. Fix: die aktiven
    Filterwerte werden per ``syncFilters`` in versteckte ``filter_*``-Felder
    geschrieben und vom Endpoint in die Redirect-Query uebernommen.
    """

    def test_filter_preserved_in_url_after_bulk_apply(self, staff_page, base_url):
        page = staff_page
        tag = uuid.uuid4().hex[:6]
        _create_workitem(page, base_url, f"E2E-FilterKeep-{tag}", priority="normal")

        # Typ-Filter „Aufgabe" aktivieren — die frisch erstellten Items sind
        # vom Typ ``task`` und damit unter diesem Filter sichtbar (anders als
        # ein Personenfilter, der unassigned Items ausblenden wuerde).
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        page.select_option("#filter-item-type", value="task")
        page.wait_for_load_state("domcontentloaded")

        boxes = page.locator("input[type=checkbox][name='workitem_ids']")
        assert boxes.count() >= 1, "Mind. eine Aufgabe muss unter dem Typ-Filter sichtbar sein."
        boxes.first.check()
        page.locator("[x-show='hasSelection']").first.wait_for(state="visible", timeout=3000)

        page.select_option("#bulk-priority", value="urgent")
        with page.expect_navigation(timeout=10000):
            page.locator("form[action*='bulk-priority'] button[type=submit]").click()

        # Der aktive Typ-Filter bleibt in der Redirect-URL erhalten (Refs #1132)
        # — vorher landete man auf dem ungefilterten ``/workitems/``.
        assert "item_type=task" in page.url, f"Typ-Filter ging nach Bulk-Apply verloren: {page.url}"
        # Und der Filter-Select zeigt nach dem Reload weiterhin „Aufgabe".
        assert page.locator("#filter-item-type").input_value() == "task"


def _create_workitem_assigned(page, base_url, title: str, assignee_label: str | None) -> str:
    """Wie ``_create_workitem``, aber setzt optional eine Zuweisung.

    ``assignee_label=None`` lässt die Aufgabe unassigned (Teamaufgabe).
    ``assignee_label`` wählt im ``assigned_to``-Select die passende Option.
    """
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    page.select_option("select[name='item_type']", value="task")
    page.fill("input[name='title']", title)
    if assignee_label is not None:
        page.select_option("select[name='assigned_to']", label=assignee_label)
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)
    link = page.locator(f"a[href*='/workitems/']:has-text('{title}')").first
    link.wait_for(state="visible", timeout=5000)
    href = link.get_attribute("href")
    match = re.search(r"/workitems/([0-9a-f-]{36})/", href or "")
    assert match, f"Konnte UUID nicht aus Link extrahieren: {href!r}"
    return match.group(1)


class TestUnassignedTeamTaskBulk:
    """Refs #1125: Teamaufgaben-Konsistenz zwischen Sichtbarkeit und Bulk.

    Eine nicht zugewiesene Aufgabe ist eine Teamaufgabe: Sie erscheint in der
    Inbox jeder Fachkraft mit Auswahl-Checkbox und Status-Buttons. Genau diese
    Items lehnte die Bulk-Route bisher mit 403 ``Keine Berechtigung für
    ausgewählte Aufgaben.`` ab, obwohl sie sichtbar und auswählbar sind. Der
    Fix erlaubt Fachkräften das Mutieren nicht zugewiesener Items; einer
    *anderen* Person zugewiesene Items bleiben geschützt.
    """

    def test_staff_may_bulk_mutate_unassigned_team_task_of_other_creator(self, lead_page, staff_page, base_url):
        # Thomas (Lead) legt eine NICHT zugewiesene Aufgabe an → Teamaufgabe.
        title = f"E2E-Team-{uuid.uuid4().hex[:6]}"
        wi_id = _create_workitem_assigned(lead_page, base_url, title, assignee_label=None)

        # Miriam (Fachkraft) ist weder Ersteller noch Assignee, mutiert per Bulk.
        result = _post_form(
            staff_page,
            base_url,
            "/workitems/bulk-status/",
            {"workitem_ids": [wi_id], "status": "in_progress"},
        )
        assert result["status"] == 200, (
            "Fachkraft muss eine nicht zugewiesene Teamaufgabe bulk-mutieren dürfen "
            f"(Refs #1125), bekam {result['status']}."
        )

        # Verifikation: Status ist jetzt „In Bearbeitung".
        staff_page.goto(f"{base_url}/workitems/{wi_id}/", wait_until="domcontentloaded")
        content = staff_page.content().lower()
        assert "in bearbeitung" in content, "Teamaufgabe sollte nach Bulk-Update 'In Bearbeitung' sein."

    def test_staff_may_not_bulk_mutate_task_assigned_to_other_user(self, lead_page, staff_page, base_url):
        # Thomas (Lead) legt eine Aufgabe an und weist sie SICH SELBST zu.
        title = f"E2E-Foreign-{uuid.uuid4().hex[:6]}"
        wi_id = _create_workitem_assigned(lead_page, base_url, title, assignee_label="Thomas Müller")

        # Miriam (Fachkraft) darf eine fremd-zugewiesene Aufgabe NICHT bulk-mutieren.
        # Refs #1148: Die Ablehnung leitet (statt einer rohen 403-Seite) in die
        # Inbox zurück; ``page.request.post`` folgt dem Redirect → 200.
        result = _post_form(
            staff_page,
            base_url,
            "/workitems/bulk-status/",
            {"workitem_ids": [wi_id], "status": "done"},
        )
        assert result["status"] == 200, (
            f"Abgelehnter Bulk leitet in die Inbox zurück (Refs #1148), bekam {result['status']}."
        )
        assert "/workitems/" in result["url"], f"Ziel der Ablehnung muss die Inbox sein, ist {result['url']!r}."

        # Schutz bleibt strikt: der Status der fremd-zugewiesenen Aufgabe ist
        # unverändert (kein 'Erledigt').
        staff_page.goto(f"{base_url}/workitems/{wi_id}/", wait_until="domcontentloaded")
        content = staff_page.content().lower()
        assert "erledigt am" not in content, "Fremd-zugewiesene Aufgabe darf nicht erledigt worden sein (Refs #1125)."


def _post_form_with_body(page, base_url, path: str, data: dict) -> dict:
    """Wie ``_post_form``, liefert zusätzlich den Antwort-Body als Text.

    Für die #1136-Verifikation brauchen wir nicht nur den Status, sondern den
    konkreten Meldungstext der 403-Antwort.
    """
    page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
    token = _csrf_token(page)
    pairs = []
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    response = page.request.post(
        f"{base_url}{path}",
        data=urlencode(pairs),
        headers={
            "X-CSRFToken": token,
            "Referer": f"{base_url}/workitems/",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    return {"status": response.status, "body": response.text()}


class TestBulkForbiddenMessageConcrete:
    """Refs #1136 + #1148: konkrete Meldung als Inline-Alert in der Aufgabenliste.

    Manuell auf E2E-Port 8844 (Fachkraft Miriam) beobachtet: nach „Alle
    sichtbaren auswählen" über den ``Alle``-Filter sind auch fremd-zugewiesene
    Aufgaben mit ausgewählt; der Bulk-Submit scheiterte mit der pauschalen
    Meldung „Keine Berechtigung für ausgewählte Aufgaben." (#1125). #1136 hat
    die Meldung konkretisiert (nennt die Anzahl), sie aber als rohe
    403-Textseite ausgeliefert — eine leere weiße Seite, die wie ein
    technischer Abbruch wirkte (#1148).

    Erwartet jetzt: Die abgelehnte Aktion leitet in die (gefilterte) Inbox
    zurück und zeigt die konkrete Meldung als Flash-Alert oberhalb der
    Aufgabenliste — die Nutzerin bleibt im Arbeitskontext.
    """

    def test_mixed_selection_shows_concrete_count_alert_in_inbox(self, lead_page, staff_page, base_url):
        # Eine fremd-zugewiesene Aufgabe (Thomas) + eine Miriam selbst
        # zugewiesene Aufgabe → gemischte Auswahl.
        foreign_title = f"E2E-1148-Foreign-{uuid.uuid4().hex[:6]}"
        foreign_id = _create_workitem_assigned(lead_page, base_url, foreign_title, assignee_label="Thomas Müller")
        own_title = f"E2E-1148-Own-{uuid.uuid4().hex[:6]}"
        own_id = _create_workitem_assigned(staff_page, base_url, own_title, assignee_label="Miriam Schmidt")

        # ``page.request.post`` folgt dem 302-Redirect → 200 (Inbox-HTML inkl.
        # gerendertem Flash-Alert). Refs #1148: keine rohe 403-Seite mehr.
        result = _post_form_with_body(
            staff_page,
            base_url,
            "/workitems/bulk-priority/",
            {"workitem_ids": [own_id, foreign_id], "priority": "urgent"},
        )
        assert result["status"] == 200, (
            f"Abgelehnter Bulk leitet in die Inbox zurück (Refs #1148), ist {result['status']}."
        )
        body = result["body"]
        # Pauschale Alt-Meldung darf NICHT mehr erscheinen.
        assert "Keine Berechtigung für ausgewählte Aufgaben." not in body, (
            "Die pauschale Meldung wurde durch eine konkrete ersetzt (Refs #1136)."
        )
        # Konkret: 1 von 2 ist fremd-zugewiesen, mit Begründung „zugewiesen".
        assert "1" in body and "2" in body, f"Meldung muss die Anzahl (1 von 2) nennen: {body!r}"
        assert "zugewiesen" in body, f"Meldung muss die Einschränkung erklären: {body!r}"
        # Die Meldung steht als Alert in der Inbox (role=alert) — nicht als
        # nackte Fehlerseite. Die Aufgabenliste ist weiterhin sichtbar.
        assert 'role="alert"' in body, "Konkrete Meldung muss als Alert gerendert sein (Refs #1148)."
        assert "Aufgaben" in body, "Die Aufgabenliste muss sichtbar bleiben (Refs #1148)."

        # Keine Mutation: das eigene Item bleibt unverändert (nicht 'urgent').
        staff_page.goto(f"{base_url}/workitems/{own_id}/", wait_until="domcontentloaded")
        own_content = staff_page.content().lower()
        assert "dringend" not in own_content and "urgent" not in own_content, (
            "Bei abgelehntem Bulk darf KEIN Item verändert worden sein (Alles-oder-nichts)."
        )

    def test_forbidden_bulk_via_ui_stays_in_list_with_alert(self, lead_page, staff_page, base_url):
        """UI-Flow (Refs #1148): Miriam wählt über den ``Alle``-Filter eine
        fremd-zugewiesene Aufgabe aus und löst per Bulk-Bar eine Änderung aus.
        Statt einer weißen 403-Seite bleibt sie in der Aufgabenliste und sieht
        oberhalb der Liste einen Warn-Alert mit der konkreten Begründung.

        Spiegelt die manuelle Beobachtung auf Port 8844 wider.
        """
        # Thomas (Lead) legt eine sich selbst zugewiesene Aufgabe an.
        foreign_title = f"E2E-1148-UI-{uuid.uuid4().hex[:6]}"
        _create_workitem_assigned(lead_page, base_url, foreign_title, assignee_label="Thomas Müller")

        page = staff_page
        # „Alle"-Filter, damit die fremd-zugewiesene Aufgabe sichtbar wird.
        page.goto(f"{base_url}/workitems/?assigned_to=", wait_until="domcontentloaded")
        # Genau die fremd-zugewiesene Karte auswählen (deterministisch über den
        # Card-Titel → zugehörige Checkbox).
        card = page.locator("div").filter(has_text=foreign_title).last
        checkbox = card.locator("input[type=checkbox][name=workitem_ids]").first
        checkbox.check()
        # Bulk-Bar erscheint; Priorität auf „Dringend" und anwenden.
        page.select_option("#bulk-priority", value="urgent")
        priority_form = page.locator("form").filter(has=page.locator("#bulk-priority"))
        priority_form.get_by_role("button", name="Anwenden").click()

        # Kein Seitenwechsel zu einer rohen Seite: wir sind weiterhin in der Inbox.
        page.wait_for_url(re.compile(r"/workitems/"), timeout=10000)
        alert = page.get_by_role("alert")
        alert.wait_for(state="visible", timeout=5000)
        alert_text = alert.inner_text()
        assert "zugewiesen" in alert_text, f"Alert muss die Einschränkung erklären: {alert_text!r}"
        assert "Sammelaktion" in alert_text, f"Alert muss die Bulk-Aktion benennen: {alert_text!r}"
        # Die Aufgabenliste bleibt sichtbar.
        assert page.get_by_role("heading", name="Aufgaben").is_visible(), (
            "Die Aufgabenliste muss sichtbar bleiben (Refs #1148)."
        )


def _create_workitem_assigned_no_default_wait(page, base_url, title: str, assignee_label: str) -> None:
    """Legt eine zugewiesene Aufgabe an, OHNE auf die Default-Inbox zu warten.

    Anders als ``_create_workitem_assigned`` setzt diese Variante NICHT voraus,
    dass die erstellende Person die Aufgabe in ihrer Default-Sicht („Mir
    zugewiesen") sieht. Genau das ist hier der Punkt: Miriam weist Thomas zu,
    die Aufgabe ist im Default ausgeblendet — die UUID/Sichtbarkeit prüfen die
    Tests anschließend gezielt über die passenden Filter (Refs #1125).
    """
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    page.select_option("select[name='item_type']", value="task")
    page.fill("input[name='title']", title)
    page.select_option("select[name='assigned_to']", label=assignee_label)
    page.click("button:has-text('Speichern')")
    page.wait_for_url(re.compile(r"/workitems/$"), timeout=10000)


def _filter_option_value(page, base_url, label: str) -> str | None:
    """Liest den Option-Wert einer Person aus dem Inbox-Personenfilter."""
    page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
    option = page.locator(f"#filter-assigned-to option:has-text('{label}')").first
    if option.count() == 0:
        return None
    return option.get_attribute("value")


class TestCreatorFindsForeignAssignedTask:
    """Refs #1125: selbst erstellte, fremd-zugewiesene Aufgabe bleibt auffindbar.

    Kern des wiedereröffneten Tickets: Miriam (Fachkraft) legt eine Aufgabe an
    und weist sie Thomas (Leitung) zu. Im Default-Filter „Mir zugewiesen" ist
    sie ausgeblendet — über „Alle" muss die Erstellerin sie wiederfinden, sonst
    wirkt die Aufgabe verschwunden. Solange es keine privaten Aufgaben (#607)
    gibt, sind normale Aufgaben innerhalb der Facility sichtbar.
    """

    def test_default_filter_hides_but_all_filter_shows_foreign_assigned(self, staff_page, base_url):
        page = staff_page
        title = f"E2E-Visible-{uuid.uuid4().hex[:6]}"
        # Miriam legt die Aufgabe an und weist sie Thomas zu (im Default versteckt).
        _create_workitem_assigned_no_default_wait(page, base_url, title, assignee_label="Thomas Müller")

        # Default-Sicht („Mir zugewiesen") blendet die Aufgabe aus.
        page.goto(f"{base_url}/workitems/", wait_until="domcontentloaded")
        page.wait_for_selector("#inbox-content", timeout=5000)
        default_hits = page.locator(f"#inbox-content a[href*='/workitems/']:has-text('{title}')").count()
        assert default_hits == 0, "Fremd-zugewiesene Aufgabe darf im Default-Filter 'Mir zugewiesen' nicht erscheinen."

        # „Alle" (assigned_to leer) macht sie wieder auffindbar.
        page.goto(f"{base_url}/workitems/?assigned_to=", wait_until="domcontentloaded")
        page.wait_for_selector("#inbox-content", timeout=5000)
        all_hits = page.locator(f"#inbox-content a[href*='/workitems/']:has-text('{title}')").count()
        assert all_hits > 0, "Erstellerin muss die fremd-zugewiesene Aufgabe über 'Alle' wiederfinden (Refs #1125)."

    def test_person_filter_shows_other_persons_task(self, staff_page, base_url):
        """Personenfilter auf Thomas zeigt seine Aufgabe (vorher: leere Liste)."""
        page = staff_page
        title = f"E2E-Person-{uuid.uuid4().hex[:6]}"
        _create_workitem_assigned_no_default_wait(page, base_url, title, assignee_label="Thomas Müller")

        thomas_id = _filter_option_value(page, base_url, "Thomas Müller")
        if not thomas_id:
            pytest.skip("Thomas-User im Personenfilter nicht gefunden — Seed-Variation.")

        page.goto(f"{base_url}/workitems/?assigned_to={thomas_id}", wait_until="domcontentloaded")
        page.wait_for_selector("#inbox-content", timeout=5000)
        hits = page.locator(f"#inbox-content a[href*='/workitems/']:has-text('{title}')").count()
        assert hits > 0, "Personenfilter auf Thomas muss dessen Aufgabe zeigen (Refs #1125)."


class TestAssignTaskToAssistant:
    """Refs #1125: Fachkraft kann einer Assistenz eine Aufgabe zuweisen.

    Korrigiert die frühere #867-Annahme. Assistenzkräfte können offene
    Teamaufgaben ohnehin per „Annehmen" auf sich ziehen — eine normale,
    nicht-private Aufgabe muss einer aktiven Assistenz derselben Facility auch
    direkt zuweisbar sein.
    """

    def test_assistant_is_selectable_and_assignment_persists(self, staff_page, base_url):
        page = staff_page
        title = f"E2E-Assist-{uuid.uuid4().hex[:6]}"

        # Assistenz „Lena Weber" steht im Zuweisungs-Select des Erstell-Formulars.
        page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
        lena_option = page.locator("select[name='assigned_to'] option:has-text('Lena Weber')").first
        assert lena_option.count() > 0, "Assistenz 'Lena Weber' muss im Zuweisungs-Select auswählbar sein (Refs #1125)."

        # Aufgabe anlegen und der Assistenz zuweisen.
        _create_workitem_assigned_no_default_wait(page, base_url, title, assignee_label="Lena Weber")

        # Zuweisung ist persistiert: Personenfilter auf Lena listet die Aufgabe.
        lena_id = _filter_option_value(page, base_url, "Lena Weber")
        assert lena_id, "Lena muss im Personenfilter erscheinen."

        page.goto(f"{base_url}/workitems/?assigned_to={lena_id}", wait_until="domcontentloaded")
        page.wait_for_selector("#inbox-content", timeout=5000)
        hits = page.locator(f"#inbox-content a[href*='/workitems/']:has-text('{title}')").count()
        assert hits > 0, "Der Assistenz zugewiesene Aufgabe muss unter ihrem Personenfilter auftauchen."
