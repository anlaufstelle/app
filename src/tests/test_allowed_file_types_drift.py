"""Drift guard between the ``allowed_file_types`` defaults (COD-11).

Two defaults describe which upload extensions are allowed:

* ``core.constants.DEFAULT_ALLOWED_FILE_TYPES`` — the fail-closed fallback for
  facilities WITHOUT a ``Settings`` row (or with an empty ``allowed_file_types``),
  used by ``file_vault.policy.enforce_allowed_file_types``.
* ``Settings.allowed_file_types`` model default — what a facility WITH a
  ``Settings`` row starts out allowing.

These drifted: the fallback set contains ``odt``, the model default does not.
Realised divergence: a facility with a Settings row silently rejects ``.odt``,
while an unconfigured one accepts it.

This guard *technically couples* the two (the model default is derived from a
single named constant, no magic string) and pins the current relationship so any
future change trips a conscious review. It deliberately does NOT decide the
``odt`` question (should the model default include it, or should the fallback
drop it?) — that is a maintainer/product call, flagged as an open question in
the PR. Refs #1478 (COD-11).
"""

from __future__ import annotations

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_SETTINGS_ALLOWED_FILE_TYPES
from core.models.settings import Settings


def _model_default_types() -> set[str]:
    field = Settings._meta.get_field("allowed_file_types")
    return {t.strip() for t in field.default.split(",") if t.strip()}


def test_model_default_is_coupled_to_named_constant():
    """The model default must come from the shared constant, not a magic string."""
    field = Settings._meta.get_field("allowed_file_types")
    assert field.default == DEFAULT_SETTINGS_ALLOWED_FILE_TYPES


def test_model_default_is_subset_of_failclosed_fallback():
    """Invariant: every type a configured facility allows by default must also be
    in the fail-closed fallback whitelist (never a type the fallback rejects)."""
    assert _model_default_types() <= set(DEFAULT_ALLOWED_FILE_TYPES)


def test_known_divergence_is_exactly_odt_pending_maintainer_decision():
    """Pin the CURRENT, deliberate gap so any change to either default trips this
    guard and forces the open ``odt`` question to be answered explicitly.

    If this assertion fails, do not just update the expected set — decide whether
    ``odt`` belongs in the model default (configured facilities accept it, matching
    the fallback) or should be dropped from the fallback, then update BOTH the
    constants and this guard. Refs #1478 (COD-11).
    """
    only_in_fallback = set(DEFAULT_ALLOWED_FILE_TYPES) - _model_default_types()
    only_in_model_default = _model_default_types() - set(DEFAULT_ALLOWED_FILE_TYPES)
    assert only_in_fallback == {"odt"}
    assert only_in_model_default == set()
