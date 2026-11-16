"""Mutation regression pins for ``core.services.system.ban_details``.

Source: targeted mutation delta "final" (end state ``b8f08a4d2``), part 2a:
``ban_list_fingerprint`` and ``resolve_ban_details``.
Refs #2809

The fingerprint is stored on the user row and has to stay stable across
deployments, so the tests pin the keyed payload itself (key salt and compact
JSON), not only that the value changes.

Each test names the mutants it kills as ``x…__mutmut_N``.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from django.utils.crypto import salted_hmac

from core.models import User
from core.services.system.ban_details import ban_list_fingerprint, resolve_ban_details

pytestmark = pytest.mark.django_db

EVENT_PK = uuid.UUID(int=11)
CLIENT_PK = uuid.UUID(int=22)


def _ban(*, client=None, grund="", until=None):
    return {"event": SimpleNamespace(pk=EVENT_PK), "client": client, "grund": grund, "bis_datum": until}


def _keyed(user, facility, payload):
    return salted_hmac(f"ban-details:{user.pk}:{facility.pk}", payload, algorithm="sha256").hexdigest()


def test_empty_list_has_the_empty_fingerprint(staff_user, facility):
    """Pins core.services.system.ban_details.x_ban_list_fingerprint__mutmut_2."""
    assert ban_list_fingerprint(staff_user, facility, []) == ""


def test_fingerprint_of_a_ban_with_a_person_is_the_keyed_hash_of_the_shown_values(staff_user, facility):
    """Pins core.services.system.ban_details.x_ban_list_fingerprint__mutmut_4, …_5, …_6, …_7, …_13,
    …_16, …_18, …_19, …_21, …_23, …_35, …_37, …_38, …_39 and …_40.

    Event, person (id and pseudonym), the shortened reason and the expiry date
    enter the payload, as compact JSON that keeps non-ASCII characters.
    """
    client = SimpleNamespace(pk=CLIENT_PK, pseudonym="Müller")
    bans = [_ban(client=client, grund="Gewalt gegen Personal", until=date(2026, 3, 10))]

    expected = _keyed(
        staff_user,
        facility,
        f'[["{EVENT_PK}","{CLIENT_PK}","Müller","Gewalt gegen Personal","2026-03-10"]]',
    )

    assert ban_list_fingerprint(staff_user, facility, bans) == expected


def test_fingerprint_of_a_ban_without_person_and_expiry_uses_empty_parts(staff_user, facility):
    """Pins core.services.system.ban_details.x_ban_list_fingerprint__mutmut_17, …_20, …_22, …_24 and …_31.

    A ban without a person must not fail, and its missing person and expiry
    are hashed as empty strings.
    """
    expected = _keyed(staff_user, facility, f'[["{EVENT_PK}","","","",""]]')

    assert ban_list_fingerprint(staff_user, facility, [_ban()]) == expected


def test_a_stale_choice_is_not_cleared_over_a_newer_one(staff_user, facility):
    """Pins core.services.system.ban_details.x_resolve_ban_details__mutmut_14 and …_18.

    This request holds an outdated stored choice, another request has meanwhile
    stored a newer one. Clearing is a compare-and-set on the outdated value, so
    the newer choice survives.
    """
    stale, newer = "b" * 64, "a" * 64
    User.objects.filter(pk=staff_user.pk).update(ban_details_fingerprint=newer)
    staff_user.ban_details_fingerprint = stale
    bans = [_ban(client=SimpleNamespace(pk=CLIENT_PK, pseudonym="Müller"))]

    state = resolve_ban_details(staff_user, facility, bans)

    assert state.hidden is False
    assert User.objects.get(pk=staff_user.pk).ban_details_fingerprint == newer
