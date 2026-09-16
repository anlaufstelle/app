"""Pin-Tests zum Meilenstein release-0.99 (#2174, #2193) — Survivors in
``core.services.security.sudo_mode``.

Quelle: Originalmodul ``src/core/services/security/sudo_mode.py``,
Mutantenbaum ``mutants/src/core/services/security/sudo_mode.py``. Herkunft
der Auswahl: die Batch-Triage ``security-1`` des Vollmutationslaufs #2174
(17 Mutanten). Schwerpunkte:

* ``_ttl_seconds``: das konfigurierte Sudo-Fenster (Default 15 Minuten) wird
  weder ignoriert noch verlaengert, wenn ``SUDO_MODE_TTL_SECONDS`` fehlt oder
  explizit gesetzt ist;
* ``is_in_sudo``: die Fenstergrenze ist EXKLUSIV — im exakten Ablaufmoment
  gilt die Session nicht mehr als privilegiert;
* ``RequireSudoModeMixin.dispatch``: alle drei Passthrough-Zweige (Toggle
  deaktiviert, anonym, bereits im SudoMode) reichen ``request``/``*args``/
  ``**kwargs`` UNVERAENDERT an die naechste ``dispatch``-Implementierung in
  der MRO durch — sonst verlieren nachgelagerte Views ihre URL-Argumente
  oder bekommen einen falschen/fehlenden Request.

Konventionen wie ``test_mutation_pins_094_lockout_recovery.py``: Zusicherungen
vergleichen EXAKT; KEINE Imports aus ``test_rls_functional``; Fixtures aus
``src/tests/conftest.py``. Jeder Test nennt seine Mutanten in der Docstring.

``SUDO_MODE_TTL_SECONDS``/``SUDO_MODE_ENABLED`` sind in JEDER Django-Settings-
Umgebung dieses Repos real gesetzt (``settings/base.py``); die ``getattr``-
Default-Werte in ``_ttl_seconds``/``dispatch`` sind deshalb nur ueber
``del settings.<NAME>`` (die ``settings``-Fixture stellt das am Testende
wieder her) beobachtbar — genau das nutzen die Tests unten.
"""

from __future__ import annotations

import time

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore

from core.services.security import sudo_mode
from core.services.security.sudo_mode import SUDO_SESSION_KEY, RequireSudoModeMixin, is_in_sudo

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ============================================================================
# _ttl_seconds — Quelle und Default des Sudo-Fensters (6 Mutanten)
# ============================================================================


class TestTtlSecondsHonorsSetting:
    """``_ttl_seconds`` muss ``settings.SUDO_MODE_TTL_SECONDS`` LESEN, nicht raten."""

    def test_override_is_honored_not_the_hardcoded_default(self, settings):
        """Pins: x__ttl_seconds__mutmut_2 (``getattr(settings, ...)`` -> ``getattr(None, ...)``),
        x__ttl_seconds__mutmut_8 (Setting-Name -> ``XXSUDO_MODE_TTL_SECONDSXX``),
        x__ttl_seconds__mutmut_9 (Setting-Name -> ``sudo_mode_ttl_seconds``).

        Original liest den ECHTEN Setting-Wert (1234); alle drei Mutanten
        loesen entweder gar nicht mehr gegen ``settings`` auf (mutmut_2) oder
        suchen den falschen Attributnamen (mutmut_8/9) und landen beim
        hartcodierten Default 900 — das Sudo-Fenster liesse sich betriebsseitig
        dann nicht mehr verkuerzen/verlaengern.
        """
        settings.SUDO_MODE_TTL_SECONDS = 1234

        assert sudo_mode._ttl_seconds() == 1234

    def test_falls_back_to_900_when_setting_is_absent(self, settings):
        """Pins: x__ttl_seconds__mutmut_4 (Default ``900`` -> ``None``),
        x__ttl_seconds__mutmut_7 (Default-Argument entfernt),
        x__ttl_seconds__mutmut_10 (Default ``900`` -> ``901``).

        ``SUDO_MODE_TTL_SECONDS`` ist in jeder Settings-Umgebung dieses Repos
        gesetzt (``settings/base.py``) — der Default-Zweig von ``getattr``
        wird nur sichtbar, wenn das Attribut fehlt. Original faellt dann auf
        900 zurueck; mutmut_4 liefert ``None`` (``int(None)`` wirft
        ``TypeError``), mutmut_7 wirft ``AttributeError`` (kein Default mehr),
        mutmut_10 liefert 901 (Off-by-one am Sudo-Fenster).
        """
        del settings.SUDO_MODE_TTL_SECONDS

        assert sudo_mode._ttl_seconds() == 900


# ============================================================================
# is_in_sudo — Fenstergrenze ist EXKLUSIV (1 Mutant)
# ============================================================================


class TestIsInSudoBoundary:
    """Im exakten Ablaufmoment (``time.time() == until``) ist die Session tot."""

    def test_is_in_sudo_false_at_the_exact_expiry_second(self, monkeypatch):
        """Pin: x_is_in_sudo__mutmut_5 — ``time.time() < until`` -> ``time.time() <= until``.

        Original wertet ``<`` (strikt kleiner) aus: im exakten Ablaufmoment
        ist die Session NICHT mehr privilegiert (``False``). Der Mutant
        wertet ``<=`` aus und haelt den SudoMode eine Sekunde laenger aktiv
        als konfiguriert — ein verlaengertes Re-Auth-Fenster fuer sensible
        Aktionen (MFA-Disable, DSGVO-Export).
        """
        rf_session = SessionStore()
        rf_session.save()

        class _Request:
            session = rf_session

        frozen = 1_800_000_000

        rf_session[SUDO_SESSION_KEY] = frozen
        monkeypatch.setattr(sudo_mode.time, "time", lambda: float(frozen))

        assert is_in_sudo(_Request()) is False


# ============================================================================
# RequireSudoModeMixin.dispatch — Passthrough aller drei Zweige (10 Mutanten)
# ============================================================================


class _RecordingBase:
    """Ersatz fuer die naechste ``dispatch``-Implementierung in der MRO —
    zeichnet exakt auf, was ``RequireSudoModeMixin`` durchreicht."""

    received = None

    def dispatch(self, request, *args, **kwargs):
        self.received = (request, args, kwargs)
        return "OK"


class _GatedView(RequireSudoModeMixin, _RecordingBase):
    """Reihenfolge wie in der Produktion (Login-/Rollen-Mixin VOR
    ``RequireSudoModeMixin``) ist fuer den Passthrough-Vertrag irrelevant —
    getestet wird ausschliesslich, was ``RequireSudoModeMixin.dispatch`` an
    ``super().dispatch`` weiterreicht."""


@pytest.mark.django_db
class TestRequireSudoModeMixinDispatchEnabledDefault:
    """``SUDO_MODE_ENABLED`` ist in jeder Settings-Umgebung real gesetzt; der
    ``getattr``-Default wird nur ueber ``del settings.SUDO_MODE_ENABLED``
    beobachtbar."""

    def test_missing_enabled_setting_still_requires_sudo_fail_closed(self, rf, settings, admin_user):
        """Pins: xǁRequireSudoModeMixinǁdispatch__mutmut_4 (Default ``True`` -> ``None``),
        xǁRequireSudoModeMixinǁdispatch__mutmut_7 (Default-Argument entfernt),
        xǁRequireSudoModeMixinǁdispatch__mutmut_10 (Default ``True`` -> ``False``).

        Original faellt bei fehlendem Setting auf ``True`` zurueck
        (fail-closed: die Sudo-Pflicht bleibt aktiv) — ein authentifizierter
        User OHNE aktive SudoMode-Session wird zu ``/sudo/`` umgeleitet,
        ``super().dispatch`` wird NICHT aufgerufen. mutmut_4/10 fallen auf
        ``None``/``False`` zurueck (fail-open) und rufen ``super().dispatch``
        DIREKT auf — die Sudo-Pflicht waere komplett uebersprungen; mutmut_7
        wirft ``AttributeError`` statt eines kontrollierten Redirects.
        """
        del settings.SUDO_MODE_ENABLED
        view = _GatedView()
        request = rf.get("/some/protected/path")
        request.user = admin_user
        session = SessionStore()
        session.save()
        request.session = session

        response = view.dispatch(request)

        assert view.received is None
        assert response.status_code == 302
        assert "/sudo/" in response.url


@pytest.mark.django_db
class TestRequireSudoModeMixinDispatchPassthrough:
    def test_disabled_toggle_branch_passes_args_and_kwargs_through(self, rf, settings):
        """Pin: xǁRequireSudoModeMixinǁdispatch__mutmut_13 —
        ``super().dispatch(request, *args, **kwargs)`` -> ``super().dispatch(request, **kwargs)``.

        Bei ``SUDO_MODE_ENABLED=False`` reicht das Mixin sofort durch.
        Original erhaelt ``args == ("extra",)``; der Mutant droppt sie
        (``args == ()``) — ein View mit unbenannten URL-Gruppen bekaeme sein
        Positionsargument nicht mehr.
        """
        settings.SUDO_MODE_ENABLED = False
        view = _GatedView()
        request = rf.get("/")

        view.dispatch(request, "extra", kw="value")

        assert view.received == (request, ("extra",), {"kw": "value"})

    def test_anonymous_branch_passes_request_args_and_kwargs_through(self, rf, settings):
        """Pins: xǁRequireSudoModeMixinǁdispatch__mutmut_16 (``request`` -> ``None``),
        xǁRequireSudoModeMixinǁdispatch__mutmut_17 (``request`` als Positionsargument entfernt),
        xǁRequireSudoModeMixinǁdispatch__mutmut_18 (``*args`` entfernt),
        xǁRequireSudoModeMixinǁdispatch__mutmut_19 (``**kwargs`` entfernt).

        Ein anonymer User faellt (vor ``LoginRequiredMixin``, isoliert
        getestet) durch das Mixin durch. Original reicht denselben
        ``request``, dieselben ``args`` und ``kwargs`` weiter; mutmut_16
        ersetzt ``request`` durch ``None``, mutmut_17 verschiebt das erste
        Positionsargument versehentlich auf den ``request``-Parameter,
        mutmut_18/19 droppen ``args``/``kwargs``.
        """
        settings.SUDO_MODE_ENABLED = True
        view = _GatedView()
        request = rf.get("/")
        request.user = AnonymousUser()

        view.dispatch(request, "extra", kw="value")

        assert view.received == (request, ("extra",), {"kw": "value"})

    def test_active_sudo_branch_passes_args_and_kwargs_through(self, rf, settings, admin_user):
        """Pins: xǁRequireSudoModeMixinǁdispatch__mutmut_23 (``*args`` entfernt),
        xǁRequireSudoModeMixinǁdispatch__mutmut_24 (``**kwargs`` entfernt).

        Ein User mit gueltiger SudoMode-Session faellt ebenfalls durch.
        Original reicht ``args``/``kwargs`` unveraendert weiter; beide
        Mutanten droppen je eines davon.
        """
        settings.SUDO_MODE_ENABLED = True
        view = _GatedView()
        request = rf.get("/")
        request.user = admin_user
        session = SessionStore()
        session[SUDO_SESSION_KEY] = int(time.time()) + 900
        session.save()
        request.session = session

        view.dispatch(request, "extra", kw="value")

        assert view.received == (request, ("extra",), {"kw": "value"})
