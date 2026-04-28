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

    @pytest.mark.smoke
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

        # The card should now show "Freigegeben" — warten, bis HTMX-Swap
        # das Badge neu gerendert hat.
        page.locator("span:has-text('Freigegeben')").first.wait_for(state="visible", timeout=5000)
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
        # Modal/Form via HTMX laden — auf textarea warten.
        reason_field = page.locator("textarea[name='reason']").first
        reason_field.wait_for(state="visible", timeout=5000)

        # Fill in reason
        reason_field.fill("E2E-Test: Gerichtsverfahren")

        # Submit hold form
        page.locator("button:has-text('Hold erstellen')").first.click()

        # The card should now show "Aufgeschoben" — warten bis Badge UND
        # der eingegebene Reason-Text gerendert sind (beides nach HTMX-Swap).
        page.locator("span:has-text('Aufgeschoben')").first.wait_for(state="visible", timeout=5000)
        page.locator("text=E2E-Test: Gerichtsverfahren").wait_for(state="visible", timeout=5000)
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
            reason_field = page.locator("textarea[name='reason']").first
            reason_field.wait_for(state="visible", timeout=5000)
            reason_field.fill("Temporärer Hold")
            page.locator("button:has-text('Hold erstellen')").first.click()
            # Warten bis Hold-Button „Hold aufheben" erscheint.
            page.locator("button:has-text('Hold aufheben')").first.wait_for(state="visible", timeout=5000)

        # Now dismiss the hold
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("button:has-text('Hold aufheben')").first.click()

        # After dismissal, card should revert — auf Reaparition der
        # Freigeben-Buttons warten (signalisiert Zurück-zu-Ausstehend).
        page.wait_for_load_state("domcontentloaded")
        page.locator("button:has-text('Freigeben')").first.wait_for(state="visible", timeout=5000)
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
