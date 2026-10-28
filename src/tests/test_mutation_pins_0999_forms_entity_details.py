"""Public episode form contracts for dynamic details.

Source: mutation delta of the release-0.99.9 checkpoint, 24 mutants.
Groups: source protection (9), payload preservation (7), configuration (4),
dynamic validation errors (4).
Follows test_mutation_pins_0998_forms_dynamic.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import gettext as _

from core.forms.episodes import EpisodeForm
from core.models import DocumentType, DocumentTypeField, Episode, FieldTemplate
from core.services.case.episodes import episode_basis_token, update_episode

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def typed_episode(episode, facility):
    target = DocumentType.objects.create(facility=facility, name="Episode details", entity_type="episode")
    field = FieldTemplate.objects.create(facility=facility, name="Optional detail", slug="optional-detail")
    DocumentTypeField.objects.create(document_type=target, field_template=field)
    episode.document_type = target
    episode.data_json = {field.slug: "Stored detail"}
    episode.save(update_fields=["document_type", "data_json"])
    episode.refresh_from_db()
    return episode, target, field


def form_data(record, **overrides):
    return {
        "title": record.title,
        "started_at": record.started_at.isoformat(),
        "document_type": str(record.document_type_id) if record.document_type_id else "",
    } | overrides


@pytest.fixture(params=[False, True], ids=["assign", "clear"])
def hidden_change(request, typed_episode, facility, staff_user):
    record, target, _field = typed_episode
    hidden = FieldTemplate.objects.create(
        facility=facility, name="Hidden source", slug="hidden-source", sensitivity="high", is_encrypted=True
    )
    DocumentTypeField.objects.create(document_type=target, field_template=hidden)
    record.data_json = {hidden.slug: "Hidden original"}
    record.save(update_fields=["data_json"])
    if not request.param:
        Episode.objects.filter(pk=record.pk).update(document_type=None)
    record.refresh_from_db()
    return EpisodeForm(
        form_data(record, document_type="" if request.param else str(target.pk)),
        instance=record,
        facility=facility,
        user=staff_user,
    )


def test_hidden_type_changes_have_a_field_error(hidden_change):
    """Assigning or clearing a type cannot relabel hidden stored source values.

    xǁEntityDetailsMixinǁ_changes_hidden_source_values__mutmut_1
    xǁEntityDetailsMixinǁ_changes_hidden_source_values__mutmut_2
    xǁEntityDetailsMixinǁ_changes_hidden_source_values__mutmut_4
    xǁEntityDetailsMixinǁ_changes_hidden_source_values__mutmut_5
    xǁEntityDetailsMixinǁconfigure_details__mutmut_4
    xǁEntityDetailsMixinǁclean__mutmut_2
    xǁEntityDetailsMixinǁclean__mutmut_3
    """
    assert not hidden_change.is_valid()
    assert _("Keine Berechtigung für diesen Dokumentationstyp.") in hidden_change.errors["document_type"]
    assert "document_type" not in hidden_change.cleaned_data


def test_direct_payload_rejects_hidden_type_changes(hidden_change):
    """Payload generation enforces source protection with a readable error.

    xǁEntityDetailsMixinǁdynamic_payload__mutmut_1
    xǁEntityDetailsMixinǁdynamic_payload__mutmut_2
    """
    with pytest.raises(ValidationError) as error:
        hidden_change.dynamic_payload()
    assert error.value.messages == [_("Keine Berechtigung für diesen Dokumentationstyp.")]


def test_payload_preserves_active_restricted_ciphertext(typed_episode, facility, staff_user):
    """Editing a visible field retains the exact active restricted source value.

    xǁEntityDetailsMixinǁdynamic_payload__mutmut_20
    """
    record, target, visible = typed_episode
    hidden = FieldTemplate.objects.create(
        facility=facility, name="Restricted", slug="restricted", sensitivity="high", is_encrypted=True
    )
    DocumentTypeField.objects.create(document_type=target, field_template=hidden)
    record.data_json[hidden.slug] = "Stored secret"
    record.save(update_fields=["data_json"])
    record.refresh_from_db()
    stored = record.data_json[hidden.slug]
    form = EpisodeForm(
        form_data(record, **{visible.slug: "Edited"}), instance=record, facility=facility, user=staff_user
    )
    assert form.is_valid(), form.errors
    assert form.dynamic_payload() == {"data_json": {visible.slug: "Edited", hidden.slug: stored}}


def test_invalid_dynamic_input_has_a_non_field_message(typed_episode, facility, staff_user):
    """Invalid dynamic input reports a normal non-field validation error.

    xǁEntityDetailsMixinǁclean__mutmut_13
    xǁEntityDetailsMixinǁclean__mutmut_14
    xǁEntityDetailsMixinǁclean__mutmut_15
    xǁEntityDetailsMixinǁclean__mutmut_16
    """
    record, _target, field = typed_episode
    field.is_required = True
    field.save(update_fields=["is_required"])
    form = EpisodeForm(form_data(record), instance=record, facility=facility, user=staff_user)
    assert not form.is_valid()
    assert list(form.non_field_errors()) == [_("Bitte prüfen Sie die zusätzlichen Angaben.")]


def test_edit_partial_url_keeps_endpoint_and_record(typed_episode, facility):
    """An existing episode loads dynamic fields from its scoped partial endpoint.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_29
    """
    record, _target, _field = typed_episode
    form = EpisodeForm(instance=record, facility=facility)
    assert form.fields["document_type"].widget.attrs["hx-get"] == (
        reverse("core:episode_fields_partial") + f"?record={record.pk}"
    )


def test_empty_selection_does_not_look_up_a_null_primary_key(facility):
    """An absent type selection skips an impossible primary-key lookup.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_58
    """
    for name in ("First type", "Second type"):
        DocumentType.objects.create(facility=facility, name=name, entity_type="episode")
    with CaptureQueriesContext(connection) as captured:
        form = EpisodeForm(facility=facility)
    assert form.details_type is None
    assert not any('"core_documenttype"."id" IS NULL' in query["sql"] for query in captured)


def test_malformed_type_is_a_validation_error(typed_episode, facility, staff_user):
    """Malformed type identifiers remain ordinary invalid form input.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_60
    """
    record, _target, _field = typed_episode
    form = EpisodeForm(form_data(record, document_type="invalid"), instance=record, facility=facility, user=staff_user)
    assert not form.is_valid()
    assert "document_type" in form.errors


def test_explicit_type_clears_omitted_optional_values(typed_episode, facility, staff_user):
    """Explicit online type input clears optional values omitted from the payload.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_74
    xǁEntityDetailsMixinǁconfigure_details__mutmut_75
    xǁEntityDetailsMixinǁconfigure_details__mutmut_76
    """
    record, _target, field = typed_episode
    form = EpisodeForm(form_data(record), instance=record, facility=facility, user=staff_user)
    assert form.is_valid(), form.errors
    assert form.dynamic_payload() == {"data_json": {field.slug: ""}}


def test_typed_form_without_actor_returns_payload(typed_episode, facility):
    """Forms without an actor still return validated dynamic data.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_97
    """
    record, _target, field = typed_episode
    form = EpisodeForm(form_data(record, **{field.slug: "New"}), instance=record, facility=facility)
    assert form.is_valid(), form.errors
    assert form.dynamic_payload() == {"data_json": {field.slug: "New"}}


def test_untyped_payload_uses_the_write_service_contract(episode, facility, staff_user):
    """An explicit blank type produces an empty data_json payload accepted by writes.

    xǁEntityDetailsMixinǁdynamic_payload__mutmut_7
    xǁEntityDetailsMixinǁdynamic_payload__mutmut_8
    """
    token = episode_basis_token(episode)
    form = EpisodeForm(form_data(episode), instance=episode, facility=facility, user=staff_user)
    assert form.is_valid(), form.errors
    assert form.dynamic_payload() == {"data_json": {}}
    updated = update_episode(episode, staff_user, basis_token=token, **form.dynamic_payload())
    assert updated.data_json == {}


def test_missing_scoped_queryset_explains_configuration_error(episode, facility):
    """The public configuration API explains a missing scoped type queryset.

    xǁEntityDetailsMixinǁconfigure_details__mutmut_18
    """
    form = EpisodeForm(instance=episode, facility=facility)
    form.fields["document_type"].queryset = None
    with pytest.raises(ValueError) as error:
        form.configure_details(facility, None, "episode")
    assert str(error.value) == "The document type field requires a scoped queryset."
