"""Pins from the targeted mutation delta DEAD, batch client_merge.

Source: delta DEAD survivors of ``core.services.client.merge.unmerge_client``.
Undo reads its inventory from the append-only ``CLIENT_MERGED`` audit row; the
pins cover an inventory that lacks optional keys and the refusal message shown
to the lead when the kept person holds its own extra fields.
"""

import pytest
from django.core.exceptions import ValidationError
from django.utils import translation

from core.models import AuditLog, Client
from core.services.audit import audit_client_event
from core.services.client.merge import merge_clients, unmerge_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def source(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin source",
        created_by=staff_user,
    )


@pytest.fixture
def target(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Pin target",
        created_by=staff_user,
    )


def _tombstone_with_inventory(source, target, user, **inventory):
    """Put ``source`` into the merge tombstone state with a hand-written audit inventory."""
    source.merged_into = target
    source.save(update_fields=["merged_into"])
    source.soft_delete(user=user)
    audit_client_event(source, user, AuditLog.Action.CLIENT_MERGED, merged_into=str(target.pk), **inventory)
    source.refresh_from_db()


def test_unmerge_with_minimal_inventory_leaves_target_type_and_data(source, target, doc_type_client_profile, lead_user):
    """An inventory without ``document_type_adopted`` does not strip the kept person.

    core.services.client.merge.x_unmerge_client__mutmut_114
    """
    Client.objects.filter(pk=target.pk).update(
        document_type=doc_type_client_profile, data_json={"wohnsituation": "own flat"}
    )
    _tombstone_with_inventory(source, target, lead_user)

    unmerge_client(source, lead_user)

    source.refresh_from_db()
    target.refresh_from_db()
    assert source.is_deleted is False
    assert source.merged_into_id is None
    assert target.document_type_id == doc_type_client_profile.pk
    assert target.data_json == {"wohnsituation": "own flat"}


def test_unmerge_clears_adopted_type_when_inventory_lacks_slug_list(source, target, doc_type_client_profile, lead_user):
    """An inventory flagging an adopted type but naming no slugs still clears the empty target type.

    core.services.client.merge.x_unmerge_client__mutmut_102,
    core.services.client.merge.x_unmerge_client__mutmut_104
    """
    Client.objects.filter(pk=target.pk).update(document_type=doc_type_client_profile, data_json={})
    _tombstone_with_inventory(source, target, lead_user, document_type_adopted=True)

    unmerge_client(source, lead_user)

    source.refresh_from_db()
    target.refresh_from_db()
    assert source.is_deleted is False
    assert target.document_type_id is None
    assert target.data_json == {}


def test_unmerge_refusal_names_the_kept_persons_own_fields_in_full(source, target, doc_type_client_profile, lead_user):
    """The refusal tells the lead what was lost and how to proceed, sentence by sentence.

    core.services.client.merge.x_unmerge_client__mutmut_129,
    core.services.client.merge.x_unmerge_client__mutmut_130,
    core.services.client.merge.x_unmerge_client__mutmut_131,
    core.services.client.merge.x_unmerge_client__mutmut_132,
    core.services.client.merge.x_unmerge_client__mutmut_133,
    core.services.client.merge.x_unmerge_client__mutmut_134,
    core.services.client.merge.x_unmerge_client__mutmut_135,
    core.services.client.merge.x_unmerge_client__mutmut_136
    """
    Client.objects.filter(pk=target.pk).update(document_type=None, data_json={"wohnsituation": "own flat"})
    source.document_type = doc_type_client_profile
    source.data_json = {"diagnose": "F32"}
    source.save()
    merge_clients(source, target, lead_user)
    source.refresh_from_db()

    with translation.override("de"), pytest.raises(ValidationError) as excinfo:
        unmerge_client(source, lead_user)

    assert excinfo.value.messages == [
        "Die behaltene Person trägt eigene Zusatzfelder, die nicht aus der Zusammenführung stammen — "
        "Aufheben nicht möglich, weil sie verloren gingen. "
        "Die eigenen Zusatzfelder der behaltenen Person zuerst leeren, dann erneut aufheben."
    ]
