"""Tests fuer SessionIdleRefreshMiddleware (Refs #1858).

Mit ``SESSION_SAVE_EVERY_REQUEST = False`` (Refs #733) verlaengerte bisher
nichts im Request-Pfad das Session-Fenster — der Timeout wirkte ABSOLUT ab
Login statt nach Inaktivitaet. Die Middleware erneuert das Fenster bei
authentifizierter Aktivitaet, aber hoechstens einmal je
``RENEWAL_FRACTION``-Anteil der konfigurierten Laufzeit, damit die
Write-Amplifikation aus #733 nicht zurueckkommt.

Mechanik-Detail: ``session.get_expiry_age()`` liefert bei int-basiertem
``_session_expiry`` (Login setzt ``set_expiry(<facility-timeout>)``) die
KONFIGURIERTE Laufzeit, nicht die Restlaufzeit — die Restlaufzeit steht nur
in ``django_session.expire_date``. Deshalb fuehrt die Middleware einen
eigenen Erneuerungs-Zeitstempel in der Session.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from core.middleware.session_idle import (
    LAST_RENEWAL_KEY,
    RENEWAL_FRACTION,
    SessionIdleRefreshMiddleware,
)

WINDOW = 1800  # Seed-Default: 30 Minuten


def _make_middleware():
    return SessionIdleRefreshMiddleware(lambda r: HttpResponse("ok"))


def _request_with_session(user, *, expiry=WINDOW, renewed_ago=None, headers=None):
    """RequestFactory-Request mit frischer (ungespeicherter) DB-Session.

    ``renewed_ago=None`` laesst den Erneuerungs-Zeitstempel weg
    (Alt-Session von vor dem Deploy), sonst liegt er ``renewed_ago``
    Sekunden in der Vergangenheit.
    """
    request = RequestFactory().get("/clients/", headers=headers or {})
    request.user = user
    session = SessionStore()
    session.set_expiry(expiry)
    if renewed_ago is not None:
        session[LAST_RENEWAL_KEY] = timezone.now().timestamp() - renewed_ago
    session.modified = False
    request.session = session
    return request


class TestSessionIdleRefreshUnit:
    """Verhalten isoliert per RequestFactory — ohne DB."""

    def test_stale_stamp_renews_window(self, staff_user):
        """Liegt die letzte Erneuerung laenger als RENEWAL_FRACTION*Laufzeit
        zurueck, wird die Session als modified markiert (SessionMiddleware
        berechnet expire_date beim Speichern neu)."""
        request = _request_with_session(staff_user, renewed_ago=WINDOW * RENEWAL_FRACTION + 1)

        _make_middleware()(request)

        assert request.session.modified is True
        assert request.session[LAST_RENEWAL_KEY] == pytest.approx(timezone.now().timestamp(), abs=5)

    def test_fresh_stamp_skips_write(self, staff_user):
        """Innerhalb des Erneuerungs-Intervalls bleibt die Session unangetastet
        — genau das schuetzt vor der Write-Amplifikation aus #733."""
        request = _request_with_session(staff_user, renewed_ago=10)

        _make_middleware()(request)

        assert request.session.modified is False

    def test_missing_stamp_renews_window(self, staff_user):
        """Alt-Sessions ohne Zeitstempel (Bestand vor dem Deploy) werden beim
        naechsten Request einmalig erneuert und gestempelt."""
        request = _request_with_session(staff_user, renewed_ago=None)

        _make_middleware()(request)

        assert request.session.modified is True
        assert LAST_RENEWAL_KEY in request.session

    def test_threshold_scales_with_facility_window(self, staff_user):
        """Der Schwellwert haengt an der KONFIGURIERTEN Laufzeit
        (facility.settings.session_timeout_minutes via set_expiry), nicht an
        der globalen SESSION_COOKIE_AGE."""
        stale_for_default_but_fresh_for_long = WINDOW * RENEWAL_FRACTION + 1
        request = _request_with_session(staff_user, expiry=7200, renewed_ago=stale_for_default_but_fresh_for_long)

        _make_middleware()(request)

        assert request.session.modified is False

    def test_offline_replay_is_not_user_activity(self, staff_user):
        """Ein Offline-Queue-Replay (Hintergrund-Sync, ``X-Offline-Replay``)
        darf das Fenster nicht verlaengern."""
        request = _request_with_session(
            staff_user,
            renewed_ago=WINDOW,
            headers={"X-Offline-Replay": "1"},
        )

        _make_middleware()(request)

        assert request.session.modified is False

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/offline/revocations/",
            "/api/v1/offline/csrf/",
            "/api/v1/offline/bundle/facility/",
        ],
    )
    def test_offline_sync_api_is_not_user_activity(self, staff_user, path):
        """Die uebrigen Schritte der Sync-Sequenz (Widerrufsliste, CSRF-Refresh,
        Bundle-Revalidierung) laufen OHNE ``X-Offline-Replay`` und werden vom
        ``online``-Event ohne jede Nutzerinteraktion ausgeloest
        (sync-orchestrator.js) — ein flappendes WLAN darf die Sitzung nicht
        unbegrenzt offen halten. Ausschluss per Pfad-Praefix."""
        request = RequestFactory().get(path)
        request.user = staff_user
        session = SessionStore()
        session.set_expiry(WINDOW)
        session.modified = False
        request.session = session

        _make_middleware()(request)

        assert request.session.modified is False

    def test_anonymous_user_is_not_touched(self):
        request = _request_with_session(AnonymousUser(), renewed_ago=None)

        _make_middleware()(request)

        assert request.session.modified is False
        assert LAST_RENEWAL_KEY not in request.session

    def test_missing_user_attribute_is_no_op(self):
        """Ohne gelaufene AuthenticationMiddleware darf nichts crashen."""
        request = RequestFactory().get("/health/")

        response = _make_middleware()(request)

        assert response.status_code == 200


class TestSessionIdleRefreshIntegration:
    """End-to-End ueber den vollen Middleware-Stack (Test-Client + DB-Backend)."""

    def test_middleware_is_registered_after_authentication(self):
        mw = "core.middleware.session_idle.SessionIdleRefreshMiddleware"
        assert mw in settings.MIDDLEWARE
        assert settings.MIDDLEWARE.index(mw) > settings.MIDDLEWARE.index(
            "django.contrib.auth.middleware.AuthenticationMiddleware"
        )

    @pytest.mark.django_db
    def test_activity_extends_expire_date(self, client, staff_user):
        """Requests nach Ablauf des Erneuerungs-Intervalls schieben
        ``django_session.expire_date`` auf jetzt + konfigurierte Laufzeit."""
        client.force_login(staff_user)
        session = client.session
        session[LAST_RENEWAL_KEY] = timezone.now().timestamp() - (WINDOW * RENEWAL_FRACTION + 1)
        session.save()
        stale_expiry = Session.objects.get(session_key=session.session_key).expire_date

        response = client.get("/clients/")

        assert response.status_code == 200
        new_expiry = Session.objects.get(session_key=session.session_key).expire_date
        assert new_expiry > stale_expiry
        remaining = (new_expiry - timezone.now()).total_seconds()
        assert remaining == pytest.approx(settings.SESSION_COOKIE_AGE, abs=30)

    @pytest.mark.django_db
    def test_quiet_interval_does_not_write_session(self, client, staff_user):
        """Innerhalb des Erneuerungs-Intervalls bleibt expire_date exakt
        unveraendert — kein Session-Write pro Request (Refs #733)."""
        client.force_login(staff_user)
        session = client.session
        session[LAST_RENEWAL_KEY] = timezone.now().timestamp()
        session.save()
        expiry_before = Session.objects.get(session_key=session.session_key).expire_date

        response = client.get("/clients/")

        assert response.status_code == 200
        expiry_after = Session.objects.get(session_key=session.session_key).expire_date
        assert expiry_after == expiry_before
