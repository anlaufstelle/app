"""Exercise browser-test timing helpers without starting an E2E server."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import Error as PlaywrightError

from tests.e2e import test_offline_android_journeys as android


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(android, "time", SimpleNamespace(time=lambda: now[0], monotonic=lambda: now[0], sleep=sleep))
    return now, sleeps


@pytest.mark.parametrize(
    "message",
    [
        "Page.evaluate: Execution context was destroyed, most likely because of a navigation",
        "Page.evaluate: Cannot find context with specified id",
    ],
)
def test_android_wait_rechecks_predicate_after_navigation(clock, message):
    predicate = Mock(side_effect=[PlaywrightError(message), {"ready": True}])

    assert android._wait_until(predicate, timeout_s=1, interval_s=0.25) == {"ready": True}
    assert predicate.call_count == 2
    assert clock[1] == [0.25]


@pytest.mark.parametrize(
    "error",
    [
        PlaywrightError("Page.evaluate: Target page, context or browser has been closed"),
        PlaywrightError("Page.evaluate: ReferenceError: unknownFunction is not defined"),
        RuntimeError("Execution context was destroyed"),
    ],
)
def test_android_wait_propagates_unrelated_errors(clock, error):
    predicate = Mock(side_effect=error)

    with pytest.raises(type(error), match=str(error)):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25)
    predicate.assert_called_once()
    assert clock[1] == []


def test_android_wait_keeps_original_deadline_during_context_loss(clock):
    predicate = Mock(
        side_effect=PlaywrightError("Execution context was destroyed, most likely because of a navigation")
    )

    with pytest.raises(AssertionError, match="Timeout.*navigation never completed"):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25, desc="navigation never completed")
    assert predicate.call_count == 4
    assert clock[0][0] == 1


def test_android_wait_times_out_on_false_predicate(clock):
    predicate = Mock(return_value=False)

    with pytest.raises(AssertionError, match="Timeout"):
        android._wait_until(predicate, timeout_s=1, interval_s=0.25)
    assert predicate.call_count == 4


def test_password_cancel_resolves_dialog_before_click_finishes(monkeypatch):
    from contextlib import contextmanager

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    from tests.e2e import test_password_change_unsynced_guard as password

    class Page:
        url = ""
        handler = None

        def goto(self, url, **kwargs):
            self.url = url

        def fill(self, *args):
            pass

        def once(self, event, handler):
            assert event == "dialog"
            self.handler = handler

        @contextmanager
        def expect_event(self, event, **kwargs):
            assert event == "dialog"
            yield SimpleNamespace(value=dialog)
            assert dialog.dismiss.called

        def click(self, selector):
            # The asynchronous count finishes before click's navigation barrier.
            if self.handler:
                self.handler(dialog)
            if not dialog.dismiss.called:
                raise PlaywrightTimeoutError("click is waiting for navigation behind an unresolved confirm dialog")

    dialog = SimpleNamespace(message="2 unsynced entries", dismiss=Mock())
    page = Page()
    contexts = []

    def new_context(**kwargs):
        context = SimpleNamespace(new_page=lambda: page, close=Mock())
        contexts.append(context)
        return context

    login = Mock()
    monkeypatch.setattr(password, "_login", login)
    seed = Mock()
    monkeypatch.setattr(password, "_seed_unsynced_events", seed)

    password.TestZZPasswordChangeUnsyncedGuard().test_unsynced_entries_show_confirm_with_count_and_cancel_keeps_password(
        SimpleNamespace(new_context=new_context), "http://testserver"
    )

    seed.assert_called_once_with(page, 2)
    dialog.dismiss.assert_called_once()
    assert login.call_count == 2
    for context in contexts:
        context.close.assert_called_once()


@pytest.mark.parametrize("delay", [0, 120])
def test_onboarding_second_factor_uses_device_window_after_boundary(monkeypatch, delay):
    import base64
    import io
    import time
    from contextlib import redirect_stdout

    from django_otp import oath
    from django_otp.oath import hotp

    from core.models import EncryptedTOTPDevice
    from tests.e2e import _helpers as helpers
    from tests.e2e import test_onboarding_journey as onboarding

    now = [3029.99]
    monkeypatch.setattr(time, "time", lambda: now[0])
    monkeypatch.setattr(oath, "time", lambda: now[0])
    device = EncryptedTOTPDevice(key="3132333435363738393031323334353637383930", confirmed=True)
    monkeypatch.setattr(device, "save", Mock())
    secret = base64.b32encode(device.bin_key).decode().rstrip("=")
    confirmation = onboarding._totp_code(secret)
    now[0] = 3030.01
    assert device.verify_token(confirmation)
    assert device.drift == -1
    assert device.last_t == 100
    now[0] += delay

    token_input = SimpleNamespace(fill=Mock())
    page = Mock()
    page.locator.return_value = token_input
    token_input.click = Mock()
    monkeypatch.setattr(onboarding, "expect", lambda locator: Mock())

    get_device = Mock(return_value=device)
    monkeypatch.setattr(EncryptedTOTPDevice.objects, "get", get_device)

    def shell(env, script):
        output = io.StringIO()
        with redirect_stdout(output):
            # Execute only the trusted helper snippet, with database lookup replaced (#2743).
            exec(script, {})  # noqa: S102
        return output.getvalue().splitlines()

    monkeypatch.setattr(helpers, "django_shell", shell)
    onboarding._verify_second_factor(page, "http://testserver", secret, {}, "invited-user")
    get_device.assert_called_once_with(user__username="invited-user", confirmed=True)

    token = token_input.fill.call_args.args[0]
    expected_counter = max(device.last_t + 1, int(now[0] / device.step) + device.drift)
    assert token == str(hotp(device.bin_key, expected_counter, digits=device.digits)).zfill(device.digits)
    assert device.verify_token(token), "The second login must accept the code after confirmation crossed a window"
