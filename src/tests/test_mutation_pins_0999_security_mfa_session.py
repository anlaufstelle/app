"""Pins for message rollback and successor-session verification.

Source: mutation delta of the release-0.99.9 checkpoint, 7 mutants.
Groups: message snapshots (6), incomplete successor storage (1).
"""

import pytest
from django.contrib.messages import INFO, SUCCESS
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.http import HttpResponse

from core.services.security.mfa_session import mfa_completion_session

pytestmark = pytest.mark.django_db


@pytest.fixture
def ceremony_request(rf, staff_user):
    request = rf.post("/mfa/backup-codes/")
    request.user = staff_user
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    return request


def test_failed_completion_restores_messages_and_request_identity(ceremony_request):
    """Rollback restores fallback children, queued messages and original request identity.

    x__message_snapshot__mutmut_3, x__message_snapshot__mutmut_9,
    x__message_snapshot__mutmut_10, x__message_snapshot__mutmut_11,
    x__message_snapshot__mutmut_12, x__message_snapshot__mutmut_13.
    """
    request = ceremony_request
    storages = [request._messages, *request._messages.storages]
    for storage in storages:
        storage.add(INFO, "Existing message")
    failure = RuntimeError("Ceremony failed")
    original_session = request.session

    with pytest.raises(RuntimeError) as error, mfa_completion_session(request, HttpResponse()):
        for storage in storages:
            storage.add(SUCCESS, "Uncommitted message")
            storage.used = True
        raise failure

    assert error.value is failure
    assert request.session is original_session
    replacement = SessionStore()
    request.session = replacement
    for storage in storages:
        assert storage.request is request
        assert storage.request.session is replacement
        assert storage.used is False
        assert [str(message) for message in storage] == ["Existing message"]
