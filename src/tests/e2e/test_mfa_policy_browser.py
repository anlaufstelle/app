"""E2E: the shared MFA access decision as the browser sees it (Refs #2568).

``core.services.security.mfa_policy`` decides between access, setup and verify;
the middleware turns the step into a redirect. The existing specs show single
paths (setup flow, backup-code login, role redirect on the enforced server).
This module covers the remaining rules end to end:

* verify step: every protected URL (GET, POST, setup/settings views) leads to
  ``/mfa/verify/`` until a code is accepted; the accepted code opens exactly the
  session that entered it;
* exempt paths stay reachable without the second factor (health, service
  worker, manifest, account recovery, language switch, password change, logout)
  and the pending state does not survive a logout. ``/static/`` is not probed
  here: WhiteNoise answers before authentication and MFA, so a request there
  never reaches the MFA middleware and could not turn red;
* setup step: a deliberate per-user flag and the facility-wide flag confine the
  account to the setup page until a device is confirmed, then access opens;
* role enforcement (enforced server): immediate roles bypass nothing through
  direct URLs; grace roles keep access while the deadline runs, are confined
  once it has passed, and a missing deadline is started (not extended).

Every case leaves the seed accounts as it found them (no device, no flag, no
deadline, German as preferred language).
"""

import base64
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django_otp.oath import TOTP

from ._helpers import SEED_PASSWORD, django_shell

pytestmark = pytest.mark.e2e

PROTECTED_PATHS = ["/clients/", "/events/", "/cases/", "/"]
LOGOUT_BUTTON = 'form[action="/logout/"] button[type="submit"]'

_RESET_ACCOUNT = (
    "from core.models import User; "
    "from django_otp.plugins.otp_totp.models import TOTPDevice; "
    "from django_otp.plugins.otp_static.models import StaticDevice; "
    "u = User.objects.get(username={username!r}); "
    "TOTPDevice.objects.filter(user=u).delete(); "
    "StaticDevice.objects.filter(user=u).delete(); "
    "User.objects.filter(pk=u.pk).update(mfa_required=False, mfa_setup_deadline=None, preferred_language='de'); "
    "s = u.facility.settings; s.mfa_enforced_facility_wide = False; s.save(); "
    "print('RESET|ok')"
)
_CREATE_DEVICE = (
    "from core.models import User; "
    "from django_otp.plugins.otp_totp.models import TOTPDevice; "
    "u = User.objects.get(username={username!r}); "
    "TOTPDevice.objects.filter(user=u).delete(); "
    "d = TOTPDevice.objects.create(user=u, name='default', confirmed=True); "
    "print('KEY|' + d.key)"
)


def _reset_account(e2e_env, username):
    assert django_shell(e2e_env, _RESET_ACCOUNT.format(username=username))[-1] == "RESET|ok"


def _confirmed_device_key(e2e_env, username) -> bytes:
    lines = django_shell(e2e_env, _CREATE_DEVICE.format(username=username))
    return bytes.fromhex(next(ln for ln in lines if ln.startswith("KEY|"))[4:])


def _code(key: bytes) -> str:
    return f"{TOTP(key).token():06d}"


def _clear_throttle(e2e_env, username):
    """Lift the failed-attempt delay of the device so the next code is judged on its merits."""
    django_shell(
        e2e_env,
        "from core.models import User; "
        "from django_otp.plugins.otp_totp.models import TOTPDevice; "
        f"TOTPDevice.objects.filter(user=User.objects.get(username={username!r}))"
        ".update(throttling_failure_count=0, throttling_failure_timestamp=None)",
    )


def _wrong_code(key: bytes) -> str:
    return f"{(TOTP(key).token() + 1) % 1_000_000:06d}"


def _set_deadline(e2e_env, username, sign, delta):
    """Move the setup deadline of *username* relative to now (``sign`` is ``+`` or ``-``)."""
    django_shell(
        e2e_env,
        "from core.models import User; from django.utils import timezone; import datetime; "
        f"User.objects.filter(username={username!r}).update("
        f"mfa_setup_deadline=timezone.now() {sign} datetime.timedelta({delta}))",
    )


def _sign_in(browser, base_url, username):
    """Password sign-in in a fresh context; returns ``(page, context)`` after the redirect."""
    context = browser.new_context(locale="de-DE")
    page = context.new_page()
    page.set_default_timeout(30000)
    page.set_default_navigation_timeout(30000)
    page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
    page.fill("#id_username", username)
    page.fill("#id_password", SEED_PASSWORD)
    page.click('#login-form button[type="submit"]')
    page.wait_for_url(lambda url: "/login/" not in url, timeout=30000)
    return page, context


def _path(page) -> str:
    return urlsplit(page.url).path


def _goto_and_path(page, base_url, path):
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    return _path(page)


def _csrf_token(page) -> str:
    """The CSRF token of the context (cookie), also on pages without the meta tag."""
    return next(cookie["value"] for cookie in page.context.cookies() if cookie["name"] == "csrftoken")


def _complete_totp_setup(page, base_url):
    """Run the first-time setup on ``/mfa/setup/``: scan, confirm, acknowledge the codes."""
    page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
    secret = (page.locator("[data-testid='mfa-secret']").text_content() or "").strip()
    assert re.fullmatch(r"[A-Z2-7]+", secret), secret
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    page.locator("[data-testid='mfa-token-input']").fill(_code(key))
    page.locator("[data-testid='mfa-confirm-button']").click()
    page.wait_for_url(re.compile(r"/mfa/backup-codes/"))
    page.locator("[data-testid='backup-codes-confirm']").check()
    page.locator("[data-testid='backup-codes-continue']").click()
    page.wait_for_url(re.compile(r"/mfa/settings/"))


def _assert_open(page, base_url, paths=PROTECTED_PATHS):
    """Every path loads as itself (no MFA redirect) with a successful response."""
    for path in paths:
        response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
        assert response is not None and response.status == 200, (path, response and response.status)
        assert _path(page) == path, f"{path} redirected to {page.url}"


def _assert_confined(page, base_url, target, paths=PROTECTED_PATHS):
    """Every path ends on ``target`` (``/mfa/setup/`` or ``/mfa/verify/``)."""
    for path in paths:
        assert _goto_and_path(page, base_url, path) == target, f"{path} reached {page.url}"


def _assert_exempt_paths_reachable(page, base_url):
    """The non-interactive exempt paths answer for themselves, not with an MFA redirect.

    Only paths that run through the MFA middleware belong here (static files are
    served by WhiteNoise before it and would pass whatever the exemption list says).
    """
    for path, expected in (
        ("/health/", {200, 503}),
        ("/sw.js", {200}),
        ("/manifest.json", {200}),
        # The view itself refuses a signed-in session with 403; without the exemption the
        # middleware would answer 302 to the verify or setup page instead.
        ("/account/recovery/", {403}),
        ("/password-change/", {200}),
    ):
        response = page.request.get(f"{base_url}{path}", max_redirects=0)
        assert response.status in expected, (path, response.status, response.headers.get("location"))


def _switch_language_to_english(page, base_url):
    """The language switch is exempt: it answers with a redirect that is not an MFA step."""
    response = page.request.post(
        f"{base_url}/i18n/setlang/",
        form={"language": "en", "next": "/mfa/verify/"},
        headers={"X-CSRFToken": _csrf_token(page)},
        max_redirects=0,
    )
    assert response.status == 302, response.status
    assert "django_language=en" in response.headers.get("set-cookie", ""), response.headers


def _sign_out_from_mfa_page(page, base_url):
    page.locator(LOGOUT_BUTTON).click()
    page.wait_for_url(re.compile(r"/login/"))
    assert _goto_and_path(page, base_url, "/clients/") == "/login/"


class TestZZMfaVerifyStep:
    """A confirmed device with an unverified session: verify, never access."""

    USER = "markus"

    @pytest.fixture(autouse=True)
    def _device(self, e2e_env):
        _reset_account(e2e_env, self.USER)
        self.key = _confirmed_device_key(e2e_env, self.USER)
        yield
        _reset_account(e2e_env, self.USER)

    def test_protected_urls_lead_to_verify_until_the_code_is_accepted(self, base_url, browser, e2e_env):
        page, context = _sign_in(browser, base_url, self.USER)
        try:
            assert _path(page) == "/mfa/verify/"
            # GET targets keep their return path, the root does not.
            for path in ("/clients/", "/events/", "/cases/"):
                assert _goto_and_path(page, base_url, path) == "/mfa/verify/"
                assert parse_qs(urlsplit(page.url).query)["next"] == [path]
            assert _goto_and_path(page, base_url, "/") == "/mfa/verify/"
            assert "next" not in parse_qs(urlsplit(page.url).query)
            # The management views under the exempt /mfa/ prefix do not open a way around.
            _assert_confined(page, base_url, "/mfa/verify/", ["/mfa/setup/", "/mfa/settings/"])

            # A wrong code changes nothing.
            assert _goto_and_path(page, base_url, "/events/") == "/mfa/verify/"
            page.locator("[data-testid='mfa-token-input']").fill(_wrong_code(self.key))
            page.locator("[data-testid='mfa-verify-button']").click()
            page.wait_for_load_state("load")
            assert _path(page) == "/mfa/verify/"
            _assert_confined(page, base_url, "/mfa/verify/", ["/clients/"])

            # The accepted code opens the access and returns to the remembered target.
            _clear_throttle(e2e_env, self.USER)
            assert _goto_and_path(page, base_url, "/events/") == "/mfa/verify/"
            page.locator("[data-testid='mfa-token-input']").fill(_code(self.key))
            page.locator("[data-testid='mfa-verify-button']").click()
            page.wait_for_url(lambda url: urlsplit(url).path == "/events/")
            _assert_open(page, base_url)
        finally:
            context.close()

    def test_unverified_post_is_redirected_and_does_not_execute(self, base_url, browser, e2e_env):
        pseudonym = "MFA-Bypass-Probe-4711"
        page, context = _sign_in(browser, base_url, self.USER)
        try:
            assert _path(page) == "/mfa/verify/"
            response = page.request.post(
                f"{base_url}/clients/new/",
                form={"pseudonym": pseudonym, "contact_stage": "identified", "age_cluster": "unknown"},
                headers={"X-CSRFToken": _csrf_token(page)},
                max_redirects=0,
            )
            assert response.status == 302, response.status
            # POST targets are not remembered, only GET ones.
            assert response.headers["location"] == "/mfa/verify/"
            created = django_shell(
                e2e_env,
                "from core.models import Client; "
                f"print('COUNT|' + str(Client.objects.filter(pseudonym={pseudonym!r}).count()))",
            )
            assert created[-1] == "COUNT|0"
        finally:
            context.close()

    def test_exempt_paths_stay_reachable_and_the_pending_state_ends_with_logout(self, base_url, browser, e2e_env):
        page, context = _sign_in(browser, base_url, self.USER)
        try:
            assert _path(page) == "/mfa/verify/"
            _assert_exempt_paths_reachable(page, base_url)

            _switch_language_to_english(page, base_url)
            page.goto(f"{base_url}/mfa/verify/", wait_until="domcontentloaded")
            assert page.locator("html").get_attribute("lang") == "en"

            _sign_out_from_mfa_page(page, base_url)
            # A new sign-in starts unverified again: the earlier pending state left nothing behind.
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill("#id_username", self.USER)
            page.fill("#id_password", SEED_PASSWORD)
            page.click('#login-form button[type="submit"]')
            page.wait_for_url(lambda url: "/login/" not in url)
            assert _path(page) == "/mfa/verify/"
        finally:
            context.close()

    def test_verification_belongs_to_the_session_that_entered_the_code(self, base_url, browser, e2e_env):
        page_a, context_a = _sign_in(browser, base_url, self.USER)
        page_b, context_b = _sign_in(browser, base_url, self.USER)
        try:
            assert _path(page_a) == _path(page_b) == "/mfa/verify/"
            page_a.locator("[data-testid='mfa-token-input']").fill(_code(self.key))
            page_a.locator("[data-testid='mfa-verify-button']").click()
            page_a.wait_for_url(lambda url: "/mfa/verify/" not in url)
            _assert_open(page_a, base_url)
            # The second session of the same account is still unverified.
            _assert_confined(page_b, base_url, "/mfa/verify/")
            # ... and the first one stays open (no side effect of the second sign-in).
            _assert_open(page_a, base_url, ["/clients/"])
        finally:
            context_a.close()
            context_b.close()


class TestZZMfaSetupStep:
    """Deliberate enforcement (per-user flag, facility-wide flag) without a device: setup."""

    USER = "emma"

    @pytest.fixture(autouse=True)
    def _clean(self, e2e_env):
        _reset_account(e2e_env, self.USER)
        yield
        _reset_account(e2e_env, self.USER)

    def _confine_then_open_after_setup(self, base_url, browser):
        page, context = _sign_in(browser, base_url, self.USER)
        try:
            assert _path(page) == "/mfa/setup/"
            # Setup targets are never remembered (there is no verify step to come back from).
            _assert_confined(page, base_url, "/mfa/setup/")
            assert "next" not in parse_qs(urlsplit(page.url).query)
            _assert_exempt_paths_reachable(page, base_url)

            _switch_language_to_english(page, base_url)
            page.goto(f"{base_url}/mfa/setup/", wait_until="domcontentloaded")
            assert page.locator("html").get_attribute("lang") == "en"

            # Logout works from the setup state and the next sign-in is confined again.
            _sign_out_from_mfa_page(page, base_url)
            page.goto(f"{base_url}/login/", wait_until="domcontentloaded")
            page.fill("#id_username", self.USER)
            page.fill("#id_password", SEED_PASSWORD)
            page.click('#login-form button[type="submit"]')
            page.wait_for_url(lambda url: "/login/" not in url)
            assert _path(page) == "/mfa/setup/"

            # A confirmed device opens the access for this very session.
            _complete_totp_setup(page, base_url)
            _assert_open(page, base_url)
        finally:
            context.close()

    def test_account_flag_confines_to_setup_until_a_device_is_confirmed(self, base_url, browser, e2e_env):
        django_shell(
            e2e_env,
            f"from core.models import User; User.objects.filter(username={self.USER!r}).update(mfa_required=True)",
        )
        self._confine_then_open_after_setup(base_url, browser)

    def test_facility_wide_flag_confines_to_setup_until_a_device_is_confirmed(self, base_url, browser, e2e_env):
        django_shell(
            e2e_env,
            "from core.models import User; "
            f"s = User.objects.get(username={self.USER!r}).facility.settings; "
            "s.mfa_enforced_facility_wide = True; s.save()",
        )
        self._confine_then_open_after_setup(base_url, browser)


class TestZZMfaRoleEnforcement:
    """Role enforcement on the enforced server: immediate roles, grace roles."""

    @pytest.fixture(autouse=True)
    def _clean(self, e2e_env):
        for username in ("admin", "miriam"):
            _reset_account(e2e_env, username)
        yield
        for username in ("admin", "miriam"):
            _reset_account(e2e_env, username)

    def test_immediate_role_is_confined_through_every_url_until_a_device_is_confirmed(
        self, enforced_base_url, browser, e2e_env
    ):
        page, context = _sign_in(browser, enforced_base_url, "admin")
        try:
            assert _path(page) == "/mfa/setup/"
            _assert_confined(page, enforced_base_url, "/mfa/setup/", [*PROTECTED_PATHS, "/admin-mgmt/"])
            _complete_totp_setup(page, enforced_base_url)
            _assert_open(page, enforced_base_url)
        finally:
            context.close()

    def test_grace_role_with_a_running_deadline_keeps_access(self, enforced_base_url, browser, e2e_env):
        _set_deadline(e2e_env, "miriam", "+", "days=3")
        page, context = _sign_in(browser, enforced_base_url, "miriam")
        try:
            _assert_open(page, enforced_base_url)
        finally:
            context.close()

    def test_grace_role_with_an_expired_deadline_is_confined_to_setup(self, enforced_base_url, browser, e2e_env):
        _set_deadline(e2e_env, "miriam", "-", "minutes=1")
        page, context = _sign_in(browser, enforced_base_url, "miriam")
        try:
            assert _path(page) == "/mfa/setup/"
            _assert_confined(page, enforced_base_url, "/mfa/setup/")
        finally:
            context.close()

    def test_grace_role_without_a_deadline_has_a_started_period_afterwards(self, enforced_base_url, browser, e2e_env):
        """Browser outcome only: a missing deadline ends up as one started period.

        The first step cannot tell the sign-in stamp from the middleware stamp
        (the sign-in path itself is pinned in ``test_mfa_grace.py::TestLoginStampsDeadline``).
        The second step isolates the middleware: the deadline is cleared behind a running
        session, so only the next request can start it again, and only once.
        """
        read_deadline = (
            "from core.models import User; from django.utils import timezone; "
            "d = User.objects.get(username='miriam').mfa_setup_deadline; "
            "days = str((d - timezone.now()).days) if d else '-'; "
            "print('DEADLINE|' + (d.isoformat() if d else 'none') + '|' + days)"
        )
        page, context = _sign_in(browser, enforced_base_url, "miriam")
        try:
            # The sign-in starts the period ...
            _assert_open(page, enforced_base_url)
            assert django_shell(e2e_env, read_deadline)[-1].split("|")[1] != "none"

            # ... and a running session without one (cleared behind its back) gets it started
            # by the next request, once: further requests neither move nor extend it.
            django_shell(
                e2e_env,
                "from core.models import User; User.objects.filter(username='miriam').update(mfa_setup_deadline=None)",
            )
            assert django_shell(e2e_env, read_deadline)[-1] == "DEADLINE|none|-"
            _assert_open(page, enforced_base_url, ["/clients/"])
            stamped = django_shell(e2e_env, read_deadline)[-1]
            assert stamped.split("|")[1] != "none"
            assert stamped.split("|")[2] in {"29", "30"}, stamped
            _assert_open(page, enforced_base_url, ["/events/", "/cases/"])
            assert django_shell(e2e_env, read_deadline)[-1].split("|")[1] == stamped.split("|")[1]
        finally:
            context.close()
