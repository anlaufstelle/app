"""Deletion exception text pins.

Source: mutation delta of the release-0.99.7 checkpoint, 1 mutant.
Groups: joined reasons in the public exception interface (1).
"""

from core.services.config.delete_blockers import ConfigDeleteBlocked


def test_exception_exposes_joined_reasons_in_text_and_args():
    """Exception exposes joined reasons in text and args.

    Pins:
    * ``xǁConfigDeleteBlockedǁ__init____mutmut_2``
    """
    reasons = ["Assigned fields", "Stored records"]
    error = ConfigDeleteBlocked(reasons)
    assert error.reasons == reasons
    assert str(error) == "; ".join(reasons)
    assert error.args == ("; ".join(reasons),)
