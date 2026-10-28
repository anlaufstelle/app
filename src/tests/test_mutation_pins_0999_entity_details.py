"""Source visibility and loss-context contracts for entity details.

Source: mutation delta of the release-0.99.9 checkpoint, 12 mutants.
Groups: loss consent and unchanged types (2), orphan source visibility (1),
loss-context keys and counters (6), visibility before decryption (3).
Follows test_mutation_pins_0998_dynamic_data.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from core.forms.episodes import EpisodeForm
from core.models import AuditLog, DocumentType, DocumentTypeField, Episode, Facility, FieldTemplate
from core.services.case.episodes import episode_basis_token, update_episode
from core.services.entity_details import entity_details_context, require_confirmed_type_loss, visible_stored_details
from core.services.file_vault import encrypt_field

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def typed_episode(episode, facility):
    source = DocumentType.objects.create(facility=facility, name="Visible source", entity_type="episode")
    field = FieldTemplate.objects.create(facility=facility, name="Visible field", slug="visible-field")
    DocumentTypeField.objects.create(document_type=source, field_template=field)
    episode.document_type = source
    episode.data_json = {field.slug: "Visible original"}
    episode.save(update_fields=["document_type", "data_json"])
    episode.refresh_from_db()
    return episode, source, field


def test_untyped_filled_orphan_needs_loss_consent(episode, facility):
    """Assigning a type to an untyped filled record still requires loss consent.

    x_require_confirmed_type_loss__mutmut_2
    """
    Episode.objects.filter(pk=episode.pk).update(data_json={"orphan": "Stored original"})
    episode.refresh_from_db()
    target = DocumentType.objects.create(facility=facility, name="Target", entity_type="episode")
    with pytest.raises(ValidationError) as error:
        require_confirmed_type_loss(episode, target, confirmed=False)
    assert error.value.message_dict == {
        "document_type": [_("Bitte bestätigen Sie den Verlust der ausgefüllten Zusatzfelder beim Typwechsel.")]
    }


def test_visible_orphan_resolves_only_within_source_facility(typed_episode, facility, staff_user):
    """Detached stored fields resolve their permissions within the source facility.

    x_visible_stored_details__mutmut_19
    """
    record, source, field = typed_episode
    other = Facility.objects.create(organization=facility.organization, name="Other facility")
    FieldTemplate.objects.create(
        facility=other, name="Foreign hidden field", slug=field.slug, sensitivity="high", is_encrypted=True
    )
    source.fields.all().delete()
    record.refresh_from_db()
    assert visible_stored_details(record, staff_user) == {field.slug: "Visible original"}


def test_empty_record_has_no_loss_warning(episode, facility, staff_user):
    """An empty record exposes no loss-warning context.

    x_entity_details_context__mutmut_15
    """
    form = EpisodeForm(instance=episode, facility=facility, user=staff_user)
    assert entity_details_context(form)["dynamic_values_at_risk"] is None


def test_filled_text_context_has_zero_files_and_original_type(typed_episode, facility, staff_user):
    """A text-only loss context retains its original type and never invents file loss.

    x_entity_details_context__mutmut_18
    x_entity_details_context__mutmut_19
    x_entity_details_context__mutmut_20
    x_entity_details_context__mutmut_21
    x_entity_details_context__mutmut_22
    """
    record, source, _field = typed_episode
    form = EpisodeForm(instance=record, facility=facility, user=staff_user)
    context = entity_details_context(form)
    assert context["data_form"] is form.data_form
    assert context["dynamic_values_at_risk"] == {"field_values": 1, "files": 0}
    assert context["original_document_type_id"] == source.pk


@pytest.mark.parametrize("hidden", [False, True], ids=["visible", "hidden"])
def test_loss_accounting_decrypts_only_visible_values(typed_episode, facility, staff_user, hidden):
    """Visible whitespace is empty; hidden ciphertext remains a filled stored envelope.

    x_entity_details_context__mutmut_3
    x_entity_details_context__mutmut_4
    x_entity_details_context__mutmut_9
    """
    record, _source, field = typed_episode
    if hidden:
        field.sensitivity = "high"
        field.is_encrypted = True
        field.save(update_fields=["sensitivity", "is_encrypted"])
    Episode.objects.filter(pk=record.pk).update(data_json={field.slug: encrypt_field("  ")})
    record.refresh_from_db()
    form = EpisodeForm(instance=record, facility=facility, user=staff_user)
    expected = {"field_values": 1, "files": 0} if hidden else None
    assert entity_details_context(form)["dynamic_values_at_risk"] == expected


def test_unchanged_type_does_not_audit_discarded_values(typed_episode, staff_user):
    """An ordinary metadata update with the same type records no discarded-field count.

    x_require_confirmed_type_loss__mutmut_4
    """
    record, _source, _field = typed_episode
    update_episode(record, staff_user, basis_token=episode_basis_token(record), title="Updated metadata")
    audit = AuditLog.objects.get(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(record.pk))
    assert "discarded_field_count" not in audit.detail
