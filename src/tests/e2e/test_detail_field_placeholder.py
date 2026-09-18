"""E2E: multi_select-Rohwerte ohne Optionen (#2243).

Ergänzt ``test_accessibility.py::TestAxeDetailseiten``: jene Matrix scannt die
Seed-Detailseiten allgemein, garantiert aber kein ``multi_select``-Feld ohne
Optionen — genau der Randfall, den #2243 behebt. Diese Datei legt ihn gezielt
an einem eigenen Dokumentationstyp an (kein Eingriff in den Seed) und prüft
das sichtbare Markup auf der Ereignis-Detailseite.

Schreibt/verändert NICHTS am geteilten Seed — ``DocumentType``/``FieldTemplate``/
``Event`` sind Zusatzobjekte in der bestehenden Einrichtung des ersten Seed-
Klienten, analog zu ``detail_pks`` in ``test_accessibility.py``.

FÄHRT NICHT dieser Agent — nur die EINE Playwright-Lane des Orchestrators
unter dem globalen CI-Lock (Standardblock Punkt 46). Kommando (seriell, Port
8844, ``POSTGRES_DB=anlaufstelle_e2e``):
``.venv/bin/python -m pytest src/tests/e2e/test_detail_field_placeholder.py -q -p no:cacheprovider``
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell

pytestmark = [pytest.mark.e2e, pytest.mark.a11y]


@pytest.fixture(scope="session")
def placeholder_event_pk(e2e_env):
    """Ereignis mit einem leeren Textfeld und einem ``multi_select`` ohne Optionen.

    Das leere Textfeld ist fuer den #2243-Fall hier ungenutzt, wird aber von
    Folgetickets an derselben Stelle gebraucht (gleicher Dokumentationstyp).

    Muster aus ``test_accessibility.py::detail_pks`` (``django_shell`` statt
    Durchklicken): ``multi_select`` ohne Optionen lässt sich über das
    Checkbox-Widget der UI nicht befüllen (keine Optionen ⇒ keine Checkboxen,
    verifiziert gegen ``_widget_multi_select``/``FieldTemplateForm.
    clean_options_json``) — der reale Weg ist eine Feldvorlage, deren Optionen
    NACH dem Erfassen entfernt wurden (Bestandsdaten, #2243-Ticket-Notiz); die
    ORM-Erstellung hier bildet exakt diesen Endzustand ab.
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
        "field_type=FieldTemplate.FieldType.MULTI_SELECT, options_json=[]);"
        "DocumentTypeField.objects.create(document_type=doc_type, field_template=text_ft, sort_order=0);"
        "DocumentTypeField.objects.create(document_type=doc_type, field_template=ms_ft, sort_order=1);"
        "event = Event.objects.create(facility=facility, client=client, document_type=doc_type, "
        "occurred_at=timezone.now(), data_json={text_ft.slug: '', ms_ft.slug: ['a', 'b']}, "
        "created_by=creator);"
        "print('event', event.pk)"
    )
    for line in django_shell(e2e_env, script):
        key, _, value = line.partition(" ")
        if key == "event" and value.strip() and value.strip() != "None":
            return value.strip()
    pytest.fail("Seed-Skript lieferte keine Event-PK für den Platzhalter-Testfall")


class TestDetailFieldPlaceholder:
    """#2243: ``multi_select`` ohne Optionen zeigt eine lesbare Liste."""

    def test_raw_multiselect_renders_readable_list(self, authenticated_page, base_url, placeholder_event_pk):
        page = authenticated_page
        page.goto(f"{base_url}/events/{placeholder_event_pk}/", wait_until="domcontentloaded")
        expect(page.locator("#main-content h1")).to_be_visible(timeout=15000)

        # #2243: multi_select ohne Optionen zeigt eine lesbare, kommagetrennte
        # Liste statt der rohen Python-Repräsentation.
        ms_dd = page.locator("dt:has-text('E2E Mehrfachauswahl ohne Optionen') + dd")
        expect(ms_dd).to_contain_text("a, b")
        expect(ms_dd).not_to_contain_text("[")
