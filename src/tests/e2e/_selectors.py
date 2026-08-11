"""Stable selector helpers for E2E tests (Refs #922 / #924).

Replaces brittle ``page.locator("a:has-text('Stern-42').first.click()`` patterns
with deterministic ``data-testid``-based lookups.

Each helper returns a Playwright ``Locator`` (not a click target) so that callers
can chain ``.click()``, ``.is_visible()``, ``.wait_for()`` etc. as needed.

Template hooks (added in the data-testid commit):

- ``data-testid="client-row"`` with ``data-pseudonym`` on each client row
- ``data-testid="client-detail-link"`` on the ``<a>`` inside the row
- ``data-testid="audit-row"`` with ``data-action`` on each audit table row
- ``data-testid="audit-detail-link"`` on the timestamp link (desktop) and the
  whole mobile card
- ``data-testid="dsgvo-download-link"`` with ``data-package`` on each DSGVO
  download link
- ``data-testid="deletion-review-link"`` with ``data-dr-pk`` on each
  pending-deletion-request link
- ``data-testid="nav-items"`` on the desktop sidebar entry container and
  ``data-testid="mobile-more-items"`` on the mobile „Mehr" dropdown (Refs #1813)

When extending: keep helpers small, return ``Locator``s, and document the
template hook each helper depends on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


def find_client_link(page: Page, pseudonym: str) -> Locator:
    """Link to the client detail page for the given pseudonym.

    Looks for the ``client-row`` that has ``data-pseudonym`` matching
    ``pseudonym`` and returns the inner ``client-detail-link``.
    """
    return page.locator(f"[data-testid='client-row'][data-pseudonym='{pseudonym}']").locator(
        "[data-testid='client-detail-link']"
    )


def find_first_client_link(page: Page) -> Locator:
    """First client detail link on the page.

    Use when a test does not care which client is opened, only that *any*
    client detail page renders (e.g. PWA-cache smoke).
    """
    return page.locator("[data-testid='client-detail-link']").first


def find_first_audit_detail_link(page: Page) -> Locator:
    """Link to the audit-detail page for the **first** (most-recent) row.

    Order in the audit table is server-side (timestamp DESC); the first link
    is deterministic for tests that only need *any* valid audit entry.
    """
    return page.locator("[data-testid='audit-detail-link']").first


def find_audit_detail_link_by_action(page: Page, action: str) -> Locator:
    """Link to the audit-detail page of the first row with the given action.

    Use this when a test needs a specific action (e.g. ``LOGIN_SUCCESS``) rather
    than just any entry.
    """
    return (
        page.locator(f"[data-testid='audit-row'][data-action='{action}']")
        .locator("[data-testid='audit-detail-link']")
        .first
    )


def find_dsgvo_download_link(page: Page, package_slug: str) -> Locator:
    """DSGVO-package download link for the given slug."""
    return page.locator(f"[data-testid='dsgvo-download-link'][data-package='{package_slug}']")


def find_first_dsgvo_download_link(page: Page) -> Locator:
    """First DSGVO-package download link on the page.

    Useful when a test does not care which package is downloaded, only that
    download triggers a file response.
    """
    return page.locator("[data-testid='dsgvo-download-link']").first


def find_deletion_approve_button(page: Page) -> Locator:
    """Approve button on a deletion-review page.

    The form uses ``<button type='submit' name='action' value='approve'>`` —
    eindeutig per ``name+value`` matchen, kein ``.first`` nötig.
    """
    return page.locator("button[name='action'][value='approve']")


def find_deletion_reject_button(page: Page) -> Locator:
    """Reject button on a deletion-review page (siehe find_deletion_approve_button)."""
    return page.locator("button[name='action'][value='reject']")


# --- Navigation (Refs #1813) ------------------------------------------------
# Die Nav-Selektoren hingen bisher an Tailwind-Klassen (``.flex-1.overflow-y-auto``)
# und an sichtbaren Labels (``a:has-text('Statistik')``). Beides bricht bei jeder
# Umbenennung oder Layout-Anpassung, ohne dass sich die Navigation fachlich
# geaendert haette. Die Registry ``core.nav`` liefert dafuer stabile Testids.


def sidebar_items(page: Page) -> Locator:
    """Alle Eintraege der Desktop-Sidebar, in Renderreihenfolge.

    Haengt am Container-Marker ``data-testid="nav-items"`` — loest den
    Tailwind-Klassen-Selektor ``.flex-1.overflow-y-auto > a`` ab.
    """
    return page.locator("[data-testid='nav-items'] > a")


def sidebar_item(page: Page, key: str) -> Locator:
    """Ein einzelner Sidebar-Eintrag ueber seinen Registry-Schluessel.

    ``key`` ist der ``NavItem.key`` aus ``core/nav.py`` (z.B. ``"system"``,
    ``"import"``, ``"deletion-requests"``). Nicht jeder Eintrag traegt heute
    ein ``data-testid`` — die Registry fuehrt den Marker pro Eintrag,
    Etappe 2 von #1801 vervollstaendigt ihn.
    """
    return page.locator(f"[data-testid='nav-{key}']")


def open_mobile_more(page: Page) -> Locator:
    """Oeffnet Slot 5 („Mehr") der mobilen Bottom-Nav und gibt die Liste zurueck."""
    page.locator("[data-testid='mobile-nav-more']").click()
    return page.locator("[data-testid='mobile-more-items']")


def subnav_tab(page: Page, key: str) -> Locator:
    """Ein Reiter einer Bereichs-Reiterzeile (``NavItem.key`` der Section).

    Der Marker ``subnav-tab-<key>`` kommt mit den Bereichs-Hubs (Etappe 2 von
    #1801); der Helfer
    steht schon hier, damit die Nav-Selektoren an einem Ort liegen.
    """
    return page.locator(f"[data-testid='subnav-tab-{key}']")
