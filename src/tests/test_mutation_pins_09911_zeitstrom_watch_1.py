"""Mutation regression pins for ``core.services.zeitstrom_watch``.

Source: targeted mutation delta ZS0, batch ``zeitstrom_watch-1`` (13 mutants,
12 triaged as equivalent, 1 pinned here).
Refs #2712
Refs #1997

The handover-note projection feeds the opaque component validator; the
``can_write`` entry is the only part of its result that no existing test
reads, so renaming the key went unnoticed.
"""

import datetime

import pytest

from core.services.zeitstrom_filters import ZeitstromState
from core.services.zeitstrom_watch import _handover_note_projection

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


def _state(target_date):
    from types import SimpleNamespace

    return ZeitstromState(
        target_date=target_date,
        time_filter=None,
        time_filters=(),
        live_context=SimpleNamespace(time_filter=None, shift_date=target_date, window_start=None, window_end=None),
        feed_type="",
        doc_type_id="",
        view="uebergabe",
        local_date=target_date,
    )


def test_handover_projection_exposes_write_permission_and_blocks(facility, staff_user):
    """Pins core.services.zeitstrom_watch.x__handover_note_projection__mutmut_14.

    A staff user may read and write the handover note, so the projection must
    carry ``can_write=True`` under exactly the keys ``can_write`` and
    ``blocks``.
    """
    result = _handover_note_projection(facility, staff_user, _state(datetime.date(2026, 1, 15)))

    assert result is not None
    assert set(result) == {"can_write", "blocks"}
    assert result["can_write"] is True
    assert len(result["blocks"]) >= 1


def test_handover_projection_is_empty_for_assistant(facility, assistant_user):
    """Companion: a role without read access gets no projection at all."""
    assert _handover_note_projection(facility, assistant_user, _state(datetime.date(2026, 1, 15))) is None
