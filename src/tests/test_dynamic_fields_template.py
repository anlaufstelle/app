"""Template-Tests für das reaktive Alpine-Frontend der Feldbedingungen (M1/P5, Refs #1057).

Prüft, dass das HTMX-geswappte Partial ``core/events/partials/dynamic_fields.html``
den CSP-konformen Alpine-Aufbau rendert:

* Ein Wurzel-``<div x-data="conditionEvaluator">`` als Träger von json_script und
  delegierten Listenern (liegt im Swap-Ziel ``#dynamic-fields``).
* Das E12-Payload als ``|json_script:"field-conditions"`` — **nur wenn** Bedingungen
  existieren (ohne Bedingungen kein Script-Tag); korrektes E12-Schema.
* Je Feld (auch FILE) ein Wrapper ``x-data="conditionalField" data-field-slug=…
  x-show="visible"``.

Der Payload wird bewusst über die Partial-View (``core:event_fields_partial``) geholt,
weil sie ``remove_restricted_fields`` unmittelbar nach der Formkonstruktion aufruft —
also genau die Form-Instanz rendert, deren ``conditions_payload`` gültig ist (P4).

``conftest.py`` bleibt unangetastet (Parallel-Konvention); Testdaten lokal gebaut.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from core.models import DocumentType, DocumentTypeField, FieldCondition, FieldGroup, FieldTemplate

FieldType = FieldTemplate.FieldType
Operator = FieldCondition.Operator


def _ft(facility, name, field_type, *, required=False, options=None):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        is_required=required,
        options_json=options or [],
    )


def _assign(doc_type, field_template, sort_order=0, group=None):
    return DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        sort_order=sort_order,
        field_group=group,
    )


def _condition(doc_type, *, target, source, operator, value=None, group_index=0):
    return FieldCondition.objects.create(
        target_field=doc_type.fields.get(field_template__slug=target),
        depends_on_field=doc_type.fields.get(field_template__slug=source),
        operator=operator,
        value=value,
        group_index=group_index,
    )


@pytest.fixture
def cond_doctype(facility):
    """Number-Quelle ``dauer`` + Textarea-Ziel ``notiz`` + FILE-Ziel ``anhang``.

    Regeln: zeige ``notiz`` wenn ``dauer`` > 10; zeige ``anhang`` wenn ``dauer`` > 10.
    Deckt beide Feld-Renderpfade (geteiltes ``_form_field.html`` und FILE-Inline-Block).
    """
    dt = DocumentType.objects.create(facility=facility, name="Bedingter Kontakt")
    _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), sort_order=0)
    _assign(dt, _ft(facility, "Notiz", FieldType.TEXTAREA, required=True), sort_order=1)
    _assign(dt, _ft(facility, "Anhang", FieldType.FILE, required=True), sort_order=2)
    _condition(dt, target="notiz", source="dauer", operator=Operator.GREATER_THAN, value=10)
    _condition(dt, target="anhang", source="dauer", operator=Operator.GREATER_THAN, value=10)
    return dt


def _partial(client, doc_type):
    return client.get(reverse("core:event_fields_partial") + f"?document_type={doc_type.pk}")


def _wrapper_tag(body, slug):
    """Öffnendes ``conditionalField``-Wrapper-Tag eines Feld-Slugs."""
    match = re.search(
        r'<div x-data="conditionalField" data-field-slug="' + re.escape(slug) + r'"[^>]*>',
        body,
    )
    assert match, f"conditionalField-Wrapper für {slug} fehlt"
    return match.group(0)


# Refs #1867: öffnendes ``conditionalGroup``-Wrapper-Tag (Gruppen-/Ungruppiert-
# Karte, komplett ausblendbar NUR wenn ALLE ihre Felder Bedingungs-Ziele sind).
_GROUP_WRAPPER_RE = re.compile(r'<div x-data="conditionalGroup"[^>]*>')


@pytest.fixture
def cond_doctype_grouped(facility):
    """Refs #1867: eine Gruppe, deren EINZIGES Feld ein Bedingungs-Ziel ist
    (komplett ausblendbar), und eine Gruppe mit einem Ziel PLUS einem freien
    Feld (bleibt immer sichtbar — Kontrollfall gemischter Block).

    Beide Ziele hängen an derselben freien Quelle ``dauer`` (ungruppiert).
    """
    dt = DocumentType.objects.create(facility=facility, name="Bedingt-Gruppiert")
    g_all = FieldGroup.objects.create(document_type=dt, name="Nur-Ziel", sort_order=0)
    g_mixed = FieldGroup.objects.create(document_type=dt, name="Gemischt", sort_order=1)
    _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), sort_order=0)
    _assign(dt, _ft(facility, "Ziel A", FieldType.TEXTAREA), sort_order=0, group=g_all)
    _assign(dt, _ft(facility, "Ziel B", FieldType.TEXT), sort_order=0, group=g_mixed)
    _assign(dt, _ft(facility, "Frei", FieldType.TEXT), sort_order=1, group=g_mixed)
    _condition(dt, target="ziel-a", source="dauer", operator=Operator.GREATER_THAN, value=10)
    _condition(dt, target="ziel-b", source="dauer", operator=Operator.GREATER_THAN, value=10)
    return dt


@pytest.fixture
def cond_doctype_ungrouped_target(facility):
    """Refs #1867: die Quelle steckt in einer Gruppe, das Ziel bleibt allein im
    freien Block — deckt die ungruppierte „Angaben"/„Weitere Angaben"-Karte ab,
    die dasselbe Markup teilt und denselben Fehler hatte.
    """
    dt = DocumentType.objects.create(facility=facility, name="Bedingt-Flach")
    g_source = FieldGroup.objects.create(document_type=dt, name="Quelle-Gruppe", sort_order=0)
    _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), sort_order=0, group=g_source)
    _assign(dt, _ft(facility, "Ziel", FieldType.TEXT), sort_order=1)
    _condition(dt, target="ziel", source="dauer", operator=Operator.GREATER_THAN, value=10)
    return dt


@pytest.mark.django_db
class TestDynamicFieldsTemplate:
    def test_dynamic_fields_template_renders_json_script_and_wrappers(self, client, staff_user, facility, cond_doctype):
        """MIT Bedingungen: Wurzel-Wrapper, json_script und Feld-Wrapper vorhanden."""
        client.force_login(staff_user)
        body = _partial(client, cond_doctype).content.decode()

        # Wurzel-Evaluator + json_script IM Swap-Ziel.
        assert 'x-data="conditionEvaluator"' in body
        assert 'id="field-conditions"' in body
        # Feld-Wrapper-Muster.
        assert 'x-data="conditionalField"' in body
        assert 'x-show="visible"' in body
        # Ein Wrapper je Feld — auch für das FILE-Feld (eigener Renderpfad).
        assert 'data-field-slug="dauer"' in body
        assert 'data-field-slug="notiz"' in body
        assert 'data-field-slug="anhang"' in body

    def test_x_cloak_only_on_condition_targets(self, client, staff_user, facility, cond_doctype):
        """``x-cloak`` NUR an Bedingungs-Zielen — nicht an Quell-/unkonditionierten Feldern.

        Resilienz: bei Alpine-Boot-Fehler bleiben Kern-Inputs (Quelle ``dauer``)
        sichtbar; nur je verborgene Ziele (``notiz``, ``anhang``) werden gecloakt.
        """
        client.force_login(staff_user)
        body = _partial(client, cond_doctype).content.decode()

        # Ziele: gecloakt.
        assert "x-cloak" in _wrapper_tag(body, "notiz")
        assert "x-cloak" in _wrapper_tag(body, "anhang")
        # Quellfeld (nie Ziel): NICHT gecloakt → ohne Alpine bedienbar.
        assert "x-cloak" not in _wrapper_tag(body, "dauer")

    def test_json_script_matches_e12_schema(self, client, staff_user, facility, cond_doctype):
        """Der json_script-Inhalt entspricht dem E12-Payload-Schema."""
        client.force_login(staff_user)
        body = _partial(client, cond_doctype).content.decode()

        match = re.search(
            r'<script id="field-conditions" type="application/json">(.*?)</script>',
            body,
            re.DOTALL,
        )
        assert match, "json_script mit id=field-conditions fehlt"
        payload = json.loads(match.group(1))

        assert set(payload.keys()) == {"fields", "conditions"}
        assert payload["fields"]["dauer"] == {"type": "number"}
        assert payload["fields"]["notiz"] == {"type": "textarea"}
        assert payload["fields"]["anhang"] == {"type": "file"}
        # Beide Bedingungen im E12-Format (Reihenfolge egal).
        assert {(c["target"], c["source"], c["operator"], c["value"], c["group"]) for c in payload["conditions"]} == {
            ("notiz", "dauer", "greater_than", 10, 0),
            ("anhang", "dauer", "greater_than", 10, 0),
        }

    def test_no_json_script_without_conditions(self, client, staff_user, facility):
        """OHNE Bedingungen: Feld-Wrapper ja, aber KEIN json_script."""
        dt = DocumentType.objects.create(facility=facility, name="Ohne Bedingung")
        _assign(dt, _ft(facility, "Frei", FieldType.TEXT), sort_order=0)

        client.force_login(staff_user)
        body = _partial(client, dt).content.decode()

        assert 'id="field-conditions"' not in body
        # Der Evaluator-Wrapper bleibt (leert den Store beim DocType-Wechsel).
        assert 'x-data="conditionEvaluator"' in body
        assert 'data-field-slug="frei"' in body
        assert 'x-show="visible"' in body
        # Ohne Bedingungen kein Ziel → gar kein x-cloak (volle Resilienz).
        assert "x-cloak" not in body


@pytest.mark.django_db
class TestConditionalGroupWrapper:
    """Refs #1867: die Gruppen-/Ungruppiert-Karte trägt den ``conditionalGroup``-
    Wrapper NUR, wenn ALLE ihre Felder Bedingungs-Ziele sind — sonst bleibt sie
    unverändert (kein zusätzliches DOM-Level für den Normalfall).
    """

    def test_wraps_named_group_whose_only_field_is_a_target(self, client, staff_user, facility, cond_doctype_grouped):
        """Gruppe „Nur-Ziel" (einziges Feld = Ziel) bekommt den Wrapper mit ihrem Slug."""
        client.force_login(staff_user)
        body = _partial(client, cond_doctype_grouped).content.decode()

        matches = _GROUP_WRAPPER_RE.findall(body)
        assert len(matches) == 1, f"Erwartet genau EIN conditionalGroup-Wrapper, gefunden: {matches}"
        assert 'data-group-slugs="ziel-a"' in matches[0]
        assert 'x-show="visible"' in matches[0]
        assert "x-cloak" in matches[0]

    def test_no_wrapper_on_mixed_group(self, client, staff_user, facility, cond_doctype_grouped):
        """Gruppe „Gemischt" (Ziel + freies Feld) bleibt ungewrappt — bereits durch
        den Einzeltreffer-Assert oben mitbewiesen (sonst gäbe es zwei Wrapper);
        hier zusätzlich explizit über den Gruppennamen geprüft.
        """
        client.force_login(staff_user)
        body = _partial(client, cond_doctype_grouped).content.decode()

        mixed_card = re.search(r'<div class="card p-4 space-y-4">\s*<h2[^>]*>\s*Gemischt', body)
        assert mixed_card, "Gemischt-Gruppenkarte nicht gefunden"
        # Kein conditionalGroup-x-data unmittelbar VOR der Gemischt-Karte (kein
        # eigener Wrapper) — die einzige Fundstelle im Dokument gehört „Nur-Ziel".
        assert body.count('data-group-slugs="ziel-b,frei"') == 0
        assert body.count('data-group-slugs="ziel-b"') == 0

    def test_wraps_ungrouped_target_only_block(self, client, staff_user, facility, cond_doctype_ungrouped_target):
        """Der freie Block „Weitere Angaben" mit dem alleinigen Ziel-Feld wird ebenso gewrappt."""
        client.force_login(staff_user)
        body = _partial(client, cond_doctype_ungrouped_target).content.decode()

        matches = _GROUP_WRAPPER_RE.findall(body)
        assert len(matches) == 1
        assert 'data-group-slugs="ziel"' in matches[0]

    def test_no_wrapper_without_all_target_block(self, client, staff_user, facility, cond_doctype):
        """``cond_doctype`` (Refs #1057) ist komplett flach/gemischt — nirgends ein Wrapper."""
        client.force_login(staff_user)
        body = _partial(client, cond_doctype).content.decode()

        assert 'x-data="conditionalGroup"' not in body

    def test_no_wrapper_without_any_conditions(self, client, staff_user, facility):
        """DocType ganz ohne Bedingungen: nie ein conditionalGroup-Wrapper."""
        dt = DocumentType.objects.create(facility=facility, name="Ohne Bedingung Gruppiert")
        g = FieldGroup.objects.create(document_type=dt, name="Gruppe", sort_order=0)
        _assign(dt, _ft(facility, "Solo", FieldType.TEXT), sort_order=0, group=g)

        client.force_login(staff_user)
        body = _partial(client, dt).content.decode()

        assert 'x-data="conditionalGroup"' not in body
        assert "Gruppe" in body
