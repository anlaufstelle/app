"""Unit-Tests fuer Session-Invalidierung bei User-Deaktivierung.

Refs Matrix AUD-SEC-AUTH-04 (Issue #926, Master #922).

Verifiziert, dass das Setzen von ``user.is_active = False`` dazu fuehrt, dass
folgende Requests des Users zur Login-Seite umgeleitet werden — Djangos
``AuthenticationMiddleware`` baut ``request.user`` ueber
``ModelBackend.get_user()``, das fuer ``is_active=False`` ``AnonymousUser``
zurueckgibt. ``LoginRequiredMixin`` (transitiv via
``AssistantOrAboveRequiredMixin`` auf der Root-View ``ZeitstromView``)
fuehrt dann den Redirect aus.

Zusaetzlich wird verifiziert, dass das ``post_save``-Signal in
``core/signals/audit.py`` einen ``USER_DEACTIVATED``-AuditLog-Eintrag
schreibt, sobald ``is_active`` von ``True`` auf ``False`` wechselt.
"""

import pytest

from core.models import AuditLog


@pytest.mark.django_db
class TestAuthSessionInvalidation:
    """AUD-SEC-AUTH-04: Deaktivierte User koennen nach Save keine Session-Requests mehr ausfuehren."""

    def test_active_user_reaches_dashboard(self, client, staff_user):
        """Aktiver User erreicht die Zeitstrom-Root-View ohne Login-Redirect."""
        tc = client
        tc.force_login(staff_user)
        resp = tc.get("/")
        # Erwarteter Pfad: 200 (Zeitstrom rendert). 302 ist legitim, falls
        # Force-Password-Change-/MFA-Setup-Middleware umleitet — entscheidend
        # ist, dass nicht zur Login-Seite umgeleitet wird.
        assert resp.status_code in (200, 302)
        if resp.status_code == 302:
            assert "/login" not in resp.url, (
                f"Aktiver User darf NICHT auf /login/ umgeleitet werden — tatsaechliches Redirect-Ziel war: {resp.url}"
            )

    def test_deactivated_user_redirected_to_login(self, client, staff_user):
        """Nach ``is_active=False``-Save wird die naechste Anfrage zur Login-Seite umgeleitet.

        Djangos ``AuthenticationMiddleware`` ruft pro Request
        ``ModelBackend.get_user(user_id)`` auf — bei ``is_active=False`` wird
        ``AnonymousUser`` zurueckgegeben, ``LoginRequiredMixin`` redirect.
        Effekt: die Session ist effektiv invalidiert, ohne dass
        ``session.flush()`` aufgerufen werden muss.
        """
        tc = client
        tc.force_login(staff_user)
        # Sanity-Check: aktiver User kommt durch.
        resp_active = tc.get("/")
        assert resp_active.status_code in (200, 302)
        if resp_active.status_code == 302:
            assert "/login" not in resp_active.url

        # User deaktivieren — wirkt sofort, da die naechste Anfrage einen
        # frischen ``request.user``-Lookup via Session-PK macht.
        staff_user.is_active = False
        staff_user.save()

        resp_inactive = tc.get("/")
        assert resp_inactive.status_code == 302, (
            f"Deaktivierter User muss zur Login-Seite umgeleitet werden — erhalten: {resp_inactive.status_code}"
        )
        assert "/login" in resp_inactive.url, f"Redirect-Ziel muss /login/ enthalten — erhalten: {resp_inactive.url}"

    def test_user_deactivated_audit_written_on_save(self, staff_user):
        """``USER_DEACTIVATED``-AuditLog-Eintrag wird beim Wechsel True → False geschrieben.

        Das Signal liegt in ``core/signals/audit.py`` und greift im
        ``post_save`` des User-Modells, sobald ``_audit_old_is_active`` True war
        und ``instance.is_active`` False ist.
        """
        before = AuditLog.objects.filter(
            action=AuditLog.Action.USER_DEACTIVATED,
            target_id=str(staff_user.pk),
        ).count()

        staff_user.is_active = False
        staff_user.save()

        entry = (
            AuditLog.objects.filter(
                action=AuditLog.Action.USER_DEACTIVATED,
                target_id=str(staff_user.pk),
            )
            .order_by("-timestamp")
            .first()
        )
        assert entry is not None, (
            f"Es muss ein USER_DEACTIVATED-AuditLog-Eintrag mit target_id={staff_user.pk!s} existieren."
        )
        after = AuditLog.objects.filter(
            action=AuditLog.Action.USER_DEACTIVATED,
            target_id=str(staff_user.pk),
        ).count()
        assert after == before + 1
        # target_type wird auf "User" gesetzt (siehe signals/audit.py:216).
        assert entry.target_type == "User"
