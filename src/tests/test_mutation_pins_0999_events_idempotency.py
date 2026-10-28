"""Pins for diagnostic errors from idempotency key resolution.

Source: mutation delta of the release-0.99.9 checkpoint, 4 mutants.
Groups: invalid header (1), invalid fallback (1), conflict (1), required key (1).
"""

import pytest

from core.services.events.idempotency import resolve_idempotency_key


@pytest.mark.parametrize(
    ("header", "fallback", "required", "message"),
    [
        ("invalid key", None, False, "invalid idempotency header"),
        (None, "invalid key", False, "invalid idempotency fallback"),
        ("header-key", "fallback-key", False, "conflicting idempotency keys"),
        (None, None, True, "missing idempotency key"),
    ],
    ids=["invalid-header", "invalid-fallback", "conflicting-keys", "required-key"],
)
def test_key_resolution_explains_rejection(header, fallback, required, message):
    """Rejected keys retain the precise diagnostic message.

    x_resolve_idempotency_key__mutmut_9, x_resolve_idempotency_key__mutmut_15,
    x_resolve_idempotency_key__mutmut_23, x_resolve_idempotency_key__mutmut_30.
    """
    with pytest.raises(ValueError) as error:
        resolve_idempotency_key(header, fallback, required=required)

    assert str(error.value) == message
