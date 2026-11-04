"""Pins fuer die Auswertung „vollstaendig angemeldet" (``core.services.security.session_state``).

Quelle: Mutations-Delta des Checkpoints release-0.99.2, 4 Mutanten. Gruppen:

* ``_evaluate`` — fehlende Sitzung (fail-closed) und fehlendes
  ``must_change_password``-Attribut (sicherer Default ``False``, kein Absturz).
* ``session_fully_authenticated`` — ein Request-Ersatz ohne setzbare
  Attribute verliert nur den Merker, nicht die Antwort.

The MFA policy read is fixed to "no factor, no enforcement" (access) in the
attribute pins, so the result depends on the pinned branches only.
"""

from types import SimpleNamespace

import pytest

from core.services.security import session_state
from core.services.security.mfa_policy import MFAState


class _UserOhnePasswortMerker:
    """Angemeldeter User-Ersatz ohne ``must_change_password``-Attribut."""

    is_authenticated = True


class _RequestOhneAttribute:
    """Request-Ersatz mit ``__slots__`` — der Merker laesst sich nicht setzen."""

    __slots__ = ("session", "user")

    def __init__(self, user, session):
        self.user = user
        self.session = session


@pytest.fixture
def keine_mfa_umleitung(monkeypatch):
    calls = []

    def _no_factor_no_enforcement(user, session):
        calls.append(session)
        return MFAState(has_confirmed_device=False)

    monkeypatch.setattr(session_state, "read_mfa_state", _no_factor_no_enforcement)
    return calls


class TestEvaluate:
    def test_angemeldet_ohne_sitzung_ist_nicht_vollstaendig(self, keine_mfa_umleitung):
        """x__evaluate__mutmut_32: ``session is None`` lieferte ``True`` —
        eine Anmeldung ohne Sitzung galt als vollstaendig."""
        request = SimpleNamespace(user=_UserOhnePasswortMerker(), session=None)
        assert session_state._evaluate(request) is False
        assert keine_mfa_umleitung == []

    def test_fehlender_passwort_merker_gilt_als_kein_zwang(self, keine_mfa_umleitung):
        """x__evaluate__mutmut_18 (``getattr`` ohne Default -> ``AttributeError``)
        und _21 (Default ``True`` -> faelschlich ``False``): ohne Attribut
        greift der Default ``False``, die Auswertung erreicht die MFA-Pruefung."""
        request = SimpleNamespace(user=_UserOhnePasswortMerker(), session={})
        assert session_state._evaluate(request) is True
        assert keine_mfa_umleitung == [request.session]


class TestSessionFullyAuthenticated:
    def test_request_ohne_setzbare_attribute_liefert_die_antwort(self, keine_mfa_umleitung):
        """x_session_fully_authenticated__mutmut_12: ``suppress(None)`` wirft
        beim fehlschlagenden ``setattr`` selbst ``TypeError``."""
        request = _RequestOhneAttribute(_UserOhnePasswortMerker(), {})
        assert session_state.session_fully_authenticated(request) is True
        assert not hasattr(request, "_session_fully_authenticated")
