"""Tests für die DocumentType-Config-CRUD-UI (M0 P5a, Refs #1056).

Deckt Rollen-Gate (nur facility_admin), Facility-Scoping/Suche/Filter der Liste,
den HTMX-Partial-Branch, das Anlegen (facility + CONFIG_CREATE-Audit über den
Service), die system_type-Immutabilität bei Bearbeitung, den freundlichen
Unique-Fehler, das blockierte/erfolgreiche Löschen (CONFIG_DELETE-Audit) sowie
die IDOR-404-Semantik. Mutations-/Audit-Feinheiten selbst prüft
``test_config_service.py`` — hier geht es um die View-Schicht.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, DocumentType, Event


def _valid_post(**overrides):
    data = {
        "name": "Neuer Typ",
        "category": DocumentType.Category.CONTACT,
        "sensitivity": DocumentType.Sensitivity.NORMAL,
        "is_active": "on",
    }
    data.update(overrides)
    return data


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Rollen-Gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_non_admin_forbidden_on_list(self, client, request, user_fixture):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(reverse("core:config_doctype_list"))
        assert response.status_code == 403

    def test_admin_allowed_on_list(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"))
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Liste: Facility-Scoping, Suche, Filter, HTMX-Partial
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestList:
    def test_foreign_doctype_not_visible(self, client, admin_user, doc_type_contact, foreign_doc_type):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"))
        body = response.content.decode()
        assert doc_type_contact.name in body
        assert foreign_doc_type.name not in body

    def test_search_filters_by_name(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Alpha", category="contact")
        DocumentType.objects.create(facility=facility, name="Beta", category="contact")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"q": "Alph"})
        body = response.content.decode()
        assert "Alpha" in body
        assert "Beta" not in body

    def test_category_filter(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Kontaktart", category="contact")
        DocumentType.objects.create(facility=facility, name="Leistungsart", category="service")
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"category": "service"})
        body = response.content.decode()
        assert "Leistungsart" in body
        assert "Kontaktart" not in body

    def test_is_active_filter(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Aktiver", category="contact", is_active=True)
        DocumentType.objects.create(facility=facility, name="Inaktiver", category="contact", is_active=False)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), {"is_active": "false"})
        body = response.content.decode()
        assert "Inaktiver" in body
        assert "Aktiver" not in body

    def test_htmx_request_renders_partial_only(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_list"), HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        names = [t.name for t in response.templates if t.name]
        assert "core/config/document_types/partials/table.html" in names
        assert "core/config/document_types/list.html" not in names
        assert "base.html" not in names


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCreate:
    def test_get_form_renders(self, client, admin_user):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_create"))
        assert response.status_code == 200

    def test_create_sets_facility_and_writes_audit(self, client, admin_user, facility):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Frisch"))
        assert response.status_code == 302
        obj = DocumentType.objects.get(name="Frisch")
        assert obj.facility == facility
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": obj.pk})
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(obj.pk)).exists()

    def test_empty_post_re_renders_without_500(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), {})
        assert response.status_code == 200
        assert not DocumentType.objects.filter(name="").exists()

    def test_unique_collision_is_form_error_not_500(self, client, admin_user, facility):
        DocumentType.objects.create(facility=facility, name="Doppel", category="contact")
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_create"), _valid_post(name="Doppel", category="contact"))
        assert response.status_code == 200
        assert DocumentType.objects.filter(facility=facility, name="Doppel", category="contact").count() == 1
        assert b"bereits" in response.content


# ---------------------------------------------------------------------------
# Update — system_type-Immutabilität, category editierbar
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdate:
    def test_system_type_immutable_on_edit(self, client, admin_user, facility):
        dt = DocumentType.objects.create(
            facility=facility, name="Bann", category="admin", system_type=DocumentType.SystemType.BAN
        )
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": dt.pk}),
            _valid_post(name="Bann", category="admin", system_type=DocumentType.SystemType.CRISIS),
        )
        assert response.status_code == 302
        dt.refresh_from_db()
        assert dt.system_type == DocumentType.SystemType.BAN

    def test_category_is_editable(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Wandel", category="contact")
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_update", kwargs={"pk": dt.pk}),
            _valid_post(name="Wandel", category="service"),
        )
        assert response.status_code == 302
        dt.refresh_from_db()
        assert dt.category == "service"
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(dt.pk)).exists()


# ---------------------------------------------------------------------------
# Delete — Block bei Events, Erfolg ohne Referenzen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDelete:
    def test_delete_blocked_by_events(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(admin_user)
        url = reverse("core:config_doctype_delete", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url)
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})
        assert DocumentType.objects.filter(pk=doc_type_contact.pk).exists()
        assert any("Ereignis" in m for m in _messages(response))
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_DELETE, target_id=str(doc_type_contact.pk)
        ).exists()

    def test_delete_without_references(self, client, admin_user, facility):
        dt = DocumentType.objects.create(facility=facility, name="Wegwerf", category="contact")
        pk = str(dt.pk)
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_delete", kwargs={"pk": dt.pk}))
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_list")
        assert not DocumentType.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).exists()

    def test_confirm_page_shows_event_count(
        self, client, admin_user, facility, client_identified, doc_type_contact, staff_user
    ):
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
            created_by=staff_user,
        )
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_delete", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 200
        assert b"doctype-delete-blocker" in response.content


# ---------------------------------------------------------------------------
# IDOR — fremde Facility → 404 (kein Existenz-Leak)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIDOR:
    @pytest.mark.parametrize("route", ["config_doctype_detail", "config_doctype_update", "config_doctype_delete"])
    def test_get_foreign_object_is_404(self, client, admin_user, foreign_doc_type, route):
        client.force_login(admin_user)
        response = client.get(reverse(f"core:{route}", kwargs={"pk": foreign_doc_type.pk}))
        assert response.status_code == 404

    @pytest.mark.parametrize("route", ["config_doctype_update", "config_doctype_delete"])
    def test_post_foreign_object_is_404(self, client, admin_user, foreign_doc_type, route):
        client.force_login(admin_user)
        response = client.post(reverse(f"core:{route}", kwargs={"pk": foreign_doc_type.pk}), _valid_post())
        assert response.status_code == 404
        assert DocumentType.objects.filter(pk=foreign_doc_type.pk).exists()
