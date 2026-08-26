"""E2E-Journey fuer den einrichtungsgebundenen Zugaenge-Hub (Refs #1871).

Aus dem manuellen Playwright-Durchlauf vom 2026-08-24 abgeleitet: Liste und
Create-Form wurden bei 1440x1000 visuell geprueft; Browserkonsole, Anlage,
Invite/Resend, Rollen-/Sicherheitsfelder, Lockout/Unlock und Audit-Attribution
waren gruen.
"""

from __future__ import annotations

import re
import subprocess
import sys
import uuid

import pytest
from playwright.sync_api import expect

from ._helpers import track_console_errors

pytestmark = pytest.mark.e2e

_DETAIL_RE = re.compile(r"/verwaltung/zugaenge/(\d+)/$")


def _shell(e2e_env, code: str) -> str:
    result = subprocess.run(
        [sys.executable, "src/manage.py", "shell", "-c", code],
        capture_output=True,
        text=True,
        env=e2e_env,
        check=True,
    )
    return result.stdout.strip().splitlines()[-1]


def test_admin_manages_invited_user_and_unlocks_account(authenticated_page, base_url, e2e_env):
    page = authenticated_page
    console_errors = track_console_errors(page)
    username = f"e2e_access_{uuid.uuid4().hex[:8]}"

    page.goto(f"{base_url}/verwaltung/zugaenge/", wait_until="domcontentloaded")
    expect(page.locator('[data-testid="user-access-list"]')).to_be_visible()
    expect(page.locator("#main-content")).not_to_contain_text("superadmin")

    # Der Einstieg in jede Mutation fuehrt zuerst durch Sudo. Danach bleiben
    # alle weiteren Schritte derselben Journey innerhalb des 15-Minuten-Fensters.
    page.locator('[data-testid="user-access-create-link"]').click()
    page.wait_for_url(lambda url: "/sudo/" in url, timeout=30000, wait_until="domcontentloaded")
    page.fill('input[name="password"]', "anlaufstelle2026")
    page.get_by_role("button", name="Bestätigen und fortfahren").click()
    page.wait_for_url(
        lambda url: url.endswith("/verwaltung/zugaenge/neu/"), timeout=30000, wait_until="domcontentloaded"
    )

    role_values = page.locator('select[name="role"] option').evaluate_all("options => options.map(o => o.value)")
    assert "super_admin" not in role_values
    page.fill('input[name="username"]', username)
    page.fill('input[name="email"]', f"{username}@example.org")
    page.fill('input[name="display_name"]', "E2E Zugang")
    page.select_option('select[name="role"]', "staff")
    page.check('input[name="mfa_required"]')
    page.check('input[name="can_confirm_deletion"]')
    page.locator('[data-testid="user-access-form"] button[type="submit"]').click()
    page.wait_for_url(_DETAIL_RE, timeout=30000, wait_until="domcontentloaded")
    match = _DETAIL_RE.search(page.url)
    assert match
    user_pk = match.group(1)

    expect(page.get_by_text("Zugang wurde angelegt und die Einladung versendet.")).to_be_visible()
    expect(page.locator('[data-testid="user-access-resend-invite"]')).to_be_visible()
    page.locator('[data-testid="user-access-resend-invite"]').click()
    expect(page.get_by_text("Einladung wurde erneut versendet.")).to_be_visible()

    page.select_option('select[name="role"]', "assistant")
    page.uncheck('input[name="mfa_required"]')
    page.uncheck('input[name="can_confirm_deletion"]')
    page.locator('[data-testid="user-access-form"] button[type="submit"]').click()
    expect(page.get_by_text("Zugang wurde gespeichert.")).to_be_visible()
    expect(page.locator('select[name="role"]')).to_have_value("assistant")
    expect(page.locator('input[name="mfa_required"]')).not_to_be_checked()
    expect(page.locator('input[name="can_confirm_deletion"]')).not_to_be_checked()

    lock_result = _shell(
        e2e_env,
        "from core.models import AuditLog, User; "
        f"u=User.objects.get(pk={user_pk}); "
        "[AuditLog.objects.create(facility=u.facility, user=u, action=AuditLog.Action.LOGIN_FAILED, "
        "detail={'username': u.username}) for _ in range(10)]; print('locked')",
    )
    assert lock_result == "locked"
    page.reload(wait_until="domcontentloaded")
    expect(page.locator('[data-testid="user-access-unlock"]')).to_be_visible()
    page.locator('[data-testid="user-access-unlock"]').click()
    expect(page.get_by_text("Account-Sperre wurde aufgehoben.")).to_be_visible()

    audit_result = _shell(
        e2e_env,
        "from core.models import AuditLog, User; "
        f"u=User.objects.get(pk={user_pk}); a=User.objects.get(username='admin'); "
        "qs=AuditLog.objects.filter(user=u); "
        "wanted={'user_created','user_invite_sent','user_role_changed','user_access_updated',"
        "'deletion_confirmer_changed','login_unlock'}; "
        "actions=set(qs.values_list('action', flat=True)); "
        "attributed=not qs.filter(action__in=wanted).exclude(actor=a).exists(); "
        "print('audit-ok' if wanted <= actions and attributed and "
        "qs.filter(action='user_invite_sent').count() == 2 else 'audit-failed')",
    )
    assert audit_result == "audit-ok"
    assert console_errors == []
