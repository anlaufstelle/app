"""E2E-Tests: Pre-Submit-Guard bei Passwortwechsel mit ungesyncter Offline-Arbeit (Refs #1415).

Ein Passwortwechsel rotiert das Offline-Salt und macht bestehende
Offline-Chiffrate kryptografisch unlesbar (docs/user-guide.md §8) — das ist
mit dem POST bereits besiegelt. ``#password-change-form`` (auth-bootstrap.js)
muss daher VOR dem POST warnen, wenn noch ungesyncte Einträge vorliegen.

Passwort-Restore per Hash, nicht per ``set_password`` (Refs #1427): diese
Tests ändern echte User-Passwörter mitten in der Suite. ``set_password``
salzt neu — der Session-Auth-Hash aller VOR dem Wechsel erzeugten Sessions
(insb. die session-scoped Storage-States aus conftest) bliebe damit dauerhaft
ungültig und jeder spätere Test der betroffenen Rollen landet auf /login.
Darum wird der exakte Hash-String gesichert und wiederhergestellt; die
Vorher-Session-Assertions am Testende verankern diese Invariante.
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

_NEW_PASSWORD = "Offline-Guard-93!Q"
_ORIG_PASSWORD = "anlaufstelle2026"


def _run_manage_shell(code: str, e2e_env) -> str:
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"manage.py shell fehlgeschlagen: {result.stderr}"
    return result.stdout.strip()


def _password_hash(username: str, e2e_env) -> str:
    """Aktuellen Passwort-Hash eines Users auslesen (für den späteren Restore)."""
    return _run_manage_shell(
        f"from core.models import User; print(User.objects.get(username='{username}').password)",
        e2e_env,
    )


def _restore_password_hash(username: str, password_hash: str, e2e_env) -> None:
    """Exakten Hash-String wiederherstellen — hält bestehende Sessions gültig.

    Django-Hashes (pbkdf2/argon2) enthalten nur ``$``-separierte
    Base64-/ASCII-Segmente, sind also als Python-Single-Quote-Literal sicher.
    """
    assert "'" not in password_hash and "\\" not in password_hash
    _run_manage_shell(
        "from core.models import User; "
        f"u = User.objects.get(username='{username}'); "
        f"u.password = '{password_hash}'; "
        "u.save(update_fields=['password'])",
        e2e_env,
    )


def _login(page, base_url, username, password=_ORIG_PASSWORD):
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)


def _assert_session_still_valid(page, base_url, username):
    """Eine VOR dem Passwortwechsel erzeugte Session muss nach dem Restore
    weiter gültig sein — sonst sterben die session-scoped Storage-States
    der Suite (Regression, siehe Modul-Docstring)."""
    page.goto(f"{base_url}/", wait_until="domcontentloaded")
    assert "/login/" not in page.url, (
        f"Vorher-Session von {username} nach Passwort-Restore ungültig — "
        "Restore muss den exakten Hash wiederherstellen, nicht set_password()"
    )


def _seed_unsynced_events(page, count):
    """Session-Key ableiten (unabhaengig vom echten Passwort — rein lokale
    AES-GCM-Ableitung) und ``count`` ungesyncte Events in IndexedDB anlegen,
    analog dem ``_bootstrap_store``-Muster in test_offline_edit_conflict.py."""
    page.evaluate(
        """async (n) => {
            await window.crypto_session.ready();
            await window.crypto_session.deriveSessionKey('pw', 'YWJjZGVmZ2hpamtsbW5vcA');
            for (let i = 0; i < n; i++) {
                await window.offlineStore.saveOfflineEdit({
                    pk: `ffffffff-0000-4000-8000-00000000141${i}`,
                    clientPk: 'client-1415-guard',
                    occurredAt: '2026-01-01T00:00:00Z',
                    localStatus: 'modified',
                    data: {formData: {notiz: 'unsynced'}, expectedUpdatedAt: ''},
                });
            }
        }""",
        count,
    )


def _submit_password_change(page, base_url, old_password, new_password):
    page.goto(f"{base_url}/password-change/", wait_until="domcontentloaded")
    page.fill("#id_old_password", old_password)
    page.fill("#id_new_password1", new_password)
    page.fill("#id_new_password2", new_password)
    page.click("#password-change-form button[type=submit]")


class TestZZPasswordChangeUnsyncedGuard:
    def test_unsynced_entries_show_confirm_with_count_and_cancel_keeps_password(self, browser, base_url):
        """7a: ungesyncte Einträge → confirm mit korrektem Zähler; Abbruch
        verhindert den POST — Login mit dem alten Passwort funktioniert
        danach weiterhin."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _login(page, base_url, "lena")

            _seed_unsynced_events(page, 2)

            # expect_event blockt, bis der (asynchrone) Zaehl-Check den
            # confirm-Dialog ausgeloest hat — kein Race auf einen festen Sleep.
            with page.expect_event("dialog", timeout=5000) as dialog_info:
                page.goto(f"{base_url}/password-change/", wait_until="domcontentloaded")
                page.fill("#id_old_password", _ORIG_PASSWORD)
                page.fill("#id_new_password1", _NEW_PASSWORD)
                page.fill("#id_new_password2", _NEW_PASSWORD)
                page.click("#password-change-form button[type=submit]")
            dialog = dialog_info.value
            message = dialog.message
            dialog.dismiss()

            assert "2" in message, f"Zähler fehlt/falsch in: {message}"

            # Kein POST gelaufen: die Seite ist noch auf /password-change/.
            assert "/password-change/" in page.url
        finally:
            context.close()

        # Login mit dem alten Passwort funktioniert weiterhin.
        verify_context = browser.new_context(locale="de-DE")
        verify_page = verify_context.new_page()
        _login(verify_page, base_url, "lena")
        verify_context.close()

    def test_unsynced_entries_confirm_accept_changes_password(self, browser, base_url, e2e_env):
        """7b: ungesyncte Einträge → confirm mit Bestätigen lässt den
        Passwortwechsel durchgehen."""
        orig_hash = _password_hash("emma", e2e_env)
        # Vorher-Session als ruhenden Cookie-Snapshot einfrieren (wie die
        # conftest-Storage-States). Der Kontext wird SOFORT geschlossen:
        # eine offen gehaltene Seite feuert Hintergrund-Requests (Sync-
        # Orchestrator/Polling), und ein Request im Fenster zwischen
        # Passwortwechsel und Hash-Restore flusht die Session serverseitig
        # endgültig (django.contrib.auth.get_user bei Hash-Mismatch).
        pre_context = browser.new_context(locale="de-DE")
        pre_page = pre_context.new_page()
        _login(pre_page, base_url, "emma")
        pre_state = pre_context.storage_state()
        pre_context.close()

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _login(page, base_url, "emma")

            _seed_unsynced_events(page, 3)

            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))

            _submit_password_change(page, base_url, _ORIG_PASSWORD, _NEW_PASSWORD)
            page.wait_for_url(f"{base_url}/", timeout=10000)

            assert len(dialogs) == 1
            assert "3" in dialogs[0]
        finally:
            context.close()
            _restore_password_hash("emma", orig_hash, e2e_env)

        check_context = browser.new_context(storage_state=pre_state, locale="de-DE")
        _assert_session_still_valid(check_context.new_page(), base_url, "emma")
        check_context.close()

    def test_no_unsynced_entries_skips_confirm_and_changes_password(self, browser, base_url, e2e_env):
        """7c: ohne ungesyncte Daten erscheint kein confirm-Dialog, der
        Wechsel geht normal durch."""
        orig_hash = _password_hash("miriam", e2e_env)
        # Ruhender Vorher-Session-Snapshot, s. 7b.
        pre_context = browser.new_context(locale="de-DE")
        pre_page = pre_context.new_page()
        _login(pre_page, base_url, "miriam")
        pre_state = pre_context.storage_state()
        pre_context.close()

        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            _login(page, base_url, "miriam")

            dialog_fired = []
            page.on("dialog", lambda dialog: (dialog_fired.append(True), dialog.dismiss()))

            _submit_password_change(page, base_url, _ORIG_PASSWORD, _NEW_PASSWORD)
            page.wait_for_url(f"{base_url}/", timeout=10000)

            assert dialog_fired == []
        finally:
            context.close()
            _restore_password_hash("miriam", orig_hash, e2e_env)

        check_context = browser.new_context(storage_state=pre_state, locale="de-DE")
        _assert_session_still_valid(check_context.new_page(), base_url, "miriam")
        check_context.close()
