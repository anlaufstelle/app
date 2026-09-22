"""E2E-Spec fuer #2272 — GESCHRIEBEN, NICHT GEFAHREN (Standardblock 46).

Lane F-1 hat in dieser Welle KEIN Playwright-Recht (nur die eine benannte
Playwright-Lane S-C darf E2E fahren, seriell unter dem globalen CI-Lock,
Port 8844, ``POSTGRES_DB=anlaufstelle_e2e``). Diese Datei ist deshalb
ausschliesslich GESCHRIEBEN worden — der Orchestrator fuehrt sie zu gegebener
Zeit aus; ein axe-Nachweis fuer die betroffenen Seiten macht ebenfalls der
Orchestrator (``make test-a11y-locked``), nicht diese Lane.

Prueft den zentralen, uebersetzbaren Klarnamenhinweis an dynamischen
Text-/Textarea-Feldern (``core.forms.dynamic.build_dynamic_form_field``,
Konstante ``DYNAMIC_FIELD_PLAINTEXT_HINT``) am ECHT GERENDERTEN Formular —
Pendant zu den Form-Objekt-Unit-Tests in
``src/tests/test_forms_dynamic.py::TestDynamicFieldPlaintextHint``:

  (1) Der Hinweis erscheint an einem TEXT-/TEXTAREA-Feld OHNE konfigurierten
      Hilfetext — an Ereignis-, Personen- UND Fall-Formular (dieselbe
      dynamische Feldlogik, ``DynamicDataForm``/``build_dynamic_form_field``
      ohne Entitaets-Sonderfall).
  (2) Ein konfigurierter fachlicher Hilfetext bleibt vollstaendig erhalten,
      der Hinweis kommt zusaetzlich dazu (kein Ersatz).
  (3) Enthaelt ein konfigurierter Hilfetext das Wort „Klarnamen“ bereits
      selbst, haengt der Hinweis NICHTS ein zweites Mal an (kein Doppeltext).
  (4) Bearbeiten (nicht nur Neuanlage) zeigt den Hinweis ebenfalls — geprueft
      am Personen-Formular als Vertreter (dieselbe Rendering-Komponente wie
      Neuanlage, ``components/_form_field.html``).

Wegwerf-Konvention wie ``test_client_dynamic_fields.py``/
``test_case_dynamic_fields.py``: Namen mit ``E2E-Hint-…``-Praefix + uuid4-
Suffix, Best-effort-Cleanup in ``finally``. Die Config-UI-/Formular-Helfer
kommen von dort (dieselbe Wegwerf-Konvention, kein Duplikat).
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors
from .test_client_dynamic_fields import (
    CLIENT_DETAIL_RE,
    ENTITY_SUBMIT,
    _create_entity_doctype,
    _create_field_template,
    _delete_doctype_safe,
    _detach_document_type,
    _slug_of,
    fill_dynamic_field,
    switch_document_type,
)
from .test_config_ui import FIELDTEMPLATE_LIST, FORM_SUBMIT, _assign_free_field
from .test_field_conditions import _delete_field_template

pytestmark = pytest.mark.e2e

# Text, den ``DYNAMIC_FIELD_PLAINTEXT_HINT`` (core/forms/dynamic.py) traegt —
# NICHT der volle Wortlaut (der duerfte sich aendern, ohne dass dieser Test
# mitziehen muesste), sondern der stabile Kern-Begriff der Zusage.
HINT_MARKER = "Klarnamen"


def _hint_name(kind: str) -> str:
    return f"E2E-Hint-{kind}-{uuid.uuid4().hex[:6]}"


def _create_field_template_with_help_text(page, base_url, name, field_type, help_text) -> None:
    """Wie ``_create_field_template`` (test_client_dynamic_fields), aber mit
    ``help_text`` in DERSELBEN Anlage — das Feld steht auf dem Anlage-
    Formular genauso wie auf dem Bearbeiten-Formular
    (``config/field_templates/form.html`` rendert beide aus demselben
    generischen Feld-Loop, ``is_edit`` steuert nur Zusatzbloecke)."""
    page.goto(f"{base_url}{FIELDTEMPLATE_LIST}new/", wait_until="domcontentloaded")
    page.fill("input#id_name", name)
    page.select_option("select#id_field_type", value=field_type)
    page.fill("textarea#id_help_text", help_text)
    page.locator(FORM_SUBMIT).click()
    page.wait_for_url(re.compile(r"/config/field-templates/$"))


class TestDynamicFieldPlaintextHintAcrossEntities:
    """(1)-(3): Hinweis an Ereignis-, Personen- und Fall-Formular, ohne
    Doppeltext und ohne einen konfigurierten Hilfetext zu verdraengen."""

    @pytest.mark.parametrize(
        ("entity_type", "create_path", "select_entity"),
        [
            ("event", "/events/new/", "events"),
            ("client", "/clients/new/", "clients"),
            ("case", "/cases/new/", "cases"),
        ],
    )
    def test_hint_present_without_duplication_on_create_form(
        self, authenticated_page, base_url, entity_type, create_path, select_entity
    ):
        page = authenticated_page
        errors = track_console_errors(page)

        ft_no_help = _hint_name("kein-hilfetext")  # text, ohne help_text
        ft_with_help = _hint_name("mit-hilfetext")  # textarea, mit fachlichem Hilfetext
        ft_already_warns = _hint_name("warnt-schon")  # text, help_text enthaelt "Klarnamen" bereits
        slug_no_help, slug_with_help, slug_already_warns = (
            _slug_of(n) for n in (ft_no_help, ft_with_help, ft_already_warns)
        )
        doc_type_name = _hint_name(f"Typ-{entity_type}")

        _create_field_template(page, base_url, ft_no_help, "text")
        _create_field_template_with_help_text(
            page, base_url, ft_with_help, "textarea", "Kurze Zusammenfassung des Anliegens."
        )
        # Fachlicher Hilfetext, der das Wort "Klarnamen" bereits selbst traegt
        # (Nachweis Punkt 3).
        _create_field_template_with_help_text(
            page, base_url, ft_already_warns, "text", "Bitte hier keine Klarnamen eintragen, nur das Kuerzel."
        )

        type_pk = _create_entity_doctype(page, base_url, doc_type_name, entity_type)
        _assign_free_field(page, ft_no_help)
        _assign_free_field(page, ft_with_help)
        _assign_free_field(page, ft_already_warns)

        try:
            # Kein ``?client=``-Prefill fuer den Fall-Pfad noetig: dieser Test
            # prueft nur das gerenderte Feldset nach dem Typwechsel, kein
            # Submit — ``CaseCreateView.get`` rendert auch ohne Client-Query.
            page.goto(f"{base_url}{create_path}", wait_until="domcontentloaded")
            switch_document_type(page, entity=select_entity, label=doc_type_name)

            # (1) Hinweis an einem Feld OHNE konfigurierten Hilfetext.
            no_help_field = page.locator(f"[name='{slug_no_help}']")
            expect(no_help_field).to_be_visible(timeout=10000)
            help_id = no_help_field.get_attribute("aria-describedby")
            assert help_id, f"{entity_type}: kein aria-describedby am Feld ohne Hilfetext"
            help_node = page.locator(f"#{help_id}")
            expect(help_node).to_contain_text(HINT_MARKER)

            # (2) Konfigurierter fachlicher Hilfetext bleibt erhalten UND der
            # Hinweis kommt zusaetzlich dazu.
            with_help_field = page.locator(f"[name='{slug_with_help}']")
            with_help_id = with_help_field.get_attribute("aria-describedby")
            with_help_node = page.locator(f"#{with_help_id}")
            expect(with_help_node).to_contain_text(HINT_MARKER)

            # (3) Kein Doppeltext, wenn der Hilfetext das Wort selbst schon
            # enthaelt.
            already_warns_field = page.locator(f"[name='{slug_already_warns}']")
            already_warns_id = already_warns_field.get_attribute("aria-describedby")
            already_warns_text = page.locator(f"#{already_warns_id}").inner_text()
            assert already_warns_text.lower().count(HINT_MARKER.lower()) == 1, (
                f"{entity_type}: Klarnamenhinweis erscheint doppelt: {already_warns_text!r}"
            )

            assert errors == [], f"Konsolen-Errors/CSP-Verstoesse ({entity_type}): {errors}"
        finally:
            _delete_doctype_safe(page, base_url, type_pk)
            for name in (ft_no_help, ft_with_help, ft_already_warns):
                _delete_field_template(page, base_url, name)


class TestDynamicFieldPlaintextHintOnEdit:
    """(4): der Hinweis erscheint auch beim BEARBEITEN, nicht nur bei der
    Neuanlage — Personen-Formular als Vertreter (identische
    Rendering-Komponente ``components/_form_field.html`` fuer alle drei
    Entitaeten, siehe Modul-Docstring)."""

    def test_hint_present_on_client_edit_form(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)

        ft_no_help = _hint_name("edit-kein-hilfetext")
        slug_no_help = _slug_of(ft_no_help)
        doc_type_name = _hint_name("Typ-edit-client")

        _create_field_template(page, base_url, ft_no_help, "text")
        type_pk = _create_entity_doctype(page, base_url, doc_type_name, "client")
        _assign_free_field(page, ft_no_help)

        client_pk = None
        try:
            pseudonym = _hint_name("Person")
            page.goto(f"{base_url}/clients/new/", wait_until="domcontentloaded")
            page.fill('input[name="pseudonym"]', pseudonym)
            switch_document_type(page, label=doc_type_name)
            fill_dynamic_field(page, f"input[name='{slug_no_help}']", "Testwert")
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CLIENT_DETAIL_RE)
            match = CLIENT_DETAIL_RE.search(page.url)
            assert match, f"Personen-Detail-URL nicht erkannt: {page.url}"
            client_pk = match.group(1)

            page.goto(f"{base_url}/clients/{client_pk}/edit/", wait_until="domcontentloaded")
            field = page.locator(f"input[name='{slug_no_help}']")
            expect(field).to_have_value("Testwert", timeout=10000)
            help_id = field.get_attribute("aria-describedby")
            assert help_id, "Bearbeiten-Formular: kein aria-describedby am Feld ohne Hilfetext"
            expect(page.locator(f"#{help_id}")).to_contain_text(HINT_MARKER)

            assert errors == [], f"Konsolen-Errors/CSP-Verstoesse (Bearbeiten): {errors}"
        finally:
            if client_pk:
                _detach_document_type(page, base_url, f"{base_url}/clients/{client_pk}/edit/", CLIENT_DETAIL_RE)
            _delete_doctype_safe(page, base_url, type_pk)
            _delete_field_template(page, base_url, ft_no_help)
