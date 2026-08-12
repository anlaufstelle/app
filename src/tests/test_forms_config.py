"""Unit-Tests für Facility-/DocumentType-Scoping in ``core.forms.config`` (Refs #1811).

Mutation-Survivor-Triage 2026-08-10 (#1811) fand sechs sicherheitsrelevante
Testlücken rund um Mandantentrennung, die die View-Ebene
(``test_config_document_types.py``, ``test_config_field_templates.py``,
``test_config_field_groups.py``) nicht trifft, weil dort immer der reguläre,
korrekt gescopte View-Aufruf getestet wird. Hier werden die drei betroffenen
``ModelForm``-Klassen direkt instanziiert, um genau die mutierten Bedingungen
zu treffen:

* ``DocumentTypeForm.__init__`` — ``and``→``or`` bei der Facility-Bindung
  einer bestehenden Instanz (mutmut_8).
* ``FieldTemplateForm.__init__`` (mutmut_3) + ``clean_name`` (mutmut_9) —
  die facility-gescopte Namens-Eindeutigkeit.
* ``FieldGroupForm.__init__`` (mutmut_4) + ``clean_name`` (mutmut_10) — die
  document-type-gescopte Namens-Eindeutigkeit.
"""

from __future__ import annotations

import pytest

from core.forms.config import DocumentTypeForm, FieldGroupForm, FieldTemplateForm
from core.models import DocumentType, FieldGroup, FieldTemplate
from tests._form_helpers import assert_field_error, assert_form_valid

# ---------------------------------------------------------------------------
# DocumentTypeForm — Facility-Bindung darf eine bestehende Instanz nie
# umhängen (Refs #1811, mutmut_8: `and` → `or`)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentTypeFormFacilityBinding:
    def test_facility_kwarg_never_reassigns_an_existing_instance(self, facility, second_facility):
        """Refs #1811 (mutmut_8, ``and``→``or``): der Bindungs-Zweig darf nur
        bei NEUEN Objekten greifen (``instance.facility_id is None``). Für ein
        bestehendes Objekt mit fremdem ``facility``-Kwarg (z. B. weil der
        Formularaufbau versehentlich die Scope-Facility statt der
        Objekt-Facility übergibt) darf die Zuordnung NIE überschrieben
        werden — sonst könnte eine Instanz einer fremden Facility zugewiesen
        werden."""
        dt = DocumentType.objects.create(facility=facility, name="Bestand")
        form = DocumentTypeForm(instance=dt, facility=second_facility)
        assert form.instance.facility == facility

    def test_facility_kwarg_still_binds_a_brand_new_instance(self, facility):
        """Gegenprobe: für ein NEUES Objekt (``instance.facility_id is None``)
        muss die Bindung weiterhin greifen — sonst würde ``form.save()`` ohne
        Facility scheitern."""
        form = DocumentTypeForm(facility=facility)
        assert form.instance.facility == facility


# ---------------------------------------------------------------------------
# FieldTemplateForm — Namens-Eindeutigkeit ist facility-gescopt
# ---------------------------------------------------------------------------


def _valid_field_template_data(**overrides):
    data = {
        "name": "Neue Vorlage",
        "field_type": FieldTemplate.FieldType.TEXT,
        "options_json": "[]",
        "is_active": "on",
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestFieldTemplateFormFacilityScoping:
    def test_facility_kwarg_is_stored_on_the_form(self, facility):
        """Refs #1811 (mutmut_3, ``self.facility = None``): ohne das
        gespeicherte Kwarg liefe die nachfolgende ``clean_name``-Prüfung
        ungescoped."""
        form = FieldTemplateForm(facility=facility)
        assert form.facility == facility

    def test_duplicate_name_within_same_facility_is_rejected(self, facility):
        """Refs #1811 (mutmut_9, ``clean_name``): der Kern der facility-
        gescopten Eindeutigkeitsprüfung — ein zweiter Name in DERSELBEN
        Einrichtung muss als Formfehler abgewiesen werden."""
        FieldTemplate.objects.create(facility=facility, name="Doppelt", field_type=FieldTemplate.FieldType.TEXT)
        form = FieldTemplateForm(data=_valid_field_template_data(name="Doppelt"), facility=facility)
        assert_field_error(form, "name", "existiert in dieser Einrichtung bereits")

    def test_same_name_in_a_different_facility_is_not_a_collision(self, facility, second_facility):
        """Refs #1811 (mutmut_9, ``clean_name``): Gegenprobe — liefe die
        Eindeutigkeitsprüfung UNGESCOPED (statt facility-gescoped), würde ein
        Name aus einer FREMDEN Einrichtung fälschlich als Kollision gemeldet.
        Das wäre zugleich ein Cross-Tenant-Enumeration-Leak über den
        Validierungsfehler."""
        FieldTemplate.objects.create(
            facility=second_facility, name="Fremdname", field_type=FieldTemplate.FieldType.TEXT
        )
        form = FieldTemplateForm(data=_valid_field_template_data(name="Fremdname"), facility=facility)
        assert_form_valid(form)


# ---------------------------------------------------------------------------
# FieldGroupForm — document_type-Bindung + Namens-Eindeutigkeit
# ---------------------------------------------------------------------------


def _valid_field_group_data(**overrides):
    data = {"name": "Neue Gruppe"}
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestFieldGroupFormDocTypeBinding:
    def test_doc_type_kwarg_never_reassigns_an_existing_instance(self, facility, doc_type_contact):
        """Refs #1811 (mutmut_4, ``and``→``or`` analog zu ``DocumentTypeForm``):
        eine bestehende ``FieldGroup`` darf nie einem anderen
        Dokumentationstyp zugeordnet werden, nur weil ein ``doc_type``-Kwarg
        mitgegeben wird."""
        other_doc_type = DocumentType.objects.create(facility=facility, name="Anderer Typ")
        group = FieldGroup.objects.create(document_type=doc_type_contact, name="Bestandsgruppe")
        form = FieldGroupForm(instance=group, doc_type=other_doc_type)
        assert form.instance.document_type == doc_type_contact

    def test_doc_type_kwarg_still_binds_a_brand_new_instance(self, doc_type_contact):
        """Gegenprobe: ein NEUES Objekt muss weiterhin gebunden werden."""
        form = FieldGroupForm(doc_type=doc_type_contact)
        assert form.instance.document_type == doc_type_contact


@pytest.mark.django_db
class TestFieldGroupFormNameScoping:
    def test_duplicate_name_within_same_doc_type_is_rejected(self, doc_type_contact):
        """Refs #1811 (mutmut_10, ``clean_name``): der Kern der
        document-type-gescopten Eindeutigkeitsprüfung."""
        FieldGroup.objects.create(document_type=doc_type_contact, name="Doppelt")
        form = FieldGroupForm(data=_valid_field_group_data(name="Doppelt"), doc_type=doc_type_contact)
        assert_field_error(form, "name", "existiert für diesen Dokumentationstyp bereits")

    def test_same_name_on_a_different_doc_type_is_not_a_collision(self, facility, doc_type_contact):
        """Refs #1811 (mutmut_10, ``clean_name``): Gegenprobe — liefe die
        Prüfung ungescoped über ALLE Dokumentationstypen (statt nur
        ``self.doc_type``), würde ein gleichnamiger Gruppenname auf einem
        ANDEREN Typ fälschlich als Kollision gemeldet — ein
        Cross-Tenant-/Cross-Type-Enumeration-Leak über den Validierungsfehler."""
        other_doc_type = DocumentType.objects.create(facility=facility, name="Anderer Typ 2")
        FieldGroup.objects.create(document_type=other_doc_type, name="Gleicher Name")
        form = FieldGroupForm(data=_valid_field_group_data(name="Gleicher Name"), doc_type=doc_type_contact)
        assert_form_valid(form)
