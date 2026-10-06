"""Guard for the deletion-request RLS probe in matrix D (Refs #2476).

The web probes of DEV-SEC-RLS-07 pass even with RLS switched off on
``core_deletionrequest``: the list and review views filter by facility on
their own. Only the SQL probe fails on a returned RLS gap, so it must be
mandatory, must pin role and table flags, and an unrunnable probe must count
as "not checked". The SQL statements are extracted from the committed matrix
and executed as written under a role without BYPASSRLS.
"""

from __future__ import annotations

import re

import pytest

from tests._matrix_text import MATRIX_D, case_section, matrix_case
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

# Reads files under docs/ — not available inside the mutation sandbox.
pytestmark = pytest.mark.architecture

RLS_DELETION_CASE = "#### DEV-SEC-RLS-07 "


class TestRlsDeletionProbeIsMandatory:
    def test_sql_probe_is_not_optional(self):
        block = matrix_case(MATRIX_D, RLS_DELETION_CASE)
        assert "optional" not in block.lower(), (
            "DEV-SEC-RLS-07: the RLS probe is the only step that fails when RLS on "
            "core_deletionrequest is gone; the web probes pass on the view filters alone."
        )

    def test_unrunnable_probe_is_not_passed(self):
        block = matrix_case(MATRIX_D, RLS_DELETION_CASE)
        assert "nicht geprüft" in case_section(block, "Erwartetes Ergebnis")

    @pytest.mark.parametrize(
        "needle",
        ["rolsuper", "rolbypassrls", "relforcerowsecurity", "app.is_super_admin"],
    )
    def test_probe_pins_role_flags_and_context(self, needle):
        steps = case_section(matrix_case(MATRIX_D, RLS_DELETION_CASE), "Schritte")
        assert needle in steps


def _rls_probe_statements() -> list[str]:
    steps = case_section(matrix_case(MATRIX_D, RLS_DELETION_CASE), "Schritte")
    return [stmt for stmt in re.findall(r"`([^`]+)`", steps) if stmt.lstrip().upper().startswith("SELECT ")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestRlsDeletionProbeRunsAsWritten:
    def test_documented_sql_separates_facilities(
        self,
        rls_test_role,  # noqa: F811
        facility,
        second_facility,
        admin_user,
    ):
        from core.models import Client
        from core.models.workitem import DeletionRequest

        person = Client.objects.create(
            facility=facility,
            pseudonym="Matrix-Probe",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=admin_user,
        )
        request_f1 = DeletionRequest.objects.create(
            facility=facility,
            target_type=DeletionRequest.TargetType.CLIENT,
            target_id=person.pk,
            requested_by=admin_user,
            reason="matrix probe",
        )
        placeholders = {
            "<F1-uuid>": str(facility.pk),
            "<F2-uuid>": str(second_facility.pk),
            "<F1-dr>": str(request_f1.pk),
        }
        statements = _rls_probe_statements()
        assert len(statements) >= 4, statements

        results = []
        with as_rls_role(rls_test_role, facility_id="") as cur:
            for statement in statements:
                for placeholder, value in placeholders.items():
                    statement = statement.replace(placeholder, value)
                assert "<" not in statement, statement
                cur.execute(statement.rstrip(";"))
                results.append((statement, cur.fetchone()))

        role_rows = [row for stmt, row in results if "pg_roles" in stmt]
        flag_rows = [row for stmt, row in results if "pg_class" in stmt]
        counts = [row[0] for stmt, row in results if stmt.startswith("SELECT count(*)")]
        assert role_rows == [(False, False)]
        assert flag_rows == [(True, True)]
        assert counts == [0, 1], results
