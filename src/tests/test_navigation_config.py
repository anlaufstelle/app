"""Sichtbarkeit des Navigations-Eintrags "Konfiguration" (Refs #1056).

Der /config/-Bereich (DocumentType-/FieldTemplate-CRUD) ist Anwendungs-
betreuung vorbehalten (``FacilityAdminRequiredMixin``). Der Sidebar-/Mehr-
Eintrag darf daher ausschliesslich fuer ``facility_admin`` erscheinen —
weder fuer die operativen Rollen (assistant/staff/lead) noch fuer
``super_admin`` (installations-weiter Bereich, kein Facility-Kontext).
"""

from __future__ import annotations

import pytest
from django.urls import reverse

CONFIG_LINK = "/config/document-types/"
CONFIG_LABEL = "Konfiguration"


@pytest.mark.django_db
class TestConfigNavEntry:
    def test_facility_admin_sees_config_entry_desktop(self, client, admin_user):
        """facility_admin sieht den Konfiguration-Link in der Desktop-Sidebar."""
        client.force_login(admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert CONFIG_LINK in content
        assert CONFIG_LABEL in content

    def test_facility_admin_sees_config_entry_mobile(self, client, admin_user):
        """facility_admin sieht den Konfiguration-Link im mobilen Mehr-Menue."""
        client.force_login(admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()
        mobile_nav = content[content.index('aria-label="Mobile Navigation"') :]

        assert CONFIG_LINK in mobile_nav
        assert CONFIG_LABEL in mobile_nav

    def test_assistant_does_not_see_config_entry(self, client, assistant_user):
        """Assistenz sieht keinen Konfiguration-Link."""
        client.force_login(assistant_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert CONFIG_LINK not in content

    def test_staff_does_not_see_config_entry(self, client, staff_user):
        """Fachkraft sieht keinen Konfiguration-Link."""
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert CONFIG_LINK not in content

    def test_lead_does_not_see_config_entry(self, client, lead_user):
        """Leitung sieht keinen Konfiguration-Link (nur Anwendungsbetreuung)."""
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert CONFIG_LINK not in content

    def test_super_admin_does_not_see_config_entry(self, client, super_admin_user):
        """super_admin (installations-weit, kein Facility-Kontext) sieht keinen Konfiguration-Link."""
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert CONFIG_LINK not in content
        assert CONFIG_LABEL not in content


FIELDTEMPLATES_LINK = "/config/field-templates/"
FIELDTEMPLATES_LABEL = "Feldvorlagen"


@pytest.mark.django_db
class TestFeldvorlagenNavEntry:
    """Refs #1717: Eigener Nav-Eintrag 'Feldvorlagen' — gleiche Sichtbarkeits-
    Regel wie 'Konfiguration' (facility_admin only)."""

    def test_facility_admin_sees_feldvorlagen_entry_desktop(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert FIELDTEMPLATES_LINK in content
        assert FIELDTEMPLATES_LABEL in content

    def test_facility_admin_sees_feldvorlagen_entry_mobile(self, client, admin_user):
        client.force_login(admin_user)
        content = client.get(reverse("core:dashboard")).content.decode()
        mobile_nav = content[content.index('aria-label="Mobile Navigation"') :]

        assert FIELDTEMPLATES_LINK in mobile_nav
        assert FIELDTEMPLATES_LABEL in mobile_nav

    def test_assistant_does_not_see_feldvorlagen_entry(self, client, assistant_user):
        client.force_login(assistant_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert FIELDTEMPLATES_LINK not in content

    def test_staff_does_not_see_feldvorlagen_entry(self, client, staff_user):
        client.force_login(staff_user)
        content = client.get(reverse("core:zeitstrom")).content.decode()

        assert FIELDTEMPLATES_LINK not in content

    def test_lead_does_not_see_feldvorlagen_entry(self, client, lead_user):
        client.force_login(lead_user)
        content = client.get(reverse("core:dashboard")).content.decode()

        assert FIELDTEMPLATES_LINK not in content

    def test_super_admin_does_not_see_feldvorlagen_entry(self, client, super_admin_user):
        client.force_login(super_admin_user)
        content = client.get(reverse("core:system_dashboard")).content.decode()

        assert FIELDTEMPLATES_LINK not in content
        assert FIELDTEMPLATES_LABEL not in content
