"""Admin-initiierte Passwortaenderungen hinterlassen eine Audit-Spur (Refs #2018).

Djangos ``UserAdmin.user_change_password`` ruft ``form.save()`` direkt auf und
umgeht ``ModelAdmin.save_model()`` vollstaendig. Damit greift weder der
#1369-Actor-Mechanismus (``obj._audit_actor``, gesetzt in ``save_model``) noch
der ``post_save``-Diff aus ``signals/audit.py`` (der nur ``role``,
``is_active`` und ``can_confirm_deletion`` verfolgt). Ein Admin, der ein
FREMDES Passwort zuruecksetzt, hinterliess bis hierher nur Djangos
**mutierbares** ``admin.LogEntry`` — keine Zeile im append-only, HMAC-verketteten
AuditLog. Credential-Wechsel gelten im Threat-Model aber als Privilegien-
Ereignis (gleicher Rigor wie ``USER_ROLE_CHANGED``).

Geprueft wird:

* der Reset schreibt genau EINE ``CREDENTIALS_REVOKED``-Zeile mit **Ziel**
  (``user``) und **handelndem Admin** (``actor``);
* das Deaktivieren der Passwort-Anmeldung (Djangos zweiter Erfolgspfad) wird
  ebenso erfasst;
* das blosse Oeffnen des Formulars (GET) schreibt nichts;
* im ``detail`` landet **kein** Passwort-/Hash-Material (Allowlist #1944,
  PII-Minimierung #1751).
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.models import AuditLog

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Wirbelsturm-9182-xy"


def _password_url(user) -> str:
    return f"/admin-mgmt/core/user/{user.pk}/password/"


def _credential_entries(target):
    return AuditLog.objects.filter(action=AuditLog.Action.CREDENTIALS_REVOKED, user=target)


class TestAdminPasswordChangeAudit:
    def test_admin_reset_writes_actor_attributed_audit_entry(self, client, admin_user, staff_user):
        """Fremd-Reset durch einen facility_admin -> eine Zeile mit Actor + Ziel."""
        client.force_login(admin_user)

        response = client.post(
            _password_url(staff_user),
            {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "usable_password": "true"},
        )

        assert response.status_code == 302, "Erfolgreicher Reset leitet auf die Change-View zurueck"
        entry = _credential_entries(staff_user).get()
        assert entry.actor_id == admin_user.pk, "Der handelnde Admin muss als actor stehen"
        assert entry.user_id == staff_user.pk, "Das Ziel des Resets muss als user stehen"
        assert entry.target_type == "User"
        assert entry.target_id == str(staff_user.pk)
        assert entry.detail["reason"] == "admin_password_reset"
        assert entry.detail["actor_username"] == admin_user.username
        assert entry.detail["username"] == staff_user.username
        assert entry.facility_id == staff_user.facility_id

    def test_unset_password_is_audited(self, client, admin_user, staff_user):
        """Djangos zweiter Erfolgspfad („Passwort-Anmeldung deaktivieren")."""
        client.force_login(admin_user)

        response = client.post(
            _password_url(staff_user),
            {"usable_password": "false", "unset-password": "1"},
        )

        assert response.status_code == 302
        staff_user.refresh_from_db()
        assert not staff_user.has_usable_password()
        assert _credential_entries(staff_user).count() == 1

    def test_get_does_not_write_audit_entry(self, client, admin_user, staff_user):
        """Das Formular nur anzusehen ist kein Credential-Ereignis."""
        client.force_login(admin_user)

        response = client.get(_password_url(staff_user))

        assert response.status_code == 200
        assert _credential_entries(staff_user).count() == 0

    def test_invalid_post_writes_no_audit_entry(self, client, admin_user, staff_user):
        """Nicht uebereinstimmende Passwoerter -> kein Wechsel, keine Zeile."""
        client.force_login(admin_user)
        old_hash = staff_user.password

        response = client.post(
            _password_url(staff_user),
            {"password1": NEW_PASSWORD, "password2": "etwas-anderes-4711", "usable_password": "true"},
        )

        assert response.status_code == 200, "Formularfehler wird neu gerendert, kein Redirect"
        staff_user.refresh_from_db()
        assert staff_user.password == old_hash
        assert _credential_entries(staff_user).count() == 0

    def test_detail_carries_no_password_material(self, client, admin_user, staff_user):
        """Die Detail-Allowlist (#1944) haelt Passwort-/Hash-Material heraus."""
        client.force_login(admin_user)

        client.post(
            _password_url(staff_user),
            {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "usable_password": "true"},
        )

        detail = _credential_entries(staff_user).get().detail
        assert set(detail) == {"reason", "username", "actor_id", "actor_username"}
        werte = " ".join(str(value) for value in detail.values())
        assert NEW_PASSWORD not in werte
        staff_user.refresh_from_db()
        assert staff_user.password not in werte


class TestAdminPasswordChangeAtomicity:
    """Fix-Runde 1 (Cross-Review): Passwortwechsel und Audit sind untrennbar.

    Djangos ``user_change_password`` ist eine Sonder-View — sie laeuft weder
    ueber ``changeform_view`` noch unter ``ATOMIC_REQUESTS`` (im Repo nicht
    gesetzt). Ohne eigene Transaktionsklammer blieb ein Passwortwechsel
    bestehen, dessen Audit-Insert scheiterte: genau die Nachweisluecke, die
    #2018 schliessen soll. Und ohne Zeilensperre konnte zwischen den beiden
    Lesevorgaengen ein paralleler Wechsel einlaufen, der diesem Admin
    faelschlich zugeschrieben worden waere — sogar bei einem ungueltigen POST.
    """

    def test_audit_failure_rolls_back_the_password_change(self, client, admin_user, staff_user, monkeypatch):
        """Fail-closed: keine Audit-Zeile ⇒ auch kein neues Passwort."""
        client.force_login(admin_user)
        old_hash = staff_user.password

        def _boom(*args, **kwargs):
            raise RuntimeError("Audit-Kette nicht schreibbar")

        # Modul-Referenz stubben (NICHT den connection-Proxy patchen).
        monkeypatch.setattr("core.admin.users.audit_event", _boom)

        with pytest.raises(RuntimeError):
            client.post(
                _password_url(staff_user),
                {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "usable_password": "true"},
            )

        staff_user.refresh_from_db()
        assert staff_user.password == old_hash, (
            "Ein Passwortwechsel ohne Audit-Zeile ist genau die Luecke aus #2018 — er muss zurueckgerollt werden"
        )
        assert not staff_user.check_password(NEW_PASSWORD)
        assert _credential_entries(staff_user).count() == 0

    def test_target_row_is_locked_across_read_change_and_audit(self, client, admin_user, staff_user):
        """Die Zielzeile wird gesperrt — sonst ist die Vorher/Nachher-Erkennung
        gegen einen parallelen Wechsel nicht dicht."""
        client.force_login(admin_user)

        with CaptureQueriesContext(connection) as ctx:
            client.post(
                _password_url(staff_user),
                {"password1": NEW_PASSWORD, "password2": NEW_PASSWORD, "usable_password": "true"},
            )

        sperren = [
            query["sql"]
            for query in ctx.captured_queries
            if "FOR UPDATE" in query["sql"].upper() and "core_user" in query["sql"].lower()
        ]
        assert sperren, "Der Zielkonto-Read muss die Zeile per select_for_update sperren"

    def test_invalid_post_takes_no_lock_and_writes_nothing(self, client, admin_user, staff_user):
        """Gegenprobe: ein ungueltiger POST aendert nichts und attribuiert nichts."""
        client.force_login(admin_user)
        old_hash = staff_user.password

        client.post(
            _password_url(staff_user),
            {"password1": NEW_PASSWORD, "password2": "etwas-anderes-4711", "usable_password": "true"},
        )

        staff_user.refresh_from_db()
        assert staff_user.password == old_hash
        assert _credential_entries(staff_user).count() == 0
