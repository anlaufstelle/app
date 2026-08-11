"""Aktiv-Markierung der Sidebar-Navigation.

Regression: Als super_admin auf der Arbeitszentrale (url_name ``dashboard``)
wurde zusaetzlich der Systembereich hervorgehoben. Ursache war das Django-
Template-Idiom ``{% if x in 'a,b,c' %}``, das auf Strings als Substring-Test
wirkt — und ``dashboard`` ist ein Substring von ``system_dashboard``.
Erwartet: genau der aktive Bereich wird markiert.

Refs #1813 (Etappe 2) verschiebt den Schauplatz, nicht die Aussage: seit der
Sidebar-Konsolidierung sieht die Systemadministration keine Arbeitszentrale
mehr (Regel N3), und die acht Einzel-Eintraege des Datenschutz-/Verwaltungs-
Blocks sind zu zwei Hub-Ankern geworden. Das Ankerpaar
``nav-dashboard``/``nav-system`` gibt es also in keiner Sidebar mehr. An seine
Stelle treten drei Aussagen:

* die **Datenebene** (``dashboard`` ist kein Element der System-Familie, und
  die Hub-Familien sind von der System-Familie disjunkt) — sie faellt selbst
  dann noch, wenn jemand die Familien wieder auf CSV-Strings zurueckdreht;
* das **Hub-Ankerpaar** ``nav-datenschutz``/``nav-verwaltung``, das
  tatsaechlich gemeinsam in einer Sidebar steht (Anwendungsbetreuung);
* die Invariante **genau ein aktiver Sidebar-Anker pro Seite**, die die
  eigentliche Regressionsklasse ("zwei Bereiche leuchten") rollenunabhaengig
  festhaelt.

Die Zwei-Ebenen-Unterscheidung bleibt am Marker ablesbar: Sidebar
``bg-accent-light``, Reiter-Pill ``bg-accent text-white``.
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


def _active_anchor_count(content: str) -> int:
    """Zahl der aktiv markierten Sidebar-Zeilen (Marker im ``nav-items``-Slice)."""
    start = content.index('data-testid="nav-items"')
    return content[start : content.index("</div>", start)].count(ACTIVE_MARKER)


def _pill(content: str, key: str) -> str:
    """Oeffnender ``<a ...>``-Tag eines Bereichs-Reiters (Refs #1813)."""
    return _opening_anchor(content, f"subnav-tab-{key}")


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


def test_hub_families_are_disjoint_from_each_other_and_from_the_system_family():
    """Das neue Ankerpaar auf Datenebene (Refs #1813).

    Zwei Bereichsanker koennen nur dann gleichzeitig leuchten, wenn sich ihre
    Familien ueberschneiden. Der Datentest schliesst das fuer die drei
    Bereiche aus, bevor ueberhaupt eine Seite gerendert wird.
    """
    assert not nav.DATENSCHUTZ_FAMILY & nav.VERWALTUNG_FAMILY
    assert not nav.DATENSCHUTZ_FAMILY & nav.SYSTEM_FAMILY
    assert not nav.VERWALTUNG_FAMILY & nav.SYSTEM_FAMILY
    assert nav.item("datenschutz").family == nav.DATENSCHUTZ_FAMILY
    assert nav.item("verwaltung").family == nav.VERWALTUNG_FAMILY


@pytest.mark.django_db
class TestSidebarActiveState:
    """Das Ankerpaar, das nach der Konsolidierung tatsaechlich gemeinsam in einer
    Sidebar steht: die beiden Bereichs-Hubs der Anwendungsbetreuung."""

    def test_datenschutz_page_does_not_activate_verwaltung(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert ACTIVE_MARKER in _opening_anchor(content, "nav-datenschutz")
        assert ACTIVE_MARKER not in _opening_anchor(content, "nav-verwaltung")

    def test_verwaltung_page_does_not_activate_datenschutz(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

        assert ACTIVE_MARKER in _opening_anchor(content, "nav-verwaltung")
        assert ACTIVE_MARKER not in _opening_anchor(content, "nav-datenschutz")

    def test_lead_arbeitszentrale_activates_only_itself(self, client, lead_user):
        """Die Arbeitszentrale gehoert seit Regel N3 der Leitung — dort gilt die
        urspruengliche Aussage unveraendert weiter."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert ACTIVE_MARKER in _opening_anchor(content, "nav-dashboard")
        assert _active_anchor_count(content) == 1


@pytest.mark.django_db
class TestFeldvorlagenNavActiveState:
    """Refs #1717: 'Feldvorlagen' und 'Dokumentationstypen' duerfen nie
    gleichzeitig aktiv sein.

    Refs #1813: beide sind keine Sidebar-Zeilen mehr, sondern Reiter des
    Bereichs „Verwaltung" — die Aussage wandert eine Ebene tiefer auf die
    Pills (Marker ``bg-accent text-white``). Der Sidebar-Anker „Verwaltung"
    ist auf beiden Seiten aktiv; genau das ist der Zwei-Ebenen-Zustand.
    """

    PILL_ACTIVE = "bg-accent text-white"

    def test_fieldtemplate_list_activates_feldvorlagen_not_konfiguration(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_fieldtemplate_list")).content.decode()

        assert self.PILL_ACTIVE in _pill(content, "config-fieldtemplates"), "Feldvorlagen sollte aktiv sein"
        assert self.PILL_ACTIVE not in _pill(content, "config"), "Dokumentationstypen darf hier nicht aktiv sein"
        assert ACTIVE_MARKER in _opening_anchor(content, "nav-verwaltung")

    def test_doctype_list_activates_konfiguration_not_feldvorlagen(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:config_doctype_list")).content.decode()

        assert self.PILL_ACTIVE in _pill(content, "config"), "Dokumentationstypen sollte aktiv sein"
        assert self.PILL_ACTIVE not in _pill(content, "config-fieldtemplates"), (
            "Feldvorlagen darf hier nicht aktiv sein"
        )
        assert ACTIVE_MARKER in _opening_anchor(content, "nav-verwaltung")


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
    def test_exactly_one_sidebar_anchor_is_active_on_every_system_page(self, client, super_admin_user, url_name):
        """Gegenprobe zur Substring-Regression, rollenunabhaengig formuliert.

        Frueher hiess sie „die Arbeitszentrale darf nicht mitleuchten" — die
        Systemadministration hat seit Regel N3 gar keine mehr. Die eigentliche
        Aussage war ohnehin allgemeiner: **ein** Ort, **eine** Markierung.
        """
        client.force_login(super_admin_user)
        content = client.get(reverse(url_name)).content.decode()

        assert _active_anchor_count(content) == 1, f"{url_name}: genau ein Sidebar-Anker darf aktiv sein"


@pytest.mark.django_db
class TestAuditNavActiveState:
    """Refs #1813: die Detailansicht eines Audit-Eintrags fehlte in der
    Audit-Familie — der Nav-Eintrag graute beim Aufklappen eines Eintrags aus.

    Seit der Sidebar-Konsolidierung traegt den Zustand der Hub-Anker
    „Datenschutz": ``audit_detail`` gehoert zur Audit-Familie, die Audit-Familie
    zum Bereich, der Bereich zum Anker. Die Aussage bleibt dieselbe, sie wird
    nur nicht mehr an einer eigenen Sidebar-Zeile abgelesen."""

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

        assert ACTIVE_MARKER in _opening_anchor(content, "nav-datenschutz")
