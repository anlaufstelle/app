"""Static config checks for the ``screenshot`` doc-generation command.

Playwright/PIL are only imported lazily inside ``Command.handle()`` (see
``core/management/commands/screenshot.py``), so the module itself — and its
``SHOTS`` config — can be imported and asserted on without a live E2E server.

Refs #1595.
"""

from core.management.commands.screenshot import LANG_SWITCH_USERNAMES, SHOTS


def _shot(name: str):
    return next(s for s in SHOTS if s.name == name)


class TestDsgvoPaketRole:
    """Refs #1595: dsgvo-paket sitzt hinter ``FacilityAdminRequiredMixin`` —
    der Default-Seed-User "emma" ist nur LEAD und bekommt 403.
    """

    def test_dsgvo_paket_shot_uses_facility_admin_role(self):
        assert _shot("dsgvo-paket").role == "admin"

    def test_lang_switch_usernames_include_admin(self):
        """Sonst rendert der EN-Durchlauf fuer den admin-Login deutsch."""
        assert "admin" in LANG_SWITCH_USERNAMES
