"""Lösch-Blocker weisen Papierkorb-Anteile aus (Refs #1737, Option A).

Die M2-Blocker zählen über den Default-Manager, der ``is_deleted`` bewusst
nicht filtert — soft-gelöschte Personen blockieren damit Feldvorlagen UND
Dokumentationstypen. Die Meldung verlangte dafür einen unmöglichen Schritt
(„müsste zuerst geändert werden"), ohne Papierkorb zu erwähnen und ohne
Ausweg zu nennen.
"""

import pytest
from django.urls import reverse

from core.models import Client, DocumentType, FieldTemplate
from core.services.config import (
    ConfigDeleteBlocked,
    delete_document_type,
    field_template_delete_blockers,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_doc_type(facility):
    return DocumentType.objects.create(
        facility=facility,
        name="Personenbogen",
        slug="personenbogen",
        entity_type=DocumentType.EntityType.CLIENT,
    )


@pytest.fixture
def slug_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Feld X",
        slug="feld-x",
        field_type=FieldTemplate.FieldType.TEXT,
    )


def _make_client(facility, doc_type, *, slug=None, deleted=False, pseudonym="P-Blocker"):
    obj = Client.objects.create(
        facility=facility,
        pseudonym=pseudonym,
        document_type=doc_type,
        data_json={slug: "Wert"} if slug else {},
    )
    if deleted:
        obj.soft_delete()
    return obj


class TestFieldTemplateBlockerMentionsTrash:
    def test_trashed_client_is_counted_out(self, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug, deleted=True)
        reasons = field_template_delete_blockers(slug_template)
        assert any("Papierkorb" in r for r in reasons)

    def test_no_trash_hint_without_trashed_clients(self, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug)
        reasons = field_template_delete_blockers(slug_template)
        assert reasons
        assert not any("Papierkorb" in r for r in reasons)


class TestDocumentTypeBlockerMentionsTrash:
    def test_trashed_client_is_counted_out(self, rf, facility, client_doc_type, admin_user):
        _make_client(facility, client_doc_type, deleted=True)
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(request, client_doc_type)
        assert any("Papierkorb" in r for r in exc.value.reasons)

    def test_no_trash_hint_without_trashed_clients(self, rf, facility, client_doc_type, admin_user):
        _make_client(facility, client_doc_type)
        request = rf.post("/")
        request.user = admin_user
        request.current_facility = facility
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(request, client_doc_type)
        assert not any("Papierkorb" in r for r in exc.value.reasons)


class TestConfirmDeletePagesOfferTheWayOut:
    def test_document_type_dialog_links_to_trash(self, client, admin_user, facility, client_doc_type):
        _make_client(facility, client_doc_type, deleted=True)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_doctype_delete", args=[client_doc_type.pk])).content.decode()
        assert "config-delete-trash-hint" in body
        assert reverse("core:client_trash") in body

    def test_document_type_dialog_without_trash_has_no_hint(self, client, admin_user, facility, client_doc_type):
        _make_client(facility, client_doc_type)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_doctype_delete", args=[client_doc_type.pk])).content.decode()
        assert "config-delete-trash-hint" not in body

    def test_field_template_dialog_links_to_trash(self, client, admin_user, facility, client_doc_type, slug_template):
        _make_client(facility, client_doc_type, slug=slug_template.slug, deleted=True)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert "config-delete-trash-hint" in body
        assert reverse("core:client_trash") in body

    def test_field_template_dialog_without_trash_has_no_hint(
        self, client, admin_user, facility, client_doc_type, slug_template
    ):
        _make_client(facility, client_doc_type, slug=slug_template.slug)
        client.force_login(admin_user)
        body = client.get(reverse("core:config_fieldtemplate_delete", args=[slug_template.pk])).content.decode()
        assert "config-delete-trash-hint" not in body
