"""Tests für die Drag-and-Drop-Batch-Reorder-Endpunkte (M0 P7 / #1713, Refs #1056/#1058).

Deckt die View-Schicht der drei Reorder-POST-Endpunkte des Feld-Boards:

* ``config_reorder_blocks`` — globale Block-Sequenz (``blocks`` = ``kind:uuid``
  für ALLE Gruppen + ungruppierte Felder, gemischt).
* ``config_reorder_fields`` — Feld-Reihenfolge in einer Gruppe (``group`` =
  Ziel-Gruppe, ``order`` = alle DTF-IDs).
* ``config_move_field`` — Feld in eine Gruppe verschieben (``group``, ``order``)
  oder an eine Top-Level-Block-Position (``group`` leer, ``blocks``).

Die Persistenz-/Audit-Mechanik selbst (strikte ID-Set-Validierung, atomares
Umschreiben, CONFIG_REORDER mit ``detail.operation``) prüft
``test_config_service.py`` auf Service-Ebene; hier wird die View-Verdrahtung
geprüft: Payload-Parsing, kaputte UUID → 400, Service-ValueError → 400, leerer
POST → 400, Fremd-DocType → 404 (IDOR vor Payload) und die Board-Partial-Antwort.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AuditLog, DocumentTypeField, FieldGroup, FieldTemplate

BOARD_PARTIAL = "core/config/document_types/partials/field_board.html"


def _template_names(response):
    return [t.name for t in response.templates if t.name]


def _latest_reorder():
    return AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).latest("timestamp")


# ---------------------------------------------------------------------------
# Globale Block-Sequenz (Gruppen + ungruppierte Felder gemischt)
# ---------------------------------------------------------------------------


def _free_ids(doc_type):
    return list(doc_type.fields.filter(field_group__isnull=True).order_by("sort_order").values_list("pk", flat=True))


@pytest.mark.django_db
class TestReorderBlocks:
    def test_persists_and_returns_board_partial(self, client, admin_user, doc_type_contact):
        # doc_type_contact: 2 freie Felder (Blockpos 0,1); dazu eine Gruppe (2).
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=2)
        f0, f1 = _free_ids(doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"blocks": [f"group:{grp.pk}", f"field:{f1}", f"field:{f0}"]},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        grp.refresh_from_db()
        assert grp.sort_order == 0
        assert doc_type_contact.fields.get(pk=f1).sort_order == 1
        assert doc_type_contact.fields.get(pk=f0).sort_order == 2

    def test_audit_operation_block_order(self, client, admin_user, doc_type_contact):
        f0, f1 = _free_ids(doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        client.post(url, {"blocks": [f"field:{f1}", f"field:{f0}"]}, HTTP_HX_REQUEST="true")
        assert _latest_reorder().detail["operation"] == "block_order"

    def test_incomplete_id_set_is_400_and_db_unchanged(self, client, admin_user, doc_type_contact):
        f0, f1 = _free_ids(doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"blocks": [f"field:{f0}"]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400
        assert doc_type_contact.fields.get(pk=f0).sort_order == 0
        assert doc_type_contact.fields.get(pk=f1).sort_order == 1

    def test_foreign_group_id_is_400(self, client, admin_user, doc_type_contact, foreign_field_group):
        f0, f1 = _free_ids(doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"blocks": [f"group:{foreign_field_group.pk}", f"field:{f0}", f"field:{f1}"]},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        assert doc_type_contact.fields.get(pk=f0).sort_order == 0

    def test_broken_block_value_is_400(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"blocks": ["nicht-eine-uuid"]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400
        # Refs #2385: postBatch() swapt die Antwort in #field-board
        # (hx-swap="outerHTML") — ohne HX-Reswap: none ersetzte der Klartext
        # das ganze Board.
        assert response["HX-Reswap"] == "none"

    def test_broken_kind_prefix_is_400(self, client, admin_user, doc_type_contact):
        f0, _f1 = _free_ids(doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"blocks": [f"bogus:{f0}"]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400

    def test_empty_post_is_400(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_reorder_blocks", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Feld-Reihenfolge innerhalb einer Gruppe
# ---------------------------------------------------------------------------


def _grouped_pair(facility, doc_type):
    grp = FieldGroup.objects.create(document_type=doc_type, name="RF")
    ft1 = FieldTemplate.objects.create(facility=facility, name="RF-1", field_type="text")
    ft2 = FieldTemplate.objects.create(facility=facility, name="RF-2", field_type="text")
    d1 = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft1, field_group=grp, sort_order=0)
    d2 = DocumentTypeField.objects.create(document_type=doc_type, field_template=ft2, field_group=grp, sort_order=1)
    return grp, d1, d2


@pytest.mark.django_db
class TestReorderFields:
    def test_within_group_persists_and_audits(self, client, admin_user, facility, doc_type_contact):
        grp, d1, d2 = _grouped_pair(facility, doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_fields", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group": str(grp.pk), "order": [str(d2.pk), str(d1.pk)]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        d1.refresh_from_db()
        d2.refresh_from_db()
        assert (d2.sort_order, d1.sort_order) == (0, 1)
        assert _latest_reorder().detail["operation"] == "field_order"

    def test_incomplete_set_is_400_and_db_unchanged(self, client, admin_user, facility, doc_type_contact):
        grp, d1, d2 = _grouped_pair(facility, doc_type_contact)
        client.force_login(admin_user)
        url = reverse("core:config_reorder_fields", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group": str(grp.pk), "order": [str(d1.pk)]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400
        d1.refresh_from_db()
        d2.refresh_from_db()
        assert (d1.sort_order, d2.sort_order) == (0, 1)

    def test_broken_group_uuid_is_400(self, client, admin_user, doc_type_contact):
        dtfs = list(doc_type_contact.fields.filter(field_group__isnull=True))
        client.force_login(admin_user)
        url = reverse("core:config_reorder_fields", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group": "kaputt", "order": [str(dtfs[0].pk)]}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"

    def test_empty_post_is_400(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_reorder_fields", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Feld verschieben (move_field)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMoveField:
    def test_sets_fk_and_position_and_audits(self, client, admin_user, doc_type_contact):
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst")
        dtf = doc_type_contact.fields.filter(field_group__isnull=True).first()
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"moved": str(dtf.pk), "group": str(grp_dst.pk), "order": [str(dtf.pk)]},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        dtf.refresh_from_db()
        assert dtf.field_group_id == grp_dst.pk
        assert dtf.sort_order == 0
        assert _latest_reorder().detail["operation"] == "move_field"

    def test_into_populated_group_orders_correctly(self, client, admin_user, facility, doc_type_contact):
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst2")
        ft_x = FieldTemplate.objects.create(facility=facility, name="X", field_type="text")
        dtf_x = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_x, field_group=grp_dst, sort_order=0
        )
        dtf_move = doc_type_contact.fields.filter(field_group__isnull=True).first()
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"moved": str(dtf_move.pk), "group": str(grp_dst.pk), "order": [str(dtf_x.pk), str(dtf_move.pk)]},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        dtf_x.refresh_from_db()
        dtf_move.refresh_from_db()
        assert (dtf_x.sort_order, dtf_move.sort_order) == (0, 1)
        assert dtf_move.field_group_id == grp_dst.pk

    def test_move_to_top_level_with_blocks(self, client, admin_user, doc_type_contact):
        # Ein gruppiertes Feld wird per Blockliste an Top-Level gezogen.
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Src", sort_order=0)
        dtf = doc_type_contact.fields.filter(field_group__isnull=True).first()
        dtf.field_group = grp
        dtf.save(update_fields=["field_group"])
        # Verbliebene freie Felder + Zielreihenfolge (Gruppe, dann Felder inkl. moved).
        other_free = list(doc_type_contact.fields.filter(field_group__isnull=True).values_list("pk", flat=True))
        blocks = [f"group:{grp.pk}", f"field:{dtf.pk}"] + [f"field:{pk}" for pk in other_free]
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"moved": str(dtf.pk), "group": "", "blocks": blocks}, HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert BOARD_PARTIAL in _template_names(response)
        dtf.refresh_from_db()
        assert dtf.field_group_id is None

    def test_foreign_target_group_is_400(self, client, admin_user, doc_type_contact, foreign_field_group):
        dtf = doc_type_contact.fields.first()
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {"moved": str(dtf.pk), "group": str(foreign_field_group.pk), "order": [str(dtf.pk)]},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 400
        dtf.refresh_from_db()
        assert dtf.field_group_id is None

    def test_broken_moved_uuid_is_400(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url, {"moved": "kaputt", "group": "", "blocks": ["field:kaputt"]}, HTTP_HX_REQUEST="true"
        )
        assert response.status_code == 400
        assert response["HX-Reswap"] == "none"

    def test_top_level_empty_blocks_is_400(self, client, admin_user, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Src")
        dtf = doc_type_contact.fields.first()
        dtf.field_group = grp
        dtf.save(update_fields=["field_group"])
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"moved": str(dtf.pk), "group": ""}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400

    def test_empty_post_is_400(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_move_field", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# IDOR — fremder DocType → 404 VOR jeder Payload-Prüfung
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIDOR:
    @pytest.mark.parametrize("url_name", ["config_reorder_blocks", "config_reorder_fields", "config_move_field"])
    def test_foreign_doctype_is_404(self, client, admin_user, foreign_doc_type, url_name):
        client.force_login(admin_user)
        url = reverse(f"core:{url_name}", kwargs={"pk": foreign_doc_type.pk})
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Rollen-Gate — nur facility_admin (Smoke; Matrix deckt die Vollmatrix)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    @pytest.mark.parametrize("url_name", ["config_reorder_blocks", "config_reorder_fields", "config_move_field"])
    def test_forbidden_for_non_admin(self, client, request, user_fixture, url_name, doc_type_contact):
        client.force_login(request.getfixturevalue(user_fixture))
        url = reverse(f"core:{url_name}", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {}, HTTP_HX_REQUEST="true")
        assert response.status_code == 403
