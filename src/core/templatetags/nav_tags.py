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

from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, urlencode

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
    divider_before: bool = False


def _href(item: nav.NavItem, carry: str = "") -> str:
    url = reverse(item.url_name) if item.url_name else item.url
    query = "&".join(part for part in (item.query, carry) if part)
    return f"{url}?{query}" if query else url


def _carry_query(section: nav.NavSection, context) -> str:
    """Der beim Reiterwechsel mitgeführte Bereichszustand (Refs #1804 §3.5).

    Zwei Quellen, eine Whitelist (``section.carry``):

    * **Aufgelöst.** Eine View, die den Zustand ohnehin parst, veröffentlicht
      ihn als ``subnav_carry`` — dann reisen die kanonischen Werte, und ein
      Alt-Deep-Link (``?period=quarter``) breitet seine Schreibweise nicht über
      die Reiterzeile weiter aus.
    * **Roh.** Ohne ``subnav_carry`` reicht der Tag die benannten Parameter
      unverändert aus ``request.GET`` weiter. Das ist kein Notnagel, sondern
      die Zusage des Freigaben-Reiters: er wertet den Zeitraum bewusst nicht
      aus (er zeigt die *N* jüngsten Herausgaben), darf ihn aber nicht
      verschlucken — sonst kostet jeder Blick in die Herausgaben den Zeitraum,
      mit dem man gekommen ist.

    Die Whitelist ist der Grund, warum der Roh-Pfad ungefährlich ist: es reisen
    nur benannte Parameter, nie beliebige Query-Bestandteile eines Requests.
    """
    if not section.carry:
        return ""
    state = context.get("subnav_carry")
    if state is None:
        request = context.get("request")
        state = request.GET if request is not None else {}
    return urlencode([(key, state[key]) for key in section.carry if state.get(key)])


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


def _query_matches(item: nav.NavItem, request) -> bool:
    """Trägt die aktuelle Anfrage die Query-Parameter des Eintrags?

    Nur relevant für Reiter mit ``query`` (heute „Datensatz-Berichte" =
    ``core:statistics?ansicht=datensaetze``, Refs #1801 §7.1). Ohne diese Prüfung
    wäre ein solcher Reiter auf **jeder** Seite seiner Route aktiv.

    Refs #1804 (F1/F5): Die kanonische Schreibweise ist deutsch, die frühere
    (``?view=berichte``) bleibt als Alias gültig. Beide müssen **denselben**
    Reiter aktivieren — täte nur die kanonische es, zeigte ein Bookmark von
    gestern zwar den Berichte-Inhalt, aber die Reiterzeile stünde ohne aktiven
    Reiter da (und der query-lose „Übersicht"-Reiter träte nicht zurück, weil
    er nur vor einem AKTIVEN Query-Reiter zurücktritt).
    """
    if not item.query:
        return True
    if request is None:
        return False
    return any(
        all(request.GET.get(key) == value for key, value in parse_qsl(query))
        for query in (item.query, *item.query_aliases)
    )


def _active_keys(items, context) -> set[str]:
    """Die Schlüssel der aktiven Einträge — inklusive der Query-Regel.

    Zwei Reiter dürfen sich eine Route teilen und sich trotzdem gegenseitig
    ausschließen: „Übersicht" (``/statistics/``) und „Datensatz-Berichte"
    (``/statistics/?view=berichte``). Ist der Query-Reiter aktiv, **tritt der
    query-lose Reiter derselben Route zurück** — sonst leuchteten beide Pills,
    und die Reiterzeile sagte nicht mehr, wo man ist.
    """
    request = context.get("request")
    current = _current_url_name(context)
    active = {item.key for item in items if current in item.family and _query_matches(item, request)}
    routes_taken_by_a_query_tab = {item.url_name for item in items if item.key in active and item.query}
    return {
        item.key
        for item in items
        if item.key in active and (item.query or item.url_name not in routes_taken_by_a_query_tab)
    }


def _rendered(items, context, *, mobile: bool = False, carry: str = "") -> list[RenderedItem]:
    visible = nav.visible_items(items, _user(context))
    active = _active_keys(visible, context)
    return [
        RenderedItem(
            key=item.key,
            label=item.label,
            href=_href(item, carry),
            testid=item.mobile_testid if mobile else item.testid,
            is_active=item.key in active,
            icon=item.icon,
            external=item.external,
            badge=item.badge,
            divider_before=item.divider_before,
        )
        for item in visible
    ]


def _without_leading_divider(items: list[RenderedItem]) -> list[RenderedItem]:
    """Eine Trennlinie über dem ersten sichtbaren Eintrag trennt nichts.

    Die Gliederung der Sidebar hängt an ``divider_before`` **eines** Eintrags;
    welche Einträge davor sichtbar sind, entscheidet die Rolle. Ohne diesen
    Schnitt bekäme eine Rolle, die nur den Hub-Block sieht, eine Linie am
    oberen Rand.
    """
    if items and items[0].divider_before:
        items[0] = replace(items[0], divider_before=False)
    return items


@register.inclusion_tag("partials/_nav_sidebar_items.html", takes_context=True)
def nav_sidebar(context):
    """Die Einträge der Desktop-Sidebar (innerhalb des ``nav-items``-Containers)."""
    return {
        "items": _without_leading_divider(_rendered(nav.SIDEBAR, context)),
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

    ``section.carry`` entscheidet, ob der Bereichszustand (heute: der Zeitraum
    der Statistik, Refs #1804 §3.5) an den Reiter-Links mitreist. Ohne
    ``carry`` bleibt die Reiterzeile exakt wie bisher — der Zustandstransport
    ist Opt-in je Bereich, kein Nav-Default.
    """
    area = nav.section(key)
    return {"section": area, "tabs": _rendered(area.tabs, context, carry=_carry_query(area, context))}


@register.inclusion_tag("components/_back_link.html", takes_context=True)
def back_link(context):
    """Der Rücklink der aktuellen Seite (Refs #1802, Konzept #1801 §7.3).

    Auflösung in der Reihenfolge des Konzepts (3.3): Ein von der View gesetztes
    ``context["back_link"]`` (``{"url": …, "label": …}``) gilt — das sind die
    Objekt-Eltern aus Konzept 3.4, deren Heimat ein Objekt statt einer Liste
    ist. Sonst antwortet die Registry über ``request.resolver_match``; liefert
    sie ``None`` (Hub, Reiter, Liste, Assistenten-Schritt), rendert dieser Tag
    **nichts**.

    Bewusst ein Inclusion-Tag statt eines Context-Processors — Begründung im
    Modul-Docstring: ein Prozessor liefe auch auf HTMX-Partials, Downloads und
    Fehlerseiten, wo es gar keine Orientierungszeile gibt.

    Einen zweiten Krümel nimmt der Tag bewusst nicht mehr entgegen (Regel R1):
    ``{% back_link current=case.title %}`` rendert „← Fälle / Wohnungsverlust"
    und wiederholt damit den Objektnamen, der zwei Zeilen tiefer als H1 steht.
    Der Parameter ist entfernt statt nur ungenutzt, damit ein Rückfall zur
    Kette sofort bricht — ``test_navigation_backlink`` prüft ihn zusätzlich
    statisch über alle Templates.
    """
    override = context.get("back_link")
    if isinstance(override, dict) and override.get("url"):
        return {"url": override["url"], "label": override.get("label", "")}
    link = nav.back_link_for(_current_url_name(context))
    if link is None:
        return {}
    return {"url": reverse(link.url_name), "label": link.label}


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
