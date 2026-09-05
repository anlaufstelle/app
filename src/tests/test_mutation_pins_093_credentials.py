"""Mutations-Pins fuer ``core.services.security.credentials._write_audit`` (Welle F, 0.93).

Quelle: ``src/core/services/security/credentials.py`` (Funktion ``_write_audit``,
Zeilen 240-258); Mutantenbaum ``mutants/src/core/services/security/credentials.py``
(``x__write_audit__mutmut_14/15/20/21/26``).

``_write_audit`` schreibt die Rechenschaftszeile ``CREDENTIALS_REVOKED`` nach
Art. 5 Abs. 2 DSGVO. Sie traegt zwei Felder, die bislang KEIN Test zusichert:
den Subjekt-FK ``user`` und den Einrichtungs-FK ``facility``. Beide werden im
Request-Zweig bewusst als Override an :func:`core.services.audit.log_audit_event`
uebergeben, weil Reset und Invite ANONYM laufen (``AnonymousUser``) und der
Helper sonst auf ``request.user`` bzw. ``request.current_facility`` zurueckfaellt
— also auf ``None``. Die vorhandenen Suiten (``test_credential_lifecycle``,
``test_auth``, ``test_invite_flow``) filtern die Zeile ueber ``action`` +
``target_id`` und pruefen nur ``detail['reason']`` und ``target_type``; ein
verlorener ``user``/``facility`` bleibt dort unsichtbar. Eine ``facility``-lose
Zeile faellt zusaetzlich aus der RLS-USING-Policy ``facility_isolation`` auf
``core_auditlog`` (Migration 0047/0085) — die Spur verschwindet fuer die
NOBYPASSRLS-App-Rolle lautlos, obwohl der INSERT durchlaeuft.

Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten); KEINE Imports aus ``test_rls_functional``
(modulweiter Skip unter ``MUTANT_UNDER_TEST`` wuerde jeden Pin entwerten);
keine gefakten Fixtures, nur echte Model-Objekte; jeder Test nennt seine
Mutanten samt abweichendem Wert in der Docstring.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore

from core.models import AuditLog
from core.services.security.credentials import RevocationReason, revoke_credentials

#: Quell-IP der Testrequests (``TRUSTED_PROXY_HOPS`` >= 1 im Testprofil, ohne
#: ``X-Forwarded-For`` faellt ``get_client_ip`` auf ``REMOTE_ADDR`` zurueck).
TEST_CLIENT_IP = "203.0.113.42"


def _request(rf, *, user=None, current_facility=None):
    """Request wie ihn die Middleware-Kette liefert: Session + ``user``.

    Ohne ``user`` ist er ANONYM — genau die Lage von Passwort-Reset und
    Invite-Setup. ``current_facility`` wird nur gesetzt, wenn ein Test den
    Rueckfallpfad des Audit-Helpers von der Override-Wahrheit unterscheiden
    will; die FacilityScopeMiddleware setzt das Attribut fuer anonyme
    Requests nicht.
    """
    request = rf.post("/", REMOTE_ADDR=TEST_CLIENT_IP)
    request.session = SessionStore()
    request.user = user if user is not None else AnonymousUser()
    if current_facility is not None:
        request.current_facility = current_facility
    return request


def _credentials_row(user) -> AuditLog:
    """Die eine ``CREDENTIALS_REVOKED``-Zeile zum Ziel-User (``get`` = Anzahl-Pin)."""
    return AuditLog.objects.get(
        action=AuditLog.Action.CREDENTIALS_REVOKED,
        target_id=str(user.pk),
    )


@pytest.mark.django_db
class TestWriteAuditSubjektFkImRequestZweig:
    """Die Zeile nennt den Ziel-User — nicht ``None``, nicht die handelnde Person."""

    def test_reset_mit_anonymem_request_traegt_den_ziel_user(self, rf, staff_user):
        """Pin: x__write_audit__mutmut_14 — ``user=user`` -> ``user=None``;
        Pin: x__write_audit__mutmut_20 — Argument ``user=user`` entfaellt.

        Passwort-Reset laeuft ueber einen ANONYMEN Request. Original schreibt
        ``user_id == staff_user.pk``. Mutant 14 setzt den Override hart auf
        ``None`` (``None`` ist ein gueltiger Override, kein ``_UNSET``), Mutant
        20 laesst das Argument weg, womit ``log_audit_event`` auf
        ``request.user`` zurueckfaellt — der ist hier ``AnonymousUser`` und
        damit nicht authentifiziert. Beide Mutanten schreiben also
        ``user_id is None``: die Rechenschaftszeile verliert ihren Subjekt-FK.
        """
        revoke_credentials(
            staff_user,
            reason=RevocationReason.PASSWORD_RESET,
            request=_request(rf),
        )

        zeile = _credentials_row(staff_user)
        assert zeile.user_id == staff_user.pk
        assert zeile.detail == {"reason": "password_reset"}

    def test_invite_setup_mit_anonymem_request_traegt_den_ziel_user(self, rf, staff_user):
        """Pin: x__write_audit__mutmut_14 — ``user=user`` -> ``user=None``;
        Pin: x__write_audit__mutmut_20 — Argument ``user=user`` entfaellt.

        Zweiter anonymer Weg (Invite-Setup), damit der Pin nicht an einem
        einzelnen Grund-Slug haengt. Original: ``user_id == staff_user.pk``;
        Mutant 14 und Mutant 20 beide ``user_id is None``.
        """
        revoke_credentials(
            staff_user,
            reason=RevocationReason.INVITE_SETUP,
            request=_request(rf),
        )

        zeile = _credentials_row(staff_user)
        assert zeile.user_id == staff_user.pk
        assert zeile.detail == {"reason": "invite_setup"}

    def test_fremde_handelnde_person_verdraengt_den_ziel_user_nicht(self, rf, staff_user, admin_user):
        """Pin: x__write_audit__mutmut_20 — Argument ``user=user`` entfaellt;
        Pin: x__write_audit__mutmut_14 — ``user=user`` -> ``user=None``.

        Trennt die beiden Mutanten voneinander: der Request ist hier
        AUTHENTIFIZIERT und gehoert einer anderen Person (``admin_user``) als
        dem Ziel des Widerrufs. Original schreibt weiter den Ziel-User
        (``user_id == staff_user.pk``); Mutant 20 faellt auf ``request.user``
        zurueck und schreibt ``user_id == admin_user.pk`` — die Zeile behauptet
        dann, das Passwort der Admin-Person sei gewechselt worden; Mutant 14
        schreibt ``user_id is None``.
        """
        revoke_credentials(
            staff_user,
            reason=RevocationReason.PASSWORD_CHANGE,
            request=_request(rf, user=admin_user),
        )

        zeile = _credentials_row(staff_user)
        assert zeile.user_id == staff_user.pk
        assert zeile.user_id != admin_user.pk


@pytest.mark.django_db
class TestWriteAuditEinrichtungsFkImRequestZweig:
    """Die Zeile bleibt im Einrichtungs-Auditlog sichtbar (RLS-Sichtbarkeit)."""

    def test_reset_mit_anonymem_request_traegt_die_einrichtung_des_ziel_users(self, rf, staff_user):
        """Pin: x__write_audit__mutmut_15 — ``facility=facility`` -> ``facility=None``;
        Pin: x__write_audit__mutmut_21 — Argument ``facility=facility`` entfaellt.

        Der anonyme Reset-Request traegt keinen ``current_facility``-Kontext.
        Original schreibt ``facility_id == staff_user.facility_id``; Mutant 15
        setzt den Override hart auf ``None``, Mutant 21 faellt auf
        ``getattr(request, "current_facility", None)`` zurueck — beide
        schreiben ``facility_id is None``. Eine solche Zeile matcht die
        RLS-USING-Policy ``facility_isolation`` nicht mehr und ist fuer die
        App-Rolle unsichtbar, obwohl der INSERT durchlaeuft.
        """
        revoke_credentials(
            staff_user,
            reason=RevocationReason.PASSWORD_RESET,
            request=_request(rf),
        )

        zeile = _credentials_row(staff_user)
        assert zeile.facility_id == staff_user.facility_id
        assert zeile.facility_id is not None

    def test_fremder_request_kontext_verdraengt_die_ziel_einrichtung_nicht(self, rf, staff_user, second_facility):
        """Pin: x__write_audit__mutmut_21 — Argument ``facility=facility`` entfaellt;
        Pin: x__write_audit__mutmut_15 — ``facility=facility`` -> ``facility=None``.

        Trennt die beiden Mutanten: der Request traegt hier ausdruecklich einen
        FREMDEN ``current_facility``. Original bleibt bei der Einrichtung des
        Ziel-Users (``facility_id == staff_user.facility_id``); Mutant 21
        schreibt ``facility_id == second_facility.pk`` — die Zeile landete im
        Auditlog der falschen Einrichtung; Mutant 15 schreibt
        ``facility_id is None``.
        """
        revoke_credentials(
            staff_user,
            reason=RevocationReason.INVITE_SETUP,
            request=_request(rf, current_facility=second_facility),
        )

        zeile = _credentials_row(staff_user)
        assert zeile.facility_id == staff_user.facility_id
        assert zeile.facility_id != second_facility.pk


@pytest.mark.django_db
class TestWriteAuditOhneRequest:
    """Service-/CLI-Kontext: derselbe Subjekt-FK, nur ohne Request-Helper."""

    def test_ohne_request_traegt_die_zeile_den_ziel_user(self, staff_user):
        """Pin: x__write_audit__mutmut_26 — ``audit_event(..., user=user)`` -> ``user=None``.

        Request-loser Zweig (``revoke_credentials`` aus Service oder CLI).
        ``audit_event`` schreibt ``user`` ungefiltert durch: Original setzt
        ``user_id == staff_user.pk``, Mutant 26 schreibt ``user_id is None``
        und damit eine Rechenschaftszeile ohne Subjekt — nur noch ``target_id``
        als String bliebe uebrig. ``facility_id`` bleibt in beiden Faellen
        gesetzt, deckt den Mutanten also nicht auf.
        """
        revoke_credentials(staff_user, reason=RevocationReason.PASSWORD_CHANGE)

        zeile = _credentials_row(staff_user)
        assert zeile.user_id == staff_user.pk
        assert zeile.facility_id == staff_user.facility_id
        assert zeile.detail == {"reason": "password_change"}
