"""A worker takeover must preserve a pending passkey-removal response."""

import json
import uuid
from contextlib import suppress
from hashlib import sha256

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect

from ._helpers import wait_for_active_service_worker
from .test_mfa_session_revocation import (
    USERNAME,
    _cleanup,
    _confirm_sudo,
    _is_signed_in,
    _is_signed_out,
    _login_with_backup_code,
    _seed_totp_with_codes,
    _shell,
)
from .test_passkey_removal_session_revocation import _new_context, _passkey_count, _seed_passkey, _set_language

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("held_response", ["post", "redirect"])
def test_takeover_keeps_the_removal_navigation(base_url, browser, playwright, e2e_env, language, held_response):
    codes = _seed_totp_with_codes(e2e_env)
    _seed_passkey(e2e_env)
    previous_language = _set_language(e2e_env, language)
    context_a, page_a = _new_context(browser, language)
    # Select one explicit update for this test, including during English login.
    # This holds update messages; it does not wait for a worker-idle interval.
    context_a.add_init_script("""(() => {
        const send = ServiceWorker.prototype.postMessage;
        window.__sendWorkerMessage = send;
        window.__heldActivations = [];
        ServiceWorker.prototype.postMessage = function(message, ...rest) {
            if (message?.type === 'SKIP_WAITING') {
                window.__heldActivations.push(this);
                return;
            }
            return send.call(this, message, ...rest);
        };
    })();""")
    context_b, page_b = _new_context(browser, language)
    transport = playwright.request.new_context()
    held = []
    decisions = []
    order = []
    probe = f"passkey-takeover-{uuid.uuid4().hex}"

    def session_digest():
        values = [cookie["value"] for cookie in context_a.cookies() if cookie["name"] == "sessionid"]
        assert len(values) == 1, "the acting browser must have one session cookie"
        return sha256(values[0].encode()).hexdigest()

    try:
        _login_with_backup_code(page_a, base_url, codes[0])
        _login_with_backup_code(page_b, base_url, codes[1])
        assert _is_signed_in(page_b, base_url)
        page_a.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
        page_a.locator("[data-testid='passkey-delete-button']").click()
        _confirm_sudo(page_a, codes[2])
        page_a.goto(f"{base_url}/mfa/settings/", wait_until="domcontentloaded")
        # Establish the old controller so the test can force an actual update;
        # no worker-idle delay is used before the removal.
        wait_for_active_service_worker(page_a)
        control = context_a.new_page()
        control.goto(f"{base_url}/manifest.json", wait_until="domcontentloaded")
        page_a.evaluate(
            """probe => {
                navigator.serviceWorker.addEventListener('controllerchange', () => {
                    if (navigator.serviceWorker.controller?.scriptURL.includes(probe)) console.info(probe);
                });
                const observer = new MutationObserver(() => {
                    if (!document.getElementById('sw-update-toast')) return;
                    observer.disconnect();
                    console.info(probe + ':protected');
                });
                observer.observe(document.body, {childList: true, subtree: true});
                navigation.addEventListener('navigate', event => {
                    if (event.navigationType === 'reload') console.info(probe + ':reload');
                });
            }""",
            probe,
        )
        page_a.evaluate("async probe => navigator.serviceWorker.register('/sw.js?' + probe, {scope: '/'})", probe)
        page_a.wait_for_function(
            "probe => window.__heldActivations.some(worker => "
            "worker.scriptURL.includes(probe) && worker.state === 'installed')",
            arg=probe,
        )
        before = session_digest()
        target = page_a.locator("form:has([data-testid='passkey-delete-button'])").get_attribute("action")

        def observe(message):
            if message.text == probe:
                order.append("controllerchange")
            if message.text in (probe + ":protected", probe + ":reload"):
                decisions.append(message.text.removeprefix(probe + ":"))

        page_a.on("console", observe)

        def activate_waiting():
            control.evaluate(
                "async () => { const registration = await navigator.serviceWorker.getRegistration(); "
                "window.__sendWorkerMessage.call(registration.waiting, {type: 'SKIP_WAITING'}); }"
            )

        def hold_response(route):
            if route.request.method != "POST" or route.request.url != base_url + target:
                route.fallback()
                return
            # Keep Set-Cookie private until the response is delivered to the browser.
            response = transport.post(
                route.request.url,
                headers=route.request.all_headers(),
                data=route.request.post_data_buffer,
                max_redirects=0,
            )
            assert response.status == 302
            held.append((route, response))
            order.append("post-response-held")
            activate_waiting()

        if held_response == "post":
            context_a.route("**/*", hold_response)
        else:
            # Redirects can be fetched by either the page or its worker. Intercept
            # both CDP targets so the test cannot accidentally hold a later reload.
            page_cdp = context_a.new_cdp_session(page_a)
            context_id = page_cdp.send("Target.getTargetInfo")["targetInfo"]["browserContextId"]
            worker_cdp = browser.new_browser_cdp_session()
            worker_id = next(
                target["targetId"]
                for target in worker_cdp.send("Target.getTargets")["targetInfos"]
                if target["type"] == "service_worker"
                and target["browserContextId"] == context_id
                and target["url"] == base_url + "/sw.js"
            )
            session_id = worker_cdp.send("Target.attachToTarget", {"targetId": worker_id, "flatten": False})[
                "sessionId"
            ]
            command_id = 0

            def worker_command(method, params):
                nonlocal command_id
                command_id += 1
                worker_cdp.send(
                    "Target.sendMessageToTarget",
                    {
                        "sessionId": session_id,
                        "message": json.dumps({"id": command_id, "method": method, "params": params}),
                    },
                )

            def hold_redirect(event, send):
                if event["request"]["method"] != "GET" or event["request"]["url"] != base_url + "/mfa/settings/":
                    send("Fetch.continueResponse", {"requestId": event["requestId"]})
                    return
                assert not held, "hold exactly the first redirect response"
                assert event["responseStatusCode"] == 200
                assert session_digest() != before, "the redirect uses the successor cookie"
                event["responseOwner"] = "worker" if send is worker_command else "browser"
                held.append((event, send))
                order.append("redirect-response-held")
                activate_waiting()

            def worker_message(message):
                if message["sessionId"] != session_id:
                    return
                event = json.loads(message["message"])
                assert "error" not in event, event
                if event.get("method") == "Fetch.requestPaused":
                    hold_redirect(event["params"], worker_command)

            worker_cdp.on("Target.receivedMessageFromTarget", worker_message)
            worker_command(
                "Fetch.enable", {"patterns": [{"urlPattern": base_url + "/mfa/settings/", "requestStage": "Response"}]}
            )
            page_cdp.on("Fetch.requestPaused", lambda event: hold_redirect(event, page_cdp.send))
            page_cdp.send(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "resourceType": "Document", "requestStage": "Response"}]},
            )
        try:
            with (
                page_a.expect_console_message(lambda message: message.text == probe),
                page_a.expect_console_message(
                    lambda message: message.text in (probe + ":protected", probe + ":reload")
                ),
            ):
                page_a.evaluate("() => document.querySelector('[data-testid=passkey-delete-button]').click()")
        except PlaywrightTimeoutError as error:
            owner = held[0][0].get("responseOwner") if held and held_response == "redirect" else "browser"
            raise AssertionError(
                f"Takeover did not protect the {held_response} response: held={len(held)}, owner={owner}, order={order}"
            ) from error
        assert len(held) == 1
        assert order == [held_response + "-response-held", "controllerchange"]
        assert decisions == ["protected"], "a takeover must not replace the pending removal navigation"
        if held_response == "post":
            assert held[0][1].status == 302
            assert session_digest() == before, "the held response must not publish its successor cookie early"
            held[0][0].fulfill(response=held[0][1])
            context_a.unroute("**/*", hold_response)
        else:
            event, send = held[0]
            assert event["responseOwner"] == "browser", "MFA redirects must remain browser-owned"
            send("Fetch.continueResponse", {"requestId": event["requestId"]})
            page_cdp.send("Fetch.disable")
            worker_cdp.detach()
        expect(page_a.locator("[data-testid='passkey-empty']")).to_be_visible()
        assert session_digest() != before
        assert _passkey_count(e2e_env) == 0
        assert _is_signed_out(page_b, base_url)
        assert _is_signed_in(page_a, base_url)
    finally:
        for context in (context_a, context_b):
            with suppress(Exception):
                context.close()
        transport.dispose()
        _set_language(e2e_env, previous_language)
        _shell(
            e2e_env,
            "from django_otp_webauthn.models import WebAuthnCredential; "
            f"WebAuthnCredential.objects.filter(user__username='{USERNAME}').delete()",
        )
        _cleanup(e2e_env)
