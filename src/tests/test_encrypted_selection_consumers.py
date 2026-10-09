"""Public consumer paths preserve encrypted selections and output contracts."""

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
