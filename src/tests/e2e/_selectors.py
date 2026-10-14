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

import re
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


def open_mobile_search_overlay(page: Page) -> Locator:
    """Oeffnet „Mehr" → „Suche" und gibt das sichtbare Such-Overlay zurueck.

    Refs #1861: Der Weg ins Overlay fuehrt ueber zwei Klicks und war in jedem
    Test einzeln ausgeschrieben. Er gehoert hierher — zumal er seit dem Fix
    eine zweite Zusage traegt: das Mehr-Dropdown ist danach **zu** (sonst
    faengt dessen ``@click.away="closeAll"`` den ersten Tap ins Suchfeld ab und
    schliesst das Overlay wieder).

    Template-Hooks: ``mobile-nav-more``, ``mobile-nav-search``,
    ``mobile-search-overlay``, ``mobile-more-items``.
    """
    page.locator("[data-testid='mobile-nav-more']").click()
    page.locator("[data-testid='mobile-nav-search']").click()
    overlay = page.locator("[data-testid='mobile-search-overlay']")
    overlay.wait_for(state="visible", timeout=10000)
    return overlay


def subnav_tab(page: Page, key: str) -> Locator:
    """Ein Reiter einer Bereichs-Reiterzeile (``NavItem.key`` der Section).

    Der Marker ``subnav-tab-<key>`` kommt mit den Bereichs-Hubs (Etappe 2 von
    #1801); der Helfer
    steht schon hier, damit die Nav-Selektoren an einem Ort liegen.
    """
    return page.locator(f"[data-testid='subnav-tab-{key}']")


# --- Aktionsleisten der Detailseiten (Refs #1813) ---------------------------
# Jede Detailseite rendert ihre Kontextaktionen ZWEIMAL: einmal im Container
# ``<entity>-actions-desktop`` (``hidden md:flex``) und einmal in
# ``<entity>-actions-mobile`` (``md:hidden``). Ein ungescopter Selektor wie
# ``page.locator("button:has-text('Löschen')")`` trifft deshalb immer zwei
# Elemente — eines davon unsichtbar (Strict-Mode-Verletzung bzw. Klick-Timeout).
# Folgenschwere Aktionen (Löschen, Fall abschließen, Datenauskunft) liegen
# zudem hinter dem ⋮-Knopf und sind erst nach dessen Klick sichtbar.
#
# Template-Haken: ``components/_action_overflow.html``
# (``desktop-overflow-menu``/``mobile-overflow-menu``) und
# ``core/<entity>/partials/_actions.html``.
#
# Refs #1864: Ob es ueberhaupt ein ⋮ gibt, haengt jetzt von Rolle und Datenlage
# ab (>= 2 Eintraege). Wer nur EINE Aktion adressieren will, nimmt deshalb
# ``action_in_bar`` statt ``open_*_overflow`` — das ist gegen kuenftige
# Umsortierungen zwischen Leiste und Menue robust.

ACTION_ENTITIES = ("client", "case", "event", "workitem")


def action_bar(page: Page, entity: str, variant: str = "desktop") -> Locator:
    """Die Aktionsleiste einer Detailseite (``entity`` aus :data:`ACTION_ENTITIES`).

    Immer als Scope verwenden — nie global nach dem Aktions-Testid suchen, sonst
    trifft der Selektor die Zwillings-Leiste der anderen Variante.
    """
    return page.locator(f"[data-testid='{entity}-actions-{variant}']")


def open_desktop_overflow(page: Page, entity: str) -> Locator:
    """Oeffnet „Weitere Aktionen" (⋮) der Desktop-Aktionsleiste.

    Gibt die Desktop-Aktionsleiste zurueck, damit der Aufrufer die
    Overflow-Eintraege darin gescopt adressieren kann::

        bar = open_desktop_overflow(page, "client")
        bar.locator("[data-testid='client-export-json-link']").click()
    """
    return _open_overflow(page, entity, "desktop")


def open_mobile_overflow(page: Page, entity: str) -> Locator:
    """Oeffnet das ⋮-Menue der mobilen Aktionsleiste (siehe :func:`open_desktop_overflow`)."""
    return _open_overflow(page, entity, "mobile")


def action_in_bar(page: Page, entity: str, testid: str, variant: str = "desktop") -> Locator:
    """Adressiert EINE Kopfaktion, egal ob sie in der Leiste oder im ⋮ liegt.

    Refs #1864: Das ⋮ „Weitere Aktionen" erscheint nur noch ab ZWEI
    Eintraegen — bleibt genau einer uebrig und ist er nicht folgenschwer,
    steht er direkt in der Leiste und es gibt gar kein ⋮ mehr (Aufgabenseite,
    Personenakte fuer die Assistenz, Ereignis ohne Fallbezug/Loeschrecht).
    ``open_*_overflow`` liefe dort ins Leere.

    Der Helfer oeffnet das Menue deshalb nur, wenn die Aktion nicht schon
    sichtbar ist UND ein ⋮ existiert. Damit bleiben kuenftige Umsortierungen
    zwischen Leiste und Menue testneutral — der Aufrufer sagt, WELCHE Aktion
    er will, nicht WO sie liegt.

    ``count()`` allein genuegt nicht: Overflow-Eintraege stehen auch bei
    geschlossenem Menue im DOM (``x-show`` setzt nur ``display: none``).
    """
    bar = action_bar(page, entity, variant)
    ziel = bar.locator(f"[data-testid='{testid}']")
    if ziel.count() and ziel.first.is_visible():
        return ziel
    if bar.locator(f"[data-testid='{variant}-overflow-menu']").count():
        _open_overflow(page, entity, variant)
    return ziel


def _open_overflow(page: Page, entity: str, variant: str) -> Locator:
    bar = action_bar(page, entity, variant)
    trigger = f"[data-testid='{variant}-overflow-menu']"
    bar.locator(trigger).click()
    # Das Panel ist das direkte Geschwister des ⋮-Knopfs (``x-show`` +
    # ``x-transition``). Auf dessen Sichtbarkeit warten, damit nicht-wartende
    # Pruefungen (``is_visible()``, ``bounding_box()``) nicht in die laufende
    # Einblend-Transition greifen.
    bar.locator(f"{trigger} + div").wait_for(state="visible")
    return bar


def wait_for_settled(locator: Locator, timeout: int = 5000) -> None:
    """Wartet, bis die Alpine-``x-transition``-Skalierung eines Panels ausgelaufen ist.

    Alpine blendet Dropdowns mit ``scale(.95) -> scale(1)`` ein. Waehrend der
    150-ms-Transition liefert ``bounding_box()`` die *skalierte* Groesse (ein
    44-px-Ziel misst dann ~42 px) — Groessen-Assertions muessen den Endzustand
    messen. ``getComputedStyle().transform`` ist waehrend der Transition eine
    Matrix und danach ``none``.
    """
    locator.page.wait_for_function(
        "el => getComputedStyle(el).transform === 'none'",
        arg=locator.element_handle(),
        timeout=timeout,
    )


# --- Forms, lists and demo entry (Refs #2561) -------------------------------


def save_button(page: Page) -> Locator:
    """Save button of the edit form in the main content, found by role and name.

    The label differs per form (``Speichern``, ``Änderungen speichern``), the role
    and the ``speichern`` part of the accessible name do not. Strict: a second
    match is a failure of the page, not something to click past.
    """
    return page.locator("#main-content").get_by_role("button", name=re.compile("speichern", re.IGNORECASE))


def event_card_toggle(page: Page) -> Locator:
    """Header button that expands the first contact card of the list.

    Template hook: ``data-testid="event-card-toggle"`` (``components/_event_card.html``).
    The list is ordered server-side, so the first card is deterministic.
    """
    return page.locator("#event-table [data-testid='event-card-toggle']").first


def demo_scenario_start(page: Page, key: str = "kontaktladen") -> Locator:
    """Start button of one demo scenario card, chosen by its scenario key.

    Template hooks: ``demo-scenario-card`` with ``data-scenario-key`` and the
    ``demo-scenario-start`` button inside it.
    """
    return page.locator(f"[data-testid='demo-scenario-card'][data-scenario-key='{key}']").locator(
        "[data-testid='demo-scenario-start']"
    )
