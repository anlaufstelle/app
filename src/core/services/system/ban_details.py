"""Per-user choice to hide the ban details in the Zeitstrom.

The choice is bound to the list the person's role saw when hiding. That list is
reduced to a keyed fingerprint: it is computed only from what the role-specific
rendering shows (people, expiry dates, the reason where the role may read it), so
a change the role cannot see leaves it untouched and a change it can see shows
the details again. The fingerprint is salted with user and facility and is the
only thing stored, on the user row that every request has loaded anyway; reading
the choice therefore costs no query.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from django.db import DatabaseError, transaction
from django.utils.crypto import salted_hmac
from django.utils.text import Truncator

from core.models import User

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")

#: Number of words of a ban reason that the Zeitstrom shows.
REASON_EXCERPT_WORDS = 10


def ban_reason_excerpt(reason) -> str:
    """The part of a ban reason the Zeitstrom renders.

    The template filter of the same name and the fingerprint both use this, so
    what is hashed is what is shown.
    """
    return Truncator(reason or "").words(REASON_EXCERPT_WORDS, truncate=" …")


@dataclass(frozen=True)
class BanDetailsState:
    """What the Zeitstrom needs to render the switch and the block."""

    fingerprint: str
    hidden: bool


def ban_list_fingerprint(user, facility, active_bans) -> str:
    """Keyed fingerprint of the rendered ban list; ``""`` for an empty list."""
    if not active_bans:
        return ""
    rows = []
    for ban in active_bans:
        client = ban.get("client")
        until = ban.get("bis_datum")
        rows.append(
            [
                str(ban["event"].pk),
                str(client.pk) if client is not None else "",
                client.pseudonym if client is not None else "",
                ban_reason_excerpt(ban.get("grund")),
                until.isoformat() if until else "",
            ]
        )
    rows.sort()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return salted_hmac(f"ban-details:{user.pk}:{facility.pk}", payload, algorithm="sha256").hexdigest()


def _store(user, facility, value: str, *, only_if: str | None = None) -> None:
    """Write the stored fingerprint of *user* (``""`` clears it).

    ``only_if`` makes the write a compare-and-set, so that two renders clearing the
    same outdated choice do not fight; a lost race is not an error.
    """
    rows = User.objects.filter(pk=user.pk, facility=facility)
    if only_if is not None:
        rows = rows.filter(ban_details_fingerprint=only_if)
    try:
        with transaction.atomic():
            updated = rows.update(ban_details_fingerprint=value)
    except DatabaseError:
        return
    if updated:
        user.ban_details_fingerprint = value


def resolve_ban_details(user, facility, active_bans) -> BanDetailsState:
    """Current fingerprint and whether the user's stored choice still applies.

    A choice made for another list is dropped here, and so is a choice that is
    still stored while no ban is active, so that a list coming back unchanged does
    not hide the details again unseen. Nothing is queried unless a choice is
    stored and has to be dropped.
    """
    fingerprint = ban_list_fingerprint(user, facility, active_bans)
    stored = user.ban_details_fingerprint
    if stored and stored != fingerprint:
        _store(user, facility, "", only_if=stored)
        stored = ""
    return BanDetailsState(fingerprint=fingerprint, hidden=bool(fingerprint) and stored == fingerprint)


def hide_ban_details(user, facility, active_bans, claimed_fingerprint: str) -> BanDetailsState:
    """Hide the details if *claimed_fingerprint* is the list as it stands now.

    The caller names the list it showed; if the list changed meanwhile nothing is
    stored and the details stay visible.
    """
    current = ban_list_fingerprint(user, facility, active_bans)
    if not current or not _FINGERPRINT_RE.fullmatch(claimed_fingerprint or "") or claimed_fingerprint != current:
        return resolve_ban_details(user, facility, active_bans)
    _store(user, facility, current)
    return BanDetailsState(fingerprint=current, hidden=user.ban_details_fingerprint == current)


def show_ban_details(user, facility, active_bans) -> BanDetailsState:
    """Show the details again (clears the stored choice)."""
    _store(user, facility, "")
    return BanDetailsState(fingerprint=ban_list_fingerprint(user, facility, active_bans), hidden=False)
