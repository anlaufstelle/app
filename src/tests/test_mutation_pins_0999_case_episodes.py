"""Episode source preservation and audit contracts.

Source: mutation delta of the release-0.99.9 checkpoint, 6 mutants.
Groups: inactive source values (2), partial writes (1), loss audit (1),
entity validation messages (2).
Follows test_mutation_pins_0998_case_episodes.py.
"""

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog, DocumentType, DocumentTypeField, FieldTemplate
from core.services.case.episodes import (
    _EPISODE_DOCUMENT_TYPE_ERROR,
    create_episode,
    episode_basis_token,
    update_episode,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def typed_episode(episode, facility):
    source = DocumentType.objects.create(facility=facility, name="Episode source", entity_type="episode")
    field = FieldTemplate.objects.create(facility=facility, name="Source value", slug="source-value")
    DocumentTypeField.objects.create(document_type=source, field_template=field)
    episode.document_type = source
    episode.data_json = {field.slug: "Original source"}
    episode.save(update_fields=["document_type", "data_json"])
    episode.refresh_from_db()
    return episode, source, field


@pytest.mark.parametrize("submitted", [{}, {"source-value": "Tampered"}])
def test_update_retains_inactive_source_values(typed_episode, staff_user, submitted):
    """Omitting or tampering with an inactive assigned field retains its stored value.

    x_update_episode__mutmut_76
    x_update_episode__mutmut_82
    """
    record, _source, field = typed_episode
    field.is_active = False
    field.save(update_fields=["is_active"])
    record.refresh_from_db()
    original = dict(record.data_json)
    update_episode(
        record, staff_user, basis_token=episode_basis_token(record), data_json=submitted, title="Edited metadata"
    )
    record.refresh_from_db()
    assert record.title == "Edited metadata"
    assert record.data_json == original


def test_metadata_update_does_not_rewrite_dynamic_data(typed_episode, staff_user):
    """A title-only update leaves dynamic storage outside the SQL write set.

    x_update_episode__mutmut_119
    """
    record, _source, _field = typed_episode
    original = dict(record.data_json)
    token = episode_basis_token(record)
    with CaptureQueriesContext(connection) as captured:
        update_episode(record, staff_user, basis_token=token, title="Edited metadata")
    writes = [query["sql"] for query in captured if query["sql"].startswith('UPDATE "core_episode"')]
    assert writes
    assert all('"data_json" =' not in sql for sql in writes)
    record.refresh_from_db()
    assert record.title == "Edited metadata"
    assert record.data_json == original


def test_confirmed_loss_audit_identifies_the_owning_case(typed_episode, facility, staff_user):
    """Confirmed loss remains attributed to the owning case without source text.

    x_update_episode__mutmut_134
    """
    record, _source, _field = typed_episode
    target = DocumentType.objects.create(facility=facility, name="Empty target", entity_type="episode")
    update_episode(
        record, staff_user, basis_token=episode_basis_token(record), document_type=target, confirm_data_loss=True
    )
    audit = AuditLog.objects.get(action=AuditLog.Action.EPISODE_UPDATE, target_id=str(record.pk))
    assert audit.detail["case_id"] == str(record.case_id)
    assert audit.detail["discarded_field_count"] == 1
    assert "Original source" not in str(audit.detail)


@pytest.mark.parametrize("operation", ["create", "update"])
def test_wrong_entity_type_has_the_episode_error(episode, facility, staff_user, operation):
    """Both episode write APIs explain a document type belonging to another entity.

    x_create_episode__mutmut_31
    x_update_episode__mutmut_75
    """
    wrong = DocumentType.objects.create(facility=facility, name="Case type", entity_type="case")
    with pytest.raises(ValidationError) as error:
        if operation == "create":
            create_episode(episode.case, staff_user, title="Invalid episode", document_type=wrong)
        else:
            update_episode(episode, staff_user, basis_token=episode_basis_token(episode), document_type=wrong)
    assert error.value.messages == [str(_EPISODE_DOCUMENT_TYPE_ERROR)]
