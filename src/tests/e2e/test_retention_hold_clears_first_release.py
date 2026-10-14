"""E2E: A hold set from the event, person or case file discards the first release (#2590, DE).

Reproduction: A grants the first release of a deletion proposal; a lead sets a legal hold from
the record of the event, the person or the case; the first release is gone (database and
dashboard). After the hold is lifted the proposal runs the whole two-person flow again: A's first
release, A's rejected second attempt, B's final release. Database state is read after each step.
The scheduled run's report of missing release evidence is a command, not a browser flow, and is
covered by the unit suite.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from ._helpers import django_shell
from .test_retention_dashboard import (
    _APPROVER_A,
    _DE,
    _ensure_proposals,
    _open_dashboard,
    _proposal_state,
    _two_person_flow,
    approver_pool,  # noqa: F401  (fixture, used via usefixtures)
)

pytestmark = pytest.mark.e2e

HOLD_REASON = "Prüfauftrag laufendes Verfahren"


def _target_pk(e2e_env, proposal_pk: str, target: str) -> str:
    """Primary key of the event, its person or a case created around it (for the given hold target)."""
    script = (
        "from core.models import Case, Event, RetentionProposal; "
        f"p = RetentionProposal.objects.get(pk='{proposal_pk}'); "
        "e = Event.objects.get(pk=p.target_id); "
        f"kind = '{target}'; "
        "c = Case.objects.create(facility=e.facility, client=e.client, title='RET-E2E-Fall') "
        "if kind == 'case' else None; "
        "print('target=' + str({'event': e.pk, 'client': e.client_id, 'case': c.pk if c else ''}[kind])); "
        "Event.objects.filter(pk=e.pk).update(case=c) if c else None"
    )
    line = next(ln for ln in django_shell(e2e_env, script) if ln.startswith("target="))
    return line.removeprefix("target=")


def _cleared_audit_rows(e2e_env, event_pk: str) -> int:
    script = (
        "from core.models import AuditLog; "
        f"print('rows=' + str(AuditLog.objects.filter(target_id='{event_pk}', "
        "detail__category='retention_proposal_first_approval_cleared').count()))"
    )
    return int(next(ln for ln in django_shell(e2e_env, script) if ln.startswith("rows=")).removeprefix("rows="))


def _proposal_event(e2e_env, proposal_pk: str) -> str:
    script = (
        "from core.models import RetentionProposal; "
        f"print('event=' + str(RetentionProposal.objects.get(pk='{proposal_pk}').target_id))"
    )
    return next(ln for ln in django_shell(e2e_env, script) if ln.startswith("event=")).removeprefix("event=")


@pytest.mark.usefixtures("approver_pool")
@pytest.mark.parametrize("target", ["event", "client", "case"])
def test_hold_from_record_clears_first_release_and_needs_both_again(
    target, browser, base_url, e2e_env, _login_storage_state, _lead_storage_state
):
    proposal = _ensure_proposals(e2e_env, 1)[0]
    event_pk = _proposal_event(e2e_env, proposal)
    target_pk = _target_pk(e2e_env, proposal, target)
    context_a = browser.new_context(storage_state=_login_storage_state, locale="de-DE")
    context_b = browser.new_context(storage_state=_lead_storage_state, locale="de-DE")
    try:
        page_a, page_b = context_a.new_page(), context_b.new_page()
        page_a.set_default_timeout(30000)
        page_b.set_default_timeout(30000)

        # A grants the first release.
        _open_dashboard(page_a, base_url)
        card_a = page_a.locator(f"#proposal-{proposal}")
        card_a.get_by_role("button", name=_DE["first_button"]).click()
        expect(card_a.get_by_text(_DE["progress"])).to_be_visible(timeout=10000)
        assert _proposal_state(e2e_env, proposal) == ("pending", _APPROVER_A)

        # A lead sets the hold from the record page of the event, the person or the case.
        page_b.goto(f"{base_url}/{target}s/{target_pk}/legal-hold/", wait_until="domcontentloaded")
        page_b.get_by_label("Begründung").fill(HOLD_REASON)
        page_b.get_by_role("button", name="Legal Hold setzen").click()
        expect(page_b.get_by_text("Legal Hold wurde gesetzt.")).to_be_visible(timeout=10000)

        # The first release is discarded, with audit evidence; nothing is approved.
        status, first_by = _proposal_state(e2e_env, proposal)
        assert first_by == "", f"first release survived the hold: {(status, first_by)}"
        assert status != "approved", status
        assert _cleared_audit_rows(e2e_env, event_pk) == 1

        # Lifting the hold does not bring the first release back.
        page_b.goto(f"{base_url}/{target}s/{target_pk}/legal-hold/", wait_until="domcontentloaded")
        page_b.get_by_role("button", name="Aufheben").click()
        expect(page_b.get_by_text("Legal Hold wurde aufgehoben.")).to_be_visible(timeout=10000)
        assert _proposal_state(e2e_env, proposal)[1] == ""

        # The proposal needs A and B again: the first release is offered, not the second.
        _open_dashboard(page_a, base_url)
        card_a = page_a.locator(f"#proposal-{proposal}")
        expect(card_a).to_be_visible(timeout=10000)
        expect(card_a.get_by_text(_DE["progress"])).to_have_count(0)
        expect(card_a.get_by_role("button", name=_DE["second_button"])).to_have_count(0)

        # The whole flow runs again: A's first release (still pending, A recorded), A's second attempt
        # rejected with 409 and a message while the status stays pending, then B's release approves it.
        _two_person_flow(page_a, page_b, base_url, proposal, _DE, e2e_env)
    finally:
        context_a.close()
        context_b.close()
