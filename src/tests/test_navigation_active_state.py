"""Aktiv-Markierung der Sidebar-Navigation.

Regression: Als super_admin auf der Arbeitszentrale (url_name ``dashboard``)
wurde zusaetzlich der Systembereich hervorgehoben. Ursache war das Django-
Template-Idiom ``{% if x in 'a,b,c' %}``, das auf Strings als Substring-Test
wirkt — und ``dashboard`` ist ein Substring von ``system_dashboard``.
Erwartet: genau der aktive Bereich wird markiert.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

ACTIVE_MARKER = "bg-accent-light"


def _opening_anchor(content: str, testid: str) -> str:
    """Gibt den oeffnenden ``<a ...>``-Tag mit dem gegebenen data-testid zurueck."""
    idx = content.index(f'data-testid="{testid}"')
    start = content.rindex("<a", 0, idx)
    end = content.index(">", idx)
    return content[start:end]


@pytest.mark.django_db
class TestSidebarActiveState:
    def test_super_admin_dashboard_does_not_activate_systembereich(self, client, super_admin_user):
        """Auf der Arbeitszentrale ist nur Arbeitszentrale aktiv, nicht Systembereich."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        dashboard_anchor = _opening_anchor(content, "nav-dashboard")
        system_anchor = _opening_anchor(content, "nav-system")

        assert ACTIVE_MARKER in dashboard_anchor, "Arbeitszentrale sollte aktiv sein"
        assert ACTIVE_MARKER not in system_anchor, "Systembereich darf auf der Arbeitszentrale nicht aktiv sein"

    def test_super_admin_systembereich_activates_only_systembereich(self, client, super_admin_user):
        """Im Systembereich ist Systembereich aktiv, nicht die Arbeitszentrale."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        dashboard_anchor = _opening_anchor(content, "nav-dashboard")
        system_anchor = _opening_anchor(content, "nav-system")

        assert ACTIVE_MARKER in system_anchor, "Systembereich sollte aktiv sein"
        assert ACTIVE_MARKER not in dashboard_anchor, "Arbeitszentrale darf im Systembereich nicht aktiv sein"
