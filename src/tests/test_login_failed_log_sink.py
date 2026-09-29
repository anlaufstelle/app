"""A failed login without a facility keeps the login name out of the server log.

``on_user_login_failed`` handles unknown login names and super admins (no
facility) in a branch of its own that also writes a server-log line. That line
is checked as delivered by both formatters configured in ``settings.LOGGING``
(text and JSON): neither may carry the entered login name. The audit entry
keeps it unchanged, and a known account is referenced by ``user_pk`` -- the
field the other server-log lines use instead of a login name.
"""

import json
import logging

import pytest
from django.contrib.auth.signals import user_login_failed
from django.test import RequestFactory

from core.models import AuditLog, User
from tests.test_log_sink_scrub import _rendered_sink_lines
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401

SIGNAL_LOGGER = "core.signals.audit"
#: Invented but realistic login names: short, close to a real name.
UNKNOWN_LOGIN_NAME = "a.mustermann"
SUPER_ADMIN_LOGIN_NAME = "j.beispiel"


@pytest.fixture
def core_caplog(caplog):
    """``core`` does not propagate (``settings.LOGGING``) -- capture it directly."""
    core_logger = logging.getLogger("core")
    core_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger="core")
    try:
        yield caplog
    finally:
        core_logger.removeHandler(caplog.handler)


def _fail_login(username):
    request = RequestFactory().post("/login/", REMOTE_ADDR="203.0.113.20")
    user_login_failed.send(sender=User, credentials={"username": username}, request=request)


def _signal_record(caplog):
    records = [record for record in caplog.records if record.name == SIGNAL_LOGGER]
    assert len(records) == 1, f"expected one line from the failed-login handler, got {len(records)}"
    return records[0]


def _assert_login_name_not_in_sink(record, login_name):
    """Neither the text nor the JSON line carries the login name."""
    leaks = [line for line in _rendered_sink_lines(record) if login_name in line]
    assert not leaks, "login name in the server log:\n" + "\n".join(leaks)


def _assert_line_is_diagnosable(record):
    verbose_line, json_line = _rendered_sink_lines(record)
    assert "INFO" in verbose_line
    payload = json.loads(json_line)
    assert payload["logger"] == SIGNAL_LOGGER
    assert payload["level"] == "INFO"


@pytest.mark.django_db
class TestFailedLoginWithoutFacilityLogSink:
    def test_unknown_login_name_is_not_in_the_server_log(self, core_caplog):
        _fail_login(UNKNOWN_LOGIN_NAME)

        record = _signal_record(core_caplog)
        _assert_login_name_not_in_sink(record, UNKNOWN_LOGIN_NAME)
        _assert_line_is_diagnosable(record)
        assert getattr(record, "user_pk", None) is None

    def test_super_admin_login_name_is_not_in_the_server_log(self, core_caplog):
        admin = User.objects.create_user(username=SUPER_ADMIN_LOGIN_NAME, role=User.Role.SUPER_ADMIN, facility=None)

        _fail_login(SUPER_ADMIN_LOGIN_NAME)

        record = _signal_record(core_caplog)
        _assert_login_name_not_in_sink(record, SUPER_ADMIN_LOGIN_NAME)
        _assert_line_is_diagnosable(record)
        assert record.user_pk == admin.pk
        for line in _rendered_sink_lines(record):
            assert str(admin.pk) in line

    def test_audit_entry_keeps_the_login_name(self, core_caplog):
        _fail_login(UNKNOWN_LOGIN_NAME)

        entry = AuditLog.objects.get(action=AuditLog.Action.LOGIN_FAILED)
        assert entry.facility_id is None
        assert entry.user_id is None
        assert entry.ip_address == "203.0.113.20"
        assert entry.detail == {"message": "Fehlgeschlagener Login-Versuch", "username": UNKNOWN_LOGIN_NAME}

    def test_under_row_level_security_without_a_session(self, rls_test_role, core_caplog):  # noqa: F811
        """No signed-in session: the handler writes into an RLS table as the app role."""
        with as_rls_role(rls_test_role, facility_id=""):
            _fail_login(UNKNOWN_LOGIN_NAME)

        record = _signal_record(core_caplog)
        _assert_login_name_not_in_sink(record, UNKNOWN_LOGIN_NAME)
        entry = AuditLog.objects.get(action=AuditLog.Action.LOGIN_FAILED)
        assert entry.facility_id is None
        assert entry.detail["username"] == UNKNOWN_LOGIN_NAME
