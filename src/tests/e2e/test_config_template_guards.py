"""E2E: Feldvorlagen — Typwechsel belegter Vorlagen und Deaktivieren belegter Pflichtfelder (CFG).

* Eine belegte Textvorlage lässt sich nicht auf Datei umstellen: Fehlermeldung statt
  Erfolg, alter Feldtyp und gespeicherte Werte bleiben; Text ↔ Textbereich bleibt möglich.
* Ein belegtes Pflichtfeld deaktivieren: das Feld fehlt im Formular, ein anderes Feld
  lässt sich speichern, der alte Wert bleibt erhalten und erscheint nach der
  Reaktivierung wieder.
"""

from __future__ import annotations

import re
import subprocess
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = pytest.mark.e2e


def _shell(e2e_env, script):
    try:
        return django_shell(e2e_env, script)
    except subprocess.CalledProcessError as exc:  # surface the traceback tail in the test report
        raise AssertionError(f"shell failed: {exc.stderr[-1200:]}") from exc


@pytest.fixture
def occupied_templates(e2e_env):
    marker = uuid.uuid4().hex[:8]
    script = f"""
from django.utils import timezone
from core.models import Client, DocumentType, DocumentTypeField, Event, FieldTemplate, Facility, User
f = Facility.objects.get(name='Hauptstelle')
u = User.objects.get(username='miriam')
dt = DocumentType.objects.create(facility=f, name='QAB-CFG-{marker}', slug='qab-cfg-{marker}',
                                 entity_type='event', sensitivity='normal')
text = FieldTemplate.objects.create(facility=f, name='QAB-Text-{marker}', slug='qab-text-{marker}', field_type='text')
req = FieldTemplate.objects.create(facility=f, name='QAB-Pflicht-{marker}', slug='qab-pflicht-{marker}',
                                   field_type='text', is_required=True)
DocumentTypeField.objects.create(document_type=dt, field_template=text, sort_order=0)
DocumentTypeField.objects.create(document_type=dt, field_template=req, sort_order=1)
c = Client.objects.create(facility=f, contact_stage='identified', pseudonym='QAB-CFG-{marker}', created_by=u)
e = Event.objects.create(facility=f, client=c, document_type=dt, occurred_at=timezone.now(), created_by=u,
                         data_json={{text.slug: 'Wichtiger Text', req.slug: 'Pflichtwert'}})
print('text=' + str(text.pk)); print('req=' + str(req.pk)); print('event=' + str(e.pk))
"""
    values = {"marker": marker}
    for line in _shell(e2e_env, script):
        key, _, value = line.partition("=")
        if key in {"text", "req", "event"}:
            values[key] = value
    assert len(values) == 4, values
    values["text_slug"] = f"qab-text-{marker}"
    values["req_slug"] = f"qab-pflicht-{marker}"
    return values


def _db(e2e_env, expression):
    script = f"from core.models import Event, FieldTemplate; print('val=' + str({expression}))"
    return next(ln for ln in _shell(e2e_env, script) if ln.startswith("val=")).removeprefix("val=")


class TestFieldTypeChangeOfUsedTemplate:
    def test_text_to_file_is_rejected_text_to_textarea_is_allowed(
        self, authenticated_page, base_url, e2e_env, occupied_templates
    ):
        page = authenticated_page
        url = f"{base_url}/config/field-templates/{occupied_templates['text']}/edit/"
        page.goto(url, wait_until="domcontentloaded")
        page.select_option("select[name='field_type']", "file")
        page.locator("#main-content form button[type='submit']").last.click()

        # Error instead of success: still on the edit form, a visible message, nothing changed.
        page.wait_for_load_state("domcontentloaded")
        assert re.search(r"/config/field-templates/[0-9a-f-]{36}/edit/", page.url), page.url
        expect(page.locator("#main-content [role='alert'], #main-content .text-red-600").first).to_be_visible(
            timeout=10000
        )
        assert _db(e2e_env, f"FieldTemplate.objects.get(pk='{occupied_templates['text']}').field_type") == "text"
        stored = _db(e2e_env, f"Event.objects.get(pk='{occupied_templates['event']}').data_json")
        assert "Wichtiger Text" in stored and "Pflichtwert" in stored

        # Text <-> Textbereich stays possible.
        page.goto(url, wait_until="domcontentloaded")
        page.select_option("select[name='field_type']", "textarea")
        page.locator("#main-content form button[type='submit']").last.click()
        page.wait_for_url(re.compile(r"/config/field-templates/$"), timeout=10000)
        assert _db(e2e_env, f"FieldTemplate.objects.get(pk='{occupied_templates['text']}').field_type") == "textarea"


class TestDeactivateUsedRequiredField:
    def _toggle(self, page, base_url, name):
        page.goto(f"{base_url}/config/field-templates/?q={name}", wait_until="domcontentloaded")
        row = page.locator("tr", has_text=name)
        expect(row).to_have_count(1, timeout=10000)
        row.locator("[data-testid='fieldtemplate-toggle-btn']").click()
        page.wait_for_url(re.compile(r"/config/field-templates/"), timeout=10000)

    def test_deactivated_required_field_is_missing_but_value_survives_and_returns(
        self, authenticated_page, base_url, e2e_env, occupied_templates
    ):
        page = authenticated_page
        marker = occupied_templates["marker"]
        edit_url = f"{base_url}/events/{occupied_templates['event']}/edit/"

        self._toggle(page, base_url, f"QAB-Pflicht-{marker}")
        assert _db(e2e_env, f"FieldTemplate.objects.get(pk='{occupied_templates['req']}').is_active") == "False"

        page.goto(edit_url, wait_until="domcontentloaded")
        expect(page.locator(f"[name='{occupied_templates['text_slug']}']")).to_have_count(1)
        expect(page.locator(f"[name='{occupied_templates['req_slug']}']")).to_have_count(0)
        page.fill(f"[name='{occupied_templates['text_slug']}']", "Geänderter Text")
        page.locator("#main-content form button[type='submit']").last.click()
        page.wait_for_url(re.compile(r"/events/[0-9a-f-]{36}/$"), timeout=10000)
        stored = _db(e2e_env, f"Event.objects.get(pk='{occupied_templates['event']}').data_json")
        assert "Geänderter Text" in stored
        assert "Pflichtwert" in stored, f"deactivated field value was lost: {stored}"

        self._toggle(page, base_url, f"QAB-Pflicht-{marker}")
        page.goto(edit_url, wait_until="domcontentloaded")
        expect(page.locator(f"[name='{occupied_templates['req_slug']}']")).to_have_value("Pflichtwert")
