"""Tests für Feld-Zuordnung + FieldGroup-Verwaltung im DocumentType-Detail (M0 P6, Refs #1056).

Deckt die View-Schicht des interaktiven Feld-Boards: Feld zuordnen (sort_order-
Vergabe, „Ohne Gruppe", Duplikat-Formfehler, Fremd-Gruppe → 400), Feld
entfernen, FieldGroup anlegen/umbenennen/löschen (mit Audit), das Board-Rendering
(Sortierung + data-Attribute), den HTMX-Branch (Partial vs. Redirect), das
Rollen-Gate und die IDOR-404-Semantik. Die Mutations-/Audit-Mechanik selbst
prüft ``test_config_service.py``.
"""

from __future__ import annotations

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldGroup, FieldTemplate

BOARD_PARTIAL = "core/config/document_types/partials/field_board.html"


def _messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def _template_names(response):
    return [t.name for t in response.templates if t.name]


# ---------------------------------------------------------------------------
# Rollen-Gate — nur facility_admin (Smoke; Matrix deckt die Vollmatrix)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_field_add_forbidden_for_non_admin(self, client, request, user_fixture, doc_type_contact):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.post(reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk}), {})
        assert response.status_code == 403

    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_group_create_forbidden_for_non_admin(self, client, request, user_fixture, doc_type_contact):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.get(reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 403

    def test_admin_allowed_on_field_add(self, client, admin_user, doc_type_contact, field_template):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk}),
            {"field_template": str(field_template.pk)},
        )
        assert response.status_code == 302  # non-HTMX → Redirect zum Detail


# ---------------------------------------------------------------------------
# Feld zuordnen — sort_order-Vergabe, „Ohne Gruppe", Duplikat, Fremd-Gruppe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldAdd:
    def test_add_into_group_appends_at_group_end(self, client, admin_user, facility, doc_type_contact, field_group):
        client.force_login(admin_user)
        ft_a = FieldTemplate.objects.create(facility=facility, name="GA", field_type="text")
        ft_b = FieldTemplate.objects.create(facility=facility, name="GB", field_type="text")
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        client.post(url, {"field_template": str(ft_a.pk), "field_group": str(field_group.pk)})
        client.post(url, {"field_template": str(ft_b.pk), "field_group": str(field_group.pk)})
        a = DocumentTypeField.objects.get(document_type=doc_type_contact, field_template=ft_a)
        b = DocumentTypeField.objects.get(document_type=doc_type_contact, field_template=ft_b)
        assert a.field_group_id == field_group.pk
        assert (a.sort_order, b.sort_order) == (0, 1)

    def test_add_without_group_is_ungrouped_at_end(self, client, admin_user, doc_type_contact, field_template):
        # doc_type_contact hat 2 ungruppierte Fixture-Felder (sort 0, 1).
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk)})
        assert response.status_code == 302
        dtf = DocumentTypeField.objects.get(document_type=doc_type_contact, field_template=field_template)
        assert dtf.field_group_id is None
        assert dtf.sort_order == 2

    def test_add_already_assigned_template_is_form_error_not_500(self, client, admin_user, facility, doc_type_contact):
        client.force_login(admin_user)
        ft_dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")  # bereits zugeordnet
        before = DocumentTypeField.objects.filter(document_type=doc_type_contact).count()
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(ft_dauer.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        assert b"field-add-errors" in response.content
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact).count() == before

    def test_add_with_foreign_group_is_400_and_creates_nothing(
        self, client, admin_user, facility, doc_type_contact, field_template
    ):
        # Gruppe eines ANDEREN doc_type (gleiche Einrichtung) → 400 (Service-Semantik).
        client.force_login(admin_user)
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ", category="contact")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremde Gruppe")
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk), "field_group": str(foreign_grp.pk)})
        assert response.status_code == 400
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_contact, field_template=field_template
        ).exists()

    def test_add_htmx_returns_board_partial_not_redirect(self, client, admin_user, doc_type_contact, field_template):
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        names = _template_names(response)
        assert BOARD_PARTIAL in names
        assert "base.html" not in names
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=field_template).exists()


# ---------------------------------------------------------------------------
# Feld entfernen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldRemove:
    def test_remove_htmx_board_partial_and_audit(self, client, admin_user, doc_type_contact, doc_type_field):
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        assert not DocumentTypeField.objects.filter(pk=doc_type_field.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=str(doc_type_field.pk)).exists()

    def test_remove_non_htmx_redirects(self, client, admin_user, doc_type_contact, doc_type_field):
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url)
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})


# ---------------------------------------------------------------------------
# Gruppe anlegen / umbenennen / löschen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGroupCreate:
    def test_create_get_renders_form(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk}))
        assert response.status_code == 200

    def test_create_writes_audit_and_renders_board(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"name": "Abschnitt A"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        grp = FieldGroup.objects.get(document_type=doc_type_contact, name="Abschnitt A")
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(grp.pk)).exists()
        assert b"Abschnitt A" in response.content

    def test_create_is_collapsible_flag(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        client.post(url, {"name": "Klappbar", "is_collapsible": "on"})
        grp = FieldGroup.objects.get(document_type=doc_type_contact, name="Klappbar")
        assert grp.is_collapsible is True

    def test_create_duplicate_name_is_form_error(self, client, admin_user, doc_type_contact):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Doppelt")
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"name": "Doppelt"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert FieldGroup.objects.filter(document_type=doc_type_contact, name="Doppelt").count() == 1
        assert b"bereits" in response.content

    def test_create_non_htmx_redirects(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"name": "Redir"})
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})


@pytest.mark.django_db
class TestGroupUpdate:
    def test_update_get_renders_prefilled_form(self, client, admin_user, doc_type_contact, field_group):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.get(url)
        assert response.status_code == 200
        assert field_group.name.encode() in response.content

    def test_update_renames_and_audits(self, client, admin_user, doc_type_contact, field_group):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, {"name": "Neuer Name"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        field_group.refresh_from_db()
        assert field_group.name == "Neuer Name"
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_group.pk)).exists()

    def test_update_keeps_slug_stable(self, client, admin_user, doc_type_contact, field_group):
        original_slug = field_group.slug
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        client.post(url, {"name": "Ganz Anders"})
        field_group.refresh_from_db()
        assert field_group.name == "Ganz Anders"
        assert field_group.slug == original_slug


@pytest.mark.django_db
class TestGroupDelete:
    def test_delete_ungroups_fields_and_audits(self, client, admin_user, doc_type_contact, field_group, doc_type_field):
        doc_type_field.field_group = field_group
        doc_type_field.save(update_fields=["field_group"])
        grp_pk = str(field_group.pk)
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_delete", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        assert not FieldGroup.objects.filter(pk=grp_pk).exists()
        doc_type_field.refresh_from_db()
        assert doc_type_field.field_group is None
        assert DocumentTypeField.objects.filter(pk=doc_type_field.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=grp_pk).exists()

    def test_delete_non_htmx_redirects(self, client, admin_user, doc_type_contact, field_group):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_delete", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url)
        assert response.status_code == 302
        assert response.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})


# ---------------------------------------------------------------------------
# Board-Rendering — Gruppen sortiert, „Ohne Gruppe" zuletzt, data-Attribute
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoardRendering:
    def test_groups_sorted_ungrouped_last_with_data_attrs(self, client, admin_user, doc_type_contact):
        g_b = FieldGroup.objects.create(document_type=doc_type_contact, name="B-Gruppe", sort_order=1)
        g_a = FieldGroup.objects.create(document_type=doc_type_contact, name="A-Gruppe", sort_order=0)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        assert body.index("A-Gruppe") < body.index("B-Gruppe")
        assert body.index("B-Gruppe") < body.index("Ohne Gruppe")
        assert f'data-group-id="{g_a.pk}"' in body
        assert f'data-group-id="{g_b.pk}"' in body
        dtf = doc_type_contact.fields.first()
        assert f'data-dtf-id="{dtf.pk}"' in body

    def test_detail_renders_board_swap_target(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        assert b'id="field-board"' in response.content


# ---------------------------------------------------------------------------
# HTMX-Branch — HX-Request → Board-Partial, normal → Redirect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHTMXBranch:
    def test_group_create_htmx_partial_vs_redirect(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        hx = client.post(url, {"name": "HX-Gruppe"}, HTTP_HX_REQUEST="true")
        assert hx.status_code == 200
        assert BOARD_PARTIAL in _template_names(hx)
        plain = client.post(url, {"name": "Plain-Gruppe"})
        assert plain.status_code == 302
        assert plain.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})


# ---------------------------------------------------------------------------
# IDOR — fremder doc_type → 404 VOR jeder weiteren Prüfung; gemischt → 404
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIDOR:
    def test_field_add_foreign_doctype_404(self, client, admin_user, foreign_doc_type):
        client.force_login(admin_user)
        response = client.post(reverse("core:config_doctype_field_add", kwargs={"pk": foreign_doc_type.pk}), {})
        assert response.status_code == 404

    def test_field_remove_foreign_doctype_404(self, client, admin_user, foreign_doc_type, foreign_doc_type_field):
        client.force_login(admin_user)
        response = client.post(
            reverse(
                "core:config_doctype_field_remove",
                kwargs={"pk": foreign_doc_type.pk, "dtf_pk": foreign_doc_type_field.pk},
            )
        )
        assert response.status_code == 404
        assert DocumentTypeField.objects.filter(pk=foreign_doc_type_field.pk).exists()

    def test_field_remove_own_doctype_foreign_dtf_404(
        self, client, admin_user, doc_type_contact, foreign_doc_type_field
    ):
        client.force_login(admin_user)
        response = client.post(
            reverse(
                "core:config_doctype_field_remove",
                kwargs={"pk": doc_type_contact.pk, "dtf_pk": foreign_doc_type_field.pk},
            )
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_group_create_foreign_doctype_404(self, client, admin_user, foreign_doc_type, method):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": foreign_doc_type.pk})
        response = getattr(client, method)(url, {"name": "X"})
        assert response.status_code == 404

    @pytest.mark.parametrize("method", ["get", "post"])
    def test_group_update_foreign_doctype_404(self, client, admin_user, foreign_doc_type, foreign_field_group, method):
        client.force_login(admin_user)
        url = reverse(
            "core:config_fieldgroup_update",
            kwargs={"pk": foreign_doc_type.pk, "group_pk": foreign_field_group.pk},
        )
        response = getattr(client, method)(url, {"name": "X"})
        assert response.status_code == 404

    def test_group_update_own_doctype_foreign_group_404(
        self, client, admin_user, doc_type_contact, foreign_field_group
    ):
        client.force_login(admin_user)
        url = reverse(
            "core:config_fieldgroup_update",
            kwargs={"pk": doc_type_contact.pk, "group_pk": foreign_field_group.pk},
        )
        response = client.post(url, {"name": "X"})
        assert response.status_code == 404

    def test_group_delete_foreign_doctype_404(self, client, admin_user, foreign_doc_type, foreign_field_group):
        client.force_login(admin_user)
        url = reverse(
            "core:config_fieldgroup_delete",
            kwargs={"pk": foreign_doc_type.pk, "group_pk": foreign_field_group.pk},
        )
        response = client.post(url)
        assert response.status_code == 404
        assert FieldGroup.objects.filter(pk=foreign_field_group.pk).exists()
