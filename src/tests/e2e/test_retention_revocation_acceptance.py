"""E2E: Four-eyes release when the first approver loses the right in between (#2590, DE).

Reproduction: A grants the first release; the "Löschbestätigung" right of A is withdrawn
in the database; B tries the second release (refused, still pending); the right is
restored; B finalises. Database state is read before and after each step.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from .test_retention_dashboard import (
    _APPROVER_A,
    _DE,
    _badge,
    _ensure_proposals,
    _open_dashboard,
    _proposal_state,
    approver_pool,  # noqa: F401  (fixture, used via usefixtures)
)

pytestmark = pytest.mark.e2e


def _set_right(e2e_env, username: str, flag: bool) -> None:
    script = (
        "from core.models import User; "
        f"print('n=' + str(User.objects.filter(username='{username}').update(can_confirm_deletion={flag})))"
    )
    assert django_shell(e2e_env, script)[-1] == "n=1"


@pytest.mark.usefixtures("approver_pool")
def test_revoked_first_approver_blocks_second_release_until_restored(
    browser, base_url, e2e_env, _login_storage_state, _lead_storage_state
):
    pk = _ensure_proposals(e2e_env, 1)[0]
    context_a = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    context_b = browser.new_context(storage_state=_lead_storage_state, locale="de-DE")
    try:
        page_a, page_b = context_a.new_page(), context_b.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)

        _open_dashboard(page_a, base_url)
        card_a = page_a.locator(f"#proposal-{pk}")
        card_a.get_by_role("button", name=_DE["first_button"]).click()
        expect(card_a.get_by_text(_DE["progress"])).to_be_visible(timeout=10000)
        assert _proposal_state(e2e_env, pk) == ("pending", _APPROVER_A)

        # Withdraw the right of A between the two releases.
        _set_right(e2e_env, _APPROVER_A, False)
        _open_dashboard(page_b, base_url)
        card_b = page_b.locator(f"#proposal-{pk}")
        expect(card_b).to_be_visible(timeout=10000)
        button = card_b.get_by_role("button", name=_DE["second_button"])
        expect(button).to_be_visible(timeout=10000)
        button.click()
        page_b.wait_for_timeout(1000)
        status = _proposal_state(e2e_env, pk)[0]
        assert status == "pending", f"second release finalised although the first approver lost the right: {status}"
        page_b.reload(wait_until="domcontentloaded")
        expect(_badge(page_b.locator(f"#proposal-{pk}"), _DE["released"])).to_have_count(0)

        # Right restored: B can now release for good.
        _set_right(e2e_env, _APPROVER_A, True)
        page_b.reload(wait_until="domcontentloaded")
        card_b = page_b.locator(f"#proposal-{pk}")
        card_b.get_by_role("button", name=_DE["second_button"]).click()
        expect(_badge(card_b, _DE["released"])).to_have_count(1, timeout=10000)
        assert _proposal_state(e2e_env, pk)[0] == "approved"
    finally:
        context_a.close()
        context_b.close()
