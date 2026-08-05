"""E2E-Tests: Retention Dashboard — Löschfristen-Management."""

import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _ensure_proposals(e2e_env, count=3):
    """Legt ``count`` pending-RetentionProposals über **eigene** Wegwerf-Daten an.

    Liefert die PKs der angelegten Proposals zurück; die Tests adressieren
    ihre Karten damit über ``#proposal-<pk>`` statt über ``.first``.

    Zwei Richtungen von Test-Isolation, beide hier nötig:

    1. *Fremde Side-Effects auf diesen Test* — andere Dateien approven oder
       deferren Proposals; deshalb verlässt sich dieser Helfer nicht auf
       Bestands-Proposals, sondern legt pro Aufruf frische an. (Die frühere
       ``get_or_create(... status__in=[...])``-Variante war zusätzlich falsch:
       ``__in`` überträgt Django in den ``GET``-Filter, nicht in den
       ``CREATE``-Pfad, und unique-Constraints verhinderten Re-Creates für
       bereits-approved Targets — isoliert grün, im geteilten Lauf rot.)
    2. *Dieser Test auf fremde Daten* — die Gegenrichtung, und die teurere:
       bis dahin band der Helfer seine Proposals an **beliebige** vorhandene
       Events (``Event.objects.filter(...)[:need]``), also im Voll-Lauf an
       Seed-Personen wie ``Blitz-08`` und an Events, die frühere Dateien
       angelegt hatten. Die Freigabe-/Hold-Tests haben damit fremden
       Lösch-/Hold-Zustand umgeschaltet: ein aufgehobener Legal Hold gibt ein
       fremdes Event für ``enforce_retention`` frei (``test_statistics_snapshot``
       fährt den Lauf im selben Suite-Durchgang), und sind danach *alle* Events
       einer Person soft-gelöscht, anonymisiert die K-Anon-Bridge sie
       (``is_active=False``) — nachfolgende Dateien finden die Person dann nicht
       mehr bzw. laufen ins Widerrufs-Gate (Refs #1771,
       ``resolve_writable_client``). Deshalb: eigener Wegwerf-Klient
       ``RET-E2E-<uuid8>``, eigene Events, Proposals **nur** darüber.

    ``manage.py shell -c "..."`` akzeptiert keine if/for-Statements, nur
    ``;``-getrennte einfache Statements — die Schleifen bleiben deshalb
    List-Comprehensions.
    """
    code = (
        "from core.models import Client, DocumentType, Event, Facility, RetentionProposal; "
        "from datetime import date, timedelta; "
        "from django.utils import timezone; "
        "import uuid; "
        "f = Facility.objects.first(); "
        "dt = DocumentType.objects.filter(facility=f, entity_type='event').first(); "
        "c = Client.objects.create(facility=f, contact_stage='identified', "
        "  pseudonym='RET-E2E-' + uuid.uuid4().hex[:8]); "
        "events = [Event.objects.create(facility=f, client=c, document_type=dt, "
        "  occurred_at=timezone.now(), data_json={}) "
        f" for _ in range({count})]; "
        "cats = ['anonymous', 'identified', 'qualified']; "
        "props = [RetentionProposal.objects.create("
        "  facility=f, target_type='Event', target_id=e.pk, "
        "  deletion_due_at=date.today() + timedelta(days=i*10+5), status='pending', "
        "  retention_category=cats[i % len(cats)], "
        "  details={'pseudonym': c.pseudonym, "
        "           'document_type': dt.name if dt else None, "
        "           'occurred_at': str(e.occurred_at)}) for i, e in enumerate(events)]; "
        "[print('proposal=' + str(p.pk)) for p in props]"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    pks = [ln.removeprefix("proposal=") for ln in result.stdout.splitlines() if ln.startswith("proposal=")]
    assert len(pks) == count, f"Seed failed: {result.stderr}\n{result.stdout}"
    return pks


class TestRetentionDashboardAccess:
    """Dashboard-Zugriff und Inhalt."""

    def test_lead_can_access_dashboard(self, lead_page, base_url, e2e_env):
        """Lead kann das Retention Dashboard aufrufen."""
        _ensure_proposals(e2e_env)
        page = lead_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1:has-text('Löschfristen')").is_visible()
        assert page.locator("text=Ausstehend").first.is_visible()
        assert page.locator("text=Aufbewahrungsfristen").is_visible()

    def test_admin_can_access_dashboard(self, authenticated_page, base_url, e2e_env):
        """Admin kann das Retention Dashboard aufrufen."""
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1:has-text('Löschfristen')").is_visible()

    def test_staff_redirected_from_dashboard(self, staff_page, base_url):
        """Staff-User wird vom Dashboard abgewiesen."""
        page = staff_page
        resp = page.goto(f"{base_url}/retention/")
        # Should get 403 or redirect to login
        assert page.url != f"{base_url}/retention/" or resp.status == 403


class TestRetentionApproveFlow:
    """Proposal freigeben via HTMX."""

    @pytest.mark.smoke
    def test_approve_proposal(self, authenticated_page, base_url, e2e_env):
        """Admin gibt einen Proposal frei — Badge wechselt zu 'Freigegeben'."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Nur die EIGENE Karte freigeben (Refs #1787): ``.first`` traf sonst ein
        # beliebiges Bestands-Proposal einer fremden Person.
        card = page.locator(f"#proposal-{pks[0]}")
        card.wait_for(state="visible", timeout=5000)
        assert card.locator("span:has-text('Ausstehend')").count() >= 1

        # Scope verhindert zugleich den Clash mit dem gleichnamigen Bulk-Toolbar-Button.
        page.on("dialog", lambda dialog: dialog.accept())
        card.locator("button:has-text('Freigeben')").click()

        # The card should now show "Freigegeben" — warten, bis HTMX-Swap
        # das Badge neu gerendert hat.
        card.locator("span:has-text('Freigegeben')").wait_for(state="visible", timeout=5000)
        assert card.locator("span:has-text('Freigegeben')").count() >= 1


class TestRetentionBulkFlow:
    """Bulk-Actions: Mehrere Löschvorschläge in einem Rutsch bearbeiten."""

    def test_bulk_approve_two_proposals(self, authenticated_page, base_url, e2e_env):
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        approved_before = page.locator("span:has-text('Freigegeben')").count()

        # Gezielt die beiden EIGENEN Vorschläge auswählen (Refs #1787) — ``nth(0/1)``
        # über alle Checkboxen hätte fremde Bestands-Vorschläge erwischt.
        for pk in pks[:2]:
            page.locator(f"#proposal-{pk} [data-bulk-proposal]").check()

        counter = page.locator("[data-testid='retention-bulk-count']")
        counter.wait_for(state="visible", timeout=10000)
        assert "2" in counter.inner_text()

        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("[data-testid='retention-bulk-approve']").click()

        # HX-Redirect triggert full-page-Reload zurück auf /retention/ —
        # auf mindestens zwei neue „Freigegeben"-Badges warten.
        page.wait_for_function(
            (
                "count => Array.from(document.querySelectorAll('span'))"
                ".filter(s => s.textContent.trim() === 'Freigegeben').length >= count"
            ),
            arg=approved_before + 2,
            timeout=10000,
        )

    def test_select_all_toggles_every_checkbox(self, authenticated_page, base_url, e2e_env):
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        total = page.locator("[data-bulk-proposal]").count()
        assert total >= 1

        page.locator("[data-testid='retention-select-all']").check()
        counter = page.locator("[data-testid='retention-bulk-count']")
        counter.wait_for(state="visible", timeout=10000)
        assert str(total) in counter.inner_text()

        page.locator("[data-testid='retention-select-all']").uncheck()
        # Nach dem Abwählen muss die Toolbar wieder verschwinden
        page.locator("[data-testid='retention-bulk-count']").wait_for(state="hidden", timeout=10000)


class TestRetentionHoldFlow:
    """Legal Hold setzen und aufheben via HTMX."""

    @staticmethod
    def _set_hold(card, reason):
        """Hold über die eigene Karte setzen und auf den HTMX-Swap warten."""
        card.locator("button:has-text('Hold setzen')").click()
        reason_field = card.locator("textarea[name='reason']")
        reason_field.wait_for(state="visible", timeout=5000)
        reason_field.fill(reason)
        card.locator("button:has-text('Hold erstellen')").click()
        card.locator("button:has-text('Hold aufheben')").wait_for(state="visible", timeout=5000)

    def test_set_hold_on_proposal(self, authenticated_page, base_url, e2e_env):
        """Hold setzen — Badge wechselt zu 'Legal Hold' (Refs #1478 COD-03)."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Nur die EIGENE Karte anfassen (Refs #1787).
        card = page.locator(f"#proposal-{pks[0]}")
        card.wait_for(state="visible", timeout=5000)
        self._set_hold(card, "E2E-Test: Gerichtsverfahren")

        # The card should now show "Legal Hold" (get_status_display, seit
        # #1478/COD-03 statt des irrefuehrenden "Aufgeschoben") — warten bis
        # Badge UND der eingegebene Reason-Text gerendert sind (nach HTMX-Swap).
        card.locator("span:has-text('Legal Hold')").wait_for(state="visible", timeout=5000)
        card.locator("text=E2E-Test: Gerichtsverfahren").wait_for(state="visible", timeout=5000)
        assert card.locator("span:has-text('Legal Hold')").count() >= 1

    def test_dismiss_hold(self, authenticated_page, base_url, e2e_env):
        """Hold aufheben — Badge zurück zu 'Ausstehend'."""
        pks = _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Refs #1787: Hold IMMER erst auf der eigenen Karte anlegen und dann
        # genau diesen wieder aufheben. Die frühere ``.first``-Variante hob den
        # erstbesten vorhandenen Hold auf — im Voll-Lauf also einen Seed-Hold
        # auf einem fremden Ereignis, das damit für ``enforce_retention``
        # freigegeben wurde (Spoliations-Schutz weg, Löschkaskade an).
        card = page.locator(f"#proposal-{pks[0]}")
        card.wait_for(state="visible", timeout=5000)
        self._set_hold(card, "Temporärer Hold")

        page.on("dialog", lambda dialog: dialog.accept())
        card.locator("button:has-text('Hold aufheben')").click()

        # After dismissal, card should revert — auf Reaparition der
        # Freigeben-Buttons warten (signalisiert Zurück-zu-Ausstehend).
        page.wait_for_load_state("domcontentloaded")
        card_btn = card.locator("button:has-text('Freigeben')")
        card_btn.wait_for(state="visible", timeout=5000)
        assert card_btn.count() >= 1


class TestRetentionNavigation:
    """Navigation zum Dashboard."""

    def test_nav_link_visible_for_lead(self, lead_page, base_url):
        """Navigationslink 'Löschfristen' ist für Lead sichtbar."""
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        nav_link = page.locator("a:has-text('Löschfristen')")
        assert nav_link.count() >= 1

    def test_nav_link_navigates_to_dashboard(self, lead_page, base_url):
        """Klick auf 'Löschfristen' navigiert zum Dashboard."""
        page = lead_page
        page.goto(f"{base_url}/")
        page.wait_for_load_state("domcontentloaded")

        page.locator("nav a:has-text('Löschfristen')").first.click()
        page.wait_for_url(re.compile(r"/retention/"))
        assert page.locator("h1:has-text('Löschfristen')").is_visible()
