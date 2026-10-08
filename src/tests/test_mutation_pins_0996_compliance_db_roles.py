"""Pins for the admin role card and its maintenance marker (``core.services.compliance.db_roles``).

Source: mutation delta of the release-0.99.6 checkpoint, 26 mutants.
Groups:

* ``_db_role_marker_check`` (21) — every card built from a marker keeps the
  stable key ``db_role_admin``, the category ``Datenbank`` and its message;
  the incomplete card keeps its credentials hint; "Geprüft am" shows the time
  stored in the marker, not the current time; an empty or unreadable problem
  list shows the dash instead of an empty list or a ``TypeError``.
* ``_latest_db_roles_marker`` (2) — the looked-up marker already carries
  ``timestamp`` and ``detail``: formatting the card from it runs no SQL; how
  the lookup itself queries is left open.
* ``record_db_roles_marker`` (3) — without RLS bypass only an ``ok`` result is
  skipped while a ``problems`` result is still written; the marker carries the
  ``DbRoleCheck`` target type.

None of these tests uses the ``rls_test_role`` fixture: the missing bypass is
simulated by patching ``has_rls_bypass_context``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import AuditLog
from core.services import ops_thresholds
from core.services.audit import audit_event
from core.services.compliance import ComplianceStatus, db_roles

pytestmark = pytest.mark.django_db

_KEY = "db_role_admin"
_CATEGORY = "Datenbank"


class _StandInEntry:
    """Marker-shaped row with a chosen timestamp and detail, no audit chain insert involved."""

    def __init__(self, timestamp, detail):
        self.timestamp = timestamp
        self.detail = detail


@pytest.fixture(autouse=True)
def _web_runtime(monkeypatch):
    """Web process without admin credentials: the card is read from the marker."""
    monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
    monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)


def _write_marker(result, problems):
    return audit_event(
        AuditLog.Action.DB_ROLES_VERIFIED,
        user=None,
        facility=None,
        target_type="DbRoleCheck",
        detail={"result": result, "problems": list(problems)},
    )


def _admin_card():
    cards = [c for c in db_roles._db_role_checks() if c.label == "Admin-DB-Rolle"]
    assert len(cards) == 1
    return cards[0]


def _card_for(entry):
    with patch.object(db_roles, "_latest_db_roles_marker", return_value=entry):
        return _admin_card()


def _formatted(timestamp):
    return f"{timezone.localtime(timestamp):%Y-%m-%d %H:%M}"


def _role_checks(*, app_bypassrls):
    return [
        db_roles.RoleCheck(
            role="app_role_p0996",
            label="App",
            expected_super=False,
            expected_bypassrls=False,
            actual_super=False,
            actual_bypassrls=app_bypassrls,
        ),
        db_roles.RoleCheck(
            role="admin_role_p0996",
            label="Admin",
            expected_super=False,
            expected_bypassrls=True,
            actual_super=False,
            actual_bypassrls=True,
        ),
    ]


def _markers():
    return list(AuditLog.objects.filter(action=AuditLog.Action.DB_ROLES_VERIFIED))


class TestFreshOkMarkerCard:
    def test_green_card_is_grouped_under_database_and_names_the_checked_roles(self):
        """A fresh ``ok`` marker gives a green card in the ``Datenbank`` group with its status message.

        Kills ``x__db_role_marker_check__mutmut_41`` (category ``None``) and
        ``x__db_role_marker_check__mutmut_43`` (message ``None``).
        """
        _write_marker("ok", [])

        card = _admin_card()

        assert card.status == ComplianceStatus.OK
        assert card.category == _CATEGORY
        assert card.message == _("Admin-Rolle korrekt (NOSUPERUSER, BYPASSRLS), App-Rolle und Ownership ebenfalls.")


class TestStaleOkMarkerCard:
    def test_stale_card_keeps_key_category_threshold_message_and_check_time(self):
        """A stale ``ok`` marker gives a warning with the stable key, the threshold and the stored check time.

        The stand-in marker lies past the threshold, so "Geprüft am" must show
        its own time, not the current one. Kills
        ``x__db_role_marker_check__mutmut_57``/``_71``/``_72`` (key),
        ``x__db_role_marker_check__mutmut_59`` (category),
        ``x__db_role_marker_check__mutmut_61`` (message) and
        ``x__db_role_marker_check__mutmut_14`` (current time instead of the marker time).
        """
        checked_at = timezone.now() - timedelta(days=ops_thresholds.DB_ROLES_CHECK_WARN_DAYS + 5)

        card = _card_for(_StandInEntry(checked_at, {"result": "ok", "problems": []}))

        assert card.status == ComplianceStatus.WARNING
        assert card.key == _KEY
        assert card.category == _CATEGORY
        assert card.message == _("Letzte Rollenprüfung ist älter als %(days)s Tage.") % {
            "days": ops_thresholds.DB_ROLES_CHECK_WARN_DAYS
        }
        assert card.detail == _("Geprüft am %(checked_at)s mit manage.py check_db_roles im Wartungsdienst.") % {
            "checked_at": _formatted(checked_at)
        }


class TestIncompleteMarkerCard:
    def test_incomplete_card_keeps_key_category_message_and_credentials_hint(self):
        """An ``incomplete`` marker gives a warning with key, group, its own message and the credentials hint.

        Kills ``x__db_role_marker_check__mutmut_82``/``_96``/``_97`` (key),
        ``x__db_role_marker_check__mutmut_84`` (category),
        ``x__db_role_marker_check__mutmut_86`` (message) and
        ``x__db_role_marker_check__mutmut_88``/``_95`` (hint dropped to ``None``).
        """
        _write_marker("incomplete", ["admin_role_not_configured"])

        card = _admin_card()

        assert card.status == ComplianceStatus.WARNING
        assert card.key == _KEY
        assert card.category == _CATEGORY
        assert card.message == _("Rollenprüfung unvollständig: nicht jede Rolle ist konfiguriert.")
        assert card.action_hint == _(
            "POSTGRES_ADMIN_USER und POSTGRES_ADMIN_PASSWORD in der .env setzen "
            "(Drei-Rollen-Modell, docs/ops-runbook.md § 9)."
        )

    def test_incomplete_marker_without_codes_lists_the_dash(self):
        """An ``incomplete`` marker with an empty code list shows ``Befunde: —``, not an empty list.

        Kills ``x__db_role_marker_check__mutmut_26`` (``or`` joins the empty list).
        """
        _write_marker("incomplete", [])

        card = _admin_card()

        assert card.status == ComplianceStatus.WARNING
        assert card.detail.endswith("Befunde: —")


class TestProblemMarkerCard:
    def test_critical_card_keeps_key_category_and_message(self):
        """A ``problems`` marker gives a critical card with the stable key, the group and its message.

        Kills ``x__db_role_marker_check__mutmut_118``/``_132``/``_133`` (key),
        ``x__db_role_marker_check__mutmut_120`` (category) and
        ``x__db_role_marker_check__mutmut_122`` (message).
        """
        _write_marker("problems", ["app_role_bypassrls"])

        card = _admin_card()

        assert card.status == ComplianceStatus.CRITICAL
        assert card.key == _KEY
        assert card.category == _CATEGORY
        assert card.message == _("Letzte Rollenprüfung meldet Befunde.")

    def test_unreadable_codes_show_the_dash_instead_of_failing(self):
        """Non-string problem codes give a critical card ending in ``Befunde: —``; nothing raises.

        Kills ``x__db_role_marker_check__mutmut_26`` (``or`` joins ``[1]`` and raises ``TypeError``).
        """
        card = _card_for(_StandInEntry(timezone.now(), {"result": "problems", "problems": [1]}))

        assert card.status == ComplianceStatus.CRITICAL
        assert card.detail.endswith("Befunde: —")


class TestLatestMarkerLoadsCardFields:
    def test_formatting_the_looked_up_marker_runs_no_sql(self):
        """Formatting the card from the looked-up marker runs no SQL; status and check time come from the marker.

        The SQL guard is attached only around the formatting step, after the
        lookup has returned, so any lookup that loads ``timestamp`` and
        ``detail`` passes. Kills ``x__latest_db_roles_marker__mutmut_3``
        (``timestamp`` deferred and reloaded while formatting) and
        ``x__latest_db_roles_marker__mutmut_4`` (``detail`` deferred and
        reloaded while formatting).
        """
        entry = _write_marker("ok", [])
        sql_while_formatting = []
        format_card = db_roles._db_role_marker_check

        def record_sql(execute, sql, params, many, context):
            sql_while_formatting.append(sql)
            return execute(sql, params, many, context)

        def format_without_sql(marker):
            with connection.execute_wrapper(record_sql):
                return format_card(marker)

        with patch.object(db_roles, "_db_role_marker_check", side_effect=format_without_sql):
            card = _admin_card()

        assert card.status == ComplianceStatus.OK
        assert _formatted(entry.timestamp) in card.detail
        assert sql_while_formatting == []


class TestRecordMarker:
    def test_without_bypass_only_an_ok_result_is_skipped(self, monkeypatch):
        """Without RLS bypass an ``ok`` result writes nothing, a ``problems`` result is still written.

        Kills ``x_record_db_roles_marker__mutmut_11`` (``!=`` would write the
        green marker and drop the finding).
        """
        monkeypatch.setattr("core.services.system.has_rls_bypass_context", lambda: False)

        skipped = db_roles.record_db_roles_marker(_role_checks(app_bypassrls=False), [])

        assert skipped
        assert _markers() == []

        written = db_roles.record_db_roles_marker(_role_checks(app_bypassrls=True), [])

        assert written is None
        markers = _markers()
        assert len(markers) == 1
        assert markers[0].detail["result"] == db_roles.DB_ROLES_RESULT_PROBLEMS

    def test_marker_carries_the_role_check_target_type(self):
        """The written marker has target type ``DbRoleCheck``, not an empty one.

        Kills ``x_record_db_roles_marker__mutmut_19`` (``None``) and
        ``x_record_db_roles_marker__mutmut_24`` (argument dropped).
        """
        assert db_roles.record_db_roles_marker(_role_checks(app_bypassrls=True), []) is None

        markers = _markers()
        assert len(markers) == 1
        assert markers[0].target_type == "DbRoleCheck"
