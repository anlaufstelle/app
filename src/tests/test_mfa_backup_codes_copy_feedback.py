"""The backup-codes page offers an accessible copy-result channel and keeps its alternatives."""

import re

import pytest

pytestmark = pytest.mark.django_db


def _page(client, user):
    client.force_login(user)
    session = client.session
    session["mfa_backup_codes"] = ["aaaa-bbbb", "cccc-dddd"]
    session.save()
    response = client.get("/mfa/backup-codes/")
    assert response.status_code == 200
    return response.content.decode()


def _tag(html, testid):
    match = re.search(rf"<[a-z]+[^>]*data-testid=\"{testid}\"[^>]*>", html, re.DOTALL)
    assert match, testid
    return match.group(0)


def test_copy_result_regions_are_live_and_start_empty(client, staff_user):
    html = _page(client, staff_user)
    status = _tag(html, "backup-codes-copy-status")
    error = _tag(html, "backup-codes-copy-error")
    assert 'role="status"' in status and 'aria-live="polite"' in status
    assert 'role="alert"' in error
    # Regions are bound to component state and carry no server-rendered message.
    assert 'x-text="copyStatus"' in status and 'x-text="copyError"' in error
    assert re.search(r'data-testid="backup-codes-copy-status"[^>]*></p>', html, re.DOTALL)
    assert re.search(r'data-testid="backup-codes-copy-error"[^>]*></p>', html, re.DOTALL)


def test_translated_messages_are_handed_to_the_component(client, staff_user):
    html = _page(client, staff_user)
    root = re.search(r"<div[^>]*x-data=\"backupCodesAcknowledge\"[^>]*>", html, re.DOTALL).group(0)
    assert 'data-copy-success="' in root
    error = re.search(r'data-copy-error="([^"]+)"', root).group(1)
    # The failure text names the available alternatives.
    assert "herunter" in error and "drucke" in error and "von Hand" in error


def test_alternatives_and_confirmation_stay_available(client, staff_user):
    html = _page(client, staff_user)
    for testid in ("backup-codes-copy", "backup-codes-download", "backup-codes-confirm", "backup-codes-continue"):
        assert f'data-testid="{testid}"' in html
    assert '@click="printCodes"' in html
    # Continue stays gated by the acknowledgement only, never by the copy result.
    assert ':disabled="notConfirmed"' in html
    assert "copyStatus" not in _tag(html, "backup-codes-continue")
