"""Pins from the mutation delta of the release-0.99.7 checkpoint, 1 mutant.

Group: field-specific errors for oversized and non-text release metadata.
The public validation API is exercised without rls_test_role.
"""

import pytest

from core.services.audit.detail_policy import (
    RELEASE_FIELD_MAX_LENGTH,
    InvalidAuditDetailValueError,
    validate_release_metadata,
)


@pytest.mark.parametrize("field", ["recipient", "purpose"])
@pytest.mark.parametrize("value", ["x" * (RELEASE_FIELD_MAX_LENGTH + 1), {"private": "SensitiveToken"}])
def test_invalid_release_metadata_names_only_the_offending_field(field, value):
    """Invalid values identify their field without disclosing submitted data.

    x_validate_release_metadata__mutmut_11.
    """
    with pytest.raises(InvalidAuditDetailValueError) as caught:
        validate_release_metadata({field: value})
    assert str(caught.value) == f"Invalid release metadata: {field}"
