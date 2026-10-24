"""E2E: a device change or 2FA deactivation signs out the account's other sessions (Refs #2640).

User paths in a real browser, always with two independent browser contexts that
share one account (context A acts, context B is the "other device"):

1. Device change in A: B is signed out on its next request, A stays signed in
   -- also after a fresh sudo confirmation and after a second login of the same
   account elsewhere (context C).
2. Voluntary deactivation in A: same expectations for B and A.
3. Offline client B: work captured offline and not yet transmitted stays in the
   IndexedDB queue when the session is revoked and survives the login redirect
   and the next sign-in (never purged, never duplicated). Its replay to the
   server after the sign-in uses the current CSRF token and saves exactly once.

The account carries seeded TOTP devices while a test runs; the teardown removes
them. Logins and sudo confirmations use backup codes, so no TOTP time window is
consumed twice.
"""

import base64
import re
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from urllib.parse import urlsplit

import pytest
from django_otp.oath import TOTP

from ._helpers import SEED_PASSWORD
from ._helpers import wait_for_active_service_worker as _wait_for_active_service_worker

pytestmark = pytest.mark.e2e

USERNAME = "markus"  # no storage-state account in the E2E fixtures
OLD_KEY_HEX = "b" * 40  # known secret of the pre-existing device
OLD_SECRET = base64.b32encode(bytes.fromhex(OLD_KEY_HEX)).decode("ascii").rstrip("=")


def _shell(e2e_env, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "--no-imports", "-c", code],
        env=e2e_env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"shell failed: {result.stderr}"
    return result.stdout


def _seed_totp_with_codes(e2e_env) -> list[str]:
    """Confirmed TOTP device plus fresh backup codes; returns the codes."""
    stdout = _shell(
        e2e_env,
        "from core.models import User, EncryptedTOTPDevice; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        "from django_otp.plugins.otp_static.models import StaticDevice; "
        "from core.services.security import generate_backup_codes; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "TOTPDevice.objects.filter(user=u).delete(); "
        "StaticDevice.objects.filter(user=u).delete(); "
        f"EncryptedTOTPDevice.objects.create(user=u, name='existing', confirmed=True, key='{OLD_KEY_HEX}'); "
        "print('CODES|' + '|'.join(generate_backup_codes(u)))",
    )
    lines = [line for line in stdout.splitlines() if line.startswith("CODES|")]
    assert lines, f"no codes printed: {stdout}"
    return lines[-1].split("|")[1:]


def _cleanup(e2e_env) -> None:
    _shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        "from django_otp.plugins.otp_static.models import StaticDevice; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "TOTPDevice.objects.filter(user=u).delete(); "
        "StaticDevice.objects.filter(user=u).delete()",
    )


def _confirmed_device_count(e2e_env) -> int:
    stdout = _shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        f"u = User.objects.get(username='{USERNAME}'); "
        "print('N|%d' % TOTPDevice.objects.filter(user=u, confirmed=True).count())",
    )
    return int([line for line in stdout.splitlines() if line.startswith("N|")][-1].split("|")[1])


def _workitem_state(e2e_env, title: str) -> str:
    """'STANDALONE' | 'HASCLIENT' | 'MISSING' for the work item with this title."""
    stdout = _shell(
        e2e_env,
        "from core.models import WorkItem; "
        f"w = WorkItem.objects.filter(title='{title}', is_deleted=False).first(); "
        "print('STATE|' + ('MISSING' if w is None else ('STANDALONE' if w.client_id is None else 'HASCLIENT')))",
    )
    return [line for line in stdout.splitlines() if line.startswith("STATE|")][-1].split("|")[1]


def _totp_code(base32_secret: str, steps_ahead: int = 0) -> str:
    """``steps_ahead=1``: code of the next time window (inside the tolerance)."""
    padded = base32_secret + "=" * (-len(base32_secret) % 8)
    totp = TOTP(base64.b32decode(padded))
    totp.time = time.time() + steps_ahead * totp.step
    return f"{totp.token():06d}"


def _new_context(browser):
    context = browser.new_context(locale="de-DE", timezone_id="Europe/Berlin")
    page = context.new_page()
    page.set_default_timeout(30000)
    return context, page


def _login_with_backup_code(page, base_url, backup_code: str) -> None:
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", USERNAME)
    page.fill("#id_password", SEED_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=15000)
    page.locator("[data-testid='mfa-toggle-mode']").click()
    backup_input = page.locator("[data-testid='mfa-backup-input']")
    backup_input.wait_for(state="visible", timeout=10000)
    backup_input.fill(backup_code)
    page.locator("[data-testid='mfa-verify-button']").click()
    page.wait_for_url(lambda url: "/mfa/verify/" not in url and "/login/" not in url, timeout=15000)


def _login_with_totp(page, base_url, code: str) -> None:
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", USERNAME)
    page.fill("#id_password", SEED_PASSWORD)
    page.click("#login-form button[type=submit]")
    page.wait_for_url(re.compile(r"/mfa/verify/"), timeout=15000)
    page.locator("[data-testid='mfa-token-input']").fill(code)
    page.locator("[data-testid='mfa-verify-button']").click()
    page.wait_for_url(lambda url: "/mfa/verify/" not in url and "/login/" not in url, timeout=15000)


def _confirm_sudo(page, backup_code: str) -> None:
    page.locator("input[name='password']").wait_for(state="visible", timeout=10000)
    page.fill("input[name='password']", SEED_PASSWORD)
    page.fill("input[name='otp_token']", backup_code)
    page.locator("form:has(input[name='password']) button[type='submit']").click()
    page.wait_for_url(lambda url: "/sudo/" not in url, timeout=15000)


def _change_device(page, base_url, sudo_code: str) -> tuple[str, list[str]]:
    """Full device change in ``page``; returns the new secret and the new backup codes."""
    page.goto(f"{base_url}/mfa/change-device/", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r"/sudo/"), timeout=10000)
    _confirm_sudo(page, sudo_code)
    page.locator("[data-testid='mfa-qrcode']").wait_for(state="visible", timeout=10000)
    secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
    assert re.fullmatch(r"[A-Z2-7]+", secret), secret
    page.locator("[data-testid='mfa-token-input']").fill(_totp_code(secret))
    page.locator("[data-testid='mfa-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/backup-codes/"), timeout=10000)
    items = page.locator("[data-testid='backup-codes-list'] li")
    assert items.count() == 10
    new_codes = [(items.nth(i).inner_text() or "").strip() for i in range(items.count())]
    page.locator("[data-testid='backup-codes-confirm']").check()
    page.locator("[data-testid='backup-codes-continue']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=10000)
    return secret, new_codes


def _deactivate(page, base_url, sudo_code: str) -> None:
    page.goto(f"{base_url}/mfa/disable/", wait_until="domcontentloaded")
    page.wait_for_url(re.compile(r"/sudo/"), timeout=10000)
    _confirm_sudo(page, sudo_code)
    page.locator("[data-testid='mfa-disable-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"), timeout=10000)


def _is_signed_out(page, base_url) -> bool:
    """True when the next authenticated request lands on the login page."""
    page.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
    return urlsplit(page.url).path == "/login/"


def _is_signed_in(page, base_url) -> bool:
    page.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
    return urlsplit(page.url).path == "/mfa/settings/"


def _raw_queue_count(page):
    """Count queue rows through the raw IndexedDB API (works on any same-origin page)."""
    return page.evaluate(
        """async () => new Promise((resolve) => {
            const req = indexedDB.open('anlaufstelle-offline');
            req.onerror = () => resolve('open-error');
            req.onsuccess = () => {
                const db = req.result;
                try {
                    const tx = db.transaction('queue', 'readonly');
                    const count = tx.objectStore('queue').count();
                    count.onsuccess = () => { db.close(); resolve(count.result); };
                    count.onerror = () => { db.close(); resolve('count-error'); };
                } catch (e) { db.close(); resolve('tx-error: ' + e.message); }
            };
        })"""
    )


def _dispatch_online_and_await_sync(page):
    """Dispatch reconnect and await the real completion event of the offline sync run."""
    return page.evaluate(
        """async () => {
            const syncOrchestrator = window.syncOrchestrator;
            const store = window.offlineStore;
            if (!syncOrchestrator || !store) {
                return {error: 'sync-runtime-missing'};
            }
            const eventName = syncOrchestrator.SYNC_FINISHED_EVENT || 'offline-sync-finished';
            return await new Promise((resolve) => {
                let settled = false;
                const finish = async (timedOut, detail = null) => {
                    if (settled) return;
                    settled = true;
                    const rows = await store.listDecrypted('queue');
                    resolve({
                        timedOut,
                        detail,
                        queue: rows.map((row) => ({
                            url: row.url,
                            localStatus: row.localStatus,
                            attempts: row.attempts || 0,
                            lastError: row.lastError || '',
                        })),
                    });
                };
                const timer = setTimeout(() => { void finish(true); }, 30000);
                window.addEventListener(eventName, (event) => {
                    clearTimeout(timer);
                    void finish(false, event.detail || null);
                }, {once: true});
                window.dispatchEvent(new Event('online'));
            });
        }"""
    )


class TestZZSessionRevocationOnFactorChange:
    """ZZ prefix: the account carries seeded devices while the tests run."""

    def test_device_change_signs_out_other_session_and_keeps_own(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        context_a = context_b = context_c = None
        try:
            context_a, page_a = _new_context(browser)
            context_b, page_b = _new_context(browser)
            _login_with_backup_code(page_a, base_url, codes[0])
            _login_with_backup_code(page_b, base_url, codes[1])
            assert _is_signed_in(page_b, base_url), "precondition: B is signed in before the change"

            secret, new_codes = _change_device(page_a, base_url, codes[2])

            assert _is_signed_out(page_b, base_url), "B must be signed out on its next request"
            assert _is_signed_in(page_a, base_url), "A must stay signed in after its own change"

            # A keeps working after a new sudo confirmation ...
            page_a.goto(f"{base_url}/sudo/?next=/mfa/settings/", wait_until="domcontentloaded")
            _confirm_sudo(page_a, new_codes[0])
            assert _is_signed_in(page_a, base_url), "A must stay signed in after sudo"

            # ... and after a second login of the same account elsewhere.
            context_c, page_c = _new_context(browser)
            _login_with_totp(page_c, base_url, _totp_code(secret, steps_ahead=1))
            assert _is_signed_in(page_c, base_url)
            assert _is_signed_in(page_a, base_url), "A must stay signed in after a second login"
            assert _is_signed_out(page_b, base_url), "B must stay signed out"
        finally:
            for context in (context_a, context_b, context_c):
                if context is not None:
                    with suppress(Exception):
                        context.close()
            _cleanup(e2e_env)

    def test_deactivation_signs_out_other_session_and_keeps_own(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        context_a = context_b = None
        try:
            context_a, page_a = _new_context(browser)
            context_b, page_b = _new_context(browser)
            _login_with_backup_code(page_a, base_url, codes[0])
            _login_with_backup_code(page_b, base_url, codes[1])
            assert _is_signed_in(page_b, base_url), "precondition: B is signed in before the deactivation"

            _deactivate(page_a, base_url, codes[2])

            assert _confirmed_device_count(e2e_env) == 0, "the device must be gone after the deactivation"
            assert _is_signed_out(page_b, base_url), "B must be signed out on its next request"
            assert _is_signed_in(page_a, base_url), "A must stay signed in after its own deactivation"
        finally:
            for context in (context_a, context_b):
                if context is not None:
                    with suppress(Exception):
                        context.close()
            _cleanup(e2e_env)


def _capture_task_offline(page, base_url, title: str) -> None:
    """Fill the task form while offline; the task stays in the encrypted local queue."""
    page.goto(f"{base_url}/workitems/new/", wait_until="domcontentloaded")
    _wait_for_active_service_worker(page)
    page.context.set_offline(True)
    page.evaluate("window.dispatchEvent(new Event('offline'))")
    page.fill("input[name='title']", title)
    page.click("button.btn-primary[type=submit]")
    page.wait_for_function("() => document.body.innerText.includes('lokal verschlüsselt')", timeout=10000)
    assert _raw_queue_count(page) == 1, "the offline task must be queued locally"
    # Network is back, but no reconnect event: the replay has not run yet.
    page.context.set_offline(False)


def _revoke_via_device_change(browser, base_url, backup_codes, sudo_index) -> tuple[object, str]:
    context_a, page_a = _new_context(browser)
    _login_with_backup_code(page_a, base_url, backup_codes[sudo_index - 1])
    secret, _ = _change_device(page_a, base_url, backup_codes[sudo_index])
    return context_a, secret


class TestZZOfflineClientKeepsUnsentWorkAcrossRevocation:
    def test_unsent_work_is_retained_across_revocation_and_sign_in(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        title = f"Revocation-Task {uuid.uuid4().hex[:6]}"
        context_a = context_b = None
        try:
            context_b, page_b = _new_context(browser)
            _login_with_backup_code(page_b, base_url, codes[0])
            page_b.evaluate("async () => { await window.crypto_session.ready(); }")
            _capture_task_offline(page_b, base_url, title)
            assert _workitem_state(e2e_env, title) == "MISSING"

            context_a, secret = _revoke_via_device_change(browser, base_url, codes, 2)

            # B's next request is signed out; the queue is neither purged nor replayed.
            assert _is_signed_out(page_b, base_url), "B must be signed out on its next request"
            assert _raw_queue_count(page_b) == 1, "the unsent task must stay in IndexedDB after the revocation"
            assert _workitem_state(e2e_env, title) == "MISSING"

            # Signing in again: the work is still there -- transmitted exactly once or still queued.
            _login_with_totp(page_b, base_url, _totp_code(secret, steps_ahead=1))
            page_b.evaluate("async () => { await window.crypto_session.ready(); }")
            page_b.goto(f"{base_url}/", wait_until="domcontentloaded")
            _dispatch_online_and_await_sync(page_b)
            queued = _raw_queue_count(page_b)
            state = _workitem_state(e2e_env, title)
            assert (queued, state) in {(1, "MISSING"), (0, "STANDALONE")}, (queued, state)
        finally:
            if context_b is not None:
                with suppress(Exception):
                    page_b.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            for context in (context_a, context_b):
                if context is not None:
                    with suppress(Exception):
                        context.close()
            _cleanup(e2e_env)

    def test_unsent_work_is_replayed_after_sign_in(self, base_url, browser, e2e_env):
        codes = _seed_totp_with_codes(e2e_env)
        title = f"Revocation-Replay {uuid.uuid4().hex[:6]}"
        context_a = context_b = None
        try:
            context_b, page_b = _new_context(browser)
            _login_with_backup_code(page_b, base_url, codes[0])
            page_b.evaluate("async () => { await window.crypto_session.ready(); }")
            _capture_task_offline(page_b, base_url, title)

            context_a, secret = _revoke_via_device_change(browser, base_url, codes, 2)
            assert _is_signed_out(page_b, base_url)

            _login_with_totp(page_b, base_url, _totp_code(secret, steps_ahead=1))
            page_b.evaluate("async () => { await window.crypto_session.ready(); }")
            page_b.goto(f"{base_url}/", wait_until="domcontentloaded")
            sync = _dispatch_online_and_await_sync(page_b)
            assert not sync.get("error"), f"sync runtime missing after sign-in: {sync!r}"
            assert not sync["timedOut"], f"sync did not finish after sign-in: {sync!r}"
            assert sync["queue"] == [], f"queue not empty after the replay: {sync!r}"
            assert _workitem_state(e2e_env, title) == "STANDALONE"
        finally:
            if context_b is not None:
                with suppress(Exception):
                    page_b.evaluate("async () => { if (window.offlineStore) await window.offlineStore.purgeAll(); }")
            for context in (context_a, context_b):
                if context is not None:
                    with suppress(Exception):
                        context.close()
            _cleanup(e2e_env)
