"""Leere dynamische Feldwerte auf der Ereignis-Detailseite (Refs #2220).

Ein optionales Zahlenfeld, das beim Anlegen leer bleibt, landet als ``None`` im
``data_json`` (ein leeres ``IntegerField`` liefert ``None`` in ``cleaned_data``,
anders als ``CharField`` → ``""`` oder ``MultipleChoiceField`` → ``[]``). Die
Detailseite rendert den Anzeigewert roh, deshalb stand dort der Python-Literal
``None`` statt einer Leerdarstellung.

``None`` ist dabei nicht auf Zahlenfelder beschränkt: über Import, Offline-Replay
oder Bestandsdaten kann es in jedem Feldtyp stehen. Die Tests prüfen deshalb
jeden Feldtyp und nicht nur den gemeldeten Fall.

Sollwert ist ein LEERES Feld — genau das, was die Nachbarfelder derselben Gruppe
„Erfasste Daten" mit ``""``/``[]`` heute schon rendern. Ein Gedankenstrich nur
für ``None`` würde dieselbe Liste in zwei Leerdarstellungen spalten.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import DocumentTypeField, Event, FieldTemplate
from tests._factories import make_document_type, make_event

#: Ein ``<dd>``, dessen gesamter Inhalt der Literalstring ``None`` ist — genau
#: die Form aus dem Trockenlauf: ``<dd class="text-sm font-medium "> None </dd>``.
DD_WITH_NONE_LITERAL = re.compile(r"<dd[^>]*>\s*None\s*</dd>")

#: Feldtypen, die einen skalaren Anzeigewert rendern (FILE geht in den
#: Marker-/Anhang-Zweig und trägt gar kein ``value``).
VALUE_FIELD_TYPES = [ft for ft in FieldTemplate.FieldType.values if ft != FieldTemplate.FieldType.FILE]


def field_value_html(content: str, label: str) -> str:
    """Der rohe ``<dd>``-Inhalt zum Feld ``label`` aus „Erfasste Daten"."""
    match = re.search(
        r"<dt[^>]*>\s*" + re.escape(label) + r"\s*(?:<span[^>]*>.*?</span>\s*)?</dt>\s*<dd[^>]*>(.*?)</dd>",
        content,
        re.DOTALL,
    )
    assert match is not None, f"Feld {label!r} steht nicht in der Gruppe „Erfasste Daten“"
    return match.group(1)


def detail_content(django_client, user, event) -> str:
    django_client.force_login(user)
    response = django_client.get(reverse("core:event_detail", kwargs={"pk": event.pk}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def doc_type_all_types(facility):
    """(Dokumentationstyp, ``{Feldtyp: Slug}``) mit je einem Feld pro Feldtyp."""
    doc_type = make_document_type(facility=facility, name="Alle Feldtypen")
    options = [{"slug": "a", "label": "Alpha", "is_active": True}]
    slugs = {}
    for sort_order, field_type in enumerate(VALUE_FIELD_TYPES):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name=f"Feld {field_type}",
            field_type=field_type,
            options_json=options if field_type in ("select", "multi_select") else [],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=sort_order)
        slugs[field_type] = ft.slug
    return doc_type, slugs


@pytest.mark.django_db
class TestEventDetailEmptyNumberField:
    """Der gemeldete Fall: „Dauer" (NUMBER) beim Anlegen leer gelassen."""

    def test_empty_number_field_renders_no_none_literal(
        self, client, staff_user, facility, client_identified, doc_type_contact
    ):
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": None, "notiz": ""},
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)

        assert "Dauer" in content, "Das leere Feld muss weiterhin mit Beschriftung erscheinen"
        assert DD_WITH_NONE_LITERAL.search(content) is None

    def test_empty_number_field_renders_like_its_empty_neighbour(
        self, client, staff_user, facility, client_identified, doc_type_contact
    ):
        """Leerdarstellung konsistent zu den Nachbarfeldern der Gruppe."""
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": None, "notiz": ""},
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)

        assert field_value_html(content, "Dauer").strip() == ""
        assert field_value_html(content, "Notiz").strip() == ""

    def test_number_field_with_value_still_renders_the_value(
        self, client, staff_user, facility, client_identified, doc_type_contact
    ):
        """Gegenprobe: gespeicherte Zahlen bleiben sichtbar — auch die 0."""
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 0, "notiz": "Testnotiz"},
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)

        assert field_value_html(content, "Dauer").strip() == "0"
        assert field_value_html(content, "Notiz").strip() == "Testnotiz"


@pytest.mark.django_db
class TestEventDetailNoneAcrossFieldTypes:
    """``None`` kann über Import/Offline/Bestand in JEDEM Feldtyp stehen."""

    @pytest.mark.parametrize("field_type", VALUE_FIELD_TYPES)
    def test_none_value_renders_empty_for_every_field_type(
        self, client, staff_user, facility, client_identified, doc_type_all_types, field_type
    ):
        doc_type, slugs = doc_type_all_types
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json=dict.fromkeys(slugs.values()),
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)
        label = f"Feld {field_type}"

        assert label in content, "Das leere Feld muss weiterhin mit Beschriftung erscheinen"
        # BOOLEAN übersetzt ``None`` schon heute zu „Nein" — die übrigen Typen
        # reichen den Wert unverändert durch und müssen leer rendern.
        expected = "Nein" if field_type == FieldTemplate.FieldType.BOOLEAN else ""
        assert field_value_html(content, label).strip() == expected

    def test_no_none_literal_anywhere_on_the_page(
        self, client, staff_user, facility, client_identified, doc_type_all_types
    ):
        doc_type, slugs = doc_type_all_types
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json=dict.fromkeys(slugs.values()),
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)

        assert DD_WITH_NONE_LITERAL.search(content) is None


@pytest.mark.django_db
class TestEventDetailOrphanedNoneValue:
    """Verwaiste Slugs (Wert bleibt, Feld nicht mehr am Typ) rendern gleich."""

    def test_orphaned_none_value_renders_empty(self, client, staff_user, facility, client_identified, doc_type_contact):
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"dauer": 5, "notiz": "x"},
            created_by=staff_user,
        )
        # Das Feld aus dem Typ nehmen, den Wert im ``data_json`` stehen lassen.
        orphan = FieldTemplate.objects.get(facility=facility, name="Dauer")
        DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=orphan).delete()
        Event.objects.filter(pk=event.pk).update(data_json={orphan.slug: None, "notiz": "x"})

        content = detail_content(client, staff_user, event)

        assert "Nicht mehr zugeordnet" in content
        assert DD_WITH_NONE_LITERAL.search(content) is None
        assert field_value_html(content, "Dauer").strip() == ""
