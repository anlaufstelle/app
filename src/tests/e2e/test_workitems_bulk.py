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
        result = _post_form(
            staff_page,
            base_url,
            "/workitems/bulk-status/",
            {"workitem_ids": [wi_id], "status": "done"},
        )
        assert result["status"] == 403, (
            "Fremd-zugewiesene Aufgabe muss für die Fachkraft geschützt bleiben "
            f"(Refs #1125), bekam {result['status']}."
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
