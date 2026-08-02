"""DocumentTypeFieldInline — ``field_group``-Dropdown auf den Parent-DocumentType
scopen (Refs #1711).

Ohne Scoping listet der Django-Admin standardmaessig ALLE FieldGroups (auch
fremder Facilities/DocumentTypes) im Inline-Dropdown — ein facility_admin
koennte versehentlich eine fremde Gruppe waehlen (das Modell-``clean()``
faengt das zwar serverseitig ab, aber die UX ist irrefuehrend). Kein
Repo-Vorbild fuer Parent-Scoping in Inlines — dieses Test-Modul etabliert das
Muster: ``DocumentTypeAdmin.get_form`` merkt sich das Parent-Objekt auf dem
Request (``request._doctype_parent_obj``); ``DocumentTypeFieldInline.
formfield_for_foreignkey`` scopet das ``field_group``-Feld darauf. Muster
``test_admin_facility_scoping.py``.
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test import RequestFactory

from core.admin.documents import DocumentTypeAdmin, DocumentTypeFieldInline
from core.admin_site import anlaufstelle_admin_site
from core.models import DocumentType, DocumentTypeField

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


def _doctype_admin():
    return DocumentTypeAdmin(DocumentType, anlaufstelle_admin_site)


def _inline():
    return DocumentTypeFieldInline(DocumentType, anlaufstelle_admin_site)


class TestFieldGroupInlineScoping:
    def test_change_view_scopes_field_group_to_parent_doctype(
        self, rf, admin_user, doc_type_contact, field_group, foreign_field_group
    ):
        """Dropdown zeigt nur Gruppen des Parent-DocumentType — weder eine
        fremde Facility noch ein fremder DocumentType (selbst wenn dieser
        zufaellig zur gleichen Facility gehoerte) tauchen auf."""
        request = _request(rf, admin_user)
        _doctype_admin().get_form(request, doc_type_contact)

        db_field = DocumentTypeField._meta.get_field("field_group")
        formfield = _inline().formfield_for_foreignkey(db_field, request)

        groups = list(formfield.queryset)
        assert groups == [field_group]
        assert foreign_field_group not in groups

    def test_add_view_yields_empty_field_group_queryset(self, rf, admin_user, field_group):
        """Ein neuer (noch ungespeicherter) DocumentType hat noch keine
        Gruppen — das Dropdown bleibt leer (nicht etwa alle Gruppen aller
        DocumentTypes)."""
        request = _request(rf, admin_user)
        _doctype_admin().get_form(request, obj=None)

        db_field = DocumentTypeField._meta.get_field("field_group")
        formfield = _inline().formfield_for_foreignkey(db_field, request)

        assert list(formfield.queryset) == []

    def test_model_clean_defense_still_rejects_cross_doctype_field_group(
        self, doc_type_contact, foreign_doc_type, foreign_field_group, field_template
    ):
        """Die Admin-Dropdown-Beschraenkung ist reine UX — die eigentliche
        Verteidigung bleibt ``DocumentTypeField.clean()`` (unveraendert):
        ein (z.B. per manipuliertem POST) untergeschobener Cross-DocumentType-
        field_group wird weiterhin serverseitig abgelehnt."""
        dtf = DocumentTypeField(
            document_type=doc_type_contact,
            field_template=field_template,
            field_group=foreign_field_group,
        )
        with pytest.raises(ValidationError):
            dtf.full_clean()
