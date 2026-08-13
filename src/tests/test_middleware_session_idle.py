"""Tests fuer SessionIdleRefreshMiddleware (Refs #1858, #1868).

Die Middleware traegt zwei Mechanismen: das absolute Sitzungs-Maximum ab
Login (#1868, ``TestSessionAbsoluteCap*``) und das Inaktivitaets-Fenster
(#1858, ``TestSessionIdleRefresh*``). Beide Kopfteile unten beschreiben das
Inaktivitaets-Fenster; das Cap laeuft davor und ohne dessen Ausnahmen.

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
    ABSOLUTE_DEADLINE_KEY,
    LAST_RENEWAL_KEY,
    RENEWAL_FRACTION,
    SessionIdleRefreshMiddleware,
)
from core.models import AuditLog
from core.models.settings import DEFAULT_SESSION_ABSOLUTE_HOURS

WINDOW = 1800  # Seed-Default: 30 Minuten

# Refs #1868: Deadline weit in der Zukunft — der Default fuer alle Tests, die
# das Inaktivitaets-Fenster (nicht das absolute Maximum) pruefen.
FAR_FUTURE = 12 * 3600


def _make_middleware():
    return SessionIdleRefreshMiddleware(lambda r: HttpResponse("ok"))


def _request_with_session(
    user, *, expiry=WINDOW, renewed_ago=None, headers=None, deadline_in=FAR_FUTURE, path="/clients/"
):
    """RequestFactory-Request mit frischer (ungespeicherter) DB-Session.

    ``renewed_ago=None`` laesst den Erneuerungs-Zeitstempel weg
    (Alt-Session von vor dem Deploy), sonst liegt er ``renewed_ago``
    Sekunden in der Vergangenheit.

    ``deadline_in`` stempelt das absolute Sitzungs-Maximum (Refs #1868) so
    viele Sekunden in die Zukunft; ``None`` laesst den Schluessel weg
    (Alt-Session / ``force_login``), negative Werte legen ihn in die
    Vergangenheit.
    """
    request = RequestFactory().get(path, headers=headers or {})
    request.user = user
    session = SessionStore()
    session.set_expiry(expiry)
    if renewed_ago is not None:
        session[LAST_RENEWAL_KEY] = timezone.now().timestamp() - renewed_ago
    if deadline_in is not None:
        session[ABSOLUTE_DEADLINE_KEY] = timezone.now().timestamp() + deadline_in
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
        session[ABSOLUTE_DEADLINE_KEY] = timezone.now().timestamp() + FAR_FUTURE
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
        # Refs #1868: ``force_login`` stempelt das absolute Maximum nicht (nur
        # der echte Login-View tut das) — ohne diesen Setup-Stempel schriebe
        # die Middleware einmalig den Grace-Stempel und der Test maesse ihn
        # statt der Erneuerungs-Drosselung.
        session[ABSOLUTE_DEADLINE_KEY] = timezone.now().timestamp() + FAR_FUTURE
        session.save()
        expiry_before = Session.objects.get(session_key=session.session_key).expire_date

        response = client.get("/clients/")

        assert response.status_code == 200
        expiry_after = Session.objects.get(session_key=session.session_key).expire_date
        assert expiry_after == expiry_before


class TestSessionAbsoluteCap:
    """Absolutes Sitzungs-Maximum ab Login (Refs #1868).

    Das Inaktivitaets-Fenster aus #1858 kennt keine Obergrenze: eine Sitzung
    lebt, solange irgendetwas pingt. Das Cap friert beim Login eine Deadline
    in der Session ein (``ABSOLUTE_DEADLINE_KEY``); ab ihr beendet die
    Middleware die Sitzung, unabhaengig von jeder Aktivitaet.
    """

    def test_expired_deadline_ends_session_without_renewal(self, staff_user):
        """Ueberschritten -> Session geflusht, Request laeuft anonym weiter,
        und die Erneuerungs-Logik stempelt NICHTS mehr."""
        request = _request_with_session(staff_user, renewed_ago=WINDOW, deadline_in=-1)

        response = _make_middleware()(request)

        assert response.status_code == 200
        assert request.user.is_anonymous
        # Flush raeumt die ganze Session — insbesondere darf die Erneuerung
        # danach nicht doch noch gestempelt haben.
        assert LAST_RENEWAL_KEY not in request.session
        assert ABSOLUTE_DEADLINE_KEY not in request.session

    @pytest.mark.parametrize(
        ("headers", "path"),
        [
            ({"X-Offline-Replay": "1"}, "/clients/"),
            (None, "/api/v1/offline/bundle/facility/"),
        ],
        ids=["offline-replay-header", "offline-sync-api-path"],
    )
    def test_cap_ignores_the_background_sync_exemptions(self, staff_user, headers, path):
        """Die Ausnahmen des Inaktivitaets-Fensters gelten fuer das Cap NICHT.

        Hintergrund-Sync darf das Fenster nicht *verlaengern* — aber ein
        abgelaufenes absolutes Maximum muss die Sitzung auch dann beenden,
        wenn der Request ein Replay oder ein Sync-Aufruf ist. Sonst haette
        ein dauersyncendes Geraet ein Schlupfloch am Cap vorbei.
        """
        request = _request_with_session(staff_user, renewed_ago=WINDOW, deadline_in=-1, headers=headers, path=path)

        _make_middleware()(request)

        assert request.user.is_anonymous
        assert ABSOLUTE_DEADLINE_KEY not in request.session

    def test_future_deadline_leaves_renewal_untouched(self, staff_user):
        """Deadline in der Zukunft -> die bestehende Erneuerungs-Logik laeuft
        unveraendert weiter."""
        request = _request_with_session(staff_user, renewed_ago=WINDOW * RENEWAL_FRACTION + 1, deadline_in=FAR_FUTURE)
        deadline_before = request.session[ABSOLUTE_DEADLINE_KEY]

        _make_middleware()(request)

        assert request.user.is_authenticated
        assert request.session.modified is True
        assert request.session[LAST_RENEWAL_KEY] == pytest.approx(timezone.now().timestamp(), abs=5)
        # Die Erneuerung darf die Deadline NIE nachschieben — sonst waere das
        # Maximum wieder ein Sliding Window.
        assert request.session[ABSOLUTE_DEADLINE_KEY] == deadline_before

    def test_missing_deadline_gets_grace_stamp_instead_of_logout(self, staff_user):
        """Alt-Sessions (Bestand vor dem Deploy) und ``force_login`` tragen
        keine Deadline. Sie werden nicht sofort ausgeloggt, sondern bekommen
        einmalig den Default-Cap ab jetzt gestempelt."""
        request = _request_with_session(staff_user, renewed_ago=10, deadline_in=None)

        _make_middleware()(request)

        assert request.user.is_authenticated
        assert request.session.modified is True
        assert request.session[ABSOLUTE_DEADLINE_KEY] == pytest.approx(
            timezone.now().timestamp() + DEFAULT_SESSION_ABSOLUTE_HOURS * 3600, abs=5
        )

    def test_anonymous_request_is_not_stamped(self):
        request = _request_with_session(AnonymousUser(), renewed_ago=None, deadline_in=None)

        _make_middleware()(request)

        assert request.session.modified is False
        assert ABSOLUTE_DEADLINE_KEY not in request.session


@pytest.mark.django_db
class TestSessionAbsoluteCapIntegration:
    """Cap ueber den vollen Middleware-Stack (Refs #1868)."""

    def _expire(self, client, staff_user):
        client.force_login(staff_user)
        session = client.session
        session[ABSOLUTE_DEADLINE_KEY] = timezone.now().timestamp() - 1
        session.save()
        return session.session_key

    def test_expired_cap_redirects_to_login_and_deletes_session_row(self, client, staff_user):
        session_key = self._expire(client, staff_user)

        response = client.get("/clients/")

        assert response.status_code == 302
        assert response.url.startswith("/login/")
        assert not Session.objects.filter(session_key=session_key).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGOUT, user=staff_user).exists()

    def test_expired_cap_gives_htmx_an_hx_redirect(self, client, staff_user):
        """Die Middleware antwortet NICHT selbst — der LoginRequiredMixin-302
        laeuft durch ``HtmxSessionMiddleware``, die ihn fuer HTMX in
        ``200 + HX-Redirect`` uebersetzt (sonst landete die Login-Seite im
        Partial-Swap)."""
        self._expire(client, staff_user)

        response = client.get("/clients/", headers={"HX-Request": "true"})

        assert response.status_code == 200
        assert response["HX-Redirect"].startswith("/login/")

    def test_expired_cap_gives_offline_replay_the_raw_302(self, client, staff_user):
        """Ein Offline-Replay traegt den eingefrorenen ``HX-Request``-Header,
        braucht aber den ROHEN 302: nur daran haelt der Queue-Klassifikator an,
        ohne die Zeile zu loeschen (ADR-030 §3, Refs #1419)."""
        self._expire(client, staff_user)

        response = client.get(
            "/clients/",
            headers={"HX-Request": "true", "X-Offline-Replay": "1"},
        )

        assert response.status_code == 302
        assert response["Location"].startswith("/login/")

    def test_login_view_stamps_the_deadline(self, client, staff_user, settings_obj):
        """Der echte Login-Pfad stempelt die Deadline aus den Facility-Settings
        — ohne ihn griffe nur der Grace-Stempel der Middleware."""
        settings_obj.session_absolute_hours = 3
        settings_obj.save()
        staff_user.set_password("testpass123")
        staff_user.save()

        response = client.post(
            "/login/",
            {"username": staff_user.username, "password": "testpass123"},
        )

        assert response.status_code == 302
        assert client.session[ABSOLUTE_DEADLINE_KEY] == pytest.approx(timezone.now().timestamp() + 3 * 3600, abs=30)
