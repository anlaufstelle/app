"""E2E-Tests fuer Bugfixes v1.0: #229 (i18n), #156 (Default-Dokumentationstyp), #154 (Statistik-Buttons).

Verifiziert die drei Bugfixes:
- HTML lang-Attribut ist korrekt gesetzt
- Standard-Dokumentationstyp wird vorausgewaehlt
- Zeitraum-Buttons aktualisieren sich visuell nach HTMX-Klick
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def _run_shell(script, env):
    """Django-Shell-Befehl mit worker-aware Environment ausfuehren."""
    python = ".venv/bin/python" if os.path.exists(".venv/bin/python") else sys.executable
    subprocess.run(
        [python, "src/manage.py", "shell", "-c", script],
        check=True,
        capture_output=True,
        env=env,
    )


def _set_default_document_type(doc_type_name, env):
    """Setzt den Standard-Dokumentationstyp via Django-Shell."""
    script = (
        "from core.models import Settings, DocumentType, Facility; "
        "f = Facility.objects.first(); "
        f"dt = DocumentType.objects.filter(facility=f, name='{doc_type_name}').first(); "
        "s, _ = Settings.objects.get_or_create(facility=f); "
        "s.default_document_type = dt; "
        "s.save()"
    )
    _run_shell(script, env)


def _clear_default_document_type(env):
    """Entfernt den Standard-Dokumentationstyp."""
    script = (
        "from core.models import Settings, Facility; "
        "f = Facility.objects.first(); "
        "s = Settings.objects.filter(facility=f).first(); "
        "s.default_document_type = None; "
        "s.save() if s else None"
    )
    _run_shell(script, env)


class TestHtmlLangAttribute:
    """#229: HTML lang-Attribut muss auf 'de' gesetzt sein."""

    def test_base_template_has_lang_de(self, authenticated_page, base_url):
        """Verifiziert, dass das <html>-Tag lang='de' hat."""
        page = authenticated_page
        page.goto(f"{base_url}/", wait_until="domcontentloaded")
        lang = page.locator("html").get_attribute("lang")
        assert lang == "de", f"Erwartet lang='de', erhalten lang='{lang}'"

    def test_login_page_has_lang_de(self, base_url, browser):
        """Verifiziert, dass die Login-Seite lang='de' hat."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
        lang = page.locator("html").get_attribute("lang")
        assert lang == "de", f"Erwartet lang='de', erhalten lang='{lang}'"
        context.close()


class TestDefaultDocumentType:
    """#156: Standard-Dokumentationstyp aus Einstellungen wird vorausgewaehlt."""

    def test_default_document_type_preselected(self, authenticated_page, base_url, e2e_env):
        """Wenn ein Standard-Dokumentationstyp gesetzt ist, wird er im Formular vorausgewaehlt."""
        _set_default_document_type("Kontakt", e2e_env)
        try:
            page = authenticated_page
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

            # Der Dokumentationstyp-Dropdown sollte "Kontakt" vorausgewaehlt haben
            selected_value = page.locator("#id_document_type").input_value()
            assert selected_value != "", "Kein Standard-Dokumentationstyp vorausgewaehlt"

            # Dynamische Felder sollten vorgerendert sein
            dynamic_fields = page.locator("#dynamic-fields")
            assert dynamic_fields.locator("label").count() > 0, "Dynamische Felder nicht vorgerendert"
        finally:
            _clear_default_document_type(e2e_env)

    def test_no_default_shows_empty_dropdown(self, authenticated_page, base_url, e2e_env):
        """Ohne Standard-Dokumentationstyp bleibt der Dropdown leer."""
        _clear_default_document_type(e2e_env)
        page = authenticated_page
        page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")

        selected_value = page.locator("#id_document_type").input_value()
        assert selected_value == "", "Dropdown sollte leer sein wenn kein Standard gesetzt"


class TestStatisticsButtons:
    """#154: Zeitraum-Buttons aktualisieren sich visuell nach HTMX-Klick."""

    def test_quarter_button_becomes_active(self, authenticated_page, base_url):
        """Klick auf 'Quartal' markiert den Button als aktiv."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")

        # Monat-Button sollte initial aktiv sein (bg-accent)
        month_button = page.locator("button:has-text('Monat')")
        assert "bg-accent" in month_button.get_attribute("class")

        # Quartal-Button sollte initial inaktiv sein
        quarter_button = page.locator("button:has-text('Quartal')")
        assert "bg-accent" not in quarter_button.get_attribute("class")

        # Klick auf Quartal und warten bis HTMX den Inhalt ersetzt hat
        quarter_button.click()

        # Warten bis der Quartal-Button die aktive Klasse hat (nach HTMX-Swap)
        page.wait_for_function(
            """() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim().includes('Quartal') &&
                        btn.classList.contains('bg-accent')) {
                        return true;
                    }
                }
                return false;
            }""",
            timeout=10000,
        )

        # Quartal-Button sollte jetzt aktiv sein
        quarter_button = page.locator("button:has-text('Quartal')")
        assert "bg-accent" in quarter_button.get_attribute("class"), "Quartal-Button ist nicht aktiv nach Klick"

        # Monat-Button sollte jetzt inaktiv sein
        month_button = page.locator("button:has-text('Monat')")
        assert "bg-accent" not in month_button.get_attribute("class"), "Monat-Button ist noch aktiv nach Quartal-Klick"

    def test_half_year_button_becomes_active(self, authenticated_page, base_url):
        """Klick auf 'Halbjahr' markiert den Button als aktiv."""
        page = authenticated_page
        page.goto(f"{base_url}/statistics/", wait_until="domcontentloaded")

        # Klick auf Halbjahr
        half_button = page.locator("button:has-text('Halbjahr')")
        half_button.click()

        # Warten bis der Halbjahr-Button die aktive Klasse hat (nach HTMX-Swap)
        page.wait_for_function(
            """() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    if (btn.textContent.trim().includes('Halbjahr') &&
                        btn.classList.contains('bg-accent')) {
                        return true;
                    }
                }
                return false;
            }""",
            timeout=10000,
        )

        half_button = page.locator("button:has-text('Halbjahr')")
        assert "bg-accent" in half_button.get_attribute("class"), "Halbjahr-Button ist nicht aktiv nach Klick"
