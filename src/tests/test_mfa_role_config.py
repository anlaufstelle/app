"""Konfigurierbare, fail-loud-validierte MFA-Enforcement-Rollen-Sets (Refs #1639).

Die frueher hartkodierten Modul-Konstanten ``MFA_ENFORCED_ROLES_IMMEDIATE`` /
``MFA_ENFORCED_ROLES_GRACE`` (in ``core.models.user``) und das LEAD-Einzelflag
``MFA_ENFORCE_INCLUDE_LEAD`` sind durch zwei per Env ueberschreibbare Settings
ersetzt. LEAD ist jetzt per Default pflichtig (Grace-Set). Unbekannte
Rollennamen oder eine Ueberschneidung beider Mengen brechen den Start
fail-closed ab.
"""

import pytest
from django.core.exceptions import ImproperlyConfigured

from anlaufstelle.settings._mfa_roles import VALID_MFA_ROLES, parse_enforced_role_sets


class TestValidRolesDriftGuard:
    def test_valid_roles_match_user_role_values(self):
        """``VALID_MFA_ROLES`` ist gegen ``User.Role`` eingefroren — ein neuer
        Rollenwert im Modell ohne Nachzug hier faellt sofort auf."""
        from core.models.user import User

        assert set(User.Role.values) == VALID_MFA_ROLES


class TestParseEnforcedRoleSets:
    def test_defaults_lead_is_in_grace_not_immediate(self):
        immediate, grace = parse_enforced_role_sets("super_admin,facility_admin", "lead,staff,assistant")
        assert immediate == ("super_admin", "facility_admin")
        assert grace == ("lead", "staff", "assistant")
        # LEAD ist per Default pflichtig, aber grace-behaftet (Refs #1639).
        assert "lead" in grace
        assert "lead" not in immediate

    def test_whitespace_is_stripped(self):
        immediate, grace = parse_enforced_role_sets(" super_admin , facility_admin ", "  lead ,staff, assistant ")
        assert immediate == ("super_admin", "facility_admin")
        assert grace == ("lead", "staff", "assistant")

    def test_empty_strings_yield_empty_sets(self):
        """Leere Listen sind erlaubt (bewusstes Abschalten) und bedeuten genau
        das: keine Rolle wird ueber diese Ebene erzwungen."""
        immediate, grace = parse_enforced_role_sets("", "")
        assert immediate == ()
        assert grace == ()

    def test_env_override_moves_lead_to_immediate(self):
        immediate, grace = parse_enforced_role_sets("super_admin,facility_admin,lead", "staff,assistant")
        assert "lead" in immediate
        assert "lead" not in grace

    def test_unknown_role_in_immediate_raises(self):
        with pytest.raises(ImproperlyConfigured) as exc:
            parse_enforced_role_sets("super_admin,root", "staff")
        assert "MFA_ENFORCED_ROLES_IMMEDIATE" in str(exc.value)
        assert "root" in str(exc.value)

    def test_unknown_role_in_grace_raises(self):
        with pytest.raises(ImproperlyConfigured) as exc:
            parse_enforced_role_sets("super_admin", "staff,teamleiter")
        assert "MFA_ENFORCED_ROLES_GRACE" in str(exc.value)
        assert "teamleiter" in str(exc.value)

    def test_overlap_between_sets_raises(self):
        with pytest.raises(ImproperlyConfigured) as exc:
            parse_enforced_role_sets("super_admin,lead", "lead,staff")
        assert "ueberschneiden" in str(exc.value)
        assert "lead" in str(exc.value)


class TestBaseSettingsDefaults:
    def test_base_default_role_sets(self):
        """``base.py`` (→ prod) liefert die konfigurierten Default-Sets: LEAD in
        Grace, super_admin/facility_admin sofort."""
        import anlaufstelle.settings.base as base_settings

        assert base_settings.MFA_ENFORCED_ROLES_IMMEDIATE == ("super_admin", "facility_admin")
        assert base_settings.MFA_ENFORCED_ROLES_GRACE == ("lead", "staff", "assistant")

    def test_include_lead_flag_is_gone(self):
        """Das nie released Setting ``MFA_ENFORCE_INCLUDE_LEAD`` ist ersatzlos
        entfernt (kein Deprecation-Pfad, Refs #1639)."""
        import anlaufstelle.settings.base as base_settings

        assert not hasattr(base_settings, "MFA_ENFORCE_INCLUDE_LEAD")


@pytest.mark.django_db
class TestLeadRoleEnforcementDefault:
    """Verhaltens-Ende-zu-Ende: LEAD ist per Default rollen-erzwungen (Grace)."""

    def test_lead_enforced_by_default_with_grace(self, settings, lead_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        assert lead_user.is_mfa_enforced is True
        assert lead_user.mfa_enforced_by_role_grace is True
        assert lead_user.mfa_enforced_by_role_immediate is False

    def test_lead_not_enforced_when_removed_from_grace_set(self, settings, lead_user):
        settings.MFA_ENFORCE_PRIVILEGED_ROLES = True
        settings.MFA_ENFORCED_ROLES_GRACE = ("staff", "assistant")
        assert lead_user.is_mfa_enforced is False

    def test_lead_in_denominator_by_default(self, settings, facility):
        from core.models.user import User, mfa_enforced_roles

        assert User.Role.LEAD in mfa_enforced_roles()
