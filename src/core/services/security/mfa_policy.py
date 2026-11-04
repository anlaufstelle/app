"""Shared MFA access decision: access, setup or verify (Refs #2568).

One rule set, two consumers:

* :class:`core.middleware.mfa.MFAEnforcementMiddleware` enforces it and turns
  the step into a redirect;
* :func:`core.services.security.session_state.session_fully_authenticated`
  only asks whether access would be granted (footer version, ``/health/``
  details).

The decision itself is pure: :func:`decide_mfa_step` works on an explicit
:class:`MFAState` and a given instant and touches neither database nor
session. :func:`read_mfa_state` only reads. Starting a missing grace period
(Refs #689) is not part of the decision; it remains an explicit mutation of
the enforcement path (and of the login view, which stamps on first sign-in).

Rules (unchanged from the middleware they were extracted from):

1. A confirmed second factor (TOTP or passkey, ADR-032) needs a verified
   session; otherwise the step is *verify*. Enforcement settings do not matter
   once a factor exists.
2. Without a factor, deliberate enforcement (``mfa_required``, facility-wide
   flag) and the immediate role set lead straight to *setup* (Refs #689,
   #1019): the enforcement was switched on on purpose or the role is highly
   privileged, so a 30-day period would be a gap.
3. Without a factor, the grace role set grants access until the setup deadline
   and requires *setup* from ``deadline <= now`` on, so existing accounts are
   not locked out when role enforcement is switched on. A missing deadline
   counts as running: the enforcement path starts the period right then.
4. Everything else is *access*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MFAStep(StrEnum):
    """What a password-authenticated session needs next."""

    ACCESS = "access"
    SETUP = "setup"
    VERIFY = "verify"


@dataclass(frozen=True, slots=True)
class MFAState:
    """The values the decision depends on.

    ``read_mfa_state`` fills only the fields that can still change the
    decision (in rule order) and leaves the rest at their defaults, so the
    read costs no more queries than the decision needs.
    """

    has_confirmed_device: bool
    session_verified: bool = False
    enforced_immediately: bool = False
    enforced_with_grace: bool = False
    setup_deadline: datetime | None = None

    @property
    def grace_period_missing(self) -> bool:
        """True when enforcement would have to start the grace period now."""
        return (
            not self.has_confirmed_device
            and not self.enforced_immediately
            and self.enforced_with_grace
            and self.setup_deadline is None
        )


def read_mfa_state(user, session) -> MFAState:
    """Read the decision-relevant MFA state of *user* and *session* (read-only)."""
    if user.has_confirmed_mfa_device:
        return MFAState(has_confirmed_device=True, session_verified=bool(session.get("mfa_verified")))
    if user.mfa_enforced_deliberately or user.mfa_enforced_by_role_immediate:
        return MFAState(has_confirmed_device=False, enforced_immediately=True)
    if user.mfa_enforced_by_role_grace:
        return MFAState(has_confirmed_device=False, enforced_with_grace=True, setup_deadline=user.mfa_setup_deadline)
    return MFAState(has_confirmed_device=False)


def decide_mfa_step(state: MFAState, *, now: datetime) -> MFAStep:
    """Decide the next MFA step for *state* at the instant *now* (pure)."""
    if state.has_confirmed_device:
        return MFAStep.ACCESS if state.session_verified else MFAStep.VERIFY
    if state.enforced_immediately:
        return MFAStep.SETUP
    if state.enforced_with_grace:
        if state.setup_deadline is None or state.setup_deadline > now:
            return MFAStep.ACCESS  # grace period running (banner only)
        return MFAStep.SETUP
    return MFAStep.ACCESS
