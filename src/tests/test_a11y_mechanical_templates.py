"""Pin-Tests fuer die mechanischen WCAG-Fixes aus #1059."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def _source(template: str) -> str:
    return (TEMPLATES / template).read_text(encoding="utf-8")


DIRECT_STANDALONE_TEMPLATES = (
    "500.html",
    "503.html",
    "auth/facility_archived.html",
    "auth/facility_missing.html",
    "auth/lockout_recovery_backup_code.html",
    "auth/lockout_recovery_confirm.html",
    "auth/lockout_recovery_invalid.html",
    "auth/lockout_recovery_request.html",
    "auth/lockout_recovery_sent.html",
    "auth/login.html",
    "auth/mfa_login.html",
    "auth/mfa_setup.html",
    "auth/password_change.html",
    "registration/password_reset_complete.html",
    "registration/password_reset_confirm.html",
    "registration/password_reset_done.html",
    "registration/password_reset_form.html",
)

BASE_INHERITED_TEMPLATES = (
    "403.html",
    "403_csrf.html",
    "404.html",
    "auth/mfa_backup_codes.html",
    "auth/mfa_settings.html",
    "auth/sudo_mode.html",
)


@pytest.mark.parametrize("template", DIRECT_STANDALONE_TEMPLATES)
def test_standalone_template_has_skip_link_and_main_landmark(template: str) -> None:
    source = _source(template)
    assert '<a href="#main-content"' in source
    assert '<main id="main-content"' in source


@pytest.mark.parametrize("template", BASE_INHERITED_TEMPLATES)
def test_remaining_standalone_family_inherits_accessible_base(template: str) -> None:
    assert '{% extends "base.html" %}' in _source(template)


def test_bound_lockout_fields_use_shared_aria_renderer() -> None:
    backup = _source("auth/lockout_recovery_backup_code.html")
    request = _source("auth/lockout_recovery_request.html")
    assert '{% include "components/_form_field.html" with field=form.username %}' in backup
    assert '{% include "components/_form_field.html" with field=form.backup_code %}' in backup
    assert '{% include "components/_form_field.html" with field=form.email %}' in request


def test_password_input_supports_error_relationships() -> None:
    source = _source("components/_password_input.html")
    assert '{% if invalid %}aria-invalid="true"{% endif %}' in source
    assert '{% if described_by %}aria-describedby="{{ described_by }}"{% endif %}' in source


def test_mfa_regeneration_toggle_exposes_panel_state() -> None:
    source = _source("auth/mfa_settings.html")
    assert 'aria-expanded="false" :aria-expanded="regenOpen.toString()"' in source
    assert 'aria-controls="backup-codes-regenerate-panel"' in source
    assert '<div id="backup-codes-regenerate-panel"' in source


def test_static_backup_code_note_is_not_an_alert() -> None:
    opening_tag = (
        _source("auth/mfa_backup_codes.html")
        .split('{% trans "Wichtig" %}', maxsplit=1)[0]
        .rsplit("<div", maxsplit=1)[1]
    )
    assert 'role="alert"' not in opening_tag
