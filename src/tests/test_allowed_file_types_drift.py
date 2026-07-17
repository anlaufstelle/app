"""Drift guard between the ``allowed_file_types`` defaults (COD-11).

Two defaults describe which upload extensions are allowed:

* ``core.constants.DEFAULT_ALLOWED_FILE_TYPES`` — the fail-closed fallback for
  facilities WITHOUT a ``Settings`` row (or with an empty ``allowed_file_types``),
  used by ``file_vault.policy.enforce_allowed_file_types``.
* ``Settings.allowed_file_types`` model default — what a facility WITH a
  ``Settings`` row starts out allowing.

These drifted for a while: the fallback set contained ``odt``, the model
default did not — a facility with a ``Settings`` row silently rejected
``.odt`` while an unconfigured one accepted it.

**Maintainer decision (2026-07-17, Refs #1640):** ``odt`` is allowed. Both
defaults are now identical (``odt``'s risk profile matches ``docx``, and
LibreOffice is common in social-work/administrative contexts). This guard no
longer pins a divergence — it asserts **equality** of the two sets, so any
future change to either constant must deliberately keep them in sync (or
consciously break the guard with a documented reason). Refs #1478 (COD-11).
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_SETTINGS_ALLOWED_FILE_TYPES
from core.models.settings import Settings
from core.services.file_vault.policy import enforce_allowed_file_types


def _model_default_types() -> frozenset[str]:
    field = Settings._meta.get_field("allowed_file_types")
    return frozenset(t.strip() for t in field.default.split(",") if t.strip())


def test_model_default_is_coupled_to_named_constant():
    """The model default must come from the shared constant, not a magic string."""
    field = Settings._meta.get_field("allowed_file_types")
    assert field.default == DEFAULT_SETTINGS_ALLOWED_FILE_TYPES


def test_model_default_equals_failclosed_fallback():
    """The model default and the fail-closed fallback must allow exactly the
    same extensions — a configured facility (has a ``Settings`` row) and an
    unconfigured one (no row, fallback applies) must behave identically for
    a freshly created facility. Refs #1640."""
    assert _model_default_types() == frozenset(DEFAULT_ALLOWED_FILE_TYPES)


@pytest.mark.django_db
def test_freshly_created_settings_row_allows_odt_upload(facility, staff_user, sample_event):
    """Behaviour test (Refs #1640): a facility whose ``Settings`` row was just
    created — no explicit ``allowed_file_types`` override, i.e. the model
    default applies — must accept ``.odt`` uploads through the extension
    whitelist, the same as an unconfigured facility (fail-closed fallback)."""
    Settings.objects.create(facility=facility)

    uploaded = SimpleUploadedFile(
        "bericht.odt",
        b"PK\x03\x04odt-stub-content",
        content_type="application/vnd.oasis.opendocument.text",
    )

    # Must not raise — .odt is part of the (now-aligned) model default.
    enforce_allowed_file_types(facility, uploaded, sample_event, staff_user)
