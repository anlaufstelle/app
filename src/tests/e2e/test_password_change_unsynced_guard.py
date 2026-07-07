"""E2E-Tests: Pre-Submit-Guard bei Passwortwechsel mit ungesyncter Offline-Arbeit (Refs #1415).

Ein Passwortwechsel rotiert das Offline-Salt und macht bestehende
Offline-Chiffrate kryptografisch unlesbar (docs/user-guide.md §8) — das ist
mit dem POST bereits besiegelt. ``#password-change-form`` (auth-bootstrap.js)
muss daher VOR dem POST warnen, wenn noch ungesyncte Einträge vorliegen.

ZZ-Prefix: diese Tests ändern echte User-Passwörter (Confirm-/Normalfall)
und stellen sie danach wieder her; andere Tests sollen den ungewöhnlichen
Zwischenzustand nicht sehen.
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e

_NEW_PASSWORD = "Offline-Guard-93!Q"
_ORIG_PASSWORD = "anlaufstelle2026"


def _reset_password(username: str, e2e_env) -> None:
    """Django-Shell-Helper: Passwort eines Users zurück auf den Seed-Wert setzen."""
    result = subprocess.run(
        [
            sys.executable,
            "src/manage.py",
            "shell",
            "--no-imports",
            "-c",
            (
                "from core.models import User; "
                f"u = User.objects.get(username='{username}'); "
                f"u.set_password('{_ORIG_PASSWORD}'); "
                "u.save()"
            ),
        ],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Passwort-Reset fehlgeschlagen: {result.stderr}"


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
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill("#id_username", "lena")
            page.fill("#id_password", _ORIG_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

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
        verify_page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
        verify_page.fill("#id_username", "lena")
        verify_page.fill("#id_password", _ORIG_PASSWORD)
        verify_page.click("button[type=submit]")
        verify_page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)
        verify_context.close()

    def test_unsynced_entries_confirm_accept_changes_password(self, browser, base_url, e2e_env):
        """7b: ungesyncte Einträge → confirm mit Bestätigen lässt den
        Passwortwechsel durchgehen."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill("#id_username", "emma")
            page.fill("#id_password", _ORIG_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

            _seed_unsynced_events(page, 3)

            dialogs = []
            page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept()))

            _submit_password_change(page, base_url, _ORIG_PASSWORD, _NEW_PASSWORD)
            page.wait_for_url(f"{base_url}/", timeout=10000)

            assert len(dialogs) == 1
            assert "3" in dialogs[0]
        finally:
            context.close()
            _reset_password("emma", e2e_env)

    def test_no_unsynced_entries_skips_confirm_and_changes_password(self, browser, base_url, e2e_env):
        """7c: ohne ungesyncte Daten erscheint kein confirm-Dialog, der
        Wechsel geht normal durch."""
        context = browser.new_context(locale="de-DE")
        page = context.new_page()
        try:
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill("#id_username", "miriam")
            page.fill("#id_password", _ORIG_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_url(lambda url: "/login/" not in url, timeout=10000)

            dialog_fired = []
            page.on("dialog", lambda dialog: (dialog_fired.append(True), dialog.dismiss()))

            _submit_password_change(page, base_url, _ORIG_PASSWORD, _NEW_PASSWORD)
            page.wait_for_url(f"{base_url}/", timeout=10000)

            assert dialog_fired == []
        finally:
            context.close()
            _reset_password("miriam", e2e_env)
