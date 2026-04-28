"""E2E-Tests: Retention Dashboard — Löschfristen-Management."""

import re

import pytest

pytestmark = pytest.mark.e2e


def _ensure_proposals(page, base_url):
    """Create retention proposals via Django shell if none exist."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "-c",
            (
                "from core.models import Event, Facility, RetentionProposal; "
                "from datetime import date, timedelta; "
                "f = Facility.objects.first(); "
                "count = RetentionProposal.objects.filter(facility=f, status='pending').count(); "
                "print(f'existing={count}'); "
                "events = list(Event.objects.filter(facility=f, is_deleted=False)[:3]) if count < 2 else []; "
                "[RetentionProposal.objects.get_or_create("
                "  facility=f, target_type='Event', target_id=e.pk, "
                "  status__in=['pending', 'held'], "
                "  defaults={'deletion_due_at': date.today() + timedelta(days=i*10+5), "
                "  'status': 'pending', 'retention_category': ['anonymous', 'identified', 'qualified'][i], "
                "  'details': {'pseudonym': e.client.pseudonym if e.client else None, "
                "  'document_type': e.document_type.name if e.document_type else None, "
                "  'occurred_at': str(e.occurred_at)}}) "
                "for i, e in enumerate(events)]; "
                "print(f'total={RetentionProposal.objects.filter(facility=f, status=\"pending\").count()}')"
            ),
        ],
        env={**__import__("os").environ, "DJANGO_SETTINGS_MODULE": "anlaufstelle.settings.e2e"},
        capture_output=True,
        text=True,
    )
    assert "total=" in result.stdout, f"Seed failed: {result.stderr}"


class TestRetentionDashboardAccess:
    """Dashboard-Zugriff und Inhalt."""

    def test_lead_can_access_dashboard(self, lead_page, base_url):
        """Lead kann das Retention Dashboard aufrufen."""
        _ensure_proposals(lead_page, base_url)
        page = lead_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        assert page.locator("h1:has-text('Löschfristen')").is_visible()
        assert page.locator("text=Ausstehend").first.is_visible()
        assert page.locator("text=Aufbewahrungsfristen").is_visible()

    def test_admin_can_access_dashboard(self, authenticated_page, base_url):
        """Admin kann das Retention Dashboard aufrufen."""
        _ensure_proposals(authenticated_page, base_url)
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

    def test_approve_proposal(self, authenticated_page, base_url):
        """Admin gibt einen Proposal frei — Badge wechselt zu 'Freigegeben'."""
        _ensure_proposals(authenticated_page, base_url)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Count pending proposals before
        pending_before = page.locator("span:has-text('Ausstehend')").count()
        assert pending_before >= 1, "Keine ausstehenden Proposals vorhanden"

        # Click first "Freigeben" button (handle confirm dialog)
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("button:has-text('Freigeben')").first.click()
        page.wait_for_timeout(1000)

        # The card should now show "Freigegeben"
        assert page.locator("span:has-text('Freigegeben')").count() >= 1


class TestRetentionHoldFlow:
    """Legal Hold setzen und aufheben via HTMX."""

    def test_set_hold_on_proposal(self, authenticated_page, base_url):
        """Hold setzen — Badge wechselt zu 'Aufgeschoben'."""
        _ensure_proposals(authenticated_page, base_url)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Click "Hold setzen" on first pending proposal
        hold_btn = page.locator("button:has-text('Hold setzen')").first
        hold_btn.click()
        page.wait_for_timeout(500)

        # Fill in reason
        page.locator("textarea[name='reason']").first.fill("E2E-Test: Gerichtsverfahren")

        # Submit hold form
        page.locator("button:has-text('Hold erstellen')").first.click()
        page.wait_for_timeout(1000)

        # The card should now show "Aufgeschoben"
        assert page.locator("span:has-text('Aufgeschoben')").count() >= 1
        assert page.locator("text=E2E-Test: Gerichtsverfahren").is_visible()

    def test_dismiss_hold(self, authenticated_page, base_url):
        """Hold aufheben — Badge zurück zu 'Ausstehend'."""
        _ensure_proposals(authenticated_page, base_url)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # First create a hold if none exists
        if page.locator("button:has-text('Hold aufheben')").count() == 0:
            hold_btn = page.locator("button:has-text('Hold setzen')").first
            hold_btn.click()
            page.wait_for_timeout(500)
            page.locator("textarea[name='reason']").first.fill("Temporärer Hold")
            page.locator("button:has-text('Hold erstellen')").first.click()
            page.wait_for_timeout(1000)

        # Now dismiss the hold
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("button:has-text('Hold aufheben')").first.click()
        page.wait_for_timeout(1000)

        # After dismissal, card should revert — the dismiss button should be gone
        # and the proposal card should show "Ausstehend" again
        page.wait_for_load_state("domcontentloaded")
        # Verify the first proposal card shows Freigeben/Hold buttons again
        assert page.locator("button:has-text('Freigeben')").count() >= 1


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
