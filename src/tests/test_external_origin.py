"""Refs #1967: Sicherheitsrelevante Links haengen an ``EXTERNAL_ORIGIN``, nicht am Request-Host.

``ALLOWED_HOSTS`` laesst in Produktion bewusst Subdomain-Wildcards zu
(``.example.org``, siehe ``settings/prod.py``) — jeder davon erfasste Host
landete ohne diese Schicht ungeprueft in einem Token-Link. Betroffen sind drei
produktive Linkquellen: die Einladungsmail, die Passwort-Reset-Mail und die
Entsperr-Mail der Lockout-Recovery.

Die Tests schicken deshalb einen fremden ``Host``-Header und pruefen, dass er in
keiner der drei Mails auftaucht — und dass stattdessen die konfigurierte Origin
darin steht.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from anlaufstelle.settings._env import env_origin
from core.admin import UserAdmin
from core.models import User
from core.services.security import build_invite_url
from core.services.security.origin import build_external_url, external_link_context, get_external_origin

ORIGIN = "https://portal.example.org"
FREMD_HOST = "angreifer.example.net"


def _admin_request(rf, admin_user, host):
    """POST-Request aus der Admin-Oberflaeche mit frei waehlbarem ``Host``-Header."""
    request = rf.post("/", headers={"host": host})
    request.user = admin_user
    request.META["REMOTE_ADDR"] = "127.0.0.1"
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.mark.django_db
class TestEinladungslink:
    def test_setup_link_nutzt_die_konfigurierte_origin(self, settings, facility):
        settings.EXTERNAL_ORIGIN = ORIGIN
        user = User.objects.create_user(username="einladung1", email="e1@example.org", facility=facility)

        assert build_invite_url(user).startswith(f"{ORIGIN}/invite/")

    def test_einladungsmail_ignoriert_den_request_host(self, settings, rf, admin_user, facility):
        """Der Admin legt das Konto ueber einen Request mit fremdem Host an —
        der Setup-Link in der Mail zeigt trotzdem auf die konfigurierte Origin."""
        settings.EXTERNAL_ORIGIN = ORIGIN
        request = _admin_request(rf, admin_user, FREMD_HOST)
        neu = User(
            username="einladung2",
            email="e2@example.org",
            role=User.Role.STAFF,
            facility=facility,
        )

        UserAdmin(User, AdminSite()).save_model(request, neu, form=None, change=False)

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert f"{ORIGIN}/invite/" in body
        assert FREMD_HOST not in body


@pytest.mark.django_db
class TestPasswortResetLink:
    def test_reset_mail_ignoriert_den_request_host(self, settings, client, staff_user):
        settings.EXTERNAL_ORIGIN = ORIGIN
        staff_user.email = "reset@example.org"
        staff_user.save(update_fields=["email"])

        client.post(
            reverse("password_reset"),
            {"email": staff_user.email},
            headers={"host": FREMD_HOST},
        )

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert f"{ORIGIN}/password-reset/" in body
        assert FREMD_HOST not in body


@pytest.mark.django_db
class TestEntsperrLink:
    def test_recovery_mail_ignoriert_den_request_host(self, settings, client, staff_user):
        settings.EXTERNAL_ORIGIN = ORIGIN
        staff_user.email = "entsperren@example.org"
        staff_user.save(update_fields=["email"])

        client.post(
            reverse("core:lockout_recovery_request"),
            {"email": staff_user.email},
            headers={"host": FREMD_HOST},
        )

        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert f"{ORIGIN}/account/recovery/confirm/" in body
        assert FREMD_HOST not in body


class TestOriginHelfer:
    def test_ohne_origin_scheitert_der_linkbau_laut(self, settings):
        """Kein stiller ``localhost``-Link mehr: ohne Origin gibt es keinen Link."""
        settings.EXTERNAL_ORIGIN = ""

        with pytest.raises(ImproperlyConfigured):
            build_external_url("/invite/abc/def/")

    def test_link_kontext_zerlegt_origin_in_protokoll_und_host(self, settings):
        settings.EXTERNAL_ORIGIN = "https://portal.example.org:8443"

        assert external_link_context() == {"protocol": "https", "domain": "portal.example.org:8443"}

    def test_origin_wird_unveraendert_durchgereicht(self, settings):
        settings.EXTERNAL_ORIGIN = ORIGIN

        assert get_external_origin() == ORIGIN


class TestEnvOriginValidierung:
    """Die Validierung greift beim Laden der Settings — fehlerhafte Werte
    duerfen nicht erst in einer bereits versendeten Mail auffallen."""

    @pytest.fixture(autouse=True)
    def _leere_umgebung(self, monkeypatch):
        monkeypatch.delenv("TEST_ORIGIN", raising=False)

    def test_leerer_wert_bleibt_leer(self):
        assert env_origin("TEST_ORIGIN") == ""

    def test_gueltige_origin_wird_uebernommen(self, monkeypatch):
        monkeypatch.setenv("TEST_ORIGIN", "https://portal.example.org")

        assert env_origin("TEST_ORIGIN") == "https://portal.example.org"

    def test_port_ist_erlaubt(self, monkeypatch):
        monkeypatch.setenv("TEST_ORIGIN", "http://localhost:8000")

        assert env_origin("TEST_ORIGIN") == "http://localhost:8000"

    @pytest.mark.parametrize(
        "wert",
        [
            "portal.example.org",  # kein Schema
            "ftp://portal.example.org",  # falsches Schema
            "https://",  # kein Host
            "https://portal.example.org/",  # abschliessender Schraegstrich
            "https://portal.example.org/app",  # Pfadanteil
            "https://portal.example.org?x=1",  # Query
            "https://portal.example.org#top",  # Fragment
        ],
    )
    def test_fehlerhafte_werte_werden_abgelehnt(self, monkeypatch, wert):
        monkeypatch.setenv("TEST_ORIGIN", wert)

        with pytest.raises(ValueError):
            env_origin("TEST_ORIGIN")

    @pytest.mark.parametrize(
        "wert",
        [
            "https://portal.example.org\n.evil.example.net",  # eingebetteter Zeilenumbruch
            "https://portal.example.org\r\n/pfad",  # CRLF
            "https://portal\t.example.org",  # Tabulator
            "https://portal .example.org",  # eingebettetes Leerzeichen
        ],
    )
    def test_steuerzeichen_werden_abgelehnt(self, monkeypatch, wert):
        """``urlsplit`` entfernt ASCII-Zeilenumbrueche und Tabs VOR dem Parsen.

        Ein Wert mit eingebettetem ``\\n`` passierte die Validierung deshalb
        anstandslos und wurde anschliessend ROH weitergereicht — validiert
        wurde also ein anderer String als der, der spaeter in Mail-Header und
        Token-Link landet. Solche Werte werden jetzt ausdruecklich abgelehnt.
        """
        monkeypatch.setenv("TEST_ORIGIN", wert)

        with pytest.raises(ValueError):
            env_origin("TEST_ORIGIN")

    def test_geprueft_wird_genau_der_zurueckgegebene_wert(self, monkeypatch):
        """Rueckgabe und validierter String muessen identisch sein."""
        monkeypatch.setenv("TEST_ORIGIN", "  https://portal.example.org  ")
        ergebnis = env_origin("TEST_ORIGIN")

        assert ergebnis == "https://portal.example.org"
        monkeypatch.setenv("TEST_ORIGIN", ergebnis)
        assert env_origin("TEST_ORIGIN") == ergebnis
