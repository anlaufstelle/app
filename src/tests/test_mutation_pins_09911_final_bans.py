"""Mutation regression pins for the ban services.

Source: targeted mutation delta "final" (end state ``b8f08a4d2``), modules
``core.services.system.bans`` and ``core.services.system.ban_details``.
Refs #2717

Each test names the mutants it kills as ``x…__mutmut_N``.
"""

from __future__ import annotations

import logging

import pytest
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.services.system import get_active_bans
from core.services.system.ban_details import (
    REASON_EXCERPT_WORDS,
    BanDetailsState,
    ban_list_fingerprint,
    ban_reason_excerpt,
    show_ban_details,
)
from core.services.system.bans import active_ban_client_ids
from tests._factories import make_client, make_document_type

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def ban_type(facility):
    return make_document_type(
        facility=facility,
        name="Ban",
        system_type=DocumentType.SystemType.BAN,
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )


def _ban(facility, user, doc_type, client, **data):
    payload = {"aktiv": True, "bis": "", "grund": "Reason", **data}
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=payload,
        created_by=user,
    )


# ---------------------------------------------------------------------------
# active_ban_client_ids
# ---------------------------------------------------------------------------


def test_active_ban_client_ids_returns_only_clients_of_the_asked_page(
    facility, staff_user, ban_type, client_identified
):
    """Pins core.services.system.bans.x_active_ban_client_ids__mutmut_18.

    A ban of a person outside the asked list must not show up in the answer.
    """
    other = make_client(facility=facility, pseudonym="Other-01", created_by=staff_user)
    _ban(facility, staff_user, ban_type, client_identified)
    _ban(facility, staff_user, ban_type, other)

    assert active_ban_client_ids(facility, [client_identified]) == {client_identified.pk}
    assert active_ban_client_ids(facility, [client_identified, other]) == {client_identified.pk, other.pk}


def test_active_ban_client_ids_ignores_events_of_other_document_types(
    facility, staff_user, ban_type, doc_type_contact, client_identified
):
    """Pins core.services.system.bans.x_active_ban_client_ids__mutmut_6 and …_19.

    A contact whose data happens to look like a ban is no ban.
    """
    _ban(facility, staff_user, doc_type_contact, client_identified)

    assert active_ban_client_ids(facility, [client_identified]) == set()


def test_active_ban_client_ids_ignores_a_deactivated_ban_type(facility, staff_user, ban_type, client_identified):
    """Pins core.services.system.bans.x_active_ban_client_ids__mutmut_7."""
    _ban(facility, staff_user, ban_type, client_identified)
    DocumentType.objects.filter(pk=ban_type.pk).update(is_active=False)

    assert active_ban_client_ids(facility, [client_identified]) == set()


@pytest.mark.parametrize("state", [{"is_active": False}, {"is_deleted": True}])
def test_active_ban_client_ids_ignores_clients_that_are_not_readable(
    facility, staff_user, ban_type, client_identified, state
):
    """Pins core.services.system.bans.x_active_ban_client_ids__mutmut_17.

    A deactivated or trashed person drops out, like in every other person read.
    """
    _ban(facility, staff_user, ban_type, client_identified)
    Client.objects.filter(pk=client_identified.pk).update(**state)

    assert active_ban_client_ids(facility, [client_identified]) == set()


def test_active_ban_client_ids_does_not_read_the_reason(facility, staff_user, ban_type, client_identified, caplog):
    """Pins core.services.system.bans.x_active_ban_client_ids__mutmut_41.

    The answer is only who is banned. A reason that cannot even be decrypted must
    not be touched, so no decryption failure is logged.
    """
    _ban(
        facility,
        staff_user,
        ban_type,
        client_identified,
        grund={"__encrypted__": True, "value": "invalid-token"},
    )

    with caplog.at_level(logging.WARNING):
        banned = active_ban_client_ids(facility, [client_identified])

    assert banned == {client_identified.pk}
    assert [record.getMessage() for record in caplog.records if "Decryption failed" in record.getMessage()] == []


# ---------------------------------------------------------------------------
# ban_details
# ---------------------------------------------------------------------------


def test_ban_reason_excerpt_marks_a_cut_with_a_spaced_ellipsis():
    """Pins core.services.system.ban_details.x_ban_reason_excerpt__mutmut_2, …_4 and …_8."""
    words = [f"w{index}" for index in range(REASON_EXCERPT_WORDS + 3)]

    assert ban_reason_excerpt(" ".join(words)) == " ".join(words[:REASON_EXCERPT_WORDS]) + " …"
    assert ban_reason_excerpt(" ".join(words[:REASON_EXCERPT_WORDS])) == " ".join(words[:REASON_EXCERPT_WORDS])


def test_ban_reason_excerpt_of_a_missing_reason_is_empty():
    """Pins core.services.system.ban_details.x_ban_reason_excerpt__mutmut_7."""
    assert ban_reason_excerpt(None) == ""
    assert ban_reason_excerpt("") == ""


def test_show_ban_details_returns_the_current_fingerprint_and_a_visible_state(
    facility, staff_user, ban_type, client_identified
):
    """Pins core.services.system.ban_details.x_show_ban_details__mutmut_8, …_9, …_14 and …_18.

    After showing the details again, the state carries the fingerprint of the
    list as it stands (not ``None``, not the empty-list fingerprint) and is not
    hidden.
    """
    _ban(facility, staff_user, ban_type, client_identified)
    bans = get_active_bans(facility, user=staff_user)
    assert len(bans) == 1
    expected = ban_list_fingerprint(staff_user, facility, bans)
    assert expected != ""

    state = show_ban_details(staff_user, facility, bans)

    assert state == BanDetailsState(fingerprint=expected, hidden=False)
    assert state.hidden is False
