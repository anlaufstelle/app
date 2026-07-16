"""Static config checks for the ``screenshot`` doc-generation command.

Playwright/PIL are only imported lazily inside ``Command.handle()`` (see
``core/management/commands/screenshot.py``), so the module itself — and its
``SHOTS`` config — can be imported and asserted on without a live E2E server.

Refs #1594, #1595.
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


class TestStatistikenWaitJs:
    """Refs #1594: die Statistik-Seite zeichnet ihre Charts clientseitig
    asynchron — der Shot muss auf das ``data-charts-ready``-Attribut warten,
    nicht nur auf den (sofort vorhandenen, aber leeren) Canvas-Selektor.
    """

    def test_statistiken_shot_waits_for_charts_ready_marker(self):
        shot = _shot("statistiken")
        assert shot.wait_js is not None
        assert "data-charts-ready" in shot.wait_js

    def test_statistik_extern_shot_unaffected(self):
        """external_report.html hat keine Charts — kein wait_js noetig."""
        assert _shot("statistik-extern").wait_js is None
