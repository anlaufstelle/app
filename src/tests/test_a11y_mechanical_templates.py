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


def test_case_controls_have_accessible_names_and_errors() -> None:
    event_list = _source("core/cases/partials/event_list.html")
    goals = _source("core/cases/partials/goals_section.html")
    episode = _source("core/cases/episode_form.html")
    assert "aria-label=\"{% trans 'Nicht zugeordnetes Ereignis zuordnen' %}\"" in event_list
    assert goals.count("aria-label=") >= 7
    assert '{% include "components/_form_field.html" with field=field %}' in episode


def test_offline_client_fields_link_labels_to_unique_controls() -> None:
    event_fields = _source("core/clients/_offline_event_fields.html")
    workitem_fields = _source("core/clients/_offline_workitem_fields.html")
    detail = _source("core/clients/offline_detail.html")
    assert ":for=\"'offline-create-field-' + field.slug\"" in event_fields
    assert event_fields.count(":id=\"'offline-create-field-' + field.slug\"") == 5
    assert workitem_fields.count('for="{{ id_prefix }}-') == 8
    assert workitem_fields.count('id="{{ id_prefix }}-') == 8
    assert 'with id_prefix="offline-wi-create"' in detail
    assert 'with id_prefix="offline-wi-edit"' in detail
    assert ":for=\"'offline-edit-field-' + field.slug\"" in detail
    assert detail.count(":id=\"'offline-edit-field-' + field.slug\"") == 5


def test_offline_client_sync_messages_are_live_regions() -> None:
    detail = _source("core/clients/offline_detail.html")
    for testid in (
        "offline-edit-synced",
        "offline-edit-pending",
        "offline-edit-conflict",
        "offline-edit-invalid",
        "offline-edit-error",
    ):
        marker = detail.index(f'data-testid="{testid}"')
        tag_start = detail.rindex("<div", 0, marker)
        opening_tag = detail[tag_start : detail.index(">", marker)]
        assert 'role="status"' in opening_tag
        assert 'aria-live="polite"' in opening_tag


def test_client_merge_matrices_have_row_headers() -> None:
    assert _source("core/clients/merge_confirm.html").count('<th scope="row"') == 7
    assert _source("core/clients/merge_review.html").count('<th scope="row"') == 5


def test_shared_list_controls_have_specific_names() -> None:
    attachments = _source("core/attachments/list.html")
    pagination = _source("components/_pagination.html")
    access = _source("core/user_access/list.html")
    assert "aria-label=\"{% trans 'Person-ID filtern...' %}\"" in attachments
    assert "aria-label=\"{% trans 'Vorherige Seite' %}\"" in pagination
    assert "aria-label=\"{% trans 'Nächste Seite' %}\"" in pagination
    assert "aria-label=\"{% trans 'Verwalten' %}: {{ row.user.display_name|default:row.user.username }}\"" in access


def test_favorite_icon_has_text_alternative() -> None:
    source = _source("core/account/partials/_recent_clients_widget.html")
    assert '<span class="sr-only">{% trans "Als Favorit markiert" %}</span>' in source
