"""DocumentTypeAdmin — slug/entity_type Immutabilität im Admin-UI (Fix-Runde 1,
Refs #1058, Review-Befund B-1).

Vor dem Fix waren ``slug``/``entity_type`` im Change-ModelForm editierbar —
eine Änderung über die Admin-UI ließ ``DocumentType.save()``s
Immutabilitäts-Guard einen unabgefangenen ``ValidationError`` werfen
(empirisch als HTTP 500 belegt). Zweitwirkung: ``slug`` war im Add-ModelForm
ein normales Pflichtfeld — ein Admin hätte ihn von Hand tippen müssen statt
die Model-Auto-Generierung greifen zu lassen.

Muster ``test_admin_doctype_inline.py`` (RequestFactory + Admin-Instanz direkt,
kein URL-/Sudo-Roundtrip — Repo hat keine Client-basierten Admin-URL-Tests,
siehe dortiger Docstring-Verweis auf ``test_admin_facility_scoping.py``).
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from core.admin.documents import DocumentTypeAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


def _request(rf, user, method="get"):
    """Admin-Request analog zur FacilityScopeMiddleware (setzt current_facility)."""
    request = getattr(rf, method)("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)


class TestChangeFormSlugEntityTypeReadonly:
    def test_change_form_excludes_slug_and_entity_type_as_editable_fields(self, rf, admin_user, doc_type_contact):
        """Django schließt ``readonly_fields`` von ``modelform_factory`` aus —
        beide Felder tauchen im Change-ModelForm gar nicht mehr als
        POST-bare Felder auf, der Crash-Pfad (Formular akzeptiert eine
        Änderung, ``save()`` wirft dann ValidationError) ist strukturell
        unmöglich."""
        request = _request(rf, admin_user)
        form_class = _admin().get_form(request, doc_type_contact)
        assert "slug" not in form_class.base_fields
        assert "entity_type" not in form_class.base_fields

    def test_get_readonly_fields_lists_slug_and_entity_type_for_existing_object(self, rf, admin_user, doc_type_contact):
        request = _request(rf, admin_user)
        readonly = _admin().get_readonly_fields(request, doc_type_contact)
        assert "slug" in readonly
        assert "entity_type" in readonly

    def test_get_readonly_fields_still_locks_system_type_when_set(self, rf, admin_user, doc_type_contact):
        """Bestehendes Verhalten (system_type) bleibt unverändert."""
        doc_type_contact.system_type = DocumentType.SystemType.CONTACT
        doc_type_contact.save()
        request = _request(rf, admin_user)
        readonly = _admin().get_readonly_fields(request, doc_type_contact)
        assert "system_type" in readonly

    def test_gesetzter_report_key_wird_readonly(self, rf, admin_user, doc_type_contact):
        """E3 (Refs #1849), Entscheid E-K: gesperrt erst, wenn ein Wert dasteht.

        Django schliesst ``readonly_fields`` von ``modelform_factory`` aus —
        ohne diese Sperre leerte jeder Change-Speichervorgang das Feld (der POST
        traegt es nicht) und liefe in den Modell-Guard.
        """
        request = _request(rf, admin_user)
        assert doc_type_contact.report_key  # Fixture traegt den Backfill-Wert
        assert "report_key" in _admin().get_readonly_fields(request, doc_type_contact)

    def test_leerer_report_key_bleibt_editierbar(self, rf, admin_user, facility):
        """Das Nachtragen ist der Reparaturpfad und muss auch im Admin offen bleiben."""
        ohne = DocumentType.objects.create(facility=facility, name="Ohne Schlüssel")
        request = _request(rf, admin_user)

        assert "report_key" not in _admin().get_readonly_fields(request, ohne)
        assert "report_key" in _admin().get_form(request, ohne).base_fields

    def test_change_form_still_allows_name_edit(self, rf, admin_user, doc_type_contact):
        request = _request(rf, admin_user)
        form_class = _admin().get_form(request, doc_type_contact)
        assert "name" in form_class.base_fields

    def test_saving_change_form_ignores_tampered_slug_post_value(self, rf, admin_user, doc_type_contact):
        """Defense-in-Depth-Beleg: ein manipulierter POST mit ``slug=…`` kann
        den (nicht mehr existenten) Formularwert gar nicht mehr einschleusen —
        kein ValidationError, kein 500, der Slug bleibt unverändert."""
        request = _request(rf, admin_user, method="post")
        original_slug = doc_type_contact.slug
        form_class = _admin().get_form(request, doc_type_contact)
        form = form_class(
            data={
                "facility": doc_type_contact.facility_id,
                "name": "Umbenannt",
                "sensitivity": doc_type_contact.sensitivity,
                "sort_order": 0,
                "slug": "tampered-value",  # existiert im Form nicht -> wird ignoriert
                "entity_type": DocumentType.EntityType.CLIENT,  # dito
            },
            instance=doc_type_contact,
        )
        assert form.is_valid(), form.errors
        obj = form.save(commit=False)
        _admin().save_model(request, obj, form, change=True)

        obj.refresh_from_db()
        assert obj.slug == original_slug
        assert obj.entity_type == DocumentType.EntityType.EVENT
        assert obj.name == "Umbenannt"


class TestAddFormSlugNotRequired:
    def test_add_form_excludes_slug_field(self, rf, admin_user):
        request = _request(rf, admin_user)
        form_class = _admin().get_form(request, obj=None)
        assert "slug" not in form_class.base_fields

    def test_add_without_slug_creates_document_type_with_auto_slug(self, rf, admin_user, facility):
        request = _request(rf, admin_user, method="post")
        form_class = _admin().get_form(request, obj=None)
        form = form_class(
            data={
                "facility": facility.pk,
                "name": "Admin-Angelegt",
                "sensitivity": DocumentType.Sensitivity.NORMAL,
                "entity_type": DocumentType.EntityType.EVENT,
                "sort_order": 0,
            }
        )
        assert form.is_valid(), form.errors
        obj = form.save(commit=False)
        _admin().save_model(request, obj, form, change=False)

        obj.refresh_from_db()
        assert obj.slug == "admin-angelegt"
        assert obj.pk is not None

    def test_add_form_still_allows_choosing_entity_type(self, rf, admin_user):
        """entity_type bleibt beim Anlegen frei waehlbar (nur nach dem
        Anlegen unveraenderlich) — anders als slug, das grundsaetzlich
        generiert wird."""
        request = _request(rf, admin_user)
        form_class = _admin().get_form(request, obj=None)
        assert "entity_type" in form_class.base_fields
