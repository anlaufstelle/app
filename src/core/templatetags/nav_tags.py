"""Template-Anbindung der Navigations-Registry ``core.nav`` (Refs #1813).

Bewusst **Inclusion-Tags statt Context-Processor**: ein Prozessor liefe auf
jedem Response — auch auf HTMX-Partials, Downloads und Fehlerseiten — und würde
die Gates dort eager auswerten, inklusive ``can_access_deletion_requests``
(eine Query für Fachkraft/Assistenz). Genau deshalb beginnen alle Prozessoren in
``core/context_processors.py`` mit einem ``HX-Request``-Frühausstieg. Ein
Inclusion-Tag wird nur ausgewertet, wo Navigation tatsächlich gerendert wird.

Die Tags reichen **vorbereitete** :class:`RenderedItem`\\ s weiter, nie rohe
``NavItem``\\ s: ``href`` (inkl. optionalem Query) und ``is_active`` werden hier
in Python berechnet. Die Templates enthalten damit weder Logik noch
Alpine-Ausdrücke — die Aktiv-Berechnung kann nicht erneut ins Template
zurückwandern, wo seinerzeit der Substring-Bug entstand.
"""

from __future__ import annotations

from dataclasses import dataclass

from django import template
from django.urls import reverse
from django.utils.functional import Promise

from core import nav

register = template.Library()


@dataclass(frozen=True)
class RenderedItem:
    """Ein renderfertiger Navigations-Eintrag — alle Entscheidungen bereits getroffen."""

    key: str
    label: str | Promise
    href: str
    testid: str
    is_active: bool
    icon: str
    external: bool
    badge: str


def _href(item: nav.NavItem) -> str:
    url = reverse(item.url_name) if item.url_name else item.url
    return f"{url}?{item.query}" if item.query else url


def _current_url_name(context) -> str:
    """``url_name`` der aktuellen Route (leer, wenn kein Request/Match vorliegt)."""
    match = getattr(context.get("request"), "resolver_match", None)
    return getattr(match, "url_name", "") or ""


def _user(context):
    """Die ``User``-Instanz **des Requests** — nie eine frisch geladene.

    Siehe ``core.nav``-Docstring, Regel 1: die ``cached_property``-Garantie von
    ``has_open_deletion_requests`` hängt daran, dass beide Navigationsleisten
    dieselbe Instanz befragen.
    """
    return getattr(context.get("request"), "user", None) or context.get("user")


def _rendered(items, context, *, mobile: bool = False) -> list[RenderedItem]:
    user = _user(context)
    current = _current_url_name(context)
    return [
        RenderedItem(
            key=item.key,
            label=item.label,
            href=_href(item),
            testid=item.mobile_testid if mobile else item.testid,
            is_active=current in item.family,
            icon=item.icon,
            external=item.external,
            badge=item.badge,
        )
        for item in nav.visible_items(items, user)
    ]


@register.inclusion_tag("partials/_nav_sidebar_items.html", takes_context=True)
def nav_sidebar(context):
    """Die Einträge der Desktop-Sidebar (innerhalb des ``nav-items``-Containers)."""
    return {
        "items": _rendered(nav.SIDEBAR, context),
        "overdue_workitems_count": context.get("overdue_workitems_count"),
        "open_workitems_count": context.get("open_workitems_count"),
    }


@register.inclusion_tag("partials/_nav_mobile_more_items.html", takes_context=True)
def nav_mobile_more(context):
    """Die Link-Einträge des mobilen „Mehr"-Menüs."""
    return {"items": _rendered(nav.MOBILE_MORE, context, mobile=True)}


@register.inclusion_tag("partials/_subnav.html", takes_context=True)
def subnav(context, key: str):
    """Die Reiterzeile eines Bereichs (``/system/`` und die Hubs, Refs #1801 §4.5).

    Die Rollen-Gates je Reiter kommen aus der Registry und werden **hier**
    ausgewertet. Damit kann eine ``LEAD_PLUS``-Seite gar keinen
    ``ADMIN_ONLY``-Reiter rendern — die Zusicherung hängt an der Datenstruktur,
    nicht an Template-Disziplin (``test_authz_template_links.py``).
    """
    area = nav.section(key)
    return {"section": area, "tabs": _rendered(area.tabs, context)}


@register.simple_tag(takes_context=True)
def nav_mobile_more_active(context) -> bool:
    """True, wenn die aktuelle Seite den „Mehr"-Button hervorheben soll."""
    return _current_url_name(context) in nav.mobile_more_catalog()


@register.simple_tag(takes_context=True)
def nav_active(context, key: str) -> bool:
    """Aktiv-Zustand eines einzelnen Eintrags — für die handgebauten Bottom-Bar-Slots.

    Die vier Slots der mobilen Bottom-Bar tragen eigene Pixelform (22px-Icon,
    Label darunter, Overdue-Badge) und bleiben deshalb ausgeschriebenes Markup;
    ihre Familie kommt trotzdem aus der Registry.
    """
    return _current_url_name(context) in nav.item(key).family
