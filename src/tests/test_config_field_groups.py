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
GROUP_FORM_PARTIAL = "core/config/document_types/partials/group_form.html"


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

    def test_add_with_foreign_group_redirects_with_message_and_creates_nothing(
        self, client, admin_user, facility, doc_type_contact, field_template
    ):
        # Group of an OTHER doc_type (same facility) → rejection (service semantics).
        # Refs #2385: a native form submit (no HX-Request) no longer gets the
        # rejection as a full-page plain-text response, but a message + redirect.
        client.force_login(admin_user)
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremde Gruppe")
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk), "field_group": str(foreign_grp.pk)})
        assert response.status_code == 302
        assert "Die Feldgruppe gehört nicht zu diesem Dokumentationstyp." in _messages(response)
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_contact, field_template=field_template
        ).exists()

    def test_add_with_malformed_group_id_redirects_with_message_not_500(
        self, client, admin_user, doc_type_contact, field_template
    ):
        # Non-UUID in the field_group POST → rejection instead of 500. Django's
        # UUIDField raises a ValidationError (NOT ValueError) when the query
        # runs; fed raw into the pk filter, that would be an uncaught 500.
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk), "field_group": "garbage"})
        assert response.status_code == 302
        assert "Ungültige Feldgruppen-ID." in _messages(response)
        assert not DocumentTypeField.objects.filter(
            document_type=doc_type_contact, field_template=field_template
        ).exists()

    def test_add_with_foreign_group_via_htmx_refuses_in_place(
        self, client, admin_user, facility, doc_type_contact, field_template
    ):
        client.force_login(admin_user)
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremde Gruppe")
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"field_template": str(field_template.pk), "field_group": str(foreign_grp.pk)},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"
        assert "Die Feldgruppe gehört nicht zu diesem Dokumentationstyp." in response.content.decode()
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
        response = client.post(url, {"group-new-name": "Abschnitt A"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        grp = FieldGroup.objects.get(document_type=doc_type_contact, name="Abschnitt A")
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(grp.pk)).exists()
        assert b"Abschnitt A" in response.content

    def test_create_is_collapsible_flag(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        client.post(url, {"group-new-name": "Klappbar", "group-new-is_collapsible": "on"})
        grp = FieldGroup.objects.get(document_type=doc_type_contact, name="Klappbar")
        assert grp.is_collapsible is True

    def test_create_duplicate_name_is_form_error(self, client, admin_user, doc_type_contact):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Doppelt")
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "Doppelt"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert FieldGroup.objects.filter(document_type=doc_type_contact, name="Doppelt").count() == 1
        assert b"bereits" in response.content

    def test_create_unslugifiable_name_is_form_error(self, client, admin_user, doc_type_contact):
        """Final-Review D3 (Refs #1058): ``FieldGroup.save()`` wirft bei einem
        Namen ohne slugifizierbare Zeichen eine ``ValidationError`` — ohne
        Formular-Guard schlaegt das als HTTP 500 auf."""
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "!!!"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert not FieldGroup.objects.filter(document_type=doc_type_contact, name="!!!").exists()
        assert "Slug" in response.content.decode()

    def test_create_non_htmx_redirects(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "Redir"})
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
        response = client.post(url, {f"group-{field_group.pk}-name": "Neuer Name"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        field_group.refresh_from_db()
        assert field_group.name == "Neuer Name"
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_group.pk)).exists()

    def test_update_keeps_slug_stable(self, client, admin_user, doc_type_contact, field_group):
        original_slug = field_group.slug
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        client.post(url, {f"group-{field_group.pk}-name": "Ganz Anders"})
        field_group.refresh_from_db()
        assert field_group.name == "Ganz Anders"
        assert field_group.slug == original_slug

    def test_update_htmx_success_retargets_whole_board(self, client, admin_user, doc_type_contact, field_group):
        # Das Edit-Formular postet per hx-target="this"; bei Erfolg soll trotzdem
        # das GANZE Board ersetzt werden → board_response(retarget=True) setzt
        # HX-Retarget/HX-Reswap und rendert das komplette field_board.
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, {f"group-{field_group.pk}-name": "Sektion Neu"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert response["HX-Retarget"] == "#field-board"
        assert response["HX-Reswap"] == "outerHTML"
        names = _template_names(response)
        assert BOARD_PARTIAL in names
        assert GROUP_FORM_PARTIAL in names  # das Board bettet das Neu-Formular ein
        body = response.content.decode()
        assert "Sektion Neu" in body  # umbenannte Gruppe erscheint im Board
        # Vollständiges Board: das „Feld hinzufügen"-Formular ist ebenfalls dabei.
        assert b'data-testid="field-add-form"' in response.content

    def test_update_htmx_invalid_renders_inline_form_without_retarget(
        self, client, admin_user, doc_type_contact, field_group
    ):
        # Duplikat-Name → Formfehler. Der Invalid-HX-Zweig ersetzt NUR das
        # Inline-group_form (kein Board, kein HX-Retarget) mit sichtbarem Fehler.
        FieldGroup.objects.create(document_type=doc_type_contact, name="Bereits Da")
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, {f"group-{field_group.pk}-name": "Bereits Da"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert "HX-Retarget" not in response
        names = _template_names(response)
        assert GROUP_FORM_PARTIAL in names
        assert BOARD_PARTIAL not in names
        assert b"group-form-errors" in response.content
        assert b"bereits" in response.content
        field_group.refresh_from_db()
        assert field_group.name != "Bereits Da"


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
# Board-Rendering — globale Block-Sequenz (Gruppen + freie Felder interleaved)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoardRendering:
    def test_blocks_interleaved_by_position_with_data_attrs(self, client, admin_user, facility, doc_type_contact):
        # Layout: freies Feld (Blockpos 0), Gruppe G (Blockpos 1), freies Feld (Blockpos 2).
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        a, b = free[0], free[1]
        a.sort_order = 0
        a.save(update_fields=["sort_order"])
        b.sort_order = 2
        b.save(update_fields=["sort_order"])
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Mittelgruppe", sort_order=1)
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        body = response.content.decode()
        # Es gibt keinen fixen „Ohne Gruppe"-Block mehr (das gleichnamige Wort
        # bleibt nur als Dropdown-Leeroption); freie Felder sitzen als eigene
        # Top-Level-Zeilen interleaved zwischen den Gruppen.
        assert 'data-testid="field-group-ungrouped"' not in body
        assert body.index(f'data-dtf-id="{a.pk}"') < body.index("Mittelgruppe")
        assert body.index("Mittelgruppe") < body.index(f'data-dtf-id="{b.pk}"')
        assert f'data-group-id="{grp.pk}"' in body
        assert 'data-testid="free-field-row"' in body
        assert 'data-sortable="blocks"' in body

    def test_detail_renders_board_swap_target(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        assert b'id="field-board"' in response.content


# ---------------------------------------------------------------------------
# Eindeutige Formular-IDs — das stets vorhandene „Neue Gruppe"-Formular und ein
# gleichzeitig offenes Edit-Formular dürfen keine kollidierende id (id_name /
# id_is_collapsible) rendern → Prefix je Instanz.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGroupFormIds:
    def test_new_group_form_has_prefixed_field_id_no_collision(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        response = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        assert b'id="id_group-new-name"' in response.content
        # Kein ungeprefixtes id_name — sonst kollidiert das Neu-Formular mit
        # einem injizierten Edit-Formular (invalides HTML, Label-Fehlfokus).
        assert b'id="id_name"' not in response.content

    def test_edit_group_form_has_group_scoped_field_id(self, client, admin_user, doc_type_contact, field_group):
        client.force_login(admin_user)
        response = client.get(
            reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        )
        assert f'id="id_group-{field_group.pk}-name"'.encode() in response.content
        assert b'id="id_name"' not in response.content


# ---------------------------------------------------------------------------
# HTMX-Branch — HX-Request → Board-Partial, normal → Redirect
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHTMXBranch:
    def test_group_create_htmx_partial_vs_redirect(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        hx = client.post(url, {"group-new-name": "HX-Gruppe"}, HTTP_HX_REQUEST="true")
        assert hx.status_code == 200
        assert BOARD_PARTIAL in _template_names(hx)
        plain = client.post(url, {"group-new-name": "Plain-Gruppe"})
        assert plain.status_code == 302
        assert plain.url == reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk})


# ---------------------------------------------------------------------------
# Flash-Messages — nur im Nicht-HX-Zweig. Bei HX-Request rendert board_response
# nur field_board.html (ohne Messages-Region) → eine hier gesetzte Meldung
# strandet im Store und poppt verspätet beim nächsten Vollseiten-Load auf
# (Konvention: workitem_actions.py — Messages nur vor dem Redirect).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoardMutationMessages:
    def test_field_add_htmx_leaves_message_store_empty(self, client, admin_user, doc_type_contact, field_template):
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []

    def test_field_add_non_htmx_redirects_with_message(self, client, admin_user, doc_type_contact, field_template):
        client.force_login(admin_user)
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(field_template.pk)})
        assert response.status_code == 302
        assert any("zugeordnet" in m for m in _messages(response))

    def test_field_add_htmx_form_error_leaves_message_store_empty(self, client, admin_user, facility, doc_type_contact):
        client.force_login(admin_user)
        ft_dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")  # bereits zugeordnet → Formfehler
        url = reverse("core:config_doctype_field_add", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"field_template": str(ft_dauer.pk)}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []

    def test_field_remove_htmx_leaves_message_store_empty(self, client, admin_user, doc_type_contact, doc_type_field):
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []

    def test_field_remove_non_htmx_redirects_with_message(self, client, admin_user, doc_type_contact, doc_type_field):
        client.force_login(admin_user)
        url = reverse(
            "core:config_doctype_field_remove",
            kwargs={"pk": doc_type_contact.pk, "dtf_pk": doc_type_field.pk},
        )
        response = client.post(url)
        assert response.status_code == 302
        assert any("entfernt" in m for m in _messages(response))

    def test_group_create_htmx_leaves_message_store_empty(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "Store-Test"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []

    def test_group_create_non_htmx_redirects_with_message(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "Store-Test-Redir"})
        assert response.status_code == 302
        assert any("angelegt" in m for m in _messages(response))

    def test_group_update_htmx_success_leaves_message_store_empty(
        self, client, admin_user, doc_type_contact, field_group
    ):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, {f"group-{field_group.pk}-name": "Umbenannt"}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []

    def test_group_delete_htmx_leaves_message_store_empty(self, client, admin_user, doc_type_contact, field_group):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_delete", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert list(get_messages(response.wsgi_request)) == []


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
