"""E2E-Tests für Pagination: Audit-Log-Liste mit >50 Einträgen."""

import re

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e

# ``AuditLog`` is append-only, so the entries cannot be removed again. The E2E
# database is re-seeded with ``--flush`` at the start of every session, and the
# fixture only tops the login entries up to the page size, so repeated module
# runs do not pile up.
_ENSURE_LOGIN_ENTRIES = """
from core.constants import AUDIT_PAGE_SIZE
from core.models import AuditLog, User

admin = User.objects.get(username='admin')
logins = AuditLog.objects.for_facility(admin.facility).filter(action=AuditLog.Action.LOGIN)
for _ in range(max(0, AUDIT_PAGE_SIZE + 1 - logins.count())):
    AuditLog.objects.create(
        facility=admin.facility,
        user=admin,
        action=AuditLog.Action.LOGIN,
        ip_address='127.0.0.1',
        detail={'username': admin.username},
    )
print('LOGIN_ENTRIES', logins.count(), 'PAGE_SIZE', AUDIT_PAGE_SIZE)
"""


@pytest.fixture(scope="module")
def paginated_audit_log(e2e_env):
    """Guarantee more login entries than one audit page holds (the precondition of every test here)."""
    lines = django_shell(e2e_env, _ENSURE_LOGIN_ENTRIES)
    matches = (re.match(r"LOGIN_ENTRIES (\d+) PAGE_SIZE (\d+)", line) for line in lines)
    match = next((m for m in matches if m), None)
    assert match, f"Audit entry setup did not report a count: {lines}"
    entries, page_size = int(match.group(1)), int(match.group(2))
    assert entries > page_size, f"Expected more than {page_size} login entries, got {entries}"
    return entries


class TestAuditPagination:
    """Pagination auf Audit-Log-Liste (50 pro Seite)."""

    def test_audit_pagination_visible(self, authenticated_page, base_url, paginated_audit_log):
        """Bei ausreichend Einträgen sind Pagination-Buttons sichtbar."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # Pagination-Container: "X–Y von Z Einträgen" + Seitennavigation
        expect(page.locator("text=/\\d+–\\d+ von \\d+ Einträgen/")).to_be_visible()
        # Seitennummer-Anzeige (z.B. "1 / 3")
        expect(page.locator("text=/\\d+ \\/ \\d+/")).to_be_visible()

    def test_audit_pagination_navigates(self, authenticated_page, base_url, paginated_audit_log):
        """Klick auf 'Nächste Seite' → URL enthält ?page=2, Tabelle zeigt Einträge."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        # Nächste-Seite-Button (›) finden
        next_button = page.locator("a:has-text('›')")
        expect(next_button).to_have_count(1)

        next_button.click()
        page.wait_for_url(re.compile(r"page=2"))
        page.wait_for_load_state("domcontentloaded")

        # Tabelle auf Seite 2 enthält Einträge
        rows = page.locator("#audit-table table tbody tr")
        assert rows.count() > 0

        # Seitennummer zeigt "2 / X"
        expect(page.locator("text=/2 \\/ \\d+/")).to_be_visible()

    def test_audit_pagination_preserves_filters(self, authenticated_page, base_url, paginated_audit_log):
        """Filter setzen + paginieren → Filter bleibt in URL erhalten."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/?action=login")
        page.wait_for_load_state("domcontentloaded")

        # Filter ist aktiv
        assert page.locator("select[name='action']").input_value() == "login"

        # Nächste-Seite-Button
        next_button = page.locator("a:has-text('›')")
        expect(next_button).to_have_count(1)

        # URL des Nächste-Buttons prüfen: enthält sowohl page= als auch action=login
        href = next_button.get_attribute("href")
        assert "page=" in href, f"Pagination-Link fehlt page-Parameter: {href}"
        assert "action=login" in href, f"Pagination-Link verliert Filter: {href}"

    def test_pagination_info_text_format(self, authenticated_page, base_url, paginated_audit_log):
        """Pagination zeigt 'X–Y von Z Einträgen' im korrekten Format."""
        page = authenticated_page
        page.goto(f"{base_url}/audit/")
        page.wait_for_load_state("domcontentloaded")

        info = page.locator("text=/\\d+–\\d+ von \\d+ Einträgen/")
        expect(info).to_have_count(1)

        text = info.inner_text()
        # Format: "1–50 von 123 Einträgen"
        match = re.match(r"(\d+)–(\d+) von (\d+) Einträgen", text)
        assert match, f"Unerwartetes Pagination-Format: {text}"

        start, end, total = int(match.group(1)), int(match.group(2)), int(match.group(3))
        assert start == 1, f"Erste Seite sollte bei 1 starten, nicht {start}"
        assert end == 50, f"Erste Seite sollte genau 50 Einträge zeigen, nicht {end}"
        assert total > 50, f"Gesamtanzahl sollte > 50 sein, nicht {total}"
