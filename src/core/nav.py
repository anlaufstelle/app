"""Navigations-Registry — die **eine** Quelle für Hauptnavigation und Bereichs-Reiter.

Refs #1813 (Konzept [#1801]). Vor dieser Registry lagen Label, Ziel-Route,
Sichtbarkeits-Gate und die „welche Seiten machen diesen Eintrag aktiv"-Familie
als CSV-Strings in ``partials/_navigation_desktop.html`` und
``partials/_navigation_mobile.html`` — dreifach gepflegt (Desktop-Eintrag,
Mehr-Eintrag, Mehr-Highlight-Katalog) und ohne Zwang, neue Routen nachzuziehen.
Genau das ging wiederholt schief: M4 vergaß ``import_detail`` im Mobil-Katalog,
der Sidebar-Eintrag „Systembereich" vergaß vier der neun ``/system/``-Reiter.

Zwei harte Regeln dieses Moduls:

1. **Niemals eine ``User``-Instanz neu laden.** Gates sind Attributnamen, und
   :func:`is_visible` liest sie mit ``getattr`` von *derselben* Instanz, die der
   Request trägt. ``User.has_open_deletion_requests`` ist eine
   ``cached_property`` (Refs #1755) — beide Navigationsleisten werten das Gate
   pro Seite aus, und nur weil sie sich eine Instanz teilen, bleibt es bei
   **einer** Query. Ein ``User.objects.get()``/``refresh_from_db()`` hier würde
   diese Garantie still zerstören (Guard: ``test_deletion_request_nav_gate.py``).
2. **Familien sind Mengen, keine Strings.** ``{% if x in 'a,b,c' %}`` ist im
   Django-Template ein *Substring*-Test — ``dashboard`` traf so
   ``system_dashboard``. Mit ``frozenset`` ist die Element-Semantik strukturell
   garantiert statt per ``|split:','``-Filter hergestellt.

**mypy:** ``make typecheck`` läuft auf ``src/core/services``, ``forms``,
``packages`` und ``management/commands`` — dieses Modul liegt bewusst außerhalb.
Es hat keinen Datenfluss, den mypy schützen könnte (eingefrorene Dataclasses mit
Literalen plus vier reine Prädikate); die real drohenden Fehlerklassen
(Gate-Tippfehler, ``url_name``-Tippfehler, String-statt-Menge) fängt
``src/tests/test_nav_registry.py``.

[#1801]: https://github.com/anlaufstelle/app/issues/1801
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    """Ein Navigations-Eintrag (Sidebar-Zeile, Mehr-Menü-Zeile oder Bereichs-Reiter).

    ``family`` sind die ``url_name``\\ s **ohne** Namespace, auf denen der Eintrag
    als aktiv markiert wird — inklusive Detail-/Edit-/Review-Geschwistern.
    ``gates`` sind ODER-verknüpfte ``User``-Attributnamen.
    """

    key: str
    label: str | Promise
    url_name: str = ""
    url: str = ""  # literaler Pfad, wenn kein url_name existiert (z.B. /admin-mgmt/)
    family: frozenset[str] = frozenset()
    gates: tuple[str, ...] = ()
    testid: str = ""  # Sidebar-Marker; leer = kein data-testid (Ist-Zustand)
    mobile_testid: str = ""  # Marker im mobilen „Mehr"-Menü
    query: str = ""  # optionaler Query-String, z.B. "view=berichte"
    icon: str = ""  # Schlüssel in partials/_nav_icons.html
    external: bool = False  # target="_blank" rel="noopener"
    badge: str = ""  # Zähler-Block in partials/_nav_sidebar_items.html
    divider_before: bool = False  # Trennlinie über dem Eintrag (Sidebar-Gliederung)


@dataclass(frozen=True)
class NavSection:
    """Ein Bereich mit eigener Reiterzeile (``/system/``, später die Hubs)."""

    key: str
    label: str | Promise
    aria_label: str | Promise
    tabs: tuple[NavItem, ...]  # tabs[0] = Landing/Übersicht
    testid: str = ""
    subtitle: str | Promise = ""
    icon: str = ""


# ---------------------------------------------------------------------------
# Prädikate — rein, ohne DB-Zugriff, ohne Neuladen des Users (Regel 1)
# ---------------------------------------------------------------------------


def is_visible(item: NavItem, user) -> bool:
    """True, wenn mindestens ein Gate des Eintrags auf ``user`` wahr ist."""
    return any(getattr(user, gate, False) for gate in item.gates)


def visible_items(items, user) -> tuple[NavItem, ...]:
    """Die sichtbaren Einträge aus ``items``, in unveränderter Reihenfolge."""
    return tuple(item for item in items if is_visible(item, user))


def section_gates(section: NavSection) -> tuple[str, ...]:
    """Gates des Bereichs = Vereinigung der Reiter-Gates (Reihenfolge stabil).

    Damit gilt „Bereich sichtbar ⟺ mindestens ein Reiter sichtbar" **per
    Konstruktion** statt als gepflegte Doppelung.
    """
    return tuple(dict.fromkeys(chain.from_iterable(tab.gates for tab in section.tabs)))


def section_is_visible(section: NavSection, user) -> bool:
    return any(getattr(user, gate, False) for gate in section_gates(section))


def section_family(section: NavSection) -> frozenset[str]:
    """Alle ``url_name``\\ s des Bereichs — die Familie seines Sidebar-Ankers."""
    return frozenset(chain.from_iterable(tab.family for tab in section.tabs))


def first_visible_tab(section: NavSection, user) -> NavItem | None:
    """Der erste für ``user`` sichtbare Reiter — Ziel der Einzelreiter-Weiterleitung.

    Wer den Übersichts-Reiter nicht sieht (Fachkraft mit eigenem Löschantrag,
    Trägerin des Rechts „Löschbestätigung" ohne Leitungsrolle), bekommt keine
    leere Hülle, sondern direkt seine einzige Seite. Abgeleitet statt
    handgeschrieben: ändern sich Reiter-Gates, zieht das Ziel automatisch mit.
    """
    for tab in section.tabs:
        if is_visible(tab, user):
            return tab
    return None


# ---------------------------------------------------------------------------
# Bereiche mit Reiterzeile
# ---------------------------------------------------------------------------

SYSTEM = NavSection(
    key="system",
    label=_("Systembereich"),
    aria_label=_("Systembereich-Navigation"),
    testid="subnav-system",
    tabs=(
        NavItem(
            key="system_dashboard",
            label=_("Übersicht"),
            url_name="core:system_dashboard",
            family=frozenset({"system_dashboard"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_organization",
            label=_("Organisation"),
            url_name="core:system_organization",
            family=frozenset({"system_organization"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_audit_list",
            label=_("Audit-Log"),
            url_name="core:system_audit_list",
            family=frozenset({"system_audit_list", "system_audit_detail"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_lockout_list",
            label=_("Sperrkonten"),
            url_name="core:system_lockout_list",
            family=frozenset({"system_lockout_list", "system_unlock"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_maintenance",
            label=_("Wartung"),
            url_name="core:system_maintenance",
            family=frozenset({"system_maintenance"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_retention",
            label=_("Retention"),
            url_name="core:system_retention",
            family=frozenset({"system_retention"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_vvt",
            label=_("VVT"),
            url_name="core:system_vvt",
            family=frozenset({"system_vvt"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_legal_hold_list",
            label=_("Legal Holds"),
            url_name="core:system_legal_hold_list",
            family=frozenset({"system_legal_hold_list"}),
            gates=("is_super_admin",),
        ),
        NavItem(
            key="system_compliance",
            label=_("Compliance"),
            url_name="core:system_compliance",
            family=frozenset({"system_compliance"}),
            gates=("is_super_admin",),
        ),
    ),
)

SYSTEM_FAMILY = section_family(SYSTEM)


# ---------------------------------------------------------------------------
# Hauptnavigation (Desktop-Sidebar) — Reihenfolge = Renderreihenfolge
# ---------------------------------------------------------------------------

# Regel N3 (Refs #1801 §3.2): nach der Vervollständigung der Rollen-Einstiege
# hat nur noch die Leitung eine eigene Arbeitszentrale — Anwendungsbetreuung und
# Systemadministration bekämen unter /start/ nur einen Redirect. Das Gate lebt
# als ``User.sees_arbeitszentrale`` am Modell, damit Nav und View dieselbe
# Bedingung auswerten.
ARBEITSZENTRALE = NavItem(
    key="dashboard",
    label=_("Arbeitszentrale"),
    url_name="core:dashboard",
    family=frozenset({"dashboard"}),
    gates=("sees_arbeitszentrale",),
    testid="nav-dashboard",
    icon="dashboard",
)
ZEITSTROM = NavItem(
    key="zeitstrom",
    label=_("Zeitstrom"),
    url_name="core:zeitstrom",
    family=frozenset({"zeitstrom"}),
    gates=("is_assistant_or_above",),
    icon="zeitstrom",
)
AUFGABEN = NavItem(
    key="workitems",
    label=_("Aufgaben"),
    url_name="core:workitem_inbox",
    family=frozenset({"workitem_inbox", "workitem_detail", "workitem_create", "workitem_update"}),
    gates=("is_assistant_or_above",),
    icon="workitems",
    badge="workitems",
)
PERSONEN = NavItem(
    key="clients",
    label=_("Personen"),
    url_name="core:client_list",
    family=frozenset({"client_list", "client_detail", "client_create", "client_update"}),
    gates=("is_assistant_or_above",),
    icon="clients",
)
FAELLE = NavItem(
    key="cases",
    label=_("Fälle"),
    url_name="core:case_list",
    family=frozenset({"case_list", "case_detail", "case_create", "case_update"}),
    gates=("is_staff_or_above",),
    icon="cases",
)
DATEIEN = NavItem(
    key="attachments",
    label=_("Dateien"),
    url_name="core:attachment_list",
    family=frozenset({"attachment_list"}),
    gates=("is_assistant_or_above",),
    testid="nav-attachments",
    icon="attachments",
)
STATISTIK = NavItem(
    key="statistics",
    label=_("Statistik"),
    url_name="core:statistics",
    family=frozenset(
        {
            "statistics",
            "statistics_csv_export",
            "statistics_pdf_export",
            "statistics_sample_report_1_export",
        }
    ),
    gates=("is_lead_or_admin",),
    icon="statistics",
)
# Refs #1736 (B): Gate am View-Gate ausgerichtet — Leitung/Admin, Träger:innen
# von „Löschbestätigung" und wer selbst einen offenen Antrag gestellt hat.
LOESCHANTRAEGE = NavItem(
    key="deletion-requests",
    label=_("Löschanträge"),
    url_name="core:deletion_request_list",
    family=frozenset({"deletion_request_list", "deletion_review"}),
    gates=("can_access_deletion_requests",),
    testid="nav-deletion-requests",
    mobile_testid="mobile-nav-deletion-requests",
    icon="deletion",
)
# Refs #1060 (M4/ADR-041): Berichtigungen prüfen ist Leitungsarbeit — Anträge
# STELLEN geht für staff+ direkt aus der Personenakte.
BERICHTIGUNGEN = NavItem(
    key="rectification-requests",
    label=_("Berichtigungen"),
    url_name="core:rectification_list",
    family=frozenset({"rectification_list", "rectification_review"}),
    gates=("is_lead_or_admin",),
    testid="nav-rectification-requests",
    mobile_testid="mobile-nav-rectification-requests",
    icon="rectification",
)
LOESCHFRISTEN = NavItem(
    key="retention",
    label=_("Löschfristen"),
    url_name="core:retention_dashboard",
    family=frozenset({"retention_dashboard"}),
    gates=("is_lead_or_admin",),
    icon="retention",
)
# Refs #1060 (M4): Masseningest ist Anwendungsbetreuung — ADMIN_ONLY, konsistent
# mit der AuthZ-Matrix (import_* in _authz_expectations).
DATENIMPORT = NavItem(
    key="import",
    label=_("Datenimport"),
    url_name="core:import_list",
    family=frozenset({"import_list", "import_create", "import_detail", "import_mapping", "import_preview"}),
    gates=("is_facility_admin",),
    testid="nav-import",
    mobile_testid="mobile-nav-import",
    icon="import",
)
AUDIT_LOG = NavItem(
    key="audit",
    label=_("Audit-Log"),
    url_name="core:audit_log",
    family=frozenset({"audit_log", "audit_detail"}),
    gates=("is_facility_admin",),
    icon="audit",
)
DSGVO_PAKET = NavItem(
    key="dsgvo",
    label=_("DSGVO-Paket"),
    url_name="core:dsgvo_package",
    family=frozenset({"dsgvo_package", "dsgvo_document"}),
    gates=("is_facility_admin",),
    icon="dsgvo",
)
# Refs #1801 (§4.4): „Konfiguration" war neben „Feldvorlagen" nichtssagend —
# #1717 hat die beiden getrennt, aber nicht benannt. Im Bereich „Verwaltung"
# benennt der Reiter jetzt seinen Inhalt; die Seiten-H1 heißt ohnehin schon so.
KONFIGURATION = NavItem(
    key="config",
    label=_("Dokumentationstypen"),
    url_name="core:config_doctype_list",
    family=frozenset(
        {
            "config_doctype_list",
            "config_doctype_create",
            "config_doctype_detail",
            "config_doctype_update",
            "config_doctype_delete",
            "config_fieldgroup_create",
            "config_fieldgroup_update",
        }
    ),
    gates=("is_facility_admin",),
    testid="nav-config",
    icon="config",
)
FELDVORLAGEN = NavItem(
    key="config-fieldtemplates",
    label=_("Feldvorlagen"),
    url_name="core:config_fieldtemplate_list",
    family=frozenset(
        {
            "config_fieldtemplate_list",
            "config_fieldtemplate_create",
            "config_fieldtemplate_update",
            "config_fieldtemplate_delete",
            "config_fieldtemplate_toggle",
        }
    ),
    gates=("is_facility_admin",),
    testid="nav-config-fieldtemplates",
    icon="fieldtemplates",
)
ADMINISTRATION = NavItem(
    key="admin-mgmt",
    label=_("Administration"),
    url="/admin-mgmt/",
    gates=("is_facility_admin",),
    icon="admin",
    external=True,
)
SYSTEMBEREICH = NavItem(
    key="system",
    label=_("Systembereich"),
    url_name="core:system_dashboard",
    # Die Familie ist die Vereinigung ALLER Reiter des Bereichs — genau das ist
    # der Punkt der Registry: ein neuer /system/-Reiter zieht den Sidebar-
    # Aktivzustand automatisch mit (Refs #1813).
    family=SYSTEM_FAMILY,
    gates=("is_super_admin",),
    testid="nav-system",
    mobile_testid="mobile-nav-system",
    icon="system",
)
ADMINISTRATION_SUPER = NavItem(
    key="admin-mgmt-super",
    label=_("Administration"),
    url="/admin-mgmt/",
    gates=("is_super_admin",),
    testid="nav-admin-super",
    icon="admin",
    external=True,
)

# ---------------------------------------------------------------------------
# Bereichs-Hubs (Refs #1801 §4) — reines Navigations-Chrom
# ---------------------------------------------------------------------------
#
# Die Reiter sind **dieselben Objekte** wie die bisherigen Einzel-Einträge:
# Label, Gate und Familie werden nicht zweitgepflegt. Damit sind die Hubs
# genau das, was sie sein sollen — eine andere Anordnung derselben Seiten,
# ohne URL-Umzug und ohne neue Rechte. Reiter[0] ist die Übersicht (Landing).

DATENSCHUTZ_UEBERSICHT = NavItem(
    key="datenschutz_uebersicht",
    label=_("Übersicht"),
    url_name="core:datenschutz_uebersicht",
    family=frozenset({"datenschutz_uebersicht"}),
    gates=("is_lead_or_admin",),
)
VERWALTUNG_UEBERSICHT = NavItem(
    key="verwaltung_uebersicht",
    label=_("Übersicht"),
    url_name="core:verwaltung_uebersicht",
    family=frozenset({"verwaltung_uebersicht"}),
    gates=("is_facility_admin",),
)

DATENSCHUTZ = NavSection(
    key="datenschutz",
    label=_("Datenschutz"),
    subtitle=_("Betroffenenrechte, Fristen und Nachweise."),
    aria_label=_("Datenschutz-Navigation"),
    testid="subnav-datenschutz",
    tabs=(
        DATENSCHUTZ_UEBERSICHT,
        LOESCHANTRAEGE,
        BERICHTIGUNGEN,
        LOESCHFRISTEN,
        AUDIT_LOG,
        DSGVO_PAKET,
    ),
)
# F1 (Konfigurationspakete + Einrichtungs-Export) ist Folgeticket — der Hub
# startet bewusst mit vier Reitern statt mit zwei leeren Versprechen.
VERWALTUNG = NavSection(
    key="verwaltung",
    label=_("Verwaltung"),
    subtitle=_("Anwendungsbetreuung: Formulare, Felder, Datenübernahme und Zugänge."),
    aria_label=_("Verwaltungs-Navigation"),
    testid="subnav-verwaltung",
    tabs=(
        VERWALTUNG_UEBERSICHT,
        KONFIGURATION,
        FELDVORLAGEN,
        DATENIMPORT,
    ),
)

SECTIONS: tuple[NavSection, ...] = (SYSTEM, DATENSCHUTZ, VERWALTUNG)

DATENSCHUTZ_FAMILY = section_family(DATENSCHUTZ)
VERWALTUNG_FAMILY = section_family(VERWALTUNG)

# Die Sidebar-Anker der Hubs. Familie und Gate sind **abgeleitet** — ein neuer
# Reiter zieht Aktivzustand und Sichtbarkeit automatisch mit (Regel N2), genau
# wie beim Systembereich.
DATENSCHUTZ_HUB = NavItem(
    key="datenschutz",
    label=DATENSCHUTZ.label,
    url_name="core:datenschutz_uebersicht",
    family=DATENSCHUTZ_FAMILY,
    gates=section_gates(DATENSCHUTZ),
    testid="nav-datenschutz",
    mobile_testid="mobile-nav-datenschutz",
    icon="datenschutz",
    divider_before=True,
)
VERWALTUNG_HUB = NavItem(
    key="verwaltung",
    label=VERWALTUNG.label,
    url_name="core:verwaltung_uebersicht",
    family=VERWALTUNG_FAMILY,
    gates=section_gates(VERWALTUNG),
    testid="nav-verwaltung",
    mobile_testid="mobile-nav-verwaltung",
    icon="config",
)


# Ziel-IA (Refs #1801 §3.2): der operative Block steht immer VOLLSTÄNDIG vor
# Statistik und den Hubs — Alltag oben, Anlass im Hub (Regel N1). Aus 18 flachen
# Einträgen (Anwendungsbetreuung sah 15) werden höchstens 8.
#
# „Administration ↗" bleibt nur für die Systemadministration ein Sidebar-
# Eintrag; für die Anwendungsbetreuung ist der Django-Admin eine markierte
# Zeile auf der Verwaltungs-Übersicht („öffnet die technische Administration ·
# erfordert Sudo-Modus", Konzept §3.1) — er ist eine eigene Anwendungshülle mit
# eigener Navigation und Sudo-Modus, kein Bereich dieser Anwendung.
SIDEBAR: tuple[NavItem, ...] = (
    ARBEITSZENTRALE,
    ZEITSTROM,
    AUFGABEN,
    PERSONEN,
    FAELLE,
    DATEIEN,
    STATISTIK,
    DATENSCHUTZ_HUB,
    VERWALTUNG_HUB,
    SYSTEMBEREICH,
    ADMINISTRATION_SUPER,
)


# ---------------------------------------------------------------------------
# Mobiles „Mehr"-Menü (Slot 5 der Bottom-Bar)
# ---------------------------------------------------------------------------

# Dieselben Objekte wie in der Sidebar — Label, Gate und Familie werden hier
# NICHT zweitgepflegt; nur Reihenfolge und Auswahl sind eigenständig.
MOBILE_MORE: tuple[NavItem, ...] = (
    FAELLE,
    DATEIEN,
    STATISTIK,
    LOESCHANTRAEGE,
    BERICHTIGUNGEN,
    LOESCHFRISTEN,
    DATENIMPORT,
    AUDIT_LOG,
    DSGVO_PAKET,
    KONFIGURATION,
    FELDVORLAGEN,
    ADMINISTRATION,
    SYSTEMBEREICH,
    ADMINISTRATION_SUPER,
)

# Routen, die den „Mehr"-Button aktiv machen, ohne aus einem Listeneintrag zu
# stammen: die Suche ist ein Overlay-Button (kein href), „Profil" und
# „Abmelden" stehen als feste Fußzeile unter der Liste.
MOBILE_MORE_EXTRA_FAMILY: frozenset[str] = frozenset({"search", "account_profile"})

# Ganze Familien, deren Anker zwar im Mehr-Menü verlinkt ist, die aber bewusst
# NICHT zum „Mehr"-Highlight beitragen.
MOBILE_MORE_EXCLUDED_KEYS: dict[str, str] = {
    "system": (
        "Redundanz-Link für super_admin: die mobile Bottom-Bar trägt für diese Rolle einen eigenen "
        "Systembereich-Slot mit derselben Familie. Beides im Katalog hieße doppelte Hervorhebung — "
        "genau die Regressionsklasse, die test_navigation_active_state.py für die Sidebar abdeckt."
    ),
}

# Einzelne Familienmitglieder ohne „du bist hier"-Zustand.
MOBILE_MORE_EXCLUDED_ROUTES: dict[str, str] = {
    "statistics_csv_export": (
        "Reiner Download-Endpunkt (CSVExportView, safe_download_response) — kein gerendertes HTML."
    ),
    "statistics_pdf_export": (
        "Reiner Download-Endpunkt (PDFExportView, safe_download_response) — kein gerendertes HTML."
    ),
    "statistics_sample_report_1_export": (
        "Reiner Download-Endpunkt (SampleReport1ExportView, safe_download_response) — kein gerendertes HTML."
    ),
}


def mobile_more_catalog() -> frozenset[str]:
    """``url_name``-Katalog, der den mobilen „Mehr"-Button aktiv markiert."""
    catalog = frozenset(
        chain.from_iterable(item.family for item in MOBILE_MORE if item.key not in MOBILE_MORE_EXCLUDED_KEYS)
    )
    return (catalog | MOBILE_MORE_EXTRA_FAMILY) - frozenset(MOBILE_MORE_EXCLUDED_ROUTES)


# ---------------------------------------------------------------------------
# Begründungspflichtige Ausnahmen gegenüber der URLconf
# ---------------------------------------------------------------------------

# Routen unter einem Navigations-Präfix, die strukturell keinen Nav-Zustand
# haben. Guard: src/tests/test_nav_registry.py.
KNOWN_NON_NAV_ROUTES: dict[str, str] = {
    # /system/
    "system_audit_export": "Download-Endpunkt (CSV/JSON via ?format=) hinter dem Audit-Reiter, keine eigene Seite.",
    # /config/
    "config_doctype_field_add": "HTMX-Board-Mutation (POST) im Dokumentationstyp-Detail, kein Seitenaufruf.",
    "config_doctype_field_remove": "HTMX-Board-Mutation (POST) im Dokumentationstyp-Detail, kein Seitenaufruf.",
    "config_fieldgroup_delete": "POST-only (ConfigFieldGroupDeleteView), antwortet mit dem Board-Partial.",
    "config_reorder_blocks": "Drag-and-Drop-Batch-Reorder (POST), antwortet mit dem Board-Partial.",
    "config_reorder_fields": "Drag-and-Drop-Batch-Reorder (POST), antwortet mit dem Board-Partial.",
    "config_move_field": "Feld-Verschiebung (POST), antwortet mit dem Board-Partial.",
    "config_condition_create": "Regel-Editor-Mutation (POST), antwortet mit dem Bedingungs-Board.",
    "config_condition_update": "Regel-Editor-Mutation (POST), antwortet mit dem Bedingungs-Board.",
    "config_condition_delete": "Regel-Editor-Mutation (POST), antwortet mit dem Bedingungs-Board.",
    "config_condition_board": "HTMX-Refresh des Bedingungs-Boards, kein eigenständiges Dokument.",
    "config_condition_value_input": "HTMX-Teilfeld des Regel-Editors, kein eigenständiges Dokument.",
    "config_condition_operator_input": "HTMX-Teilfeld des Regel-Editors, kein eigenständiges Dokument.",
    # /import/
    "import_commit": "POST-only Übernahme-Schritt des Assistenten, leitet auf import_detail weiter.",
    "import_abort": "POST-only Abbruch des Assistenten, leitet auf import_list weiter.",
    # /retention/
    "retention_bulk_approve": "POST-only Massenaktion der Löschfristen-Übersicht.",
    "retention_bulk_defer": "POST-only Massenaktion der Löschfristen-Übersicht.",
    "retention_bulk_reject": "POST-only Massenaktion der Löschfristen-Übersicht.",
    # /statistics/
    "statistics_chart_data": "JSON-Endpunkt für die Diagramme der Statistikseite, kein Seitenaufruf.",
    "statistics_report_panel": "HTMX-Partial ohne base.html; antwortet ohne ?backend= mit 400 — als Seite unbrauchbar.",
    "statistics_report_export": "Generischer Download-Endpunkt der Berichts-Plugins.",
    "statistics_external_report": (
        "Verwaiste Seite: aktuell existiert kein einziger Link darauf (nur per Direkt-URL erreichbar). "
        "Etappe 3 von #1801 gibt ihr einen Reiter in der Statistik-Reiterzeile."
    ),
    "statistics_report_releases": (
        "Verwaiste Seite (K4a, Refs #1770): nur aus dem Berichts-Panel heraus verlinkt, kein Nav-Eintrag. "
        "Etappe 3 von #1801 gibt ihr einen Reiter in der Statistik-Reiterzeile."
    ),
}


def all_items() -> tuple[NavItem, ...]:
    """Alle registrierten Einträge, dedupliziert (``MOBILE_MORE`` teilt Objekte)."""
    seen: dict[str, NavItem] = {}
    for entry in chain(SIDEBAR, MOBILE_MORE, *(section.tabs for section in SECTIONS)):
        seen.setdefault(entry.key, entry)
    return tuple(seen.values())


def item(key: str) -> NavItem:
    """Ein Eintrag per Schlüssel — für die handgebauten Slots der mobilen Bottom-Bar.

    Wirft ``KeyError`` statt still ein leeres Ergebnis zu liefern: ein
    Tippfehler im Template soll auffallen, nicht den Aktiv-Zustand verschlucken.
    """
    for entry in all_items():
        if entry.key == key:
            return entry
    raise KeyError(f"Unbekannter Navigations-Schlüssel: {key!r}")


def section(key: str) -> NavSection:
    """Ein Bereich per Schlüssel — für ``{% subnav "datenschutz" %}``.

    Wirft ``KeyError`` wie :func:`item`: ein Tippfehler im Template soll die
    Reiterzeile hörbar zerbrechen, nicht still leer rendern.
    """
    for entry in SECTIONS:
        if entry.key == key:
            return entry
    raise KeyError(f"Unbekannter Bereichs-Schlüssel: {key!r}")
