"""Sichtbarkeit der Konfigurations-Einstiege (Refs #1056, #1717, #1813).

Der /config/-Bereich (DocumentType-/FieldTemplate-CRUD) ist Anwendungs-
betreuung vorbehalten (``FacilityAdminRequiredMixin``). Der Navigations-
Einstieg darf daher ausschliesslich fuer ``facility_admin`` erscheinen —
weder fuer die operativen Rollen (assistant/staff/lead) noch fuer
``super_admin`` (installations-weiter Bereich, kein Facility-Kontext).

**Refs #1813 verschiebt den Ort, nicht die Aussage.** Aus den beiden
Sidebar-Zeilen „Konfiguration" und „Feldvorlagen" sind Reiter des Bereichs
„Verwaltung" geworden; „Konfiguration" heisst dabei jetzt
„Dokumentationstypen" (Konzept #1801 §4.4 — das alte Label war neben
„Feldvorlagen" nichtssagend, die Seiten-H1 hiess ohnehin schon so). Der
Dateiname bleibt: die QA-Handtest-Matrix referenziert ihn (ENT-CONFIG-01).

Zwei Praezisierungen gegenueber vorher:

* Die **Positiv**-Aussage haengt an den Reiter-Testids (``subnav-tab-config``,
  ``subnav-tab-config-fieldtemplates``) statt an einem blossen Substring —
  ein Link irgendwo im Seitenkoerper wuerde den Test sonst gruen faerben.
* Die **Negativ**-Aussage zum Label ist auf die Navigations-Ausschnitte
  eingeengt. „Dokumentationstypen" ist ein normales Fachwort und kommt
  legitim im Seitenkoerper vor (z. B. in der Statistik); seitenweit
  verboten waere es ein Fehlalarm-Generator.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

CONFIG_LINK = "/config/document-types/"
CONFIG_LABEL = "Dokumentationstypen"
CONFIG_TAB = 'data-testid="subnav-tab-config"'

FIELDTEMPLATES_LINK = "/config/field-templates/"
FIELDTEMPLATES_LABEL = "Feldvorlagen"
FIELDTEMPLATES_TAB = 'data-testid="subnav-tab-config-fieldtemplates"'


def _nav_slices(content: str) -> str:
    """Die Navigations-Ausschnitte einer Seite: Desktop-Sidebar + mobiles Menue.

    Der Rest der Seite bleibt aussen vor — dort duerfen Fachbegriffe wie
    „Dokumentationstypen" oder „Feldvorlagen" frei vorkommen.
    """
    start = content.index('data-testid="nav-items"')
    sidebar = content[start : content.index("</div>", start)]
    mobile = content[content.index('aria-label="Mobile Navigation"') :]
    return sidebar + mobile


@pytest.mark.django_db
class TestConfigNavEntry:
    def test_facility_admin_reaches_document_types_via_the_verwaltung_subnav(self, client, admin_user):
        """Der Einstieg liegt jetzt im Bereich „Verwaltung" — als benannter Reiter."""
        client.force_login(admin_user)
        content = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

        assert CONFIG_TAB in content
        assert CONFIG_LINK in content
        assert CONFIG_LABEL in content

    def test_facility_admin_reaches_document_types_mobile_via_the_verwaltung_hub(self, client, admin_user):
        """facility_admin erreicht die Dokumentationstypen weiterhin mobil.

        Refs #1813 (Etappe 3): der Weg hat sich geaendert, die Aussage nicht.
        Bis zur Ziel-IA (Konzept #1801 §5.2) stand „Konfiguration" als eigene
        Zeile im Mehr-Menue; jetzt steht dort der Bereich „Verwaltung", und der
        Reiter liegt auf dessen Seite. Der Test laeuft den Weg deshalb ab,
        statt nur eine Zeile zu zaehlen — nur so belegt er „mobil erreichbar"
        und nicht bloss „Link vorhanden".
        """
        client.force_login(admin_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        mobile_nav = content[content.index('aria-label="Mobile Navigation"') :]

        assert 'data-testid="mobile-nav-verwaltung"' in mobile_nav

        hub = client.get(reverse("core:verwaltung_uebersicht")).content.decode()
        assert CONFIG_TAB in hub
        assert CONFIG_LINK in hub

    def test_assistant_does_not_see_config_entry(self, client, assistant_user):
        """Assistenz sieht keinen Konfigurations-Einstieg."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert CONFIG_LINK not in content
        assert CONFIG_LABEL not in _nav_slices(content)

    def test_staff_does_not_see_config_entry(self, client, staff_user):
        """Fachkraft sieht keinen Konfigurations-Einstieg."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert CONFIG_LINK not in content
        assert CONFIG_LABEL not in _nav_slices(content)

    def test_lead_does_not_see_config_entry(self, client, lead_user):
        """Leitung sieht keinen Konfigurations-Einstieg (nur Anwendungsbetreuung)."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert CONFIG_LINK not in content
        assert CONFIG_LABEL not in _nav_slices(content)

    def test_lead_datenschutz_hub_has_no_config_tab(self, client, lead_user):
        """Gegenprobe auf der Reiter-Ebene: der Hub, den die Leitung sieht, fuehrt
        nicht in die Verwaltung."""
        client.force_login(lead_user)
        content = client.get(reverse("core:datenschutz_uebersicht")).content.decode()

        assert CONFIG_TAB not in content
        assert FIELDTEMPLATES_TAB not in content

    def test_super_admin_does_not_see_config_entry(self, client, super_admin_user):
        """super_admin (installations-weit, kein Facility-Kontext) sieht keinen Einstieg."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert CONFIG_LINK not in content
        assert CONFIG_LABEL not in _nav_slices(content)


@pytest.mark.django_db
class TestFeldvorlagenNavEntry:
    """Refs #1717: 'Feldvorlagen' ist ein eigener Einstieg, getrennt von den
    Dokumentationstypen — gleiche Sichtbarkeits-Regel (facility_admin only)."""

    def test_facility_admin_reaches_fieldtemplates_via_the_verwaltung_subnav(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:verwaltung_uebersicht")).content.decode()

        assert FIELDTEMPLATES_TAB in content
        assert FIELDTEMPLATES_LINK in content
        assert FIELDTEMPLATES_LABEL in content

    def test_facility_admin_reaches_fieldtemplates_mobile_via_the_verwaltung_hub(self, client, admin_user):
        """Wie bei den Dokumentationstypen: mobil fuehrt der Weg ueber den Hub."""
        client.force_login(admin_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()
        mobile_nav = content[content.index('aria-label="Mobile Navigation"') :]

        assert 'data-testid="mobile-nav-verwaltung"' in mobile_nav

        hub = client.get(reverse("core:verwaltung_uebersicht")).content.decode()
        assert FIELDTEMPLATES_TAB in hub
        assert FIELDTEMPLATES_LINK in hub

    def test_assistant_does_not_see_feldvorlagen_entry(self, client, assistant_user):
        client.force_login(assistant_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert FIELDTEMPLATES_LINK not in content
        assert FIELDTEMPLATES_LABEL not in _nav_slices(content)

    def test_staff_does_not_see_feldvorlagen_entry(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert FIELDTEMPLATES_LINK not in content
        assert FIELDTEMPLATES_LABEL not in _nav_slices(content)

    def test_lead_does_not_see_feldvorlagen_entry(self, client, lead_user):
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert FIELDTEMPLATES_LINK not in content
        assert FIELDTEMPLATES_LABEL not in _nav_slices(content)

    def test_super_admin_does_not_see_feldvorlagen_entry(self, client, super_admin_user):
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert FIELDTEMPLATES_LINK not in content
        assert FIELDTEMPLATES_LABEL not in _nav_slices(content)
