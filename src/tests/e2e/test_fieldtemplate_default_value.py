"""E2E-Tests für FieldTemplate.default_value (Refs #624)."""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _run_shell(script, env):
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    subprocess.run(
        [python, "src/manage.py", "shell", "--no-imports", "-c", script],
        check=True,
        capture_output=True,
        env=env,
    )


def _set_default_value(slug, value, env):
    """Setzt ``default_value`` an der Vorlage **der Einrichtung des Testnutzers**.

    Ohne den ``facility``-Filter greift ``.first()`` irgendeine gleichnamige
    Vorlage: seit Refs #1970 stattet auch der Admin-Anlagepfad jede neue
    Einrichtung mit dem Standardkatalog aus, und ``test_admin_site.py`` legt im
    selben Lauf eine zweite Einrichtung an. Der Aufraeumschritt traf dann die
    fremde Zeile, waehrend die hier gepruefte ihren Wert behielt.
    """
    script = (
        "from core.models import FieldTemplate, User; "
        "facility = User.objects.get(username='admin').facility; "
        f"ft = FieldTemplate.objects.filter(slug='{slug}', facility=facility).first(); "
        f"ft.default_value = '{value}'; "
        "ft.save() if ft else None"
    )
    _run_shell(script, env)


def _clear_default_value(slug, env):
    _set_default_value(slug, "", env)


class TestDefaultValuePrefill:
    """Bei Neu-Anlage eines Events zeigt ein FieldTemplate mit default_value den Wert vorausgefüllt."""

    def test_number_default_appears_in_create_form(self, authenticated_page, base_url, e2e_env):
        _set_default_value("dauer", "15", e2e_env)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

            page.select_option("select[name='document_type']", label="Kontakt")
            page.wait_for_selector("input[name='dauer']", state="visible", timeout=5000)

            dauer_value = page.locator("input[name='dauer']").input_value()
            assert dauer_value == "15", f"Erwartet '15', bekam '{dauer_value}'"
        finally:
            _clear_default_value("dauer", e2e_env)

    def test_default_not_set_when_empty(self, authenticated_page, base_url, e2e_env):
        _clear_default_value("dauer", e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
        page.select_option("select[name='document_type']", label="Kontakt")
        page.wait_for_selector("input[name='dauer']", state="visible", timeout=5000)

        dauer_value = page.locator("input[name='dauer']").input_value()
        assert dauer_value == "", f"Erwartet leer, bekam '{dauer_value}'"
