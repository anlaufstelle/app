"""Mutation-Nacharbeit zu ``core.forms.config`` (Refs #1998).

Schließt sieben von acht in #1998 benannten Testlücken (Posten 1–6, 8) —
Posten 7 erweitert stattdessen den bestehenden Test
``test_create_client_type_with_system_type_is_form_error`` in
``test_config_document_types.py`` (dort liegt er thematisch richtig). Jeder
Test hier pinnt eine konkrete, bisher ungetestete oder unter-getestete
Verzweigung in ``FieldTemplateForm``/``FieldConditionForm``/``FieldGroupForm``
— der jeweilige mutmut-Cluster steht im Docstring der Testklasse.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate


def _dtf(doc_type, slug):
    return doc_type.fields.get(field_template__slug=slug)


def _valid_field_template_post(**overrides):
    data = {
        "name": "Feld Neu",
        "field_type": FieldTemplate.FieldType.TEXT,
        "options_json": "[]",
        "is_active": "on",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Posten 1 — FieldTemplateForm.clean_options_json: 'is_active' in allowed_keys
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOptionsJsonIsActiveAllowedKey:
    """Refs #1998 (mutmut_30/31): ``allowed_keys`` muss ``'is_active'`` weiter
    zulassen — sonst würde ein POST mit explizit gesetztem ``is_active`` als
    „unbekannter Schlüssel" abgewiesen statt normalisiert übernommen."""

    def test_explicit_is_active_true_and_false_are_normalized(self, client, admin_user):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_field_template_post(
                name="Optionen Aktiv-Flag",
                field_type=FieldTemplate.FieldType.SELECT,
                options_json=(
                    '[{"slug":"an","label":"An","is_active":true},{"slug":"aus","label":"Aus","is_active":false}]'
                ),
            ),
        )
        assert response.status_code == 302, response.context["form"].errors
        ft = FieldTemplate.objects.get(name="Optionen Aktiv-Flag")
        assert ft.options_json == [
            {"slug": "an", "label": "An", "is_active": True},
            {"slug": "aus", "label": "Aus", "is_active": False},
        ]


# ---------------------------------------------------------------------------
# Posten 2 — FieldTemplateForm.clean_options_json: Nicht-Objekt-Element
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOptionsJsonNonObjectElement:
    """Refs #1998 (mutmut_38-45): ein Nicht-Objekt-Element in der Options-Liste
    war bisher komplett ungetestet — die Zeile schützt vor ``AttributeError``
    beim nachfolgenden ``option.get(...)``."""

    @pytest.mark.parametrize("options_json", ["[123]", '["x"]'])
    def test_non_object_element_is_form_error(self, client, admin_user, options_json):
        client.force_login(admin_user)
        response = client.post(
            reverse("core:config_fieldtemplate_create"),
            _valid_field_template_post(
                name="Optionen kein Objekt",
                field_type=FieldTemplate.FieldType.SELECT,
                options_json=options_json,
            ),
        )
        assert response.status_code == 200
        errors = [str(e) for e in response.context["form"].errors["options_json"]]
        assert any("muss ein JSON-Objekt sein" in e for e in errors)
        assert not FieldTemplate.objects.filter(name="Optionen kein Objekt").exists()


# ---------------------------------------------------------------------------
# Posten 3 — FieldConditionForm.clean: group_index-Persistenz
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConditionGroupIndexPersistence:
    """Refs #1998 (mutmut_3-10): Reset-auf-0-Bug an Board-ODER-Gruppen — bisher
    prüften nur GET/initial-Tests den ``group_index``, nie den tatsächlich
    über POST persistierten Wert."""

    def test_create_without_group_index_persists_zero(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "greater_than",
                "value": "5",
                # group_index bewusst NICHT gepostet.
            },
        )
        assert response.status_code == 302
        condition = FieldCondition.objects.get(target_field=notiz, depends_on_field=dauer)
        assert condition.group_index == 0

    def test_create_with_group_index_two_persists_two(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "greater_than",
                "value": "5",
                "group_index": "2",
            },
        )
        assert response.status_code == 302
        condition = FieldCondition.objects.get(target_field=notiz, depends_on_field=dauer)
        assert condition.group_index == 2


# ---------------------------------------------------------------------------
# Posten 4 — FieldConditionForm.clean: Crash-Pfad fehlendes depends_on_field
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConditionMissingSourceCrashGuard:
    """Refs #1998 (mutmut_21): ohne den ``source is None or not operator``-
    Kurzschluss griffe ``source.field_template`` auf ``None`` zu
    (``AttributeError`` → HTTP 500) statt eines Formfehlers auf
    ``depends_on_field``."""

    def test_missing_depends_on_field_with_valid_operator_is_form_error_not_500(
        self, client, admin_user, doc_type_contact
    ):
        client.force_login(admin_user)
        notiz = _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": "",
                "operator": "equals",
                "value": "1",
                "group_index": "0",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert "depends_on_field" in response.context["condition_form"].errors
        assert not FieldCondition.objects.filter(target_field=notiz).exists()


# ---------------------------------------------------------------------------
# Posten 5 — FieldConditionForm.clean: leerer Vergleichswert
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConditionEmptyValueForValueRequiredOperator:
    """Refs #1998 (mutmut_50,52-62): ein wertbenötigender Operator (``equals``)
    mit leerem ``value`` muss exakt diesen Feldfehler zeigen."""

    def test_empty_value_is_form_error_with_expected_wording(self, client, admin_user, doc_type_contact):
        client.force_login(admin_user)
        dauer, notiz = _dtf(doc_type_contact, "dauer"), _dtf(doc_type_contact, "notiz")
        url = reverse("core:config_condition_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(
            url,
            {
                "target_field": str(notiz.pk),
                "depends_on_field": str(dauer.pk),
                "operator": "equals",
                "value": "",
                "group_index": "0",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        form = response.context["condition_form"]
        assert "value" in form.errors
        assert "Für diesen Operator ist ein Vergleichswert erforderlich." in [str(e) for e in form.errors["value"]]
        assert not FieldCondition.objects.filter(target_field=notiz).exists()


# ---------------------------------------------------------------------------
# Posten 6 — FieldTemplateForm.clean: report_key-Konflikt nur bei is_active
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldTemplateReportKeyConflictRespectsIsActive:
    """Refs #1998 (mutmut_13/17/18): eine im selben Request deaktivierte
    Vorlage bindet nicht mehr — der Konfliktfehler darf dann nicht auftreten.
    Reaktivierung mit demselben, bereits vergebenen Schlüssel muss dagegen
    wieder als Konflikt abgewiesen werden."""

    def test_deactivating_while_setting_colliding_report_key_has_no_conflict_then_reactivating_conflicts(
        self, client, admin_user, facility, doc_type_contact
    ):
        # doc_type_contact traegt bereits "Dauer" (report_key=dauer_minuten, aktiv).
        ft_zweit = FieldTemplate.objects.create(
            facility=facility,
            name="Zweitzeit",
            field_type=FieldTemplate.FieldType.TEXT,
            is_active=True,
        )
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft_zweit, sort_order=5)
        client.force_login(admin_user)
        url = reverse("core:config_fieldtemplate_update", kwargs={"pk": ft_zweit.pk})

        # 1) Deaktivieren + kollidierenden report_key setzen -> KEIN Konflikt.
        response = client.post(
            url,
            {
                "name": "Zweitzeit",
                "field_type": FieldTemplate.FieldType.TEXT,
                "options_json": "[]",
                "report_key": "dauer_minuten",
                # is_active bewusst NICHT gepostet -> Checkbox aus.
            },
        )
        if response.status_code != 302:
            raise AssertionError(f"Formfehler statt Redirect: {response.context['form'].errors}")
        ft_zweit.refresh_from_db()
        assert ft_zweit.report_key == "dauer_minuten"
        assert ft_zweit.is_active is False

        # 2) Gegenprobe: Reaktivieren -> derselbe Schluessel kollidiert jetzt.
        response2 = client.post(
            url,
            {
                "name": "Zweitzeit",
                "field_type": FieldTemplate.FieldType.TEXT,
                "options_json": "[]",
                "is_active": "on",
                # report_key-Feld ist jetzt disabled (bereits gesetzt) -> nicht gepostet.
            },
        )
        assert response2.status_code == 200
        assert "report_key" in response2.context["form"].errors
        ft_zweit.refresh_from_db()
        assert ft_zweit.is_active is False


# ---------------------------------------------------------------------------
# Posten 8 — FieldGroupForm.clean_name: Duplikat-Query nur bei Namensgleichheit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldGroupNameDuplicateGuard:
    """Refs #1998 (mutmut_11/13): das Duplikat-Query darf weder JEDE Gruppe im
    Dokumentationstyp treffen (mutmut_11) noch beim Bearbeiten sich selbst
    (mutmut_13, fehlender Self-Exclude)."""

    def test_second_differently_named_group_can_be_created(self, client, admin_user, doc_type_contact):
        FieldGroup.objects.create(document_type=doc_type_contact, name="Erste Gruppe")
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_create", kwargs={"pk": doc_type_contact.pk})
        response = client.post(url, {"group-new-name": "Zweite Gruppe"})
        assert response.status_code == 302
        assert FieldGroup.objects.filter(document_type=doc_type_contact, name="Zweite Gruppe").exists()

    def test_update_without_name_change_is_not_rejected_as_duplicate(
        self, client, admin_user, doc_type_contact, field_group
    ):
        client.force_login(admin_user)
        url = reverse("core:config_fieldgroup_update", kwargs={"pk": doc_type_contact.pk, "group_pk": field_group.pk})
        response = client.post(
            url,
            {
                f"group-{field_group.pk}-name": field_group.name,
                f"group-{field_group.pk}-is_collapsible": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        assert b"bereits" not in response.content
        field_group.refresh_from_db()
        assert field_group.is_collapsible is True
