"""L10 (Refs #1375) — Laufzeit-Assertion: Migrate-Connection umgeht RLS.

``check_db_roles`` prüft nur, DASS App-/Admin-Rolle mit den richtigen
Attributen in ``pg_roles`` existieren — nicht, dass die tatsächlich laufende
Migrate-Connection selbst als BYPASSRLS-Admin verbindet. ``docker-migrate.sh``
ruft daher zusätzlich :func:`current_connection_bypasses_rls`, damit ein
versehentlich als NOBYPASSRLS-App-Rolle laufender Migrate-Job fail-fast
abbricht (sonst übersehen Daten-Migrationen unter RLS Zeilen und
``normalize_db_ownership`` schlägt fehl).
"""

from __future__ import annotations

import pytest
from django.db import transaction

from core.management.commands.check_db_roles import current_connection_bypasses_rls
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)


@pytest.mark.django_db
def test_default_test_connection_bypasses_rls():
    """Die Test-/Migrate-Connection läuft als Superuser bzw. BYPASSRLS -> True."""
    assert current_connection_bypasses_rls() is True


@pytest.mark.django_db(transaction=True)
def test_non_bypass_role_is_detected(rls_test_role):  # noqa: F811
    """Unter einer NOSUPERUSER/NOBYPASSRLS-Rolle liefert die Assertion False —
    genau der Fehlkonfigurationsfall, den docker-migrate.sh fail-fast abbricht."""
    with transaction.atomic(), as_rls_role(rls_test_role, facility_id=""):
        assert current_connection_bypasses_rls() is False
