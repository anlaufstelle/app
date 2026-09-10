"""Rotation der Django-Session-ID nach einer Rechteanhebung (Refs #2034).

``django.contrib.auth.login`` rotiert die **Django-Session-ID** — den Wert des
``sessionid``-Cookies und zugleich den Primaerschluessel der
``django_session``-Zeile — beim Anmelden. Jede WEITERE Rechteanhebung innerhalb
derselben Sitzung lief bis #2034 ohne Rotation: MFA-Verify per TOTP oder
Backup-Code, der Abschluss des MFA-Setups, eine erfolgreiche WebAuthn-Assertion
und der Sudo-Grant hoben die Faehigkeiten der bestehenden Session an, ohne ihre
ID zu wechseln. Ein vor der Anhebung entwendetes oder auf einem fremden Geraet
zurueckgelassenes Cookie erbte damit die angehobene Faehigkeit (Szenario A9).

Begriffsabgrenzung: „Session-Key" bezeichnet in ``docs/security-notes.md`` und
``docs/threat-model.md`` ueberwiegend den **Offline-Krypto-Schluessel** (den
non-extractable AES-Schluessel in IndexedDB). Hier geht es ausschliesslich um
Djangos Session-ID (``request.session.session_key``) — mit dem Offline-Vault hat
diese Rotation nichts zu tun.

``cycle_key()`` uebernimmt die kompletten Session-Daten in die neue Zeile; die
Rotation kostet also keinen der folgenden Werte, und genau das sichern die
Guard-Tests in ``src/tests/test_session_rotation.py`` je Rotationsort ab:

* ``_auth_user_id`` / ``_auth_user_backend`` / ``_auth_user_hash`` — die
  Anmeldung selbst bleibt bestehen, rotiert wird nur die ID,
* ``_session_expiry`` — die je Einrichtung konfigurierte Sitzungslaufzeit,
* ``_idle_window_renewed_at`` und ``_session_absolute_deadline`` — Inaktivitaets-
  und Absolutfrist (``core/middleware/session_idle.py``); die Rotation darf
  keine der beiden Fristen zuruecksetzen oder verschieben,
* ``otp_device_id`` (``django_otp.DEVICE_ID_SESSION_KEY``) — im WebAuthn-Pfad
  von ``otp_login`` gesetzt, muss die Rotation ueberleben,
* ``mfa_backup_codes`` — die verschluesselte Anzeige-Zwischenablage — und der
  Abbau von ``mfa_setup_pending``.

Der Facility-Scope steht bewusst NICHT in dieser Liste: ihn liest
``FacilityScopeMiddleware`` pro Request live aus ``request.user.facility``
(Datenbank, ``core/middleware/facility_scope.py:185``), nicht aus der Session —
es gibt also keinen Facility-Session-Wert, der verloren gehen koennte.
"""

from __future__ import annotations

from django.http import HttpRequest


def rotate_session_after_privilege_gain(request: HttpRequest) -> None:
    """Erneuert die Django-Session-ID und entwertet die bisherige.

    ``SessionBase.cycle_key()`` legt eine neue Session-ID an, uebernimmt die
    Session-Daten und loescht die alte Zeile — der alte ``sessionid``-Cookie ist
    danach nicht mehr angemeldet.

    Aufrufreihenfolge am Rotationsort (bindend): Erfolg feststellen → rotieren →
    erst danach das Rechte-Flag (``mfa_verified``, ``sudo_until``) schreiben.
    Nur nach FESTGESTELLTEM Erfolg aufrufen: auf einem Fehlschlagspfad waere die
    Rotation ein kostenloser Sitzungs-Reset fuer jeden, der das Cookie besitzt.
    """
    request.session.cycle_key()
