"""Guard: Admin-DB-Rollen-Karte des Compliance-Dashboards (Refs #2310).

Auf einer korrekt nach Anleitung installierten Instanz liegen die
Admin-Zugangsdaten bewusst NICHT im Web-Container (Secret-Grenze, Refs #1962:
``docker-compose.prod.yml`` gibt dem ``web``-Dienst eine explizite
Environment-Allowlist ohne ``POSTGRES_ADMIN_USER``). ``check_db_roles`` meldet
dort deshalb dauerhaft einen Konfigurations-Hinweis, und das Dashboard machte
daraus eine stehende gelbe Warnung „Admin-DB-Rolle — Admin-/Maintenance-Rolle
nicht konfiguriert." Die Rollenpruefung selbst laeuft im ``maintenance``-Dienst
(``manage.py check_db_roles``) und ist dort gruen.

Dieser Guard pinnt beide Seiten:

* Web-Runtime ohne Admin-Zugangsdaten => **neutraler** Status (``unknown``),
  Zaehler „Warnungen" bleibt bei 0.
* Fehlende/falsche Admin-Rolle, wo sie pruefbar ist => weiterhin ein echter
  Befund (``warning`` im Wartungs-Kontext, ``unknown``/``critical`` bei
  vorhandenen Zugangsdaten). Der Statusvertrag der uebrigen Checks bleibt
  unveraendert.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse

from core.services.compliance import ComplianceStatus, db_roles

# Wortlaut wie ``check_db_roles`` ihn erzeugt, wenn POSTGRES_ADMIN_USER fehlt.
_CONFIG_ERROR = (
    "POSTGRES_ADMIN_USER ist nicht gesetzt — Admin-/Maintenance-Rolle nicht prüfbar. "
    "Refs #902: Drei-Rollen-Modell in docker-compose.prod.yml."
)


def _app_role(*, actual_super: bool | None = False, actual_bypassrls: bool | None = False) -> db_roles.RoleCheck:
    return db_roles.RoleCheck(
        role="anlaufstelle_app",
        label="App",
        expected_super=False,
        expected_bypassrls=False,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


def _admin_role(*, actual_super: bool | None, actual_bypassrls: bool | None) -> db_roles.RoleCheck:
    return db_roles.RoleCheck(
        role="anlaufstelle_admin",
        label="Admin",
        expected_super=False,
        expected_bypassrls=True,
        actual_super=actual_super,
        actual_bypassrls=actual_bypassrls,
    )


def _set_process_role(monkeypatch, process_role: str | None) -> None:
    """Setzt (oder entfernt) ``ANLAUFSTELLE_PROCESS_ROLE`` fuer den Lauf."""
    monkeypatch.delenv("POSTGRES_ADMIN_USER", raising=False)
    if process_role is None:
        monkeypatch.delenv("ANLAUFSTELLE_PROCESS_ROLE", raising=False)
    else:
        monkeypatch.setenv("ANLAUFSTELLE_PROCESS_ROLE", process_role)


def _checks(monkeypatch, *, process_role, role_checks, config_errors):
    _set_process_role(monkeypatch, process_role)
    with patch.object(db_roles, "check_db_roles", return_value=(role_checks, config_errors)):
        return db_roles._db_role_checks()


def _card(checks, key: str):
    return next(c for c in checks if c.key == key)


# Database access: in the web runtime the card looks up the latest
# ``DB_ROLES_VERIFIED`` marker (Refs #2415); an empty log keeps it neutral.
@pytest.mark.django_db
class TestWebRuntimeWithoutAdminCredentials:
    """Der Bug aus Refs #2310: stehende Warnung auf korrekter Installation."""

    @pytest.mark.parametrize("process_role", [None, "runtime", "RUNTIME", " runtime "])
    def test_admin_card_is_neutral_not_a_warning(self, monkeypatch, process_role):
        checks = _checks(
            monkeypatch,
            process_role=process_role,
            role_checks=[_app_role()],
            config_errors=[_CONFIG_ERROR],
        )

        card = _card(checks, "db_role_admin_missing")
        assert card.status != ComplianceStatus.WARNING
        assert card.status == ComplianceStatus.UNKNOWN

    def test_admin_card_points_at_the_maintenance_service(self, monkeypatch):
        checks = _checks(
            monkeypatch,
            process_role=None,
            role_checks=[_app_role()],
            config_errors=[_CONFIG_ERROR],
        )

        card = _card(checks, "db_role_admin_missing")
        assert "Wartungsdienst" in card.message
        assert card.action_hint is not None
        assert "docker compose -f docker-compose.prod.yml run --rm maintenance check_db_roles" in card.action_hint
        # Der technische Grund bleibt im Detail sichtbar, nur eben nicht als Warnung.
        assert "POSTGRES_ADMIN_USER" in (card.detail or "")

    def test_remaining_db_checks_keep_their_status(self, monkeypatch):
        """Statusvertrag der uebrigen Checks unveraendert (Refs #2310)."""
        checks = _checks(
            monkeypatch,
            process_role=None,
            role_checks=[_app_role()],
            config_errors=[_CONFIG_ERROR],
        )

        assert _card(checks, "db_role_app_nosuperuser").status == ComplianceStatus.OK
        assert _card(checks, "db_role_app_nobypassrls").status == ComplianceStatus.OK


@pytest.mark.django_db
class TestDashboardWarningCounter:
    """Die Zaehlung „1 Warnung" im Dashboard-Header (Refs #2310)."""

    def test_dashboard_counts_no_warning_for_the_admin_card(self, client, super_admin_user, monkeypatch):
        _set_process_role(monkeypatch, None)
        client.force_login(super_admin_user)

        with (
            patch.object(
                db_roles,
                "check_db_roles",
                return_value=([_app_role()], [_CONFIG_ERROR]),
            ),
            patch("core.views.system.compliance.aggregate_checks", db_roles._db_role_checks),
        ):
            response = client.get(reverse("core:system_compliance"))

        assert response.status_code == 200
        assert response.context["summary"]["warning"] == 0
        assert response.context["worst_status"] != ComplianceStatus.WARNING.value
        content = response.content.decode("utf-8")
        assert 'data-testid="compliance-check-db_role_admin_missing" data-status="unknown"' in content


class TestRealFindingsStayVisible:
    """Regression: echte Befunde duerfen NICHT mit neutralisiert werden."""

    def test_maintenance_context_without_admin_config_stays_a_warning(self, monkeypatch):
        checks = _checks(
            monkeypatch,
            process_role="maintenance",
            role_checks=[_app_role()],
            config_errors=[_CONFIG_ERROR],
        )

        card = _card(checks, "db_role_admin_missing")
        assert card.status == ComplianceStatus.WARNING

    def test_admin_role_absent_although_credentials_exist_is_not_ok(self, monkeypatch):
        """Zugangsdaten da, Rolle fehlt in ``pg_roles`` => bestehender Vertrag."""
        checks = _checks(
            monkeypatch,
            process_role=None,
            role_checks=[_app_role(), _admin_role(actual_super=None, actual_bypassrls=None)],
            config_errors=[],
        )

        card = _card(checks, "db_role_admin")
        assert card.status == ComplianceStatus.UNKNOWN
        assert card.status != ComplianceStatus.OK
        assert not any(c.key == "db_role_admin_missing" for c in checks)

    def test_admin_role_with_wrong_attributes_stays_critical(self, monkeypatch):
        checks = _checks(
            monkeypatch,
            process_role=None,
            role_checks=[_app_role(), _admin_role(actual_super=False, actual_bypassrls=False)],
            config_errors=[],
        )

        assert _card(checks, "db_role_admin").status == ComplianceStatus.CRITICAL

    @pytest.mark.django_db  # web runtime: marker lookup (Refs #2415)
    def test_app_role_superuser_stays_critical(self, monkeypatch):
        checks = _checks(
            monkeypatch,
            process_role=None,
            role_checks=[_app_role(actual_super=True)],
            config_errors=[_CONFIG_ERROR],
        )

        assert _card(checks, "db_role_app_nosuperuser").status == ComplianceStatus.CRITICAL
