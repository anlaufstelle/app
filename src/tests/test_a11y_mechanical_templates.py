"""Pin-Tests fuer die mechanischen WCAG-Fixes aus #1059."""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse

pytestmark = pytest.mark.architecture

TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
INPUT_CSS = Path(__file__).resolve().parents[1] / "static" / "css" / "input.css"


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
    "offline.html",
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


def test_search_field_declares_combobox_role_for_alpine_variant() -> None:
    source = _source("partials/_search_field_control.html")
    assert 'role="combobox" aria-autocomplete="list" aria-haspopup="listbox"' in source


def test_event_and_offline_controls_have_accessible_names() -> None:
    conflict = _source("core/events/conflict_review.html")
    offline_event = _source("core/events/offline_create.html")
    offline_workitem = _source("core/workitems/offline_create.html")
    assert "aria-label=\"{% trans 'Version für dieses Feld wählen' %}\"" in conflict
    for control_id in ("offline-event-client", "offline-event-doctype", "offline-event-occurred-at"):
        assert f'for="{control_id}"' in offline_event
        assert f'id="{control_id}"' in offline_event
    assert 'for="offline-workitem-client"' in offline_workitem
    assert 'id="offline-workitem-client"' in offline_workitem
    assert 'with id_prefix="offline-workitem-create"' in offline_workitem


def test_offline_event_reactive_messages_expose_status() -> None:
    source = _source("core/events/offline_create.html")
    assert 'data-testid="offline-event-create-stage-hint" role="status" aria-live="polite"' in source
    assert 'data-testid="offline-event-create-error" role="alert"' in source


def test_zeitstrom_filters_and_day_links_are_named() -> None:
    source = _source("core/zeitstrom/index.html")
    assert "aria-label=\"{% trans 'Nach Dokumentationstyp filtern' %}\"" in source
    assert source.count("aria-label=\"{% trans 'Vorheriger Tag' %}\"") == 2
    assert source.count("aria-label=\"{% trans 'Nächster Tag' %}\"") == 2
    assert source.count('aria-disabled="true" tabindex="-1"') == 2


def test_event_and_handover_low_opacity_text_is_removed() -> None:
    event_create = _source("core/events/create.html")
    event_card = _source("components/_event_card.html")
    handover = _source("core/handover/partials/_summary_full.html")
    assert "text-accent opacity-70" not in event_create
    assert '<span class="opacity-70">{{ pf.label }}:</span>' not in event_card
    assert "text-accent opacity-80" not in handover


def test_handover_highlight_timestamp_uses_conforming_text_token() -> None:
    highlights = _source("core/handover/partials/_highlights.html")
    assert '<span class="font-mono text-ink">{{ h.time|time:"H:i" }}</span>' in highlights
    assert 'class="font-mono text-ink-muted"' not in highlights


def test_shared_border_token_meets_the_calculated_contrast_target() -> None:
    assert "--border: oklch(0.64 0.012 75);" in INPUT_CSS.read_text(encoding="utf-8")


def test_condition_editor_uses_slot_specific_partial_ids() -> None:
    form = _source("core/config/document_types/partials/condition_form.html")
    operator = _source("core/config/document_types/partials/condition_operator_input.html")
    value = _source("core/config/document_types/partials/condition_value_input.html")
    assert 'for="{{ condition_id_prefix }}-operator"' in form
    assert 'for="{{ condition_id_prefix }}-value"' in form
    assert 'id="{{ condition_id_prefix }}-operator"' in operator
    assert value.count('id="{{ condition_id_prefix }}-value"') == 6


class _LabelTargetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.label_targets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if element_id := attributes.get("id"):
            self.ids.append(element_id)
        if tag == "label" and (target := attributes.get("for")):
            self.label_targets.append(target)


@pytest.mark.django_db
def test_condition_board_create_and_edit_forms_have_unique_label_targets(
    client, admin_user, doc_type_contact, field_condition
) -> None:
    client.force_login(admin_user)
    board = client.get(reverse("core:config_condition_board", kwargs={"pk": doc_type_contact.pk}))
    edit = client.get(
        reverse(
            "core:config_condition_update",
            kwargs={"pk": doc_type_contact.pk, "condition_pk": field_condition.pk},
        )
    )
    assert board.status_code == edit.status_code == 200

    parser = _LabelTargetParser()
    parser.feed(board.content.decode() + edit.content.decode())
    id_counts = Counter(parser.ids)
    assert [element_id for element_id, count in id_counts.items() if count > 1] == []
    assert parser.label_targets
    assert all(id_counts[target] == 1 for target in parser.label_targets)


@pytest.mark.django_db
def test_condition_valueless_edit_form_has_no_label_without_target(
    client, admin_user, doc_type_contact, field_condition
) -> None:
    field_condition.operator = "is_empty"
    field_condition.value = None
    field_condition.save(update_fields=["operator", "value"])
    client.force_login(admin_user)
    response = client.get(
        reverse(
            "core:config_condition_update",
            kwargs={"pk": doc_type_contact.pk, "condition_pk": field_condition.pk},
        )
    )
    assert response.status_code == 200
    parser = _LabelTargetParser()
    parser.feed(response.content.decode())
    assert not any(target.endswith("-value") for target in parser.label_targets)


def test_statistics_controls_and_chart_headings_expose_state_and_structure() -> None:
    range_bar = _source("core/statistics/_zeitraum_leiste.html")
    pill = _source("core/statistics/_zeitraum_pill.html")
    dashboard = _source("core/statistics/partials/dashboard_content.html")
    for control_id in ("statistics-date-from", "statistics-date-to"):
        assert f'for="{control_id}"' in range_bar
        assert f'id="{control_id}"' in range_bar
    assert pill.count('{% if period == pill_key %}aria-current="true"{% endif %}') == 2
    assert dashboard.count('<h2 class="text-[13px] font-bold text-ink-soft mb-3">') == 3
    assert dashboard.count('<h3 class="text-[13px] font-bold text-ink-soft mb-3">') == 0


def test_system_status_dots_have_an_image_role() -> None:
    for template in ("core/system/compliance.html", "core/system/dashboard.html"):
        source = _source(template)
        for label in ("OK", "Warnung", "Kritisch", "Unbekannt"):
            assert f'role="img" aria-label="{{% trans \'{label}\' %}}"' in source


def test_colored_action_buttons_use_conforming_backgrounds() -> None:
    proposal = _source("core/retention/partials/proposal_card.html")
    retention = _source("core/retention/partials/dashboard_content.html")
    maintenance = _source("core/system/maintenance.html")
    withdrawal = _source("core/deletion_requests/withdraw_confirm.html")
    assert "bg-green-700 text-white hover:bg-green-800" in proposal
    assert "bg-yellow-800 text-white hover:bg-yellow-900" in proposal
    assert "bg-green-700 text-white hover:bg-green-800" in retention
    assert "bg-orange-700 text-white hover:bg-orange-800" in maintenance
    assert "bg-amber-700 text-white" in withdrawal
    assert "hover:bg-amber-800" in withdrawal
