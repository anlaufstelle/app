"""Template-Guard-Test: keine Aktion, die enger gegated ist als ihre Seite (Refs #1735).

**Soll-Quelle ist ausschliesslich** ``src/tests/_authz_expectations.py`` — dieselbe
deklarative Tabelle, die schon ``test_authz_matrix.py`` (vertikal),
``test_authz_idor.py`` (horizontal) und ``e2e/test_authz_audit.py`` speist. Hier
kommt die dritte Achse dazu: **was die Seite anbietet**.

Der Test rendert fuer jede Rolle jede Seite, die sie laut Tabelle per GET
erreichen darf, sammelt alle Ziel-URLs aus dem Markup (``href``, ``action``,
``hx-get``/``hx-post``/…, ``data-…-url``), loest sie ueber die URLconf zurueck auf
ihren Endpoint-Namen und prueft ihn gegen dieselbe Tabelle. Ein Link auf eine
Route, die fuer die aktuelle Rolle **enger** berechtigt ist als die Seite selbst,
ist ein Befund: serverseitig greift der Guard (403), fuer die Nutzerin sieht es
nach einer kaputten Anwendung aus (Refs #1735, Fundstellen 1–5).

**Bewusst ableitungsbasiert, keine gepflegte Seitenliste:** Seiten, Rollen und
Erlaubnisse kommen alle aus ``EXPECTATIONS``. Neue Endpoints muessen dort ohnehin
deklariert werden (Vollstaendigkeits-Gate in ``test_authz_matrix``) und fallen
damit automatisch in diese Pruefung — auch wenn die Templates parallel von
jemand anderem geaendert werden. Es gibt keinen eingefrorenen Snapshot: der Test
vergleicht immer gegen den aktuellen Soll-Zustand.

Grenzen (bewusst):

* Geprueft wird nur, was mit den Test-Fixtures **tatsaechlich gerendert** wird.
  Ein Link in einem Zweig ohne Testdaten faellt durch das Raster — deshalb legt
  ``guard_content`` zu jeder Fixture-Person Fall, Ereignis, Aufgabe, Loeschantrag
  usw. an, damit Listen und Detailseiten gefuellt sind.
* Rein clientseitig zusammengebaute URLs (String-Konkatenation in JS) sind nicht
  sichtbar; das ``data-…-url``-Muster deckt den im Projekt ueblichen Weg ab, die
  Basis-URL aus dem Template zu reichen (genau Fundstelle 5).
"""

from __future__ import annotations

import re

import pytest
from django.urls import Resolver404, resolve, reverse

from tests._authz_expectations import EXPECTATIONS, ROLES
from tests._rbac_helpers import resolve_fixture_kwargs

# Kategorien, die nie eine HTML-Seite mit Links liefern (Downloads, JSON-APIs).
# Sie wuerden ohnehin am Content-Type-Filter scheitern — der PDF-Export kostet
# aber echte Sekunden (WeasyPrint), deshalb gar nicht erst anfordern.
SKIPPED_CATEGORIES = frozenset({"export", "offline-api"})

# Attribute, in denen im Projekt Ziel-URLs stehen, samt HTTP-Methode, mit der
# sie aufgerufen werden. ``data-…-url`` ist das etablierte Muster, eine URL aus
# dem Template an eine Alpine-/JS-Komponente zu reichen (per fetch = GET).
#
# ``formaction`` MUSS eigens aufgezaehlt werden: ``\baction`` matcht darin
# nicht (vor ``action`` steht ein Wortzeichen), und ohne den Eintrag waere jede
# per Submit-Button angesteuerte Route still ungeprueft — genau der Fall der
# Statistik-Export-Aktionen seit
# #1770. Es steht
# bewusst VOR ``action``, damit die Absicht beim Lesen nicht untergeht.
_LINK_ATTR_RE = re.compile(
    r'\b(href|formaction|action|hx-get|hx-post|hx-put|hx-patch|hx-delete|data-[\w-]*url)\s*=\s*"([^"]*)"',
    re.IGNORECASE,
)
# ``formaction`` erbt die Methode seines Formulars. Im Projekt ist das
# durchgaengig ``method="get"`` (Download-Ausloeser mit mitgeschickten Feldern);
# als POST gewertet meldete der Guard bei jeder GET-only-Route einen Fehlalarm.
# Ein kuenftiges POST-Formular mit ``formaction`` braeuchte hier eine feinere
# Auswertung (Methode des umschliessenden ``<form>``) — bis dahin ist die
# GET-Annahme die, die zu den Fundstellen passt.
_POST_ATTRS = frozenset({"action", "hx-post", "hx-put", "hx-patch", "hx-delete"})

# Praefixe, die nie auf eine View zeigen (Resolver404 waere nur teurer).
_NON_VIEW_PREFIXES = ("/static/", "/media/")

EXPECTATION_BY_NAME = {e.url_name: e for e in EXPECTATIONS}

# Begruendete Ausnahmen: (Seite, Ziel, Rolle) -> Grund. Bewusst als Triple, damit
# eine Ausnahme nie versehentlich fuer alle Seiten gilt. Jeder Eintrag ist eine
# bewusste, begruendete Abweichung — nicht „Rauschen abschalten".
#
# Die Rolle ``"*"`` gilt fuer alle Rollen: die beiden Offline-Shells werden vom
# Service Worker EINMAL zur Install-Zeit vorgecacht und danach jeder Rolle aus
# demselben Cache-Eintrag ausgeliefert. Ein rollenabhaengiger Template-Guard
# waere dort aktiv falsch (er wuerde die Sicht des Erst-Installierenden
# einfrieren); die Rollenschranke liegt in beiden Faellen im Client
# (leeres ``assignable_users``-Roster blendet das Formular aus) und am Server
# beim Replay der Queue.
EXEMPTIONS: dict[tuple[str, str, str], str] = {
    (
        "offline_fallback",
        "core:client_list",
        "*",
    ): "SW-vorgecachte Statikseite, eine Kopie fuer alle Rollen (views/offline.py)",
    ("offline_fallback", "core:workitem_inbox", "*"): "wie oben — SW-Precache ist rollenagnostisch",
    ("offline_fallback", "core:zeitstrom", "*"): "wie oben — SW-Precache ist rollenagnostisch",
    (
        "core:offline_workitem_shell",
        "core:workitem_create",
        "*",
    ): "SW-vorgecachter Shell an der kanonischen /workitems/new/-URL; Staff+-Gate liegt im Client (Refs #1522)",
    (
        "core:offline_event_shell",
        "core:event_create",
        "*",
    ): "SW-vorgecachter Shell an der kanonischen /events/new/-URL (Refs #1322)",
}


@pytest.fixture
def guard_users(db, facility):
    """Fuenf Akteure analog ``test_authz_matrix.matrix_users`` (Seed-Rollenmodell)."""
    from core.models import User

    def make(username, role, fac, confirm=False):
        return User.objects.create_user(
            username=username, role=role, facility=fac, is_staff=True, can_confirm_deletion=confirm
        )

    return {
        "facility_admin": make("tl_admin", User.Role.FACILITY_ADMIN, facility, confirm=True),
        "lead": make("tl_lead", User.Role.LEAD, facility, confirm=True),
        "staff": make("tl_staff", User.Role.STAFF, facility),
        "assistant": make("tl_assistant", User.Role.ASSISTANT, facility),
        "super_admin": make("tl_super", User.Role.SUPER_ADMIN, None),
    }


@pytest.fixture
def guard_content(request):
    """Datenbestand, damit Listen/Details nicht leer rendern.

    Ohne Inhalt bliebe der Test blind fuer genau die Klasse Fundstellen, die
    erst mit Daten erscheint (z. B. die Fall-Links im Personen-Detail).
    """
    for name in (
        "client_identified",
        "case_open",
        "episode",
        "outcome_goal",
        "milestone",
        "sample_event",
        "sample_workitem",
        "deletion_request",
        "retention_proposal",
        "legal_hold",
        "audit_entry",
        "settings_obj",
    ):
        request.getfixturevalue(name)


def _iter_link_targets(html: str):
    """``(attribut, methode, pfad)`` fuer jede Ziel-URL im Markup."""
    for attr, value in _LINK_ATTR_RE.findall(html):
        attr = attr.lower()
        path = value.split("#", 1)[0].split("?", 1)[0].strip()
        if not path or not path.startswith("/") or path.startswith("//"):
            continue
        if path.startswith(_NON_VIEW_PREFIXES):
            continue
        yield attr, ("POST" if attr in _POST_ATTRS else "GET"), path


def _target_endpoint(path: str) -> str | None:
    """Namespaced Endpoint-Name eines Pfads (``None``, wenn nicht aufloesbar)."""
    try:
        return resolve(path).view_name
    except Resolver404:
        return None


def violations_in_html(page_url_name: str, html: str, role: str) -> list[str]:
    """Befunde einer gerenderten Seite: Links auf fuer ``role`` engere Routen.

    Als eigenstaendige Funktion, damit der Selbsttest unten sie ohne HTTP-Runde
    fuettern kann (Schutz vor einem stumpfen Guard, Muster
    ``test_authz_matrix.test_gate_detects_missing_declaration``).
    """
    found: list[str] = []
    for attr, method, path in _iter_link_targets(html):
        target = _target_endpoint(path)
        if target is None:
            continue
        target_exp = EXPECTATION_BY_NAME.get(target)
        if target_exp is None:
            # Nicht deklarierte Namespaces (z. B. Django-Admin) — die
            # Vollstaendigkeit der eigenen URLconf erzwingt test_authz_matrix.
            continue
        target_allowed = dict(target_exp.methods).get(method)
        if target_allowed is None or role in target_allowed:
            continue
        if (page_url_name, target, role) in EXEMPTIONS or (page_url_name, target, "*") in EXEMPTIONS:
            continue
        found.append(f"{page_url_name} → {attr}={path} [{method} {target}]")
    return found


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
def test_pages_link_only_to_routes_the_role_may_use(client, role, request, guard_users, guard_content):
    """Fuer jede erreichbare Seite: kein Link/Formular auf eine engere Route."""
    client.force_login(guard_users[role])

    violations: list[str] = []
    for exp in EXPECTATIONS:
        if exp.category in SKIPPED_CATEGORIES:
            continue
        allowed_get = dict(exp.methods).get("GET")
        if allowed_get is None or role not in allowed_get:
            continue

        url = reverse(exp.url_name, kwargs=resolve_fixture_kwargs(exp.url_kwargs, request) or None)
        response = client.get(url)
        # Nur echte HTML-Antworten tragen Links; alles andere (Redirect wegen
        # Sudo-Modus, 404 aus ``extra_ok``, Downloads) ist hier uninteressant.
        if response.status_code != 200 or "text/html" not in response.headers.get("Content-Type", ""):
            continue

        violations.extend(violations_in_html(exp.url_name, response.content.decode(), role))

    assert not violations, (
        f"Rolle {role!r}: Seite bietet Aktionen an, die fuer sie im 403 enden "
        f"(Soll-Quelle _authz_expectations.py, Refs #1735):\n  " + "\n  ".join(sorted(set(violations)))
    )


class TestGuardMechanics:
    """Selbsttests: der Guard muss finden, wonach er sucht — und nichts sonst."""

    def test_detects_a_link_to_a_narrower_route(self):
        html = '<a href="/clients/new/">Neue Person</a>'
        assert violations_in_html("core:client_list", html, "assistant")
        assert not violations_in_html("core:client_list", html, "staff")

    def test_detects_a_form_action_to_a_narrower_route(self):
        html = '<form method="post" action="/clients/new/"></form>'
        assert violations_in_html("core:client_list", html, "assistant")

    def test_detects_a_formaction_on_a_submit_button(self):
        """``formaction`` ist eine vollwertige Ziel-URL — und war ein blinder
        Fleck.

        Das Muster ist keine Exotik: seit der Freigabe-Erfassung
        (#1770) tragen
        die Export-Aktionen der Statistikseite ihre Ziele als ``formaction``
        eines GET-Formulars, damit die optionalen Felder Empfaenger/Zweck ohne
        JavaScript an allen Zielen ankommen. ``\b(href|action|…)`` matchte dort
        NICHT (in ``formaction`` steht vor ``action`` ein Wortzeichen) — drei
        Routen waren still ungeprueft.
        """
        html = '<form method="get"><button type="submit" formaction="/clients/new/">x</button></form>'
        assert violations_in_html("core:client_list", html, "assistant")
        assert not violations_in_html("core:client_list", html, "staff")

    def test_formaction_wird_als_get_gewertet(self):
        """``formaction`` erbt die Methode seines Formulars; im Projekt ist das
        durchgaengig ``method="get"`` (Download-Ausloeser). Wuerde es als POST
        gewertet, meldete der Guard bei jeder GET-only-Route einen Fehlalarm."""
        html = '<form method="get"><button type="submit" formaction="/clients/new/">x</button></form>'

        attrs = {attr: method for attr, method, _path in _iter_link_targets(html)}

        assert attrs["formaction"] == "GET"

    def test_detects_a_data_url_attribute(self):
        """Fundstelle 5 aus #1735 kam ausschliesslich ueber ``data-…-url``."""
        html = '<form data-cases-for-client-url="/partials/cases/for-client/"></form>'
        assert violations_in_html("core:event_create", html, "assistant")

    def test_ignores_static_external_and_fragment_targets(self):
        html = (
            '<a href="/static/js/app.js">s</a><a href="https://example.org/clients/new/">x</a>'
            '<a href="#main">a</a><a href="mailto:x@example.org">m</a>'
        )
        assert not violations_in_html("core:client_list", html, "assistant")

    def test_query_string_and_fragment_are_stripped_before_resolving(self):
        html = '<a href="/clients/new/?from=list#top">Neue Person</a>'
        assert violations_in_html("core:client_list", html, "assistant")

    def test_exemptions_reference_real_endpoints(self):
        """Tippfehler in EXEMPTIONS duerfen nicht lautlos ins Leere zeigen."""
        stray = [
            key
            for key in EXEMPTIONS
            if key[0] not in EXPECTATION_BY_NAME or key[1] not in EXPECTATION_BY_NAME or key[2] not in (*ROLES, "*")
        ]
        assert not stray, f"EXEMPTIONS-Eintraege ohne Endpoint/Rolle: {stray}"

    def test_exemptions_have_a_reason(self):
        assert all(reason.strip() for reason in EXEMPTIONS.values())
