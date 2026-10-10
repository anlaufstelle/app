"""Leere dynamische Feldwerte auf der Ereignis-Detailseite (Refs #2220, #2243, #2244).

Ein optionales Zahlenfeld, das beim Anlegen leer bleibt, landet als ``None`` im
``data_json`` (ein leeres ``IntegerField`` liefert ``None`` in ``cleaned_data``,
anders als ``CharField`` → ``""`` oder ``MultipleChoiceField`` → ``[]``). Die
Detailseite rendert den Anzeigewert roh, deshalb stand dort der Python-Literal
``None`` statt einer Leerdarstellung.

``None`` ist dabei nicht auf Zahlenfelder beschränkt: über Import, Offline-Replay
oder Bestandsdaten kann es in jedem Feldtyp stehen. Die Tests prüfen deshalb
jeden Feldtyp und nicht nur den gemeldeten Fall.

Sollwert ist ein visuell LEERES Feld — genau das, was die Nachbarfelder
derselben Gruppe „Erfasste Daten" mit ``""``/``[]`` heute schon rendern. Ein
Gedankenstrich nur für ``None`` würde dieselbe Liste in zwei Leerdarstellungen
spalten. Seit dem Maintainer-Entscheid vom 2026-09-18 (#2244) trägt das leere
Feld zusätzlich einen ``sr-only``-Text „Nicht angegeben" für Screenreader —
das sichtbare Layout bleibt unverändert (Test via :func:`visible_only`).

#2243: ``multi_select`` ohne ``options_json`` rendert(e) die rohe
Python-Listen-Repräsentation (``['a']`` → ``[&#x27;a&#x27;]``) statt einer
lesbaren, kommagetrennten Aufzählung.
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

#: Der ``sr-only``-Platzhalter aus ``core_tags.display_dynamic_value`` (#2244).
SR_ONLY_SPAN = re.compile(r'<span class="sr-only">[^<]*</span>')


def visible_only(html: str) -> str:
    """Sichtbarer Rest, nachdem der ``sr-only``-Platzhalter entfernt wurde.

    Belegt, dass der #2244-Platzhalter das sichtbare Layout NICHT ändert —
    nur den ``sr-only``-Span entfernt lässt sich die alte Leer-Erwartung
    („nichts sichtbar") weiterprüfen.
    """
    return SR_ONLY_SPAN.sub("", html).strip()


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

        assert visible_only(field_value_html(content, "Dauer")) == ""
        assert visible_only(field_value_html(content, "Notiz")) == ""
        # #2244: der sr-only-Platzhalter ist da, aber unsichtbar (siehe oben).
        assert "Nicht angegeben" in field_value_html(content, "Dauer")
        assert "Nicht angegeben" in field_value_html(content, "Notiz")

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
        # #2244: echte Werte bekommen KEINEN „Nicht angegeben"-Platzhalter.
        assert "Nicht angegeben" not in field_value_html(content, "Dauer")
        assert "Nicht angegeben" not in field_value_html(content, "Notiz")


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
        dd_html = field_value_html(content, label)
        if field_type == FieldTemplate.FieldType.BOOLEAN:
            assert visible_only(dd_html) == "Nein"
            assert "Nicht angegeben" not in dd_html
        else:
            assert visible_only(dd_html) == ""
            # #2244: sr-only-Platzhalter für jeden leeren Feldtyp.
            assert "Nicht angegeben" in dd_html

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
        assert visible_only(field_value_html(content, "Dauer")) == ""
        assert "Nicht angegeben" in field_value_html(content, "Dauer")


@pytest.mark.django_db
class TestEventDetailMultiSelectWithoutOptions:
    """#2243: ``multi_select`` ohne ``options_json`` — über die config_ui

    verifiziert erzeugbar (``FieldTemplateForm.clean_options_json`` erzwingt
    für SELECT/MULTI_SELECT keine nicht-leere Optionsliste, ``src/core/forms/
    config.py``) — rendert(e) vorher die rohe Python-Listen-Repräsentation
    statt einer lesbaren, kommagetrennten Aufzählung.
    """

    def test_raw_list_does_not_render_python_repr(self, client, staff_user, facility, client_identified):
        doc_type = make_document_type(facility=facility, name="Mehrfachauswahl ohne Optionen")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Freie Mehrfachauswahl",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        # Unconfigured or invalid slugs are historical values, not new selections.
        type(event).objects.filter(pk=event.pk).update(data_json={ft.slug: ["a", "b"]})

        content = detail_content(client, staff_user, event)
        dd_html = field_value_html(content, "Freie Mehrfachauswahl")

        # Kein Python-repr() (weder roh noch HTML-escaped als &#x27;).
        assert "[" not in dd_html and "]" not in dd_html
        assert "&#x27;" not in dd_html
        assert dd_html.strip() == "a, b"

    def test_raw_list_values_are_escaped(self, client, staff_user, facility, client_identified):
        """Kein ``safe`` — HTML-Metazeichen in Slugs werden escaped (Brief Punkt 1)."""
        doc_type = make_document_type(facility=facility, name="Mehrfachauswahl escaping")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Escaping-Feld",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )

        # Unconfigured or invalid slugs are historical values, not new selections.
        type(event).objects.filter(pk=event.pk).update(data_json={ft.slug: ["<script>alert(1)</script>"]})

        content = detail_content(client, staff_user, event)
        dd_html = field_value_html(content, "Escaping-Feld")

        assert "<script>" not in dd_html
        assert "&lt;script&gt;" in dd_html


@pytest.mark.django_db
class TestEventDetailEmptyListValue:
    """#2244: eine leer gespeicherte ``multi_select``-Auswahl (``[]``, keine

    Checkbox angekreuzt) bekommt denselben ``sr-only``-Platzhalter wie
    ``None`` — nicht nur der ``None``-Zweig des Filters, auch der
    ``value == []``-Zweig ist ein eigener Leerfall.
    """

    def test_empty_list_value_renders_placeholder(self, client, staff_user, facility, client_identified):
        doc_type = make_document_type(facility=facility, name="Mehrfachauswahl leer gewaehlt")
        ft = FieldTemplate.objects.create(
            facility=facility,
            name="Leer gewaehltes Feld",
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "a", "label": "Alpha", "is_active": True}],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=0)
        event = make_event(
            facility=facility,
            client=client_identified,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={ft.slug: []},
            created_by=staff_user,
        )

        content = detail_content(client, staff_user, event)
        dd_html = field_value_html(content, "Leer gewaehltes Feld")

        assert visible_only(dd_html) == ""
        assert "Nicht angegeben" in dd_html
