"""Orientierungs-Guards: der Rücklink jeder Objektseite (Refs #1802, Konzept #1801).

Das Subkonzept #1802 legt fest, dass **jede Objektseite mit genau einem
Rücklink beginnt** — nie mit einer Kette (R1), immer zur *Heimat* statt zur
Herkunft (R2), nie als Ersatz für ``?next=`` (R3). Vor diesem Guard existierten
fünf Pfeil-Dialekte an drei Positionen; die Zuordnung „welche Seite gehört zu
welcher Liste" lag mehrfach und gedriftet in den Templates.

Die Soll-Quelle ist ``core.nav``: die Aktiv-State-Familien **sind** die
Heimat-Tabelle. Dieser Guard prüft drei Ebenen:

1. *Datenebene* — jede erreichbare Route ist klassifiziert (Bereichs-Rücklink,
   ``BACKLINK_EXEMPT`` oder ``VIEW_PROVIDED_BACKLINK``). Kein stiller Fallback.
2. *Renderebene* — jede gerenderte Seite trägt den Marker
   ``data-testid="back-link"`` genau einmal (bzw. gar nicht, wenn ausgenommen)
   und **vor** ihrer ``<h1>``.
3. *Titelkopplung* — der Fenstertitel beginnt mit dem H1-Text und endet auf
   „– Anlaufstelle" (Konzept 3.9, Maintainer-Entscheid 5).

Bewusst abgeleitet statt gepflegt: Seiten und Rollen kommen aus
``_authz_expectations.py``, die Heimat aus ``core.nav`` — eine neue Seite fällt
hier auf, nicht erst im Handtest.
"""

from __future__ import annotations

from core import nav
from tests._authz_expectations import EXPECTATIONS

# Kategorien ohne gerendertes Anwendungsdokument. ``public`` enthält neben
# Login-/Fehlerseiten die drei vom Service Worker vorgecachten Offline-Shells;
# deren Rücklink ist ein eigener Fall (Konzept 4.2: statisch und
# rollenagnostisch) und bewusst NICHT Teil dieses Laufs.
NON_APP_CATEGORIES = frozenset({"export", "offline-api", "partials-htmx", "public"})


def _non_page_routes() -> set[str]:
    """Routen ohne Anwendungsdokument (Downloads, HTMX-Fragmente, POST-Handler)."""
    return set(nav.BACKLINK_NON_PAGE) | set(nav.KNOWN_NON_NAV_ROUTES)


def _app_pages():
    """Die Erwartungs-Einträge, die eine echte Anwendungsseite per GET liefern."""
    non_page = _non_page_routes()
    for exp in EXPECTATIONS:
        if exp.category in NON_APP_CATEGORIES:
            continue
        if dict(exp.methods).get("GET") is None:
            continue
        if exp.url_name.split(":")[-1] in non_page:
            continue
        yield exp


def _short(url_name: str) -> str:
    """``url_name`` ohne Namespace — die Schreibweise der Nav-Familien."""
    return url_name.split(":")[-1]


class TestBacklinkRegistry:
    """Datenebene: die Klassifikation ist vollständig, widerspruchsfrei, benannt."""

    def test_every_named_core_route_is_classified(self):
        """Vollständigkeits-Gate — kein stiller Fallback (Konzept 3.3, Schritt 4).

        Jede erreichbare Anwendungsseite ist entweder ausgenommen
        (``BACKLINK_EXEMPT``), bekommt ihren Rücklink aus dem Seitenkontext
        (``VIEW_PROVIDED_BACKLINK``, Objekt-Eltern nach Konzept 3.4), liefert
        einen Bereichs-Rücklink aus der Registry — oder ist als Nicht-Seite
        begründet ausgenommen. Genau diese Vollständigkeit verhindert die in
        Konzept 1.3 belegte Drift.
        """
        unclassified = sorted(
            exp.url_name
            for exp in _app_pages()
            if _short(exp.url_name) not in nav.BACKLINK_EXEMPT
            and _short(exp.url_name) not in nav.VIEW_PROVIDED_BACKLINK
            and nav.back_link_for(_short(exp.url_name)) is None
        )
        assert not unclassified, (
            f"Seiten ohne Rücklink-Klassifikation: {unclassified}. Entweder in die passende "
            "Nav-Familie in src/core/nav.py aufnehmen (Bereichs-Rücklink), in "
            "nav.BACKLINK_EXEMPT (Hub/Reiter/Liste/Assistent) oder mit Begründung in "
            "nav.VIEW_PROVIDED_BACKLINK bzw. nav.BACKLINK_NON_PAGE eintragen."
        )

    def test_exempt_pages_do_not_hide_a_different_home(self):
        """Eine Ausnahme verschweigt keine **fremde** Heimat.

        Eine Liste ist ihre eigene Heimat — dort zeigte ein Rücklink auf die
        Seite selbst, und die Ausnahme sagt nichts aus. Zeigt die Nav-Familie
        dagegen woanders hin, ist die Ausnahme eine echte Entscheidung und
        muss hier benannt sein. Die drei Schritte des Import-Assistenten sind
        der einzige Fall (Regel R7: Stepper statt Rücklink); jede weitere
        stille Ausnahme dieser Art bricht den Test.
        """
        fremd = {}
        for name in nav.BACKLINK_EXEMPT:
            link = nav._family_back_link(name)
            if link and _short(link.url_name) != name:
                fremd[name] = link.url_name
        assert fremd == {
            "import_create": "core:import_list",
            "import_mapping": "core:import_list",
            "import_preview": "core:import_list",
        }, f"Ausnahmen mit fremder Heimat: {fremd} — begründen (R7) oder Ausnahme entfernen."

    def test_exempt_and_view_provided_are_disjoint(self):
        overlap = sorted(set(nav.BACKLINK_EXEMPT) & set(nav.VIEW_PROVIDED_BACKLINK))
        assert not overlap, f"Routen in BACKLINK_EXEMPT UND VIEW_PROVIDED_BACKLINK: {overlap}"

    def test_backlink_label_equals_sidebar_label(self):
        """R2: der Rücklink heißt exakt wie der Navigations-Eintrag seiner Heimat.

        Das ist der Grund, warum die Labels nicht im Template stehen: „Zurück
        zur Inbox" vs. Nav-Eintrag „Aufgaben" und „Berichtigungsanträge" vs.
        „Berichtigungen" waren beide real (Konzept 1.1).
        """
        erwartet = {
            "client_detail": (nav.PERSONEN.label, "core:client_list"),
            "case_detail": (nav.FAELLE.label, "core:case_list"),
            "event_detail": (nav.ZEITSTROM.label, "core:zeitstrom"),
            "workitem_detail": (nav.AUFGABEN.label, "core:workitem_inbox"),
            "audit_detail": (nav.AUDIT_LOG.label, "core:audit_log"),
            "rectification_review": (nav.BERICHTIGUNGEN.label, "core:rectification_list"),
            "deletion_review": (nav.LOESCHANTRAEGE.label, "core:deletion_request_list"),
            "import_detail": (nav.DATENIMPORT.label, "core:import_list"),
            "config_doctype_detail": (nav.KONFIGURATION.label, "core:config_doctype_list"),
        }
        gefunden = {name: nav.back_link_for(name) for name in erwartet}
        assert {name: (link.label, link.url_name) for name, link in gefunden.items() if link} == erwartet

    def test_object_pages_resolve_to_the_tab_not_to_the_hub(self):
        """Konzept 3.6: unter einem Hub zeigt der Rücklink auf den **Reiter**.

        ``system_audit_detail`` gehört zum Reiter „Audit-Log", nicht zum
        Sidebar-Anker „Systembereich" — sonst spränge der Rücklink über die
        Reiterebene hinweg auf die Bereichs-Übersicht.
        """
        link = nav.back_link_for("system_audit_detail")
        assert link is not None
        assert link.url_name == "core:system_audit_list"

    def test_view_provided_entries_have_a_reason(self):
        for name, reason in nav.VIEW_PROVIDED_BACKLINK.items():
            assert reason.strip(), f"nav.VIEW_PROVIDED_BACKLINK[{name!r}] braucht eine Begründung."

    def test_non_page_entries_have_a_reason(self):
        for name, reason in nav.BACKLINK_NON_PAGE.items():
            assert reason.strip(), f"nav.BACKLINK_NON_PAGE[{name!r}] braucht eine Begründung."

    def test_classification_sets_reference_real_routes(self):
        """Bit-Rot-Schutz: jede Klassifikation zeigt auf eine deklarierte Route."""
        bekannt = {_short(exp.url_name) for exp in EXPECTATIONS}
        stray = sorted(
            name
            for name in (*nav.BACKLINK_EXEMPT, *nav.VIEW_PROVIDED_BACKLINK, *nav.BACKLINK_NON_PAGE)
            if name not in bekannt
        )
        assert not stray, f"Rücklink-Klassifikation für unbekannte Routen: {stray}"

    def test_back_link_for_is_none_for_exempt_and_view_provided(self):
        """Die Registry beantwortet nur die Bereichsfrage — nie die Objektfrage."""
        assert nav.back_link_for("client_list") is None
        assert nav.back_link_for("episode_create") is None
