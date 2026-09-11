"""Leere dynamische Feldwerte in Personen-/Fall-Detail und Auskunft (Refs #2224).

Dieselbe Klasse wie auf der Ereignis-Detailseite (Refs #2220): ein optionales
Zahlen-, Datums- oder Zeitfeld, das leer bleibt, liegt als ``None`` im
``data_json``; über Import, Offline-Replay oder Bestandsdaten kann ``None`` in
jedem Feldtyp stehen. Wird der Wert ungefiltert gerendert, steht der
Python-Literal ``None`` auf der Seite — im Fall der Auskunft nach Art. 15 sogar
in einem Dokument, das die betroffene Person ausgehändigt bekommt.

Geprüft werden die drei Flächen, die dieselben Anzeigewerte rendern:

* Personen-Detail und Fall-Detail über das gemeinsame Partial
  ``core/partials/dynamic_data_display.html`` (reguläre und verwaiste Felder),
* die Auskunft (PDF-HTML) über ``core/export/client_data_pdf.html`` —
  Stammdaten, Fälle, Fälle im Papierkorb und die Ereignis-Spalte „Daten".

Sollwert ist auch hier ein LEERES Feld, konsistent zu den Nachbarfeldern, die
mit ``""``/``[]`` schon heute leer rendern. Eine gespeicherte ``0`` ist ein
echter Wert und bleibt sichtbar.
"""

from __future__ import annotations

import re

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Case, DocumentType, DocumentTypeField, FieldTemplate
from tests._factories import make_document_type, make_event

#: Ein ``<dd>`` des gemeinsamen Partials, dessen Inhalt nur ``None`` ist.
DD_WITH_NONE_LITERAL = re.compile(r"<dd[^>]*>\s*None\s*</dd>")

#: Feldtypen mit skalarem Anzeigewert (FILE geht in den Marker-/Anhang-Zweig).
VALUE_FIELD_TYPES = [ft for ft in FieldTemplate.FieldType.values if ft != FieldTemplate.FieldType.FILE]


def build_all_types_doc_type(facility, entity_type, name, label_prefix):
    """(DocumentType, ``{Slug: None}``) mit je einem Feld pro Feldtyp."""
    doc_type = make_document_type(facility=facility, name=name, entity_type=entity_type)
    slugs = []
    for sort_order, field_type in enumerate(VALUE_FIELD_TYPES):
        ft = FieldTemplate.objects.create(
            facility=facility,
            name=f"{label_prefix} {field_type}",
            field_type=field_type,
            options_json=[{"slug": "a", "label": "Alpha", "is_active": True}]
            if field_type in ("select", "multi_select")
            else [],
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=sort_order)
        slugs.append(ft.slug)
    return doc_type, dict.fromkeys(slugs)


def add_orphan_slug(facility, label):
    """Eine Feldvorlage OHNE Zuordnung zum Typ — ihr Slug wird verwaist befüllt."""
    return FieldTemplate.objects.create(
        facility=facility,
        name=label,
        field_type=FieldTemplate.FieldType.NUMBER,
    ).slug


def field_value_html(content: str, label: str) -> str:
    """Der rohe ``<dd>``-Inhalt zum Feld ``label`` im gemeinsamen Partial."""
    match = re.search(
        r"<dt[^>]*>\s*" + re.escape(label) + r"\s*</dt>\s*<dd[^>]*>(.*?)</dd>",
        content,
        re.DOTALL,
    )
    assert match is not None, f"Feld {label!r} steht nicht in den Zusatzfeldern"
    return match.group(1)


def detail_content(django_client, user, url_name, pk) -> str:
    """Detailseite als HTML — angemeldet, mit Statuspruefung."""
    django_client.force_login(user)
    response = django_client.get(reverse(url_name, kwargs={"pk": pk}))
    assert response.status_code == 200
    return response.content.decode()


@pytest.fixture
def client_with_empty_fields(facility, client_identified):
    """Person mit einem Zusatzfeld je Feldtyp — alle leer — plus verwaistem Slug."""
    doc_type, data = build_all_types_doc_type(facility, DocumentType.EntityType.CLIENT, "Personenprobe", "PF")
    orphan_slug = add_orphan_slug(facility, "PF verwaist")
    data[orphan_slug] = None
    client_identified.document_type = doc_type
    client_identified.data_json = data
    client_identified.save()
    return client_identified


@pytest.fixture
def case_with_empty_fields(facility, client_with_empty_fields, staff_user):
    """Fall mit einem Zusatzfeld je Feldtyp — alle leer — plus verwaistem Slug."""
    doc_type, data = build_all_types_doc_type(facility, DocumentType.EntityType.CASE, "Fallprobe", "FF")
    orphan_slug = add_orphan_slug(facility, "FF verwaist")
    data[orphan_slug] = None
    return Case.objects.create(
        facility=facility,
        client=client_with_empty_fields,
        title="Probefall",
        document_type=doc_type,
        data_json=data,
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestClientDetailEmptyValues:
    """Personen-Detail (``core/partials/dynamic_data_display.html``)."""

    def test_no_none_literal_in_dynamic_fields(self, client, lead_user, client_with_empty_fields):
        client.force_login(lead_user)
        response = client.get(reverse("core:client_detail", kwargs={"pk": client_with_empty_fields.pk}))
        assert response.status_code == 200
        content = response.content.decode()

        assert "Zusatzfelder" in content
        assert DD_WITH_NONE_LITERAL.search(content) is None

    @pytest.mark.parametrize("field_type", VALUE_FIELD_TYPES)
    def test_empty_value_renders_empty_for_every_field_type(
        self, client, lead_user, client_with_empty_fields, field_type
    ):
        content = detail_content(client, lead_user, "core:client_detail", client_with_empty_fields.pk)

        # BOOLEAN löst ``None`` schon heute zu „Nein" auf.
        expected = "Nein" if field_type == FieldTemplate.FieldType.BOOLEAN else ""
        assert field_value_html(content, f"PF {field_type}").strip() == expected

    def test_orphaned_field_renders_empty(self, client, lead_user, client_with_empty_fields):
        content = detail_content(client, lead_user, "core:client_detail", client_with_empty_fields.pk)

        assert "Nicht mehr zugeordnete Felder" in content
        assert field_value_html(content, "PF verwaist").strip() == ""

    def test_stored_values_stay_visible(self, client, lead_user, facility, client_identified):
        """Gegenprobe: die gespeicherte 0 ist ein echter Wert."""
        doc_type, data = build_all_types_doc_type(facility, DocumentType.EntityType.CLIENT, "Werteprobe", "WF")
        data[next(slug for slug in data if slug.endswith("number"))] = 0
        data[next(slug for slug in data if slug.endswith("text"))] = "Bestandswert"
        client_identified.document_type = doc_type
        client_identified.data_json = data
        client_identified.save()

        content = detail_content(client, lead_user, "core:client_detail", client_identified.pk)

        assert field_value_html(content, "WF number").strip() == "0"
        assert field_value_html(content, "WF text").strip() == "Bestandswert"


@pytest.mark.django_db
class TestCaseDetailEmptyValues:
    """Fall-Detail — dasselbe Partial, eigener Einstieg."""

    def test_no_none_literal_in_dynamic_fields(self, client, lead_user, case_with_empty_fields):
        client.force_login(lead_user)
        response = client.get(reverse("core:case_detail", kwargs={"pk": case_with_empty_fields.pk}))
        assert response.status_code == 200
        content = response.content.decode()

        assert "Zusatzfelder" in content
        assert DD_WITH_NONE_LITERAL.search(content) is None

    def test_empty_number_field_renders_empty(self, client, lead_user, case_with_empty_fields):
        content = detail_content(client, lead_user, "core:case_detail", case_with_empty_fields.pk)

        assert field_value_html(content, "FF number").strip() == ""
        assert field_value_html(content, "FF date").strip() == ""

    def test_orphaned_field_renders_empty(self, client, lead_user, case_with_empty_fields):
        content = detail_content(client, lead_user, "core:case_detail", case_with_empty_fields.pk)

        assert "Nicht mehr zugeordnete Felder" in content
        assert field_value_html(content, "FF verwaist").strip() == ""


@pytest.mark.django_db
class TestClientExportPdfEmptyValues:
    """Auskunft nach Art. 15 (``core/export/client_data_pdf.html``, HTML-Stufe)."""

    def _render(self, client_obj, facility, user):
        from core.services.client.export import render_client_export_html

        return render_client_export_html(client_obj, facility, user)

    def test_no_none_literal_anywhere_in_the_export(
        self, facility, admin_user, staff_user, client_with_empty_fields, case_with_empty_fields
    ):
        """Kein Python-Literal im ausgehändigten Dokument — über alle Abschnitte."""
        deleted_doc_type, deleted_data = build_all_types_doc_type(
            facility, DocumentType.EntityType.CASE, "Papierkorbprobe", "TF"
        )
        deleted_orphan = add_orphan_slug(facility, "TF verwaist")
        deleted_data[deleted_orphan] = None
        Case.objects.create(
            facility=facility,
            client=client_with_empty_fields,
            title="Papierkorbfall",
            document_type=deleted_doc_type,
            data_json=deleted_data,
            created_by=staff_user,
            is_deleted=True,
        )
        event_doc_type, event_data = build_all_types_doc_type(
            facility, DocumentType.EntityType.EVENT, "Ereignisprobe", "EF"
        )
        make_event(
            facility=facility,
            client=client_with_empty_fields,
            document_type=event_doc_type,
            occurred_at=timezone.now(),
            data_json=event_data,
            created_by=staff_user,
            case=case_with_empty_fields,
        )

        html = self._render(client_with_empty_fields, facility, admin_user)

        # Alle vier Abschnitte, die dynamische Werte tragen, sind im Dokument.
        for marker in ("Personenprobe", "Fallprobe", "Papierkorbprobe", "Ereignisprobe"):
            assert marker in html, f"Abschnitt mit {marker!r} fehlt im Export"
        assert "None" not in html

    def test_stored_values_stay_visible(self, facility, admin_user, client_identified):
        """Gegenprobe: die gespeicherte 0 steht in der Auskunft."""
        doc_type, data = build_all_types_doc_type(facility, DocumentType.EntityType.CLIENT, "Werteprobe", "WF")
        data[next(slug for slug in data if slug.endswith("number"))] = 0
        client_identified.document_type = doc_type
        client_identified.data_json = data
        client_identified.save()

        html = self._render(client_identified, facility, admin_user)

        assert re.search(r"<th>WF number</th><td>\s*0\s*</td>", html) is not None
        assert "None" not in html
