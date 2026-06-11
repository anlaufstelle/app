"""Vertikale AuthZ-Matrix + Vollständigkeits-Gate (Refs #1055).

Soll-Quelle: src/tests/_authz_expectations.py — jeder benannte URL-Pattern
MUSS dort deklariert sein. Das Gate failt in beide Richtungen (Pattern ohne
Eintrag / Eintrag ohne Pattern), damit neue Endpoints eine bewusste
AuthZ-Deklaration erzwingen.
"""

import pytest
from django.conf import settings
from django.urls import get_resolver, reverse
from django.urls.resolvers import URLPattern, URLResolver

from tests._authz_expectations import EXPECTATIONS, ROLES
from tests._rbac_helpers import resolve_fixture_kwargs

# Django-Admin-Site (obfuskierter Pfad, eigene Permission-Maschinerie,
# abgedeckt durch test_admin_site_permissions.py) und Debug-Toolbar sind
# bewusst nicht Teil der Matrix.
EXCLUDED_NAMESPACES = {"admin", "djdt"}
# Django-i18n-Include (URLResolver auf i18n/); der eigene set_language-Wrapper
# auf i18n/setlang/ ist ein benannter URLPattern und separat deklariert.
EXCLUDED_ROUTE_PREFIXES = ("i18n/",)
# Permanente Redirects ohne eigene AuthZ-Logik (RedirectView, Refs core/urls.py).
UNNAMED_ALLOWLIST = {"aktivitaetslog/", "timeline/"}


def _collect_patterns():
    """Alle URL-Patterns: benannte (Set + Liste, namespace-qualifiziert) + unbenannte Routen."""
    named, unnamed, named_list = set(), set(), []

    def walk(resolver, namespace, prefix):
        for p in resolver.url_patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLPattern):
                if p.name:
                    qualified = f"{namespace}:{p.name}" if namespace else p.name
                    named.add(qualified)
                    named_list.append(qualified)
                else:
                    unnamed.add(route)
            elif isinstance(p, URLResolver):
                ns = p.namespace
                if ns in EXCLUDED_NAMESPACES:
                    continue
                if any(route.startswith(x) for x in EXCLUDED_ROUTE_PREFIXES):
                    continue
                child_ns = f"{namespace}:{ns}" if namespace and ns else (ns or namespace)
                walk(p, child_ns, route)

    walk(get_resolver(), None, "")
    return named, unnamed, named_list


class TestCompletenessGate:
    def test_every_named_pattern_is_declared(self):
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        undeclared = named - declared
        assert not undeclared, f"URL-Patterns ohne AuthZ-Deklaration in _authz_expectations.py: {sorted(undeclared)}"

    def test_every_declaration_has_a_pattern(self):
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        stale = declared - named
        assert not stale, f"Deklarationen ohne URL-Pattern (Drift): {sorted(stale)}"

    def test_unnamed_patterns_are_allowlisted(self):
        _, unnamed, _ = _collect_patterns()
        unexpected = unnamed - UNNAMED_ALLOWLIST
        assert not unexpected, f"Unbenannte URL-Patterns außerhalb der Allowlist: {sorted(unexpected)}"

    def test_no_duplicate_declarations(self):
        names = [e.url_name for e in EXPECTATIONS]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Doppelte Deklarationen: {sorted(dupes)}"

    def test_gate_detects_missing_declaration(self):
        """Selbsttest: Ein fehlender Eintrag MUSS auffallen (Schutz vor Gate-Bugs)."""
        named, _, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS} - {"core:client_detail"}
        assert "core:client_detail" in named - declared, "Gate hat den künstlich entfernten Eintrag nicht bemerkt"

    def test_no_duplicate_url_names_in_urlconf(self):
        """Doppelte URL-Namen kollabieren im Set — reverse() träfe nur eines."""
        _, _, named_list = _collect_patterns()
        dupes = {n for n in named_list if named_list.count(n) > 1}
        assert not dupes, f"Doppelte URL-Namen im URLconf: {sorted(dupes)}"

    def test_every_pk_endpoint_has_idor_probe_or_exemption(self):
        """Jeder Endpoint mit URL-Kwargs braucht eine IDOR-Probe oder begründete Ausnahme."""
        missing = [e.url_name for e in EXPECTATIONS if e.url_kwargs and not e.idor and not e.idor_exempt]
        assert not missing, f"pk-Endpoints ohne IDOR-Probe und ohne begründete Ausnahme: {sorted(missing)}"

    def test_idor_declarations_are_consistent(self):
        """idor und idor_exempt schließen sich aus; idor setzt url_kwargs voraus."""
        both = [e.url_name for e in EXPECTATIONS if e.idor and e.idor_exempt]
        assert not both, f"Einträge mit idor UND idor_exempt (widersprüchlich): {sorted(both)}"
        no_kwargs = [e.url_name for e in EXPECTATIONS if e.idor and not e.url_kwargs]
        assert not no_kwargs, f"Einträge mit idor ohne url_kwargs: {sorted(no_kwargs)}"


# ---- Vertikale Matrix: Endpoint × Methode × Akteur (Test-Client) ----------

LOGIN_PATH = settings.LOGIN_URL
ACTORS = (*ROLES, "anonymous")

# Bekannte, als Issue dokumentierte Lücken: Zelle → Begründung mit Issue-Ref.
# Format: ("core:foo", "POST", "assistant"): "200 statt 403 — Issue #XXXX"
_EVENT_DISPATCH_GAP = (
    "404 statt Login-Redirect für anonym — dispatch() lädt das Event VOR dem "
    "LoginRequired-Check (super().dispatch()), src/core/views/events.py. "
    "Issue folgt (#1055-Befund 1)"
)
KNOWN_GAPS: dict[tuple[str, str, str], str] = {
    ("core:event_update", "GET", "anonymous"): _EVENT_DISPATCH_GAP,
    ("core:event_update", "POST", "anonymous"): _EVENT_DISPATCH_GAP,
    ("core:event_delete", "GET", "anonymous"): _EVENT_DISPATCH_GAP,
    ("core:event_delete", "POST", "anonymous"): _EVENT_DISPATCH_GAP,
}


def test_known_gaps_reference_existing_cells():
    """Tippfehler in KNOWN_GAPS-Keys dürfen nicht lautlos ins Leere zeigen."""
    valid = {(exp.url_name, method, actor) for exp in EXPECTATIONS for method, _ in exp.methods for actor in ACTORS}
    stray = set(KNOWN_GAPS) - valid
    assert not stray, f"KNOWN_GAPS-Einträge ohne Matrix-Zelle: {sorted(stray)}"


def _cells():
    """Generiert je Tabellen-Zeile × Methode × Akteur einen Test-Parameter."""
    for exp in EXPECTATIONS:
        for method, allowed in exp.methods:
            for actor in ACTORS:
                marks = []
                if (exp.url_name, method, actor) in KNOWN_GAPS:
                    marks.append(pytest.mark.xfail(reason=KNOWN_GAPS[(exp.url_name, method, actor)], strict=True))
                yield pytest.param(
                    exp,
                    method,
                    allowed,
                    actor,
                    id=f"{exp.url_name}-{method}-{actor}",
                    marks=marks,
                )


@pytest.fixture
def matrix_users(db, facility):
    """Fünf Akteure analog Seed (Refs #867/#1053): confirm-Flag für admin+lead."""
    from core.models import User

    def make(username, role, fac, confirm=False):
        # Kein Passwort nötig: force_login umgeht die Authentifizierung;
        # create_user reicht can_confirm_deletion als Kwarg ans Model durch.
        return User.objects.create_user(
            username=username, role=role, facility=fac, is_staff=True, can_confirm_deletion=confirm
        )

    return {
        "facility_admin": make("mx_admin", User.Role.FACILITY_ADMIN, facility, confirm=True),
        "lead": make("mx_lead", User.Role.LEAD, facility, confirm=True),
        "staff": make("mx_staff", User.Role.STAFF, facility),
        "assistant": make("mx_assistant", User.Role.ASSISTANT, facility),
        "super_admin": make("mx_super", User.Role.SUPER_ADMIN, None),
    }


@pytest.mark.django_db
@pytest.mark.parametrize("exp,method,allowed,actor", _cells())
def test_authz_cell(client, exp, method, allowed, actor, request):
    """Eine Matrix-Zelle: erlaubt = kein 403/404/Login-Redirect, verboten = 403/404."""
    kwargs = resolve_fixture_kwargs(exp.url_kwargs, request)
    url = reverse(exp.url_name, kwargs=kwargs or None)
    if actor != "anonymous":
        # Lazy: anonyme Zellen brauchen keine fünf User-Objekte.
        users = request.getfixturevalue("matrix_users")
        client.force_login(users[actor])

    response = getattr(client, method.lower())(url)
    status = response.status_code
    location = getattr(response, "url", "")

    if actor == "anonymous":
        if exp.anonymous_ok:
            assert status < 500, f"{url}: anonym (anonymous_ok) erwartet <500, bekam {status}"
        else:
            assert status == 302 and location.startswith(LOGIN_PATH), (
                f"{url}: anonym erwartet Login-Redirect, bekam {status} {location}"
            )
        return

    if actor in allowed:
        allowed_here = (status not in (403, 404)) or (status in exp.extra_ok)
        assert allowed_here, f"{url}: {actor} erwartet Zugriff, bekam {status}"
        assert status < 500, f"{url}: {actor} erhielt Serverfehler {status}"
        if status == 302:
            # Ein echter Auth-Bounce (redirect_to_login) trägt immer ?next=…;
            # ein nacktes /login/ ist z. B. der legitime Logout-Redirect
            # (LOGOUT_REDIRECT_URL) und kein Berechtigungsproblem.
            assert not location.startswith(f"{LOGIN_PATH}?next="), f"{url}: {actor} wurde zum Login umgeleitet"
    else:
        assert status in (403, 404), f"{url}: {actor} erwartet 403/404, bekam {status} {location}"
