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


class TestBeratungsstelleGalerieShot:
    """Refs #2148 (Maintainer-Entscheid 2026-09-06): einziger Shot, der den
    Paket-Unterschied der Zweigstelle Nord zeigt (Typwahl Kurzberatung/
    Beratungsgespraech/Krisengespraech/Begleitung statt Kontaktladen-Typen).
    """

    def test_shot_uses_emma_1_role_and_is_not_mobile(self):
        shot = _shot("ereignis-anlegen-beratungsstelle")
        assert shot.route == "core:event_create"
        assert shot.role == "emma_1"
        assert shot.mobile is False

    def test_lang_switch_usernames_include_emma_1(self):
        """Sonst rendert der EN-Durchlauf fuer den emma_1-Login deutsch."""
        assert "emma_1" in LANG_SWITCH_USERNAMES


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


class TestNeueFlaechenV081:
    """Refs #1843/#1900: die vier mit #1803/#1813 entstandenen Flaechen —
    Bereichs-Uebersichten, Statistik-Reiterzeile (Datensatz-Berichte-Reiter)
    und die ``/search/``-Vollseite — gehoeren in die Doku-Screenshots.
    """

    def test_hub_shots_exist_with_admin_role(self):
        """Beide Hubs zeigen Verwaltungs-Statuslisten; ``/verwaltung/`` sitzt
        hinter ``FacilityAdminRequiredMixin`` — wie beim dsgvo-paket-Shot
        braucht es den admin-Login (emma ist nur LEAD).
        """
        assert _shot("datenschutz-uebersicht").role == "admin"
        assert _shot("verwaltung-uebersicht").role == "admin"

    def test_statistik_berichte_shot_uses_german_tab_param(self):
        """Der Datensatz-Berichte-Reiter zeigt Reiterzeile + Berichtskarten;
        die Adresse ist seit #1804 deutsch (``?ansicht=datensaetze``).
        """
        assert _shot("statistik-berichte").route == "/statistics/?ansicht=datensaetze"

    def test_suche_shot_resolves_query_from_seed_client(self):
        """Der Bulk-Seed wuerfelt Pseudonyme — eine hartkodierte Query faende
        je nach Lauf nichts. Der Shot traegt deshalb einen Platzhalter, den
        ``_url`` aus dem ersten Seed-Client aufloest (Wortteil, damit die
        Ergebnisseite mehrere Treffer zeigt).
        """
        shot = _shot("suche")
        assert "{client_pseudonym}" in shot.route
        assert shot.full_page is True

    def test_url_substitutes_client_pseudonym_placeholder(self):
        from core.management.commands.screenshot import Command

        class _FakeClient:
            pseudonym = "Fuchs-12"

        cmd = Command()
        shot = _shot("suche")
        url = cmd._url("http://x", shot, {"client": _FakeClient()})
        assert url == "http://x/search/?q=Fuchs"

    def test_url_returns_none_without_client(self):
        """Kein Seed-Client → Shot wird uebersprungen statt eine leere
        Ergebnisseite zu dokumentieren (Muster der pk_kind-Aufloesung).
        """
        from core.management.commands.screenshot import Command

        cmd = Command()
        assert cmd._url("http://x", _shot("suche"), {"client": None}) is None
