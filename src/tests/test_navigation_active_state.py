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

from core import nav

ACTIVE_MARKER = "bg-accent-light"


def _opening_anchor(content: str, testid: str) -> str:
    """Gibt den oeffnenden ``<a ...>``-Tag mit dem gegebenen data-testid zurueck."""
    idx = content.index(f'data-testid="{testid}"')
    start = content.rindex("<a", 0, idx)
    end = content.index(">", idx)
    return content[start:end]


def _sidebar_anchor_by_href(content: str, href: str) -> str:
    """Oeffnender ``<a ...>``-Tag der Sidebar-Zeile mit diesem ``href``.

    Fuer die Eintraege ohne eigenes ``data-testid`` (Refs #1813 aendert die
    Testid-Landschaft bewusst nicht). Der Schnitt auf den ``nav-items``-
    Container haelt gleichnamige Links aus Seiteninhalt und Mobile-Nav heraus.
    """
    nav_start = content.index('data-testid="nav-items"')
    nav_end = content.index("</div>", nav_start)
    items = content[nav_start:nav_end]
    idx = items.index(f'href="{href}"')
    return items[items.rindex("<a", 0, idx) : items.index(">", idx)]


def test_dashboard_is_not_a_substring_member_of_system_family():
    """Dieselbe Aussage wie die Render-Tests unten — aber auf Datenebene.

    Refs #1813: Die Familien sind seit der Registry ``frozenset``\\ s, ``in``
    ist damit strukturell ein Element- und kein Substring-Test. Dieser
    Datentest haelt die Aussage fest, ohne eine Seite rendern zu muessen: er
    faellt selbst dann noch, wenn jemand die Familien versehentlich wieder auf
    einen CSV-String zurueckdreht (dort waere ``"dashboard" in family`` wahr).
    """
    assert "dashboard" not in nav.SYSTEM_FAMILY
    assert "system_dashboard" in nav.SYSTEM_FAMILY
    assert "dashboard" not in nav.item("system").family


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


@pytest.mark.django_db
class TestFeldvorlagenNavActiveState:
    """Refs #1717: 'Feldvorlagen' ist ein eigener Nav-Eintrag, getrennt von
    'Konfiguration' (Dokumentationstypen) — beide duerfen nie gleichzeitig
    aktiv sein."""

    def test_fieldtemplate_list_activates_feldvorlagen_not_konfiguration(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_fieldtemplate_list")).content.decode()

        feldvorlagen_anchor = _opening_anchor(content, "nav-config-fieldtemplates")
        konfiguration_anchor = _opening_anchor(content, "nav-config")

        assert ACTIVE_MARKER in feldvorlagen_anchor, "Feldvorlagen sollte aktiv sein"
        assert ACTIVE_MARKER not in konfiguration_anchor, "Konfiguration darf hier nicht aktiv sein"

    def test_doctype_list_activates_konfiguration_not_feldvorlagen(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_list")).content.decode()

        feldvorlagen_anchor = _opening_anchor(content, "nav-config-fieldtemplates")
        konfiguration_anchor = _opening_anchor(content, "nav-config")

        assert ACTIVE_MARKER in konfiguration_anchor, "Konfiguration sollte aktiv sein"
        assert ACTIVE_MARKER not in feldvorlagen_anchor, "Feldvorlagen darf hier nicht aktiv sein"


@pytest.mark.django_db
class TestSystembereichActiveOnEveryTab:
    """Refs #1813: der Sidebar-Eintrag „Systembereich" markierte nur sieben der
    neun ``/system/``-Reiter. Auf Retention, VVT, Legal Holds und Compliance
    graute er aus — die Ortsanzeige verschwand mitten im Bereich. Ursache war
    eine handgepflegte CSV-Familie, die bei jedem neuen Reiter (#875, #876,
    #877, #919) nachgezogen werden musste und es nie wurde."""

    @pytest.mark.parametrize("url_name", [tab.url_name for tab in nav.SYSTEM.tabs])
    def test_sidebar_entry_is_active_on_every_system_page(self, client, super_admin_user, url_name):
        client.force_login(super_admin_user)
        content = client.get(reverse(url_name)).content.decode()

        assert ACTIVE_MARKER in _opening_anchor(content, "nav-system"), f"Systembereich muss auf {url_name} aktiv sein"

    @pytest.mark.parametrize("url_name", [tab.url_name for tab in nav.SYSTEM.tabs])
    def test_arbeitszentrale_stays_inactive_on_every_system_page(self, client, super_admin_user, url_name):
        """Gegenprobe zur Substring-Regression: nie beide gleichzeitig."""
        client.force_login(super_admin_user)
        content = client.get(reverse(url_name)).content.decode()

        assert ACTIVE_MARKER not in _opening_anchor(content, "nav-dashboard"), (
            f"Arbeitszentrale darf auf {url_name} nicht aktiv sein"
        )


@pytest.mark.django_db
class TestAuditNavActiveState:
    """Refs #1813: die Detailansicht eines Audit-Eintrags fehlte in der
    Audit-Familie — der Nav-Eintrag graute beim Aufklappen eines Eintrags aus."""

    def test_audit_entry_stays_active_on_the_detail_page(self, client, admin_user, facility):
        from core.models import AuditLog

        entry = AuditLog.objects.create(
            facility=facility,
            user=admin_user,
            action=AuditLog.Action.LOGIN,
            detail={"username": admin_user.username},
        )
        client.force_login(admin_user)
        content = client.get(reverse("core:audit_detail", args=[entry.pk])).content.decode()

        assert ACTIVE_MARKER in _sidebar_anchor_by_href(content, "/audit/")
