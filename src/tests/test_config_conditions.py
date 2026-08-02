"""Tests für den Regel-Editor bedingter Feldsichtbarkeit (M1 P6, Refs #1057).

Deckt die View-/Form-/Service-Schicht des Bedingungs-Boards im DocumentType-
Detail: Anlegen/Ändern/Löschen einer Feldbedingung (inkl. Audit-``detail``-
Schema E18 + ``changed_fields`` E21), die Zyklus-Abweisung mit Pfad-Meldung im
Board, die gescopten Dropdown-Querysets (verschlüsselte/file-Quellen raus,
#774), die Operator×Typ-Matrix als Formfehler, die typabhängige
Wert-Konvertierung (number/boolean/select) + Valueless-Clear, die
ODER-Gruppen-Vorbelegung, die IDOR-404-Semantik aller vier Endpoints, das
Rollen-Gate und das typisierte Wert-Input-Partial.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.forms.config import FieldConditionForm
from core.models import AuditLog, DocumentTypeField, FieldCondition, FieldTemplate


def _dtf(doc_type, slug):
    return doc_type.fields.get(field_template__slug=slug)


def _make_dtf(doc_type, facility, name, field_type, *, options_json=None, is_encrypted=False, sort_order=5):
    ft = FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        is_encrypted=is_encrypted,
        options_json=options_json or [],
    )
    return DocumentTypeField.objects.create(document_type=doc_type, field_template=ft, sort_order=sort_order)


# ---------------------------------------------------------------------------
# Anlegen / Ändern / Löschen + Audit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMutations:
    def test_create_condition_persists_and_audits(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "greater_than",
                "value": "10",
                "group_index": "0",
            },
        )
        assert response.status_code == 302  # non-HTMX → Redirect zum Detail
        condition = FieldCondition.objects.get(target_field=notiz)
        assert condition.depends_on_field_id == dauer.pk
        assert condition.operator == "greater_than"
        assert condition.value == 10  # String „10" → typisiert (int)

        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(condition.pk)).latest(
            "timestamp"
        )
        assert entry.detail["target_slug"] == "notiz"
        assert entry.detail["source_slug"] == "dauer"
        assert entry.detail["operator"] == "greater_than"
        assert entry.detail["group_index"] == 0
        assert entry.detail["changed_fields"] == []

    def test_update_condition_audits_changed_fields(self, client, admin_user, doc_type_contact, field_condition):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse(
            "core:config_condition_update",
            kwargs={"pk": doc_type_contact.pk, "condition_pk": field_condition.pk},
        )
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "less_than",  # war greater_than
                "value": "20",  # war 10
                "group_index": "0",
            },
        )
        assert response.status_code == 302
        field_condition.refresh_from_db()
        assert field_condition.operator == "less_than"
        assert field_condition.value == 20

        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(field_condition.pk)).latest(
            "timestamp"
        )
        assert set(entry.detail["changed_fields"]) == {"operator", "value"}
        assert entry.detail["operator"] == "less_than"

    def test_delete_condition_audits(self, client, admin_user, doc_type_contact, field_condition):
        client.force_login(admin_user)
        pk = str(field_condition.pk)
        url = reverse(
            "core:config_condition_delete",
            kwargs={"pk": doc_type_contact.pk, "condition_pk": field_condition.pk},
        )
        response = client.post(url)
        assert response.status_code == 302
        assert not FieldCondition.objects.filter(pk=field_condition.pk).exists()
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
        assert entry.detail["target_slug"] == "notiz"
        assert entry.detail["source_slug"] == "dauer"


# ---------------------------------------------------------------------------
# Zyklus + Matrix + Konvertierung (Form/Board-Semantik)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValidation:
    def test_cycle_rejected_with_message_in_board(self, client, admin_user, doc_type_contact, field_condition):
        # Bestehende Kante dauer→notiz (field_condition). Die Gegenrichtung
        # notiz→dauer schließt den Zyklus → Fehlermeldung mit Pfad im Board.
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(dauer.pk),
                "depends_on_field": str(notiz.pk),
                "operator": "is_not_empty",
                "value": "",
                "group_index": "0",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Zyklus" in content
        assert "dauer" in content and "notiz" in content
        # Keine zweite Bedingung angelegt.
        assert FieldCondition.objects.filter(target_field__document_type=doc_type_contact).count() == 1

    def test_encrypted_and_file_sources_not_in_dropdown_queryset(self, doc_type_contact, facility):
        _make_dtf(doc_type_contact, facility, "Geheim", FieldTemplate.FieldType.TEXT, is_encrypted=True, sort_order=6)
        _make_dtf(doc_type_contact, facility, "Datei", FieldTemplate.FieldType.FILE, sort_order=7)
        form = FieldConditionForm(document_type=doc_type_contact)
        source_types = {dtf.field_template.field_type for dtf in form.fields["depends_on_field"].queryset}
        assert "file" not in source_types
        assert all(not dtf.field_template.is_encrypted for dtf in form.fields["depends_on_field"].queryset)

    def test_operator_not_allowed_for_type_form_error(self, client, admin_user, doc_type_contact):
        # boolean kennt nur equals/not_equals — greater_than ist unzulässig.
        client.force_login(admin_user)
        bool_dtf = _make_dtf(doc_type_contact, doc_type_contact.facility, "Flag", FieldTemplate.FieldType.BOOLEAN)
        notiz = _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(bool_dtf.pk),
                "operator": "greater_than",
                "value": "1",
                "group_index": "0",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "nicht zulässig" in response.content.decode()
        assert not FieldCondition.objects.filter(target_field=notiz).exists()

    def test_value_conversion_number_boolean_select(self, doc_type_contact, facility):
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        bool_dtf = _make_dtf(doc_type_contact, facility, "Flag", FieldTemplate.FieldType.BOOLEAN, sort_order=6)
        select_dtf = _make_dtf(
            doc_type_contact,
            facility,
            "Auswahl",
            FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "A"}, {"slug": "b", "label": "B"}],
            sort_order=7,
        )

        number_form = FieldConditionForm(
            data={
                "target_field": notiz.pk,
                "depends_on_field": dauer.pk,
                "operator": "equals",
                "value": "42",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert number_form.is_valid(), number_form.errors
        assert number_form.cleaned_data["value_json"] == 42
        assert isinstance(number_form.cleaned_data["value_json"], int)

        bool_form = FieldConditionForm(
            data={
                "target_field": notiz.pk,
                "depends_on_field": bool_dtf.pk,
                "operator": "equals",
                "value": "true",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert bool_form.is_valid(), bool_form.errors
        assert bool_form.cleaned_data["value_json"] is True

        select_form = FieldConditionForm(
            data={
                "target_field": notiz.pk,
                "depends_on_field": select_dtf.pk,
                "operator": "equals",
                "value": "a",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert select_form.is_valid(), select_form.errors
        assert select_form.cleaned_data["value_json"] == "a"
        # Unbekannter Slug wird abgewiesen.
        bad = FieldConditionForm(
            data={
                "target_field": notiz.pk,
                "depends_on_field": select_dtf.pk,
                "operator": "equals",
                "value": "zzz",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert not bad.is_valid()
        assert "value" in bad.errors

    def test_valueless_operator_clears_value(self, doc_type_contact):
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        form = FieldConditionForm(
            data={
                "target_field": notiz.pk,
                "depends_on_field": dauer.pk,
                "operator": "is_empty",
                "value": "999",
                "group_index": "0",
            },
            document_type=doc_type_contact,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["value_json"] is None


# ---------------------------------------------------------------------------
# Board-Kontext / ODER-Gruppe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBoard:
    def test_or_group_button_uses_max_plus_one(self, client, admin_user, doc_type_contact):
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        FieldCondition.objects.create(
            target_field=notiz, depends_on_field=dauer, operator="greater_than", value=1, group_index=2
        )
        client.force_login(admin_user)
        detail = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        assert detail.status_code == 200
        assert detail.context["condition_next_group_index"] == 3
        assert detail.context["condition_form"].initial["group_index"] == 3
        content = detail.content.decode()
        # ODER-Affordanz vorhanden + auf max(group_index)+1 vorbelegt.
        assert 'data-testid="condition-add-or-group"' in content
        assert "group_index=3" in content
        # Der Button (hx-get) rendert das Neu-Formular mit verstecktem group_index=3.
        seeded = client.get(
            reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk}), {"group_index": "3"}
        )
        assert seeded.status_code == 200
        body = seeded.content.decode()
        assert 'type="hidden"' in body and 'name="group_index"' in body and 'value="3"' in body

    def test_and_button_uses_group_index(self, client, admin_user, doc_type_contact):
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        FieldCondition.objects.create(
            target_field=notiz, depends_on_field=dauer, operator="greater_than", value=1, group_index=2
        )
        client.force_login(admin_user)
        detail = client.get(reverse("core:config_doctype_detail", kwargs={"pk": doc_type_contact.pk}))
        content = detail.content.decode()
        # UND-Affordanz je Gruppe vorhanden + auf DEN Gruppenindex (2) + Zielfeld vorbelegt.
        assert 'data-testid="condition-add-and"' in content
        assert "group_index=2" in content
        assert f"target={notiz.pk}" in content
        # Der Button rendert das Neu-Formular mit verstecktem group_index=2 + vorausgewähltem Ziel.
        seeded = client.get(
            reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk}),
            {"group_index": "2", "target": str(notiz.pk)},
        )
        assert seeded.status_code == 200
        body = seeded.content.decode()
        assert 'name="group_index"' in body and 'value="2"' in body
        assert f'value="{notiz.pk}" selected' in body

    def test_htmx_returns_board_partial_full_request_redirects(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        payload = {
            "target_field": str(notiz.pk),
            "depends_on_field": str(dauer.pk),
            "operator": "greater_than",
            "value": "10",
            "group_index": "0",
        }
        hx = client.post(url, payload, HTTP_HX_REQUEST="true")
        assert hx.status_code == 200
        assert "core/config/document_types/partials/condition_board.html" in {t.name for t in hx.templates if t.name}

        FieldCondition.objects.all().delete()
        full = client.post(url, payload)
        assert full.status_code == 302

    def test_get_with_garbage_group_index_and_target_falls_back_to_defaults(self, client, admin_user, doc_type_contact):
        """Müll-Query-Params dürfen den Neu-Formular-GET nicht crashen (Defense-in-Depth).

        ``?group_index=abc`` (kein Int) und ``?target=keine-uuid`` (kein UUID)
        werden beide von ``contextlib.suppress`` in ``ConditionCreateView.get``
        abgefangen — das Formular fällt auf seine Defaults zurück statt eines
        500ers: kein vorbelegter ``group_index`` (wird beim späteren Anlegen zu
        0, ``form.cleaned_data.get("group_index") or 0``) und kein vorausgewähltes
        Zielfeld.
        """
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.get(url, {"group_index": "abc", "target": "keine-uuid"})
        assert response.status_code == 200
        form = response.context["condition_form"]
        assert form.initial.get("group_index") in (None, 0)
        assert "target_field" not in form.initial
        body = response.content.decode()
        assert 'value="abc"' not in body
        assert f'value="{dauer.pk}" selected' not in body
        assert f'value="{notiz.pk}" selected' not in body


# ---------------------------------------------------------------------------
# AuthZ / IDOR
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessControl:
    @pytest.mark.parametrize("user_fixture", ["lead_user", "staff_user", "assistant_user", "super_admin_user"])
    def test_403_for_lead_staff_assistant_super_admin(self, client, request, user_fixture, doc_type_contact):
        client.force_login(request.getfixturevalue(user_fixture))
        response = client.post(reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk}), {})
        assert response.status_code == 403

    def test_foreign_doctype_404(self, client, admin_user, foreign_doc_type, foreign_field_condition):
        client.force_login(admin_user)
        create = client.post(reverse("core:config_condition_create", kwargs={"pk": foreign_doc_type.pk}), {})
        assert create.status_code == 404
        update = client.get(
            reverse(
                "core:config_condition_update",
                kwargs={"pk": foreign_doc_type.pk, "condition_pk": foreign_field_condition.pk},
            )
        )
        assert update.status_code == 404
        delete = client.post(
            reverse(
                "core:config_condition_delete",
                kwargs={"pk": foreign_doc_type.pk, "condition_pk": foreign_field_condition.pk},
            )
        )
        assert delete.status_code == 404
        value_input = client.get(reverse("core:config_condition_value_input", kwargs={"pk": foreign_doc_type.pk}))
        assert value_input.status_code == 404


# ---------------------------------------------------------------------------
# Typisiertes Wert-Input-Partial (E19)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestValueInputPartial:
    def test_value_input_partial_renders_options_dropdown_for_select(
        self, client, admin_user, doc_type_contact, facility
    ):
        client.force_login(admin_user)
        select_dtf = _make_dtf(
            doc_type_contact,
            facility,
            "Auswahl",
            FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "rot", "label": "Rot"}, {"slug": "blau", "label": "Blau"}],
        )
        url = reverse("core:config_condition_value_input", kwargs={"pk": doc_type_contact.pk})
        response = client.get(url, {"depends_on_field": str(select_dtf.pk), "operator": "equals"})
        assert response.status_code == 200
        content = response.content.decode()
        assert '<select name="value"' in content
        assert 'value="rot"' in content and 'value="blau"' in content

    def test_value_input_missing_source_is_bad_request(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        url = reverse("core:config_condition_value_input", kwargs={"pk": doc_type_contact.pk})
        response = client.get(url)
        assert response.status_code == 400

    def test_value_input_root_is_slot_wrapper(self, client, admin_user, doc_type_contact):
        """Bug 1: Die Swap-Antwort trägt `.condition-value-slot` an der WURZEL.

        Die auslösenden Selects erben `hx-swap="outerHTML"` vom <form> und zielen
        auf `next .condition-value-slot` — nur wenn die Antwort ihren eigenen
        Wrapper mitrendert, bleibt das Ziel über beliebig viele Swaps erhalten.
        """
        client.force_login(admin_user)
        dauer = _dtf(doc_type_contact, "dauer")  # number
        url = reverse("core:config_condition_value_input", kwargs={"pk": doc_type_contact.pk})
        response = client.get(url, {"depends_on_field": str(dauer.pk), "operator": "greater_than"})
        assert response.status_code == 200
        body = response.content.decode().strip()
        assert body.startswith('<div class="condition-value-slot"')
        assert body.endswith("</div>")
        assert 'type="number"' in body

    def test_value_input_two_consecutive_swaps_keep_slot(self, client, admin_user, doc_type_contact, facility):
        """Bug 1: Zwei aufeinanderfolgende Swaps (Quellwechsel → Operatorwechsel)
        liefern beide eine Antwort mit `.condition-value-slot`-Wurzel — das Ziel
        für den jeweils nächsten Swap bleibt idempotent erhalten."""
        client.force_login(admin_user)
        dauer = _dtf(doc_type_contact, "dauer")  # number
        select_dtf = _make_dtf(
            doc_type_contact,
            facility,
            "Auswahl",
            FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "A"}],
        )
        url = reverse("core:config_condition_value_input", kwargs={"pk": doc_type_contact.pk})
        # Swap 1: Quellfeld number gewählt.
        first = client.get(url, {"depends_on_field": str(dauer.pk), "operator": "greater_than"})
        # Swap 2: Quellfeld auf select gewechselt (Folgeswap).
        second = client.get(url, {"depends_on_field": str(select_dtf.pk), "operator": "equals"})
        for response in (first, second):
            assert response.status_code == 200
            assert 'class="condition-value-slot"' in response.content.decode()
            # Genau EINE Wrapper-Wurzel je Antwort (kein Verschachteln/Verlust).
            assert response.content.decode().count('class="condition-value-slot"') == 1

    def test_edit_form_prefills_typed_input(self, client, admin_user, doc_type_contact, field_condition):
        """Minor 2: Der Edit-GET rendert für eine number-Quelle das typisierte
        Input (`type="number"`) vorbefüllt (Wert 10), nicht das generische Textfeld."""
        client.force_login(admin_user)
        url = reverse(
            "core:config_condition_update",
            kwargs={"pk": doc_type_contact.pk, "condition_pk": field_condition.pk},
        )
        response = client.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        assert 'type="number"' in body
        assert 'value="10"' in body
        # Value-Slot-Wrapper auch im Edit-Formular vorhanden (Swap-Ziel).
        assert 'class="condition-value-slot"' in body
