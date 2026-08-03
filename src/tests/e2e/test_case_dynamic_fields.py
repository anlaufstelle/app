"""E2E-Tests: dynamische Zusatzfelder an Fällen (M2 P15b, Refs #1058).

Schlankes Pendant zu ``test_client_dynamic_fields.py`` — der Fall-Pfad (P8)
spiegelt den Personen-Pfad (P7) 1:1, hier wird deshalb nur geprüft, was am Fall
strukturell anders ist:

  (a) **Roundtrip mit ``?client=``-Vorbelegung** (P8-C-7): das Fall-Formular trägt
      neben den HTMX-nachgeladenen Zusatzfeldern die Alpine-Autocomplete für die
      Person. Beide Mechaniken im selben Formular sind der eigentliche Prüfpunkt:
      Vorbelegung aus der URL (sichtbares Pseudonym + verstecktes ``client``-Feld),
      Typ-Select → HTMX-Swap → Felder füllen → Detail zeigt die Werte, den
      verschlüsselten im Klartext.

  (b) **Regressionswächter Ereignis-Picker**: weder ein ``entity_type=client``- noch
      ein ``entity_type=case``-Typ darf im Ereignis-Formular auftauchen (Gegenprobe:
      die Seed-Ereignistypen bleiben wählbar).

Die Config-UI-Helfer (Feldvorlage/Dokumentationstyp anlegen, HTMX-Typwechsel,
Cleanup) kommen aus ``test_client_dynamic_fields`` — dort sind sie samt
Wegwerf-Konvention dokumentiert. Cleanup-Grenze wie dort: der Wegwerf-**Fall** und
die Wegwerf-**Person** bleiben bestehen (die UI kennt für beide nur Lösch-Workflows
mit Vier-Augen-Prinzip bzw. Begründung); die E2E-DB wird pro Session frisch
``seed --flush`` aufgesetzt.
"""

import re
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors
from .test_client_dynamic_fields import (
    DOC_TYPE_SELECT,
    DYNAMIC_SECTION,
    ENTITY_SUBMIT,
    _create_entity_doctype,
    _create_field_template,
    _delete_doctype_safe,
    _detach_document_type,
    _slug_of,
    create_client,
    fill_dynamic_field,
    switch_document_type,
)
from .test_config_ui import _assign_free_field
from .test_field_conditions import _delete_field_template

pytestmark = pytest.mark.e2e

CASE_DETAIL_RE = re.compile(r"/cases/([0-9a-f-]+)/$")
CLIENT_AUTOCOMPLETE = "input[placeholder='Pseudonym eingeben...']"


def _case_name(kind: str) -> str:
    """Eindeutiger Wegwerf-Name mit ``E2E-Case``-Präfix."""
    return f"E2E-Case-{kind}-{uuid.uuid4().hex[:6]}"


class TestCaseDynamicFields:
    """Fall mit vorbelegter Person + Zusatzfeldern anlegen und im Detail lesen."""

    def test_case_with_client_prefill_and_dynamic_fields(self, authenticated_page, base_url):
        page = authenticated_page
        errors = track_console_errors(page)

        ft_plain = _case_name("ziel")  # text, unverschlüsselt
        ft_secret = _case_name("verlauf")  # textarea, verschlüsselt
        slug_plain, slug_secret = _slug_of(ft_plain), _slug_of(ft_secret)
        case_type = _case_name("Fallakte")

        value_plain = "Wohnungssicherung Q3"
        value_secret = "Vertraulicher Verlauf, Gespraech mit Amt"

        _create_field_template(page, base_url, ft_plain, "text")
        _create_field_template(page, base_url, ft_secret, "textarea", encrypted=True)
        type_pk = _create_entity_doctype(page, base_url, case_type, "case")
        _assign_free_field(page, ft_plain)
        _assign_free_field(page, ft_secret)

        case_pk = None
        try:
            pseudonym = _case_name("Person")
            client_pk = create_client(page, base_url, pseudonym)

            # --- Fall MIT ?client=-Vorbelegung anlegen --------------------------
            page.goto(f"{base_url}/cases/new/?client={client_pk}", wait_until="domcontentloaded")
            # Alpine übernimmt die Vorbelegung aus den data-Attributen: sichtbares
            # Pseudonym + verstecktes Pflichtfeld ``client``.
            expect(page.locator(CLIENT_AUTOCOMPLETE)).to_have_value(pseudonym, timeout=10000)
            expect(page.locator("input[name='client']")).to_have_value(client_pk)

            # Abgrenzung: das Fall-Select zeigt nur entity_type=case.
            select = page.locator(DOC_TYPE_SELECT)
            expect(select.locator("option", has_text=case_type)).to_have_count(1)
            expect(select.locator("option", has_text="Krisengespräch")).to_have_count(0)

            title = _case_name("Fall")
            page.fill('input[name="title"]', title)
            switch_document_type(page, entity="cases", label=case_type)
            fill_dynamic_field(page, f"input[name='{slug_plain}']", value_plain)
            fill_dynamic_field(page, f"textarea[name='{slug_secret}']", value_secret)
            page.locator(ENTITY_SUBMIT).click()
            page.wait_for_url(CASE_DETAIL_RE)
            case_pk = CASE_DETAIL_RE.search(page.url).group(1)

            # --- Detail zeigt Typ, Labels und Werte (verschlüsselt ⇒ Klartext) --
            expect(page.locator("h1")).to_contain_text(title)
            section = page.locator(DYNAMIC_SECTION)
            expect(section).to_be_visible(timeout=10000)
            expect(section).to_contain_text(case_type)
            expect(section).to_contain_text(ft_plain)
            expect(section).to_contain_text(value_plain)
            expect(section).to_contain_text(ft_secret)
            expect(section).to_contain_text(value_secret)
            # Die Person aus der URL-Vorbelegung hängt tatsächlich am Fall.
            expect(page.locator("#main-content")).to_contain_text(pseudonym)

            assert errors == [], f"Konsolen-Errors/CSP-Verstöße im Fall-Fluss: {errors}"
        finally:
            if case_pk:
                _detach_document_type(page, base_url, f"{base_url}/cases/{case_pk}/edit/", CASE_DETAIL_RE)
            _delete_doctype_safe(page, base_url, type_pk)
            for name in (ft_plain, ft_secret):
                _delete_field_template(page, base_url, name)


class TestEventPickerExcludesNonEventTypes:
    """Regressionswächter: der Ereignis-Picker bleibt auf ``entity_type=event``."""

    @pytest.mark.smoke
    def test_event_form_hides_client_and_case_types(self, authenticated_page, base_url):
        page = authenticated_page
        client_type = _case_name("PersonTyp")
        case_type = _case_name("FallTyp")
        client_pk = _create_entity_doctype(page, base_url, client_type, "client")
        case_type_pk = _create_entity_doctype(page, base_url, case_type, "case")
        try:
            page.goto(f"{base_url}/events/new/", wait_until="domcontentloaded")
            select = page.locator(DOC_TYPE_SELECT)
            expect(select.locator("option", has_text=client_type)).to_have_count(0)
            expect(select.locator("option", has_text=case_type)).to_have_count(0)
            # Auch sonst nirgends auf der Seite (kein Leck über Quick-Vorlagen o. Ä.).
            expect(page.locator("#main-content")).not_to_contain_text(client_type)
            expect(page.locator("#main-content")).not_to_contain_text(case_type)
            # Gegenprobe: der Filter ist nicht zu scharf.
            expect(select.locator("option", has_text="Krisengespräch")).to_have_count(1)
        finally:
            for pk in (client_pk, case_type_pk):
                _delete_doctype_safe(page, base_url, pk)
