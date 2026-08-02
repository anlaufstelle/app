"""Tests für Hidden-Required + Conditions-Payload im Event-Formular (M1/P4, Refs #1057).

Deckt die neue ``DynamicEventDataForm``-Logik ab:

* Ziel-Felder einer Bedingung werden ``required=False`` gerendert, tragen aber den
  Marker ``is_conditionally_required`` (Pflicht-Stern trotz optionaler Django-Validierung).
* ``clean()`` erzwingt die Pflicht **nur für sichtbare** konditionale Ziel-Felder
  (Evaluator ``core.services.conditions``); versteckte bleiben leer zulässig
  (Issue-Kernfall), Kaskaden-Verstecken greift auch serverseitig.
* ``conditions_payload()`` liefert das E12-Schema **zur Aufrufzeit** (nach
  ``remove_restricted_fields``) und wendet die #774-Drop-Regel an.
* FILE-Ziele: ein bestehender Anhang-Marker (E17) zählt beim Bearbeiten als
  „gefüllt" — transportiert über den Form-kwarg ``existing_file_slugs``, den die
  Update-Views aus den Original-``data_json``-Markern befüllen.
* Decimal-vs-float-Gleichheit (Django-Number-Felder) explizit.

``conftest.py`` bleibt unangetastet (Parallel-Agent P6); Bedingungen werden hier
lokal über Helfer gebaut (Basis: die Bestands-Fixtures ``facility`` /
``doc_type_contact`` mit ``dauer``/number + ``notiz``/textarea).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse
from django.utils import timezone

from core.forms.events import DynamicEventDataForm, _coerce_condition_number
from core.models import DocumentType, DocumentTypeField, Event, FieldCondition, FieldTemplate
from core.services.conditions import evaluate_condition
from core.services.events.fields import file_marker_slugs, remove_restricted_fields

FieldType = FieldTemplate.FieldType
Operator = FieldCondition.Operator


# ---------------------------------------------------------------------------
# Lokale Helfer / Fixtures (conftest ist TABU — P6)
# ---------------------------------------------------------------------------


def _ft(facility, name, field_type, *, required=False, sensitivity="", options=None):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        is_required=required,
        sensitivity=sensitivity,
        options_json=options or [],
    )


def _assign(doc_type, field_template, sort_order=0):
    return DocumentTypeField.objects.create(
        document_type=doc_type,
        field_template=field_template,
        sort_order=sort_order,
    )


def _condition(doc_type, *, target, source, operator, value=None, group_index=0):
    """Baut eine ``FieldCondition`` über die Slugs zweier DTFs des DocumentType."""
    return FieldCondition.objects.create(
        target_field=doc_type.fields.get(field_template__slug=target),
        depends_on_field=doc_type.fields.get(field_template__slug=source),
        operator=operator,
        value=value,
        group_index=group_index,
    )


@pytest.fixture
def cond_doctype(facility):
    """Number-Quelle ``dauer`` + pflichtiges Textarea-Ziel ``notiz``.

    Regel: zeige ``notiz``, wenn ``dauer`` > 10.
    """
    dt = DocumentType.objects.create(facility=facility, name="Bedingter Kontakt")
    _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), sort_order=0)
    _assign(dt, _ft(facility, "Notiz", FieldType.TEXTAREA, required=True), sort_order=1)
    _condition(dt, target="notiz", source="dauer", operator=Operator.GREATER_THAN, value=10)
    return dt


# ---------------------------------------------------------------------------
# E16: Ziel-Feld required=False + Marker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHiddenRequiredMarker:
    def test_target_field_rendered_not_required(self, facility, cond_doctype):
        """Ein Bedingungs-Ziel wird ``required=False`` gebaut (Validierung erst in clean)."""
        form = DynamicEventDataForm(document_type=cond_doctype, facility=facility)
        assert form.fields["notiz"].required is False

    def test_marker_is_conditionally_required_set(self, facility, cond_doctype):
        """Der Marker spiegelt die ursprüngliche Pflicht (Pflicht-Stern)."""
        form = DynamicEventDataForm(document_type=cond_doctype, facility=facility)
        assert form.fields["notiz"].is_conditionally_required is True
        # Nicht-Ziel-/Nicht-Pflicht-Felder tragen keinen Marker.
        assert getattr(form.fields["dauer"], "is_conditionally_required", False) is False

    def test_non_target_required_field_keeps_required(self, facility):
        """Ein Pflichtfeld ohne Bedingung bleibt regulär ``required=True``."""
        dt = DocumentType.objects.create(facility=facility, name="Ohne Bedingung")
        _assign(dt, _ft(facility, "Pflicht", FieldType.TEXT, required=True))
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields["pflicht"].required is True


# ---------------------------------------------------------------------------
# clean(): Pflicht nur für sichtbare Ziele
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCleanHiddenRequired:
    def test_hidden_required_field_passes_clean(self, facility, cond_doctype):
        """Issue-Kernfall: verstecktes Pflicht-Ziel darf leer bleiben (kein Fehler)."""
        # dauer=5 → 5>10 False → notiz versteckt.
        form = DynamicEventDataForm({"dauer": "5", "notiz": ""}, document_type=cond_doctype, facility=facility)
        assert form.is_valid(), form.errors
        assert "notiz" not in form.errors

    def test_visible_required_field_empty_adds_required_error(self, facility, cond_doctype):
        """Sichtbares Pflicht-Ziel leer → Standard-Required-Fehler."""
        form = DynamicEventDataForm({"dauer": "15", "notiz": ""}, document_type=cond_doctype, facility=facility)
        assert not form.is_valid()
        assert "notiz" in form.errors

    def test_visible_required_field_filled_ok(self, facility, cond_doctype):
        """Sichtbares Pflicht-Ziel gefüllt → gültig."""
        form = DynamicEventDataForm({"dauer": "15", "notiz": "etwas"}, document_type=cond_doctype, facility=facility)
        assert form.is_valid(), form.errors

    def test_error_in_source_field_treated_as_empty(self, facility, cond_doctype):
        """Quellfeld mit eigenem Fehler fehlt in cleaned_data → gilt als leer → Ziel versteckt.

        Das Ziel wird NICHT zusätzlich mit einem Required-Fehler belegt (kein Doppelfehler).
        """
        form = DynamicEventDataForm({"dauer": "keine-zahl", "notiz": ""}, document_type=cond_doctype, facility=facility)
        assert not form.is_valid()
        assert "dauer" in form.errors
        assert "notiz" not in form.errors

    def test_values_kept_when_field_hidden(self, facility, cond_doctype):
        """E6 „Behalten": ein Wert im versteckten Feld bleibt in cleaned_data (kein Clear)."""
        form = DynamicEventDataForm({"dauer": "5", "notiz": "behalten"}, document_type=cond_doctype, facility=facility)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["notiz"] == "behalten"

    def test_cascade_hidden_source_hides_dependents_in_clean(self, facility):
        """Zweistufige Kaskade: verstecktes Quellfeld versteckt seine Abhängigen auch in clean()."""
        dt = DocumentType.objects.create(facility=facility, name="Kaskade")
        _assign(dt, _ft(facility, "Quelle", FieldType.NUMBER), sort_order=0)
        _assign(dt, _ft(facility, "Mitte", FieldType.TEXT, required=True), sort_order=1)
        _assign(dt, _ft(facility, "Ende", FieldType.TEXT, required=True), sort_order=2)
        _condition(dt, target="mitte", source="quelle", operator=Operator.GREATER_THAN, value=10)
        _condition(dt, target="ende", source="mitte", operator=Operator.IS_NOT_EMPTY)

        # quelle=5 → mitte versteckt → (leer) → ende versteckt. Beide leer zulässig.
        hidden = DynamicEventDataForm({"quelle": "5", "mitte": "", "ende": ""}, document_type=dt, facility=facility)
        assert hidden.is_valid(), hidden.errors

        # quelle=15 → mitte sichtbar+gefüllt → ende sichtbar+pflicht+leer → Fehler nur bei ende.
        visible = DynamicEventDataForm({"quelle": "15", "mitte": "x", "ende": ""}, document_type=dt, facility=facility)
        assert not visible.is_valid()
        assert "ende" in visible.errors
        assert "mitte" not in visible.errors


# ---------------------------------------------------------------------------
# conditions_payload(): E12-Schema, Timing, #774-Drop
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConditionsPayload:
    def test_payload_format_matches_spec(self, facility, cond_doctype):
        """E12: ``{"fields": {slug: {"type": t}}, "conditions": [{target,source,operator,value,group}]}``."""
        form = DynamicEventDataForm(document_type=cond_doctype, facility=facility)
        payload = form.conditions_payload()
        assert payload is not None
        assert set(payload.keys()) == {"fields", "conditions"}
        assert payload["fields"]["dauer"] == {"type": "number"}
        assert payload["fields"]["notiz"] == {"type": "textarea"}
        assert payload["conditions"] == [
            {"target": "notiz", "source": "dauer", "operator": "greater_than", "value": 10, "group": 0}
        ]

    def test_payload_none_without_conditions(self, facility, doc_type_contact):
        """Ohne Bedingungen im DocumentType → kein Payload."""
        form = DynamicEventDataForm(document_type=doc_type_contact, facility=facility)
        assert form.conditions_payload() is None

    def test_restricted_source_condition_dropped_payload_and_clean(self, facility, assistant_user):
        """#774: Quellfeld, das der User nicht sehen darf, fällt aus Payload UND Kaskade.

        Der Vergleichswert der Bedingung darf so nie ins Frontend gelangen. Das Ziel
        ist dann unbedingt sichtbar und behält seine Pflicht (kein stiller Opt-out).
        """
        dt = DocumentType.objects.create(
            facility=facility, name="Sensibel", sensitivity=DocumentType.Sensitivity.NORMAL
        )
        # ELEVATED-Quelle (nicht verschlüsselt ⇒ gültige Quelle), für Assistant unsichtbar.
        _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER, sensitivity=DocumentType.Sensitivity.ELEVATED), 0)
        _assign(dt, _ft(facility, "Notiz", FieldType.TEXTAREA, required=True), 1)
        _condition(dt, target="notiz", source="dauer", operator=Operator.GREATER_THAN, value=10)

        form = DynamicEventDataForm({"notiz": ""}, document_type=dt, facility=facility)
        removed = remove_restricted_fields(assistant_user, dt, form)
        assert "dauer" in removed
        # Kein Leak: die Bedingung (inkl. value 10) taucht nicht mehr im Payload auf.
        assert form.conditions_payload() is None
        # notiz ist unbedingt sichtbar + pflichtig → wird durchgesetzt.
        assert not form.is_valid()
        assert "notiz" in form.errors

    def test_partial_drop_target_required_enforced_with_remaining_payload(self, facility, assistant_user):
        """Teil-Drop: zwei Bedingungen auf zwei Ziele, nur eine Quelle ist restricted.

        Das Ziel der gedroppten Bedingung (``notiz-a``) wird unbedingt sichtbar
        und pflichtig (kein stiller Opt-out, analog dem #774-Einzelfall), das
        Ziel der verbleibenden Bedingung (``notiz-b``) folgt weiter seiner
        eigenen Bedingung, und das Payload enthält die gedroppte Bedingung
        nicht mehr.
        """
        dt = DocumentType.objects.create(
            facility=facility, name="Teil-Drop", sensitivity=DocumentType.Sensitivity.NORMAL
        )
        # Bedingung A: ELEVATED-Quelle → für Assistant restricted → wird gedroppt.
        _assign(dt, _ft(facility, "Dauer A", FieldType.NUMBER, sensitivity=DocumentType.Sensitivity.ELEVATED), 0)
        _assign(dt, _ft(facility, "Notiz A", FieldType.TEXTAREA, required=True), 1)
        _condition(dt, target="notiz-a", source="dauer-a", operator=Operator.GREATER_THAN, value=10)
        # Bedingung B: normale Quelle → bleibt für Assistant erhalten.
        _assign(dt, _ft(facility, "Dauer B", FieldType.NUMBER), 2)
        _assign(dt, _ft(facility, "Notiz B", FieldType.TEXTAREA, required=True), 3)
        _condition(dt, target="notiz-b", source="dauer-b", operator=Operator.GREATER_THAN, value=10)

        form = DynamicEventDataForm({"notiz-a": "", "dauer-b": "5", "notiz-b": ""}, document_type=dt, facility=facility)
        removed = remove_restricted_fields(assistant_user, dt, form)
        assert "dauer-a" in removed

        # Kein Leak: nur die verbleibende Bedingung (B) taucht im Payload auf.
        payload = form.conditions_payload()
        assert payload is not None
        assert {c["target"] for c in payload["conditions"]} == {"notiz-b"}
        assert {c["source"] for c in payload["conditions"]} == {"dauer-b"}

        # notiz-a: Bedingung gedroppt → unbedingt sichtbar+pflichtig → leer → Fehler.
        # notiz-b: dauer-b=5 → 5>10 False → weiterhin versteckt → leer bleibt zulässig.
        assert not form.is_valid()
        assert "notiz-a" in form.errors
        assert "notiz-b" not in form.errors

        # Gegenprobe: dauer-b>10 → notiz-b wird sichtbar+pflichtig → jetzt dort ein Fehler,
        # notiz-a bleibt (unverändert unbedingt pflichtig, diesmal gefüllt) fehlerfrei.
        form_visible = DynamicEventDataForm(
            {"notiz-a": "x", "dauer-b": "15", "notiz-b": ""}, document_type=dt, facility=facility
        )
        remove_restricted_fields(assistant_user, dt, form_visible)
        assert not form_visible.is_valid()
        assert "notiz-b" in form_visible.errors
        assert "notiz-a" not in form_visible.errors

    def test_payload_read_after_remove_restricted_fields(self, facility, staff_user):
        """Timing: der Payload liest ``self.fields`` NACH ``remove_restricted_fields``.

        Ein für den User sichtbares (STAFF sieht ELEVATED) Feld bleibt drin — hier
        wird nichts gedroppt, der Payload trägt die Bedingung.
        """
        dt = DocumentType.objects.create(facility=facility, name="Sichtbar")
        _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), 0)
        _assign(dt, _ft(facility, "Notiz", FieldType.TEXTAREA, required=True), 1)
        _condition(dt, target="notiz", source="dauer", operator=Operator.GREATER_THAN, value=10)

        form = DynamicEventDataForm(document_type=dt, facility=facility)
        remove_restricted_fields(staff_user, dt, form)
        payload = form.conditions_payload()
        assert payload is not None
        assert {c["source"] for c in payload["conditions"]} == {"dauer"}


# ---------------------------------------------------------------------------
# FILE-Ziele (E17) + Decimal-Falle
# ---------------------------------------------------------------------------


def _file_cond_doctype(facility):
    """Number-Quelle ``dauer`` + pflichtiges FILE-Ziel ``anhang``; zeige anhang wenn dauer > 10."""
    dt = DocumentType.objects.create(facility=facility, name="Datei-Bedingung")
    _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), 0)
    _assign(dt, _ft(facility, "Anhang", FieldType.FILE, required=True), 1)
    _condition(dt, target="anhang", source="dauer", operator=Operator.GREATER_THAN, value=10)
    return dt


@pytest.mark.django_db
class TestFileTargetAndDecimal:
    def test_file_target_create_visible_required_empty_fails(self, facility):
        """Neu-Anlage: sichtbares FILE-Pflichtziel ohne Upload und ohne Bestandsmarker → Fehler."""
        dt = _file_cond_doctype(facility)
        form = DynamicEventDataForm({"dauer": "15"}, {}, document_type=dt, facility=facility)
        assert not form.is_valid()
        assert "anhang" in form.errors

    def test_file_target_edit_existing_marker_counts_as_filled(self, facility):
        """E17: ein bestehender Anhang-Marker (via ``existing_file_slugs``) zählt als gefüllt."""
        dt = _file_cond_doctype(facility)
        form = DynamicEventDataForm(
            {"dauer": "15"},
            {},
            document_type=dt,
            facility=facility,
            existing_file_slugs={"anhang"},
        )
        assert form.is_valid(), form.errors

    def test_file_target_hidden_not_required(self, facility):
        """Verstecktes FILE-Pflichtziel (dauer≤10) ohne Upload/Marker → zulässig."""
        dt = _file_cond_doctype(facility)
        form = DynamicEventDataForm({"dauer": "5"}, {}, document_type=dt, facility=facility)
        assert form.is_valid(), form.errors

    def test_number_decimal_value_coerced_to_float(self):
        """Decimal-Falle: ``Decimal("1.1") != 1.1`` (float); clean() zieht Number-Werte auf float."""
        # Ohne Koerzierung schlägt die Gleichheit fehl (bekannte Decimal/float-Falle).
        assert evaluate_condition("equals", "number", Decimal("1.1"), 1.1) is False
        # Der Koerzierungs-Helfer bringt beide Seiten auf float.
        coerced = _coerce_condition_number(Decimal("1.1"))
        assert isinstance(coerced, float)
        assert coerced == 1.1
        assert evaluate_condition("equals", "number", coerced, 1.1) is True
        # None bleibt None (leerer Wert).
        assert _coerce_condition_number(None) is None

    def test_number_equals_json_float_value_shows_target(self, facility):
        """Ende-zu-Ende: int-Ist-Wert (IntegerField) == JSON-float-``value`` → Ziel sichtbar+pflicht."""
        dt = DocumentType.objects.create(facility=facility, name="Number-Equals")
        _assign(dt, _ft(facility, "Dauer", FieldType.NUMBER), 0)
        _assign(dt, _ft(facility, "Notiz", FieldType.TEXTAREA, required=True), 1)
        _condition(dt, target="notiz", source="dauer", operator=Operator.EQUALS, value=15.0)

        form = DynamicEventDataForm({"dauer": "15", "notiz": ""}, document_type=dt, facility=facility)
        assert not form.is_valid()
        assert "notiz" in form.errors


# ---------------------------------------------------------------------------
# View-Roundtrip (E17-Marker-Transport)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdateViewMarkerTransport:
    def test_file_marker_slugs_extracts_both_formats(self, facility):
        """Der publizierte Helfer erkennt Stufe-A- und Stufe-B-Marker."""
        data_json = {
            "dauer": 15,
            "single": {"__file__": True, "attachment_id": str(uuid4())},
            "multi": {"__files__": True, "entries": [{"id": str(uuid4()), "sort": 0}]},
            "leer": "",
        }
        assert file_marker_slugs(data_json) == {"single", "multi"}
        assert file_marker_slugs({}) == set()
        assert file_marker_slugs(None) == set()

    def test_update_view_passes_existing_file_slugs_from_markers(self, client, staff_user, facility):
        """Roundtrip: Die Update-View leitet die FILE-Marker-Slugs des Events an die Form.

        Ersetzt den alten ``..._keeps_marker_and_form_skips_initial``-Test: Marker
        erreichen die Form nicht mehr über ``initial_data`` (der Service strippt sie),
        sondern über den expliziten kwarg ``existing_file_slugs``.
        """
        dt = _file_cond_doctype(facility)
        event = Event.objects.create(
            facility=facility,
            document_type=dt,
            occurred_at=timezone.now(),
            data_json={
                "dauer": 15,
                "anhang": {"__files__": True, "entries": [{"id": str(uuid4()), "sort": 0}]},
            },
            created_by=staff_user,
        )

        captured: dict[str, object] = {}
        real_init = DynamicEventDataForm.__init__

        def spy_init(self, *args, **kwargs):
            captured["existing_file_slugs"] = kwargs.get("existing_file_slugs")
            return real_init(self, *args, **kwargs)

        client.force_login(staff_user)
        with patch.object(DynamicEventDataForm, "__init__", spy_init):
            resp = client.get(reverse("core:event_update", kwargs={"pk": event.pk}))

        assert resp.status_code == 200
        assert captured["existing_file_slugs"] == {"anhang"}


# ---------------------------------------------------------------------------
# Template: Pflicht-Stern an beiden Render-Pfaden
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRequiredStarRendering:
    def test_conditional_required_star_in_shared_field_partial(self, client, staff_user, facility, cond_doctype):
        """Der Nicht-FILE-Renderer (``_form_field.html``) zeigt den Stern trotz required=False."""
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={cond_doctype.pk}")
        body = resp.content.decode()
        # Der Notiz-Block trägt einen Stern, obwohl das Feld Django-seitig optional ist.
        assert "Notiz" in body
        assert " *" in body

    def test_conditional_required_star_in_file_block(self, client, staff_user, facility):
        """Der FILE-Inline-Block (``dynamic_fields.html``) zeigt den Stern für ein FILE-Ziel."""
        dt = _file_cond_doctype(facility)
        client.force_login(staff_user)
        resp = client.get(reverse("core:event_fields_partial") + f"?document_type={dt.pk}")
        body = resp.content.decode()
        assert "Anhang" in body
        assert " *" in body
