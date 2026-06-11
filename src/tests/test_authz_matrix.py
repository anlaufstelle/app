"""Vertikale AuthZ-Matrix + Vollständigkeits-Gate (Refs #1055).

Soll-Quelle: src/tests/_authz_expectations.py — jeder benannte URL-Pattern
MUSS dort deklariert sein. Das Gate failt in beide Richtungen (Pattern ohne
Eintrag / Eintrag ohne Pattern), damit neue Endpoints eine bewusste
AuthZ-Deklaration erzwingen.
"""

from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from tests._authz_expectations import EXPECTATIONS

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
    """Alle URL-Patterns: benannte (namespace-qualifiziert) + unbenannte Routen."""
    named, unnamed = set(), set()

    def walk(resolver, namespace, prefix):
        for p in resolver.url_patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLPattern):
                if p.name:
                    named.add(f"{namespace}:{p.name}" if namespace else p.name)
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
    return named, unnamed


class TestCompletenessGate:
    def test_every_named_pattern_is_declared(self):
        named, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        undeclared = named - declared
        assert not undeclared, f"URL-Patterns ohne AuthZ-Deklaration in _authz_expectations.py: {sorted(undeclared)}"

    def test_every_declaration_has_a_pattern(self):
        named, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS}
        stale = declared - named
        assert not stale, f"Deklarationen ohne URL-Pattern (Drift): {sorted(stale)}"

    def test_unnamed_patterns_are_allowlisted(self):
        _, unnamed = _collect_patterns()
        unexpected = unnamed - UNNAMED_ALLOWLIST
        assert not unexpected, f"Unbenannte URL-Patterns außerhalb der Allowlist: {sorted(unexpected)}"

    def test_no_duplicate_declarations(self):
        names = [e.url_name for e in EXPECTATIONS]
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"Doppelte Deklarationen: {sorted(dupes)}"

    def test_gate_detects_missing_declaration(self):
        """Selbsttest: Ein fehlender Eintrag MUSS auffallen (Schutz vor Gate-Bugs)."""
        named, _ = _collect_patterns()
        declared = {e.url_name for e in EXPECTATIONS} - {"core:client_detail"}
        assert named - declared, "Gate hat den künstlich entfernten Eintrag nicht bemerkt"
