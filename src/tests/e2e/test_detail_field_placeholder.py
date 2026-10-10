"""E2E: sr-only-Platzhalter „Nicht angegeben" (#2244) + multi_select-Rohwerte (#2243).

Ergänzt ``test_accessibility.py::TestAxeDetailseiten``: jene Matrix scannt die
Seed-Detailseiten allgemein, garantiert aber weder ein LEERES dynamisches Feld
noch ein ``multi_select``-Feld ohne Optionen — genau die beiden Randfälle, die
#2243/#2244 beheben. Diese Datei legt beide gezielt an einem eigenen
Dokumentationstyp an (kein Eingriff in den Seed) und prüft sowohl das
sichtbare Markup als auch axe (WCAG) auf der Ereignis-Detailseite.

Schreibt/verändert NICHTS am geteilten Seed — ``DocumentType``/``FieldTemplate``/
``Event`` sind Zusatzobjekte in der bestehenden Einrichtung des ersten Seed-
Klienten, analog zu ``detail_pks`` in ``test_accessibility.py``.

Serieller Lauf unter dem globalen CI-Lock. Kommando (seriell, Port
8844, ``POSTGRES_DB=anlaufstelle_e2e``):
``.venv/bin/python -m pytest src/tests/e2e/test_detail_field_placeholder.py -q -p no:cacheprovider``
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._axe import assert_within_baseline, load_baseline, run_axe
from ._helpers import django_shell
from .test_accessibility import BASELINE_PATH

pytestmark = [pytest.mark.e2e, pytest.mark.a11y]


@pytest.fixture(scope="session")
def placeholder_event_pk(e2e_env):
    """Ereignis mit einem leeren Textfeld und einem ``multi_select`` ohne Optionen.

    Muster aus ``test_accessibility.py::detail_pks`` (``django_shell`` statt
    Durchklicken): ``multi_select`` ohne Optionen lässt sich über das
    Checkbox-Widget der UI nicht befüllen (keine Optionen ⇒ keine Checkboxen,
    verifiziert gegen ``_widget_multi_select``/``FieldTemplateForm.
    clean_options_json``) — der reale Weg ist eine Feldvorlage, deren Optionen
    NACH dem Erfassen entfernt wurden (Bestandsdaten, #2243-Ticket-Notiz); die
    ORM-Erstellung hier bildet exakt diesen Endzustand ab. Das Ereignis wird
    mit gueltigen Optionen gespeichert (der Schreibpfad prueft Optionsbezuege);
    die Optionen entfernt ein direktes SQL-Update, weil Vorlagen-Speichern und
    Queryset-Updates verwendete Optionen schuetzen — der Zustand ist nur noch
    als Altbestand (Import, Direktzugriff) erreichbar, das Rendering muss ihn
    dennoch lesbar darstellen.
    """
    script = (
        "from django.utils import timezone;"
        "from core.models import Client, DocumentType, DocumentTypeField, Event, FieldTemplate;"
        "client = Client.objects.active().order_by('created_at').first();"
        "facility = client.facility;"
        "creator = Event.objects.order_by('created_at').first().created_by;"
        "doc_type = DocumentType.objects.create(facility=facility, "
        "name='E2E Platzhalter 2243 2244', entity_type=DocumentType.EntityType.EVENT);"
        "text_ft = FieldTemplate.objects.create(facility=facility, name='E2E Leerfeld', "
        "field_type=FieldTemplate.FieldType.TEXT);"
        "ms_ft = FieldTemplate.objects.create(facility=facility, "
        "name='E2E Mehrfachauswahl ohne Optionen', "
        "field_type=FieldTemplate.FieldType.MULTI_SELECT, "
        "options_json=[{'slug': 'a', 'label': 'A'}, {'slug': 'b', 'label': 'B'}]);"
        "DocumentTypeField.objects.create(document_type=doc_type, field_template=text_ft, sort_order=0);"
        "DocumentTypeField.objects.create(document_type=doc_type, field_template=ms_ft, sort_order=1);"
        "event = Event.objects.create(facility=facility, client=client, document_type=doc_type, "
        "occurred_at=timezone.now(), data_json={text_ft.slug: '', ms_ft.slug: ['a', 'b']}, "
        "created_by=creator);"
        "from django.db import connection;"
        "meta = FieldTemplate._meta;"
        "connection.cursor().execute('UPDATE ' + meta.db_table + ' SET options_json = %s WHERE ' "
        "+ meta.pk.column + ' = %s', ['[]', str(ms_ft.pk)]);"
        "print('event', event.pk)"
    )
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition(" ")
        if key == "event" and value.strip() and value.strip() != "None":
            return value.strip()
    pytest.fail("Seed-Skript lieferte keine Event-PK für den Platzhalter-Testfall")


class TestDetailFieldPlaceholder:
    """#2243 (rohe multi_select-Liste) + #2244 (sr-only „Nicht angegeben")."""

    def test_empty_field_and_raw_multiselect_render_correctly(self, authenticated_page, base_url, placeholder_event_pk):
        page = authenticated_page
        page.goto(f"{base_url}/events/{placeholder_event_pk}/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible(timeout=15000)

        # #2243: multi_select ohne Optionen zeigt eine lesbare, kommagetrennte
        # Liste statt der rohen Python-Repräsentation.
        ms_dd = page.locator("dt:has-text('E2E Mehrfachauswahl ohne Optionen') + dd")
        expect(ms_dd).to_contain_text("a, b")
        expect(ms_dd).not_to_contain_text("[")

        # #2244: das leere Textfeld bleibt visuell leer (kein sichtbarer
        # Platzhalter), trägt aber den sr-only-Text „Nicht angegeben".
        text_dd = page.locator("dt:has-text('E2E Leerfeld') + dd")
        expect(text_dd.locator(".sr-only")).to_contain_text("Nicht angegeben")
        expect(text_dd).to_contain_text("Nicht angegeben")

    def test_no_new_axe_findings(self, authenticated_page, base_url, placeholder_event_pk):
        page = authenticated_page
        page.goto(f"{base_url}/events/{placeholder_event_pk}/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible(timeout=15000)

        baseline = load_baseline(BASELINE_PATH)
        result = run_axe(page, base_url)
        # Eigener page_key, NICHT in der Baseline ⇒ jeder critical/serious-Fund
        # ist rot. Die geteilte Baseline-Datei bleibt
        # unverändert (nur Lesezugriff über ``load_baseline``).
        assert_within_baseline("event_detail_field_placeholder", result, baseline)
