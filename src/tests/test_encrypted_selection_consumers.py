"""Public consumer paths preserve encrypted selections and output contracts."""

import csv
import io
from html.parser import HTMLParser

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Case, DocumentTypeField, FieldTemplate
from core.services.file_vault import generate_key, is_encrypted_value, safe_decrypt
from tests._factories import make_client, make_document_type, make_event


class ConsumerHTMLParser(HTMLParser):
    """Inspect checkbox selection and the rendered definition's whitespace rule."""

    def __init__(self):
        super().__init__()
        self.inputs = []
        self.definitions = []
        self.current_definition = None

    def handle_starttag(self, tag, attrs):
        if tag == "input":
            self.inputs.append(dict(attrs))
        if tag == "dd":
            self.current_definition = [dict(attrs), ""]

    def handle_data(self, data):
        if self.current_definition is not None:
            self.current_definition[1] += data

    def handle_endtag(self, tag):
        if tag == "dd" and self.current_definition is not None:
            self.definitions.append(self.current_definition)
            self.current_definition = None


@pytest.fixture
def selection_entity(request, facility, client_identified, staff_user):
    entity_type, required, encrypted = request.param
    document_type = make_document_type(facility=facility, name="Selection profile", entity_type=entity_type)
    template = FieldTemplate.objects.create(
        facility=facility,
        name="Private selection",
        slug="selection",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
        is_encrypted=encrypted,
        is_required=required,
        options_json=[
            {"slug": "a", "label": "First label", "is_active": True},
            {"slug": "b", "label": "Second label", "is_active": False},
            {"slug": "c", "label": "Unused label", "is_active": False},
        ],
    )
    DocumentTypeField.objects.create(document_type=document_type, field_template=template)
    with override_settings(ENCRYPTION_KEYS="", ENCRYPTION_KEY=generate_key()):
        kwargs = dict(
            facility=facility, created_by=staff_user, document_type=document_type, data_json={"selection": ["a", "b"]}
        )
        if entity_type == "client":
            entity = make_client(pseudonym="Selection person", **kwargs)
        elif entity_type == "case":
            entity = Case.objects.create(client=client_identified, title="Selection case", **kwargs)
        else:
            entity = make_event(client=client_identified, occurred_at=timezone.now(), **kwargs)
        yield entity, template


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selection_entity",
    [(kind, required, True) for kind in ("client", "case", "event") for required in (False, True)],
    indirect=True,
)
def test_unchanged_selection_survives_update_view(selection_entity, client, staff_user):
    entity, template = selection_entity
    client.force_login(staff_user)
    url = reverse(f"core:{entity._meta.model_name}_update", kwargs={"pk": entity.pk})
    response = client.get(url)
    assert response.status_code == 200
    data_form = response.context["data_form"]
    assert data_form[template.slug].value() == ["a", "b"]
    assert {slug for slug, _label in data_form.fields[template.slug].choices} == {"a", "b"}
    rendered = ConsumerHTMLParser()
    rendered.feed(str(data_form[template.slug]))
    assert {attrs["value"] for attrs in rendered.inputs if "checked" in attrs} == {"a", "b"}
    payload = {
        template.slug: data_form[template.slug].value(),
        "expected_updated_at": response.context["expected_updated_at"],
    }
    if entity._meta.model_name != "event":
        form = response.context["form"]
        for name in form.fields:
            value = form[name].value()
            payload[name] = "" if value is None else value
    else:
        payload["expected_form_state"] = response.context["form_state_token"]
    result = client.post(url, payload)
    assert result.status_code == 302, getattr(result, "context", None)
    entity.refresh_from_db()
    stored = entity.data_json[template.slug]
    assert is_encrypted_value(stored)
    assert set(stored) == {"__encrypted__", "value"}
    assert safe_decrypt(stored) == ["a", "b"]
    assert type(safe_decrypt(stored)) is list
    for detail in AuditLog.objects.filter(target_id=entity.pk).values_list("detail", flat=True):
        assert "First label" not in str(detail)
        assert "Second label" not in str(detail)
        assert ["a", "b"] not in (detail or {}).values()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selection_entity",
    [(kind, False, encrypted) for kind in ("client", "case", "event") for encrypted in (False, True)],
    indirect=True,
)
def test_detail_view_renders_each_selection_label_on_its_own_line(selection_entity, client, staff_user):
    entity, _template = selection_entity
    client.force_login(staff_user)
    response = client.get(reverse(f"core:{entity._meta.model_name}_detail", kwargs={"pk": entity.pk}))
    assert response.status_code == 200
    rendered = ConsumerHTMLParser()
    rendered.feed(response.content.decode())
    definitions = [(attrs, text.strip()) for attrs, text in rendered.definitions if "First label" in text]
    assert len(definitions) == 1
    attrs, text = definitions[0]
    assert text == "First label\nSecond label"
    assert "whitespace-pre-wrap" in attrs["class"].split()
    assert "Unused label" not in response.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selection_entity", [("event", False, encrypted) for encrypted in (True, False)], indirect=True
)
def test_csv_export_resolves_decrypted_selection_labels(selection_entity, facility, staff_user):
    from core.services.system.export import export_events_csv

    _entity, template = selection_entity
    today = timezone.localdate()
    output = "".join(export_events_csv(facility, today, today, user=staff_user))
    rows = list(csv.reader(io.StringIO(output)))
    assert len(rows) == 3
    assert rows[2][rows[1].index(template.name)] == "First label, Second label"
    assert "Unused label" not in output
    assert "__encrypted__" not in output


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("event", False, True)], indirect=True)
def test_article_20_event_json_keeps_real_slug_lists(selection_entity, facility, staff_user):
    import json

    from core.services.client import export_client_data

    entity, template = selection_entity
    data = export_client_data(entity.client, facility, staff_user)
    serialized = json.loads(json.dumps(data))
    assert len(serialized["events"]) == 1
    event = serialized["events"][0]
    assert event["data"][template.slug] == ["a", "b"]
    assert type(event["data"][template.slug]) is list
    assert "First label" not in json.dumps(event["data"])
    assert "__encrypted__" not in json.dumps(event["data"])


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selection_entity", [(kind, False, True) for kind in ("client", "case", "event")], indirect=True
)
def test_article_15_pdf_uses_readable_selection_labels(selection_entity, facility, staff_user):
    from core.services.client.export import export_client_data_pdf, render_client_export_html

    entity, _template = selection_entity
    person = entity if entity._meta.model_name == "client" else entity.client
    html = render_client_export_html(person, facility, staff_user)
    assert "First label" in html
    assert "Second label" in html
    assert "Unused label" not in html
    assert "[&#x27;a&#x27;, &#x27;b&#x27;]" not in html
    assert "__encrypted__" not in html
    assert export_client_data_pdf(person, facility, staff_user).startswith(b"%PDF")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("selection_entity", "deleted"),
    [
        pytest.param((kind, False, encrypted), deleted, id=f"{kind}-{deleted}-{encrypted}")
        for kind, deleted in (("client", False), ("case", False), ("case", True))
        for encrypted in (False, True)
    ],
    indirect=["selection_entity"],
)
@pytest.mark.parametrize("orphaned", [False, True], ids=["assigned", "orphaned"])
def test_disclosure_html_preserves_selection_boundaries(selection_entity, facility, staff_user, deleted, orphaned):
    from core.services.client.export import render_client_export_html

    entity, template = selection_entity
    if deleted:
        entity.is_deleted = True
        entity.deleted_at = timezone.now()
        entity.save(update_fields=["is_deleted", "deleted_at"])
    if orphaned:
        DocumentTypeField.objects.filter(document_type=entity.document_type, field_template=template).delete()
    person = entity if entity._meta.model_name == "client" else entity.client
    html = render_client_export_html(person, facility, staff_user)
    assert "First label<br>Second label" in html
    assert "First label\nSecond label" not in html
    assert "Unused label" not in html
    assert "__encrypted__" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("client", False, True)], indirect=True)
def test_disclosure_selection_breaks_keep_labels_and_unknown_slugs_escaped(selection_entity, facility, staff_user):
    from core.services.client.export import render_client_export_html

    person, template = selection_entity
    template.options_json = [{"slug": "a", "label": "First <label>"}, {"slug": "b", "label": "Second & label"}]
    # Legacy unknown slugs predate selection validation at persistence.
    template.field_type = FieldTemplate.FieldType.TEXT
    # Refs #2572: historical invalid states bypass current type-change guards.
    template.save(update_fields=["options_json"])
    FieldTemplate.objects.for_facility(facility).filter(pk=template.pk).update(field_type=template.field_type)
    person.data_json = {template.slug: ["a", "b", "missing<&>"]}
    person.save(update_fields=["data_json"])
    template.field_type = FieldTemplate.FieldType.MULTI_SELECT
    FieldTemplate.objects.for_facility(facility).filter(pk=template.pk).update(field_type=template.field_type)
    html = render_client_export_html(person, facility, staff_user)
    assert "First &lt;label&gt;<br>Second &amp; label<br>missing&lt;&amp;&gt;" in html
    assert "<label>" not in html
    assert "__encrypted__" not in html


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("client", False, True)], indirect=True)
@pytest.mark.parametrize("value_side", ["old", "new"])
def test_disclosure_rectification_preserves_selection_boundaries(selection_entity, facility, staff_user, value_side):
    from core.services.client.export import render_client_export_html
    from core.services.client.rectification import request_rectification

    person, template = selection_entity
    request_rectification(person, staff_user, f"data_json.{template.slug}", ["b", "a"], "Correction requested")
    html = render_client_export_html(person, facility, staff_user)
    section = html.split("11. Berichtigungsanträge", 1)[1].split("12. Aufbewahrung", 1)[0]
    labels = ("First label", "Second label") if value_side == "old" else ("Second label", "First label")
    assert "<br>".join(labels) in section
    assert "__encrypted__" not in section


@pytest.mark.django_db
@pytest.mark.parametrize(
    "selection_entity", [(kind, False, True) for kind in ("client", "case", "event")], indirect=True
)
def test_option_usage_recognizes_each_encrypted_selection_slug(selection_entity):
    from core.services.option_usage import option_slugs_in_use

    _entity, template = selection_entity
    assert option_slugs_in_use(template) == {"a", "b"}


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("event", False, True)], indirect=True)
@pytest.mark.parametrize("history_side", ["data_before", "data_after"])
@pytest.mark.parametrize("encrypted", [True, False])
def test_option_usage_includes_history_only_selection_slugs(selection_entity, staff_user, history_side, encrypted):
    from core.models import EventHistory
    from core.services.file_vault import encrypt_field
    from core.services.option_usage import option_slugs_in_use

    entity, template = selection_entity
    entity.data_json = {}
    entity.save()
    value = encrypt_field(["a", "b"]) if encrypted else ["a", "b"]
    EventHistory.objects.create(
        event=entity, changed_by=staff_user, action=EventHistory.Action.UPDATE, **{history_side: {template.slug: value}}
    )
    assert option_slugs_in_use(template) == {"a", "b"}


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("event", False, True)], indirect=True)
@pytest.mark.parametrize("expected_slug, target_visible", [("a", True), ("b", True), ("c", False)])
def test_condition_evaluator_matches_individual_decrypted_selection_slugs(
    selection_entity, facility, expected_slug, target_visible
):
    from core.models import FieldCondition
    from core.services.conditions import build_conditions_payload, evaluate_conditions, normalize_value
    from core.services.events import decrypt_event_text_data

    entity, template = selection_entity
    target = FieldTemplate.objects.create(
        facility=facility, name="Conditional note", slug="note", field_type=FieldTemplate.FieldType.TEXT
    )
    target_field = DocumentTypeField.objects.create(document_type=entity.document_type, field_template=target)
    source_field = entity.document_type.fields.get(field_template=template)
    FieldCondition.objects.create(
        target_field=target_field,
        depends_on_field=source_field,
        operator=FieldCondition.Operator.CONTAINS,
        value=expected_slug,
    )
    payload = build_conditions_payload(entity.document_type, {"selection", "note"})
    fields = {slug: spec["type"] for slug, spec in payload["fields"].items()}
    initial = decrypt_event_text_data(entity)
    values = {slug: normalize_value(field_type, initial.get(slug)) for slug, field_type in fields.items()}
    visible = evaluate_conditions(fields, payload["conditions"], values)
    assert visible == ({"selection", "note"} if target_visible else {"selection"})


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("event", False, True)], indirect=True)
def test_event_update_keeps_selection_history_encrypted(selection_entity, staff_user):
    from core.models import EventHistory
    from core.services.events import update_event
    from core.services.option_usage import option_slugs_in_use

    entity, template = selection_entity
    update_event(entity, staff_user, data_json={template.slug: ["b"]})
    history = EventHistory.objects.get(event=entity, action=EventHistory.Action.UPDATE)
    for payload, expected in ((history.data_before, ["a", "b"]), (history.data_after, ["b"])):
        stored = payload[template.slug]
        assert is_encrypted_value(stored)
        assert set(stored) == {"__encrypted__", "value"}
        assert safe_decrypt(stored) == expected
        assert type(safe_decrypt(stored)) is list
    assert option_slugs_in_use(template) == {"a", "b"}


@pytest.mark.django_db
@pytest.mark.parametrize("selection_entity", [("event", False, True)], indirect=True)
def test_option_usage_excludes_other_facility_history(selection_entity, other_facility, staff_user):
    from core.models import EventHistory
    from core.services.file_vault import encrypt_field
    from core.services.option_usage import option_slugs_in_use

    entity, template = selection_entity
    entity.data_json = {}
    entity.save()
    foreign_type = make_document_type(facility=other_facility, name="Foreign selections")
    foreign_event = make_event(
        facility=other_facility,
        client=None,
        created_by=staff_user,
        document_type=foreign_type,
        data_json={},
        occurred_at=timezone.now(),
    )
    EventHistory.objects.create(
        event=foreign_event, action=EventHistory.Action.UPDATE, data_before={template.slug: encrypt_field(["c"])}
    )
    assert option_slugs_in_use(template) == set()
