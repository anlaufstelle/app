"""CSP ``style-src`` ist strikt — Header-Ende-zu-Ende-Test (Refs #1660, I5 aus #1376).

Der Architektur-Guard in ``test_architecture_guards_templates.py`` prueft die
*Konfiguration* (Settings-Dict) und den *Quelltext* der Templates. Dieser Test
schliesst die Kette auf der anderen Seite: er liest den tatsaechlich
**ausgelieferten** ``Content-Security-Policy``-Header und vergleicht ihn gegen
das **gerenderte** Template-Markup. Nur so faellt auf, wenn Middleware-Rewrites
(Admin-/Demo-Relax) oder ein Template-Tag im ``<style>``-Block die Policy bzw.
den Hash unbemerkt verschieben.
"""

import base64
import hashlib
import re

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings
from django.urls import reverse

_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)

# Selbsttragende Sonderseiten mit per Hash freigegebenem Inline-``<style>``.
HASHED_STYLE_TEMPLATES = ("500.html", "503.html", "offline.html")


def csp_hash(block: str) -> str:
    """CSP-``'sha256-…'``-Token fuer den Inhalt eines ``<style>``-Elements."""
    return f"'sha256-{base64.b64encode(hashlib.sha256(block.encode('utf-8')).digest()).decode('ascii')}'"


def directive(csp: str, name: str) -> str:
    """Liest eine Direktive aus einem CSP-Header-Wert."""
    for part in csp.split(";"):
        part = part.strip()
        if part == name or part.startswith(f"{name} "):
            return part
    raise AssertionError(f"Direktive {name!r} fehlt in der Policy: {csp!r}")


@pytest.fixture
def delivered_csp(client):
    """``Content-Security-Policy``-Header einer normalen HTML-Antwort."""
    response = client.get(reverse("login"))
    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy")
    assert csp, "Keine CSP ausgeliefert — CSPMiddleware nicht aktiv?"
    return csp


@pytest.mark.django_db
class TestDeliveredStyleSrc:
    def test_style_src_has_no_unsafe_inline(self, delivered_csp):
        """Kernakzeptanzkriterium aus #1660."""
        style_src = directive(delivered_csp, "style-src")
        assert "'unsafe-inline'" not in style_src, (
            f"Der ausgelieferte CSP-Header erlaubt weiterhin Inline-Styles.\nstyle-src: {style_src}"
        )
        assert "'self'" in style_src

    def test_style_src_has_no_unsafe_hashes(self, delivered_csp):
        """``'unsafe-hashes'`` wuerde die sha256-Freigaben auf
        ``style=``-Attribute ausdehnen und den Gewinn kassieren."""
        assert "'unsafe-hashes'" not in directive(delivered_csp, "style-src")

    def test_script_src_stays_strict(self, delivered_csp):
        """Regression: die style-src-Umstellung darf script-src nicht anfassen."""
        script_src = directive(delivered_csp, "script-src")
        assert "'unsafe-inline'" not in script_src
        assert "'unsafe-eval'" not in script_src

    @pytest.mark.parametrize("template_name", HASHED_STYLE_TEMPLATES)
    def test_rendered_special_page_style_block_is_pinned(self, delivered_csp, template_name):
        """Der ``<style>``-Block der Sonderseiten muss im **gerenderten**
        Markup exakt den in der Policy gepinnten Hash ergeben.

        Faellt der Test, wuerde der Browser den Block stumm blocken: die
        Fehler-/Wartungs-/Offline-Seite kaeme voellig ungestylt an.
        """
        blocks = _STYLE_BLOCK.findall(render_to_string(template_name))
        assert blocks, f"{template_name} enthaelt keinen <style>-Block mehr."
        style_src = directive(delivered_csp, "style-src")
        for block in blocks:
            assert csp_hash(block) in style_src, (
                f"Der gerenderte <style>-Block aus {template_name} ist nicht in "
                "der CSP freigegeben.\n"
                f"Erwarteter Token: {csp_hash(block)}\n"
                f"style-src: {style_src}"
            )


@pytest.mark.django_db
class TestAdminStyleSrcRelax:
    """Das gevendorte django-unfold rendert einen aus ``UNFOLD['COLORS']``
    zusammengesetzten ``<style>``-Block (nicht hashbar) plus rund ein Dutzend
    ``style=``-Attribute. ``/admin-mgmt/`` behaelt daher — wie schon bei
    ``'unsafe-eval'`` — eine eng gescopte Ausnahme."""

    def test_admin_login_page_relaxes_style_src(self, client):
        response = client.get("/admin-mgmt/login/", follow=True)
        csp = response.headers.get("Content-Security-Policy")
        assert csp, "Keine CSP auf der Admin-Login-Seite"
        assert "'unsafe-inline'" in directive(csp, "style-src")
        assert "'unsafe-eval'" in directive(csp, "script-src")

    def test_app_pages_stay_strict(self, delivered_csp):
        """Die Admin-Ausnahme darf nicht auf App-Routen durchschlagen."""
        assert "'unsafe-inline'" not in directive(delivered_csp, "style-src")


@pytest.mark.django_db
class TestMaintenanceResponseStyleBlock:
    """``503.html`` wird von ``MaintenanceModeMiddleware`` **vor** der
    ``CSPMiddleware`` beantwortet — die Antwort traegt heute gar keine CSP.
    Der Hash steht trotzdem in der Policy: er kostet nichts und haelt die
    Seite auch dann heil, wenn die Middleware-Reihenfolge je wandert.
    """

    def test_503_body_style_block_hash_is_in_policy(self, tmp_path, delivered_csp):
        from core.middleware.maintenance import MaintenanceModeMiddleware

        flag = tmp_path / "maintenance.flag"
        flag.write_text("")
        MaintenanceModeMiddleware._cache = None
        try:
            with override_settings(MAINTENANCE_FLAG_FILE=str(flag), MAINTENANCE_ALLOW_IPS=[]):
                response = MaintenanceModeMiddleware(lambda r: r)(RequestFactory().get("/clients/"))
            assert response.status_code == 503
            blocks = _STYLE_BLOCK.findall(response.content.decode())
            assert blocks, "503-Antwort ohne <style>-Block"
            for block in blocks:
                assert csp_hash(block) in directive(delivered_csp, "style-src")
        finally:
            MaintenanceModeMiddleware._cache = None
