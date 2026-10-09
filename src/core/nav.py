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

[#1801]: #1801
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
    query: str = ""  # optionaler Query-String, z.B. "ansicht=datensaetze"
    # Refs #1804 (F5): frueher ausgelieferte Schreibweisen desselben Reiters.
    # Sie werden nie verlinkt, machen den Reiter aber aktiv — sonst zeigte ein
    # Bookmark von gestern eine Reiterzeile ohne einen einzigen aktiven Reiter,
    # und der „genau ein aktiver Reiter"-Guard braeche leise.
    query_aliases: tuple[str, ...] = ()
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
    # Refs #1804 (Konzept §3.5): Query-Parameter, die beim Reiterwechsel an
    # JEDEN Reiter dieses Bereichs mitgehen. Leer heisst „Reiterwechsel ist ein
    # Ortswechsel, kein Zustandstransport" — das ist die Vorgabe und gilt fuer
    # ``/system/`` und beide Hubs unveraendert. Der Statistik-Bereich ist der
    # eine Bereich mit EINEM Zeitraum ueber alle Reiter; ohne Mitfuehrung
    # kostete jeder Reiterklick die Zeitraum-Auswahl, und das provoziert den
    # teuersten Fehler des Bereichs: „Am Bildschirm stand Q1, exportiert wurde
    # Q2." Die Liste ist zugleich die **Whitelist** fuer den Roh-Durchreich-
    # Pfad (siehe ``nav_tags._carry_query``) — nur benannte Parameter reisen,
    # nie beliebige Query-Bestandteile.
    carry: tuple[str, ...] = ()


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


# Refs #1801 §7.1: drei Statistik-Seiten waren praktisch unerreichbar —
# ``/statistics/external/`` hatte **keinen einzigen Link** im ganzen Repo, die
# Freigabe-Übersicht nur einen Textlink im Berichts-Panel, und die
# Berichts-Sektion hing unten am Dashboard. Die Reiterzeile gibt ihnen eine
# Heimat, ohne eine einzige Route zu verschieben.
#
# Der Reiter „Datensatz-Berichte" ist der Sonderfall (Befund N2): sein
# natürliches Ziel ``core:statistics_report_panel`` ist ein HTMX-Partial ohne
# ``base.html``, das ohne ``?backend=`` mit 400 antwortet — als Seite
# unbrauchbar. Statt einer neuen Route trägt er ``?ansicht=datensaetze`` auf
# ``core:statistics``; das ist das Hausmuster der Bereichs-Konsolidierung
# (Zeitstrom ``?view=``, Refs #1124) und hält Deep-Links abwärtskompatibel.
#
# Refs #2505: the former "Externer Bericht" tab is gone. Its address only
# redirects to the "Allgemeine Kennzahlen" report card, so it is not a tab
# and belongs to no family; the report export is the only way out.
STATISTIK_UEBERSICHT = NavItem(
    key="statistik_uebersicht",
    label=_("Übersicht"),
    url_name="core:statistics",
    # Der CSV-Endpunkt (interne Ereignisliste) gehört zur Übersicht, nicht zum
    # Berichte-Reiter: er hängt an der internen Exportleiste des Dashboards.
    family=frozenset({"statistics", "statistics_csv_export"}),
    gates=("is_lead_or_admin",),
)
STATISTIK_DATENSATZ_BERICHTE = NavItem(
    key="statistik_berichte",
    label=_("Datensatz-Berichte"),
    url_name="core:statistics",
    query="ansicht=datensaetze",
    query_aliases=("view=berichte",),
    # Dieselbe Route wie die Übersicht. Der Aktiv-Zustand unterscheidet die
    # beiden über ``query`` (``nav_tags._rendered``): trüge dieser Reiter die
    # Route nicht in seiner Familie, wäre er nie aktiv — trügen beide sie ohne
    # Query-Prüfung, leuchteten auf ``?ansicht=datensaetze`` beide.
    family=frozenset({"statistics"}),
    gates=("is_lead_or_admin",),
)
STATISTIK_FREIGABEN = NavItem(
    key="statistik_releases",
    label=_("Freigaben"),
    url_name="core:statistics_report_releases",
    family=frozenset({"statistics_report_releases"}),
    gates=("is_lead_or_admin",),
)

STATISTIK_SECTION = NavSection(
    key="statistik",
    label=_("Statistik"),
    aria_label=_("Statistik-Navigation"),
    testid="subnav-statistik",
    # Refs #1804 (§3.5): EIN Zeitraum fuer den ganzen Bereich. Alle Reiter
    # beantworten dieselbe Frage ueber denselben Zeitraum in verschiedenen
    # Fassungen — getrennte Zeitraeume je Reiter erschwerten genau den
    # Vergleich, um dessentwillen die Freigabe-Uebersicht existiert.
    #
    # Beide Schreibweisen stehen drin: kanonisch deutsch (``zeitraum``/``von``/
    # ``bis``/``jahr``) und die vom Zeitraum-Formular ausgelieferten
    # ``date_from``/``date_to``/``year``. Die Whitelist muss den Roh-Durchreich-
    # Pfad des Freigaben-Reiters abdecken, der den Zeitraum bewusst NICHT parst.
    # ``doktyp`` fehlt absichtlich: der Filter existiert nur auf der Uebersicht,
    # und ein Reiter, der ihn mitschleppte, verspraeche den anderen dreien eine
    # Verengung, die dort niemand auswertet.
    carry=("zeitraum", "period", "von", "bis", "date_from", "date_to", "jahr", "year"),
    tabs=(
        STATISTIK_UEBERSICHT,
        STATISTIK_DATENSATZ_BERICHTE,
        STATISTIK_FREIGABEN,
    ),
)
STATISTIK_FAMILY = section_family(STATISTIK_SECTION)


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
    mobile_testid="mobile-nav-dashboard",
    icon="dashboard",
)
ZEITSTROM = NavItem(
    key="zeitstrom",
    label=_("Zeitstrom"),
    url_name="core:zeitstrom",
    # Ereignis-Vollseiten gehören zum Zeitstrom-Anker (Verify-Befund #1813,
    # dieselbe Klasse wie der audit_detail-Fix): Detail, Neu, Edit und die
    # GET-Lösch-Bestätigung sind "du bist hier"-Zustände.
    family=frozenset({"zeitstrom", "event_detail", "event_create", "event_update", "event_delete"}),
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
    # Refs #1874: Die Bestaetigungsseite des ersatzlosen Loeschens gehoert in
    # die Familie — sie ist eine Vollseite unter der Dateien-Uebersicht und
    # bekommt von hier ihren Rücklink „← Dateien". Der Nav-Eintrag selbst bleibt
    # dadurch auch waehrend der Bestaetigung aktiv markiert.
    family=frozenset({"attachment_list", "attachment_delete"}),
    gates=("is_assistant_or_above",),
    testid="nav-attachments",
    icon="attachments",
)
STATISTIK = NavItem(
    key="statistics",
    label=STATISTIK_SECTION.label,
    url_name="core:statistics",
    # Abgeleitet wie bei „Systembereich" und den Hubs: ein neuer Statistik-
    # Reiter zieht den Sidebar-Aktivzustand automatisch mit. Vorher fehlten
    # hier die beiden verwaisten Seiten — auf ihnen erlosch die Ortsanzeige.
    family=STATISTIK_FAMILY,
    gates=("is_lead_or_admin",),
    # Refs #1804 (§1.10): der einzige Anker seiner Umgebung ohne Marker — der
    # Aktiv-State-Guard greift über ``data-testid`` und sah ihn deshalb nicht.
    testid="nav-statistik",
    icon="statistics",
)
# Refs #1736 (B): Gate am View-Gate ausgerichtet — Leitung/Admin, Träger:innen
# von „Löschbestätigung" und wer selbst einen offenen Antrag gestellt hat.
LOESCHANTRAEGE = NavItem(
    key="deletion-requests",
    label=_("Löschanträge"),
    url_name="core:deletion_request_list",
    # ``deletion_withdraw`` gehoert in die Familie, damit der Dialog seine
    # Ortsmarkierung behaelt — seinen Rueckweg liefert die View aber selbst
    # (``VIEW_PROVIDED_BACKLINK`` unten): der Familien-Rueckweg zeigte auf die
    # gate-abhaengige Liste und endete fuer eine reine Antragstellerin im 403
    # (Refs #2040/#1736 B).
    family=frozenset({"deletion_request_list", "deletion_review", "deletion_withdraw"}),
    gates=("can_access_deletion_requests",),
    testid="nav-deletion-requests",
    icon="deletion",
    # Refs #1984: Zaehler offener Loeschantraege (Sichtmenge der Liste) —
    # gerendert in partials/_subnav.html ueber partials/_nav_badge.html.
    badge="deletion_requests",
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
    icon="rectification",
    # Refs #1984: Zaehler offener Berichtigungsantraege (pending-Definition
    # der Liste) — gerendert in partials/_subnav.html.
    badge="rectification_requests",
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
HILFE = NavItem(
    key="help",
    label=_("Hilfe"),
    url_name="core:help_overview",
    family=frozenset({"help_overview", "help_page"}),
    gates=("is_authenticated",),
    testid="nav-help",
    mobile_testid="mobile-nav-help",
    icon="help",
    divider_before=True,
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
EINSTELLUNGEN = NavItem(
    key="facility-settings",
    label=_("Einstellungen"),
    url_name="core:facility_settings",
    family=frozenset({"facility_settings"}),
    gates=("is_facility_admin",),
    testid="nav-facility-settings",
    icon="settings",
)
ZUGAENGE = NavItem(
    key="user-access",
    label=_("Zugänge"),
    url_name="core:user_access_list",
    family=frozenset(
        {
            "user_access_list",
            "user_access_create",
            "user_access_update",
            "user_access_invite",
            "user_access_unlock",
            "user_access_mfa_reset",
        }
    ),
    gates=("is_facility_admin",),
    testid="nav-user-access",
    icon="admin",
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
# F1 (Konfigurationspakete + Einrichtungs-Export) ist Folgeticket. #1885
# ergaenzt den Einstellungen-Reiter ueber den bestehenden Audit-Service.
VERWALTUNG = NavSection(
    key="verwaltung",
    label=_("Verwaltung"),
    subtitle=_("Anwendungsbetreuung: Einstellungen, Formulare, Felder, Datenübernahme und Zugänge."),
    aria_label=_("Verwaltungs-Navigation"),
    testid="subnav-verwaltung",
    tabs=(
        VERWALTUNG_UEBERSICHT,
        EINSTELLUNGEN,
        ZUGAENGE,
        KONFIGURATION,
        FELDVORLAGEN,
        DATENIMPORT,
    ),
)

SECTIONS: tuple[NavSection, ...] = (SYSTEM, STATISTIK_SECTION, DATENSCHUTZ, VERWALTUNG)

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
    # Refs #1984: Summen-Zaehler beider Antragssorten am Hub-Anker — sonst
    # waeren die Zaehler erst nach dem Klick in den Hub sichtbar.
    badge="datenschutz_review",
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
    HILFE,
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
#
# Ziel-IA mobil (Refs #1801 §5.2): dieselbe Gliederung wie die Sidebar, nur ohne
# die vier Einträge, die als Bottom-Bar-Slot schon da sind. Aus bis zu 14 Zeilen
# werden höchstens sechs. Zwei Änderungen tragen das:
#
# * **Arbeitszentrale als erste Zeile.** Sie war mobil bisher gar nicht
#   erreichbar — ein Desktop-Eintrag ohne Gegenstück. Kein Slot-Tausch: die fünf
#   Slots sind erprobt, und die Arbeitszentrale ist ein Orientierungs-, kein
#   Erfassungseinstieg (Konzept §5.3).
# * **Die acht Datenschutz-/Verwaltungs-Einträge werden zwei Hub-Zeilen.**
#   Dieselben Objekte wie in der Sidebar, dieselben abgeleiteten Gates — die
#   Reiter dahinter sind die alten Ziele, unverändert adressiert.
#
# Bewusst **nicht** in dieser Liste:
# * „Administration ↗" für die Anwendungsbetreuung — der Django-Admin ist für
#   diese Rolle eine markierte Zeile auf der Verwaltungs-Übersicht (§3.1); mobil
#   wäre er ein Absprung in eine fremde Anwendungshülle mit eigener Navigation
#   und Sudo-Modus. Für die Systemadministration bleibt er die einzige Zeile.
# * „Systembereich" — die Systemrolle hat dafür einen eigenen Bottom-Bar-Slot;
#   beides hieße, denselben Ort zweimal anzubieten und beide Marker gleichzeitig
#   leuchten zu lassen.
MOBILE_MORE: tuple[NavItem, ...] = (
    ARBEITSZENTRALE,
    FAELLE,
    DATEIEN,
    STATISTIK,
    DATENSCHUTZ_HUB,
    VERWALTUNG_HUB,
    ADMINISTRATION_SUPER,
    HILFE,
)

# Routen, die den „Mehr"-Button aktiv machen, ohne aus einem Listeneintrag zu
# stammen: die Suche ist ein Overlay-Button (kein href), „Profil" und
# „Abmelden" stehen als feste Fußzeile unter der Liste.
MOBILE_MORE_EXTRA_FAMILY: frozenset[str] = frozenset({"search", "account_profile"})

# Ganze Familien, deren Anker zwar im Mehr-Menü verlinkt ist, die aber bewusst
# NICHT zum „Mehr"-Highlight beitragen.
#
# Derzeit leer: der einzige Fall war der Redundanz-Link „Systembereich", den die
# Systemadministration zusätzlich zu ihrem eigenen Bottom-Bar-Slot im Mehr-Menü
# hatte. Seit der Ziel-IA (Refs #1801 §5.2) steht er dort gar nicht mehr — die
# Ausnahme ist damit gegenstandslos statt still wirkungslos. Der Mechanismus
# bleibt: „im Menü verlinkt, aber ohne Highlight-Beitrag" ist ein Zustand, den
# der nächste Sonderfall wieder braucht, und die Begründungspflicht dafür ist in
# ``test_navigation_mehr_catalog_drift.py`` (inkl. synthetischer Selbsttests)
# festgeschrieben.
MOBILE_MORE_EXCLUDED_KEYS: dict[str, str] = {}

# Einzelne Familienmitglieder ohne „du bist hier"-Zustand.
MOBILE_MORE_EXCLUDED_ROUTES: dict[str, str] = {
    "statistics_csv_export": (
        "Reiner Download-Endpunkt (CSVExportView, safe_download_response) — kein gerendertes HTML."
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
    # /statistics/
    "statistics_external_report": (
        "Redirect-only address of the former external report tab; forwards to the "
        '"Allgemeine Kennzahlen" report card (Refs #2505).'
    ),
    # /events/
    "attachment_download": "Datei-Download-Endpunkt am Ereignis (entschlüsselt, streamt), keine eigene Seite.",
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
    "statistics_report_panel": (
        "HTMX-Partial ohne base.html; antwortet ohne ?backend= mit 400 — als Seite unbrauchbar (Befund N2). "
        "Der Reiter „Datensatz-Berichte“ zeigt deshalb auf core:statistics?ansicht=datensaetze, wo dieses "
        "nachgeladen wird."
    ),
    "statistics_report_export": "Generischer Download-Endpunkt der Berichts-Plugins.",
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


# ---------------------------------------------------------------------------
# Rücklink-Auflösung (Refs #1802, Konzept #1801 §7.3)
# ---------------------------------------------------------------------------
#
# Der Rücklink zeigt zur **Heimat**, nicht zur Herkunft (Regel R2): zu der einen
# Liste bzw. dem einen Reiter, unter dem das Objekt hier geführt wird. Genau das
# sagen die Aktiv-State-Familien schon — sie ein zweites Mal als eigene
# Breadcrumb-Tabelle zu pflegen wäre die nächste Drift (Konzept 1.3). Deshalb
# gibt es hier keine dritte Zuordnungstabelle, sondern nur die Antwort auf die
# beiden Fragen, die die Familien NICHT beantworten:
#
# * Welche Seiten zeigen bewusst **keinen** Rücklink (``BACKLINK_EXEMPT``)?
# * Welche Seiten haben als Heimat ein **Objekt** statt einer Liste
#   (``VIEW_PROVIDED_BACKLINK``)?
#
# Abgrenzung zu ``?next=`` (Regel R3): ``safe_redirect_path()`` beantwortet
# „wohin nach dem Absenden", der Rücklink „wo bin ich zuhause". Beide dürfen
# dasselbe Ziel haben, werden aber nie voneinander abgeleitet.


@dataclass(frozen=True)
class BackLink:
    """Ziel und Beschriftung eines Bereichs-Rücklinks."""

    label: str | Promise
    url_name: str


# Seiten ohne Rücklink (Regel R5: wer Reiter zeigt, zeigt keinen Rücklink).
# Vier Gruppen: Hub-/Rollen-Einstiege, Reiterseiten, operative Listen und die
# Schritte des Import-Assistenten (Regel R7: ein Stepper ist kein Breadcrumb —
# er zeigt Fortschritt auf einer verwerfbaren Strecke, nicht Verortung in einem
# Bestand). Dazu die Konto-/Anmeldeflächen, die außerhalb der Bereichs-IA liegen.
BACKLINK_EXEMPT: frozenset[str] = frozenset(
    {
        # Hub-Übersichten und Rollen-Arbeitszentrale
        "dashboard",
        "datenschutz_uebersicht",
        "verwaltung_uebersicht",
        "system_dashboard",
        # Reiterseiten der Bereiche
        "system_organization",
        "system_audit_list",
        "system_lockout_list",
        "system_maintenance",
        "system_retention",
        "system_vvt",
        "system_legal_hold_list",
        "system_compliance",
        "statistics",
        "statistics_report_releases",
        "dsgvo_package",
        # Operative Listen (die Heimat selbst — ein Rücklink zeigte auf sich)
        "zeitstrom",
        "workitem_inbox",
        "client_list",
        "client_trash",
        "case_list",
        "case_trash",
        "attachment_list",
        "search",
        "deletion_request_list",
        "rectification_list",
        "retention_dashboard",
        "audit_log",
        "user_access_list",
        "config_doctype_list",
        "config_fieldtemplate_list",
        "import_list",
        # Schritte des Import-Assistenten (R7): Stepper + „Abbrechen", sonst nichts.
        "import_create",
        "import_mapping",
        "import_preview",
        # Konto- und Anmeldeflächen außerhalb der Bereichs-IA
        "account_profile",
        "password_change",
        "sudo_mode",
        "mfa_setup",
        "mfa_verify",
        "mfa_backup_codes",
    }
)

# Objekt-Eltern (Konzept 3.4): Heimat ist ein Objekt, nicht eine Liste — der
# Rücklink kommt deshalb aus dem Seitenkontext (``context["back_link"]``), nicht
# aus dieser Registry. Kein Rollengefälle in dieser Gruppe: das Ziel ist überall
# gleich oder weiter gegated als die Seite selbst (verifiziert gegen
# ``_authz_expectations.py``), die Liste braucht darum keine Rollenlogik — nur
# der Guard muss sie einfrieren.
VIEW_PROVIDED_BACKLINK: dict[str, str] = {
    "episode_create": "Episode liegt unter einem Fall (cases/<pk>/episodes/…) — Rücklink ist der Fallname.",
    "episode_update": "wie episode_create.",
    "rectification_create": "Antrag wird AUS der Personenakte gestellt (clients/<pk>/rectification/new/).",
    "client_delete_request": "Löschantrag wird aus der Personenakte gestellt (clients/<pk>/delete-request/).",
    "client_merge": "Zusammenführung wird aus der Personenakte betreten (clients/<pk>/merge/).",
    "case_delete_request": "Löschweg wird aus der Fallakte betreten (cases/<pk>/delete/) — Rücklink ist der Fallname.",
    "event_update": "Bearbeiten kehrt zum Ereignis zurück, nicht in den Zeitstrom.",
    "event_delete": "Lösch-Bestätigung kehrt zum Ereignis zurück, nicht in den Zeitstrom.",
    "mfa_settings": "Konto-Ebene: Heimat ist das eigene Profil, kein Navigationsbereich.",
    "deletion_withdraw": (
        "Rückweg ist gate-abhängig: für eine reine Antragstellerin trägt "
        "``can_access_deletion_requests`` nur der eigene offene Antrag (Refs #1736 B), "
        "der mit dem Rückzug entfällt. Ein Familien-Rücklink auf die Liste endete "
        "deshalb im 403 — die View berechnet das Ziel selbst (Refs #2040)."
    ),
}

# Routen, die per GET zwar erreichbar sind, aber kein Anwendungsdokument
# liefern (Downloads, HTMX-Fragmente, POST-Handler mit GET-Deklaration in der
# AuthZ-Tabelle). Ergänzt ``KNOWN_NON_NAV_ROUTES`` um die Fälle außerhalb der
# Navigations-Präfixe; beide Listen beantworten dieselbe Frage — „gibt es hier
# überhaupt eine Seite?" — und werden vom Guard vereinigt gelesen.
BACKLINK_NON_PAGE: dict[str, str] = {
    "client_attachment_download": "Datei-Download an der Personenakte (entschlüsselt, streamt).",
    "case_attachment_download": "Datei-Download am Fall (entschlüsselt, streamt).",
    "dsgvo_document": "Vorlagen-Download des DSGVO-Pakets, kein gerendertes Dokument.",
    "workitem_bulk_status": "Massenaktion der Aufgabenliste, antwortet mit der Liste bzw. leitet weiter.",
    "workitem_bulk_priority": "wie workitem_bulk_status.",
    "workitem_bulk_assign": "wie workitem_bulk_status.",
    # Konzept #1802 §3.4 zählt die Feldgruppen-Routen zu den Objekt-Eltern-
    # Seiten. Das ist überholt: beide rendern seit M0 (Refs #1056) das
    # Inline-Formular ``document_types/partials/group_form.html`` im Feld-Board
    # — ein HTMX-Fragment ohne ``base.html``. Regel R6 gilt: ein Fragment, das
    # in der Seite bleibt, ist kein Ort und bekommt keine Orientierungszeile.
    "config_fieldgroup_create": "Inline-Formular des Feld-Boards (HTMX-Fragment ohne base.html), keine Seite.",
    "config_fieldgroup_update": "wie config_fieldgroup_create.",
}


def _family_back_link(url_name: str) -> BackLink | None:
    """Der Bereichs-Rücklink laut Nav-Familien — ohne Ausnahmen zu beachten.

    Gewinner ist die **kleinste** Familie, die die Route enthält. Damit zeigt
    eine Objektseite unter einem Hub auf ihren Reiter („← Audit-Log") statt auf
    den Bereichsanker („← Datenschutz"), obwohl beide Familien sie führen —
    Konzept 3.6, ohne dafür eine zweite Tabelle zu pflegen.
    """
    kandidaten = [entry for entry in all_items() if entry.url_name and url_name in entry.family]
    if not kandidaten:
        return None
    beste = min(kandidaten, key=lambda entry: (len(entry.family), entry.key))
    return BackLink(label=beste.label, url_name=beste.url_name)


def back_link_for(url_name: str) -> BackLink | None:
    """Der Rücklink einer Route — ``None``, wenn die Registry keinen liefert.

    ``None`` heißt zweierlei und ist deshalb NIE ein stiller Fallback: entweder
    zeigt die Seite bewusst keinen Rücklink (``BACKLINK_EXEMPT``), oder ihn
    liefert der Seitenkontext (``VIEW_PROVIDED_BACKLINK``). Dass jede Route
    genau einen dieser drei Zustände hat, sichert
    ``tests/test_navigation_backlink.py``.
    """
    if url_name in BACKLINK_EXEMPT or url_name in VIEW_PROVIDED_BACKLINK:
        return None
    return _family_back_link(url_name)
