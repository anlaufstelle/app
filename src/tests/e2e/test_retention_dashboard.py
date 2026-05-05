"""E2E-Tests: Retention Dashboard — Löschfristen-Management."""

import re

import pytest

pytestmark = pytest.mark.e2e


def _ensure_proposals(e2e_env, min_pending=3):
    """Garantiert mindestens ``min_pending`` pending-RetentionProposals.

    Robust gegen Side-Effects anderer Tests, die proposals approven oder
    deferren: zählt pending, holt fehlende Anzahl Events ohne existierende
    Proposal-Verknüpfung und legt für jedes ein neues pending-Proposal an.

    Frühere Implementierung nutzte ``get_or_create(... status__in=[...])`` —
    ``__in`` ist als Lookup in ``get_or_create`` nicht zuverlässig (Django
    überträgt die Bedingung in den ``GET``-Filter, aber nicht in den
    ``CREATE``-Pfad), und unique-Constraints verhinderten Re-Creates für
    bereits-approved Targets. Folge: bei isolierten Single-File-Runs grün,
    aber im parallelen ``loadfile``-Scheduling rote Asserts mit
    ``count >= 2 false``.
    """
    import subprocess
    import sys

    # ``manage.py shell -c "..."`` akzeptiert keine if/for-Statements,
    # nur einzelne Ausdrücke. Daher gesamte Logik als List-Comprehension
    # mit Conditional-Slicing (need=0 → free_events=[], leerer Comprehension).
    code = (
        "from core.models import Event, Facility, RetentionProposal; "
        "from datetime import date, timedelta; "
        "f = Facility.objects.first(); "
        "pending = RetentionProposal.objects.filter(facility=f, status='pending').count(); "
        f"need = max(0, {min_pending} - pending); "
        "used = set(RetentionProposal.objects.filter(facility=f).values_list('target_id', flat=True)); "
        "free_events = [e for e in Event.objects.filter(facility=f, is_deleted=False) if e.pk not in used][:need]; "
        "cats = ['anonymous', 'identified', 'qualified']; "
        "[RetentionProposal.objects.create("
        "  facility=f, target_type='Event', target_id=e.pk, "
        "  deletion_due_at=date.today() + timedelta(days=i*10+5), status='pending', "
        "  retention_category=cats[i % len(cats)], "
        "  details={'pseudonym': e.client.pseudonym if e.client else None, "
        "           'document_type': e.document_type.name if e.document_type else None, "
        "           'occurred_at': str(e.occurred_at)}) for i, e in enumerate(free_events)]; "
        "print(f'pending={RetentionProposal.objects.filter(facility=f, status=\"pending\").count()}')"
    )
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert "pending=" in result.stdout, f"Seed failed: {result.stderr}\n{result.stdout}"


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
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        # Count pending proposals before
        pending_before = page.locator("span:has-text('Ausstehend')").count()
        assert pending_before >= 1, "Keine ausstehenden Proposals vorhanden"

        # Click first "Freigeben" button on a proposal card (handle confirm dialog).
        # Scope verhindert Clash mit dem gleichnamigen Bulk-Toolbar-Button.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator(".proposal-card button:has-text('Freigeben')").first.click()

        # The card should now show "Freigegeben" — warten, bis HTMX-Swap
        # das Badge neu gerendert hat.
        page.locator("span:has-text('Freigegeben')").first.wait_for(state="visible", timeout=5000)
        assert page.locator("span:has-text('Freigegeben')").count() >= 1


class TestRetentionBulkFlow:
    """Bulk-Actions: Mehrere Löschvorschläge in einem Rutsch bearbeiten."""

    def test_bulk_approve_two_proposals(self, authenticated_page, base_url, e2e_env):
        _ensure_proposals(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/retention/")
        page.wait_for_load_state("domcontentloaded")

        checkboxes = page.locator("[data-bulk-proposal]")
        n = checkboxes.count()
        assert n >= 2, "Für den Test werden mindestens 2 pending Vorschläge gebraucht"

        approved_before = page.locator("span:has-text('Freigegeben')").count()

        checkboxes.nth(0).check()
        checkboxes.nth(1).check()

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

    def test_set_hold_on_proposal(self, authenticated_page, base_url, e2e_env):
        """Hold setzen — Badge wechselt zu 'Aufgeschoben'."""
        _ensure_proposals(e2e_env)
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

    def test_dismiss_hold(self, authenticated_page, base_url, e2e_env):
        """Hold aufheben — Badge zurück zu 'Ausstehend'."""
        _ensure_proposals(e2e_env)
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
        card_btn = page.locator(".proposal-card button:has-text('Freigeben')")
        card_btn.first.wait_for(state="visible", timeout=5000)
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
