"""Boundary-Tests für Unicode/Emoji/RTL/Null-Byte/Whitespace in user-input.

Refs #927, Master #922.

Überprüft Verhalten an den Rändern der Eingabevalidierung für die
zentralen CharField-Eingabefelder (Pseudonym, Titel-Felder) sowie das
Speicher-Verhalten bei kritischen Bytes (Null-Byte) auf Modell-Ebene.

Felder unter Test:

- ``Client.pseudonym`` (max_length=100, ``ClientForm.clean_pseudonym``)
- ``Case.title`` (max_length=200, ``CaseForm``)
- ``Episode.title`` (max_length=200, ``EpisodeForm``)
- ``WorkItem.title`` (max_length=200, ``WorkItemForm``)
- ``OutcomeGoal.title`` (max_length=200, kein Form -> ``full_clean``)
- ``Milestone.title`` (max_length=200, kein Form -> ``full_clean``)

Hinweis: ``Event`` legt Nutzer-Inhalte in ``data_json`` (JSONField) ab —
es gibt kein einfaches CharField-Notes-Feld am Modell. Boundary-Tests
für JSON-Strukturen sind ein eigenes Thema und hier bewusst ausgespart.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from core.forms.cases import CaseForm
from core.forms.clients import ClientForm
from core.forms.episodes import EpisodeForm
from core.forms.workitems import WorkItemForm
from core.models import Case, Client, WorkItem
from core.models.outcome import Milestone, OutcomeGoal
from tests._form_helpers import (
    assert_clean_value,
    assert_form_valid,
)

# --- Konstanten -----------------------------------------------------------

EMOJI = "Stern-✨"  # "Stern-✨"
EMOJI_2 = "Klient\U0001f680"  # "Klient🚀"
RTL_OVERRIDE = "Test‮evil"  # U+202E RIGHT-TO-LEFT OVERRIDE
NULL_BYTE = "Test\x00Inject"
WHITESPACE_PADDED = "  Test  "
ONLY_WHITESPACE = "   "


def _client_form(facility, pseudonym):
    return ClientForm(
        data={
            "pseudonym": pseudonym,
            "contact_stage": Client.ContactStage.IDENTIFIED,
            "age_cluster": Client.AgeCluster.UNKNOWN,
            "notes": "",
        },
        facility=facility,
    )


def _case_form(facility, client, title):
    return CaseForm(
        data={
            "client": str(client.pk),
            "title": title,
            "description": "",
        },
        facility=facility,
    )


def _episode_form(title, started_at=None):
    return EpisodeForm(
        data={
            "title": title,
            "description": "",
            "started_at": (started_at or timezone.now().date()).isoformat(),
            "ended_at": "",
        },
    )


def _workitem_form(facility, title):
    return WorkItemForm(
        data={
            "item_type": WorkItem.ItemType.TASK,
            "title": title,
            "description": "",
            "priority": WorkItem.Priority.NORMAL,
            "due_date": "",
            "remind_at": "",
            "recurrence": WorkItem.Recurrence.NONE,
            "assigned_to": "",
        },
        facility=facility,
    )


def _build_client_form(request, value):
    return _client_form(request.getfixturevalue("facility"), value)


def _build_case_form(request, value):
    return _case_form(
        request.getfixturevalue("facility"),
        request.getfixturevalue("client_identified"),
        value,
    )


def _build_episode_form(_request, value):
    return _episode_form(value)


def _build_workitem_form(request, value):
    return _workitem_form(request.getfixturevalue("facility"), value)


_EMOJI_FORM_CASES = (
    (_build_client_form, "pseudonym", EMOJI),
    (_build_client_form, "pseudonym", EMOJI_2),
    (_build_case_form, "title", EMOJI),
    (_build_episode_form, "title", EMOJI),
    (_build_workitem_form, "title", EMOJI_2),
)

_EMOJI_MODEL_CASES = (
    (
        OutcomeGoal,
        "title",
        EMOJI,
        {},
        {"case": "case_open", "created_by": "staff_user"},
    ),
    (
        Milestone,
        "title",
        EMOJI_2,
        {},
        {"goal": "outcome_goal"},
    ),
)

_RTL_FORM_CASES = (
    (_build_client_form, "pseudonym", RTL_OVERRIDE),
    (_build_case_form, "title", RTL_OVERRIDE),
    (_build_episode_form, "title", RTL_OVERRIDE),
    (_build_workitem_form, "title", RTL_OVERRIDE),
)

_NULL_BYTE_CASES = (
    (
        Client,
        "pseudonym",
        _build_client_form,
        {"contact_stage": Client.ContactStage.IDENTIFIED},
        {"facility": "facility"},
    ),
    (
        Case,
        "title",
        _build_case_form,
        {"status": Case.Status.OPEN},
        {
            "facility": "facility",
            "client": "client_identified",
            "created_by": "staff_user",
        },
    ),
    (
        OutcomeGoal,
        "title",
        None,
        {},
        {"case": "case_open", "created_by": "staff_user"},
    ),
    (
        Milestone,
        "title",
        None,
        {},
        {"goal": "outcome_goal"},
    ),
)

_WHITESPACE_TRIM_FORM_CASES = (
    (_build_client_form, "pseudonym", "Test"),
    (_build_case_form, "title", "Test"),
    (_build_episode_form, "title", "Test"),
    (_build_workitem_form, "title", "Test"),
)

_WHITESPACE_ONLY_FORM_CASES = (
    (_build_client_form, "pseudonym", False),
    (_build_case_form, "title", False),
    (_build_episode_form, "title", False),
    (_build_workitem_form, "title", False),
)


# =========================================================================
# 1. EMOJI / UNICODE
# =========================================================================


@pytest.mark.django_db
class TestEmojiAccepted:
    """Emoji und erweitertes Unicode müssen im UTF-8-Stack durchlaufen."""

    @pytest.mark.parametrize(
        ("form_builder", "field", "expected"),
        _EMOJI_FORM_CASES,
        ids=(
            "client-pseudonym-emoji",
            "client-pseudonym-supplementary-emoji",
            "case-title-emoji",
            "episode-title-emoji",
            "workitem-title-emoji",
        ),
    )
    def test_form_value_accepted(self, request, form_builder, field, expected):
        form = form_builder(request, expected)
        assert_form_valid(form)
        assert_clean_value(form, field, expected)

    @pytest.mark.parametrize(
        ("model", "field", "expected", "required_defaults", "fixture_deps"),
        _EMOJI_MODEL_CASES,
        ids=("outcomegoal-title-emoji", "milestone-title-emoji"),
    )
    def test_model_value_persists(self, request, model, field, expected, required_defaults, fixture_deps):
        defaults = required_defaults | {
            key: request.getfixturevalue(fixture_name) for key, fixture_name in fixture_deps.items()
        }
        instance = model.objects.create(**defaults, **{field: expected})
        instance.full_clean()  # Model-Layer-Validierung
        instance.refresh_from_db()
        assert getattr(instance, field) == expected


# =========================================================================
# 2. RTL-OVERRIDE-MARKER (U+202E)
# =========================================================================


@pytest.mark.django_db
class TestRTLOverrideMarker:
    """U+202E ist Unicode-konformes Zeichen — heute akzeptiert.

    Dokumentiert das aktuelle Verhalten: der Marker kann in
    Eingabefeldern gespeichert werden. Dadurch kann eine UI-Darstellung
    visuell manipuliert werden (z. B. Pseudonym ``Test<RLO>evil`` wird
    als ``Testlive`` gerendert). Aktuell ist das eine bewusste Lücke;
    falls in Zukunft ein Filter eingeführt wird, müssen diese Tests
    angepasst werden.
    """

    @pytest.mark.parametrize(
        ("form_builder", "field", "expected"),
        _RTL_FORM_CASES,
        ids=(
            "client-pseudonym-rtl-override",
            "case-title-rtl-override",
            "episode-title-rtl-override",
            "workitem-title-rtl-override",
        ),
    )
    def test_form_value_accepted(self, request, form_builder, field, expected):
        form = form_builder(request, expected)
        assert_form_valid(form)
        assert_clean_value(form, field, expected)


# =========================================================================
# 3. NULL-BYTE (\x00)
# =========================================================================


@pytest.mark.django_db
class TestNullByteRejected:
    """PostgreSQL TEXT/VARCHAR akzeptiert keine NUL-Bytes (\\x00).

    Django wirft je nach Version/Pfad entweder ``ValidationError`` (Form-
    /Model-Validation) oder eine DB-Exception beim ``save``. Beide
    Verhalten sind aus DSGVO-/Integritätssicht akzeptabel — entscheidend
    ist, dass der Wert NICHT klaglos gespeichert wird.
    """

    @pytest.mark.parametrize(
        ("model", "field", "form_builder", "required_defaults", "fixture_deps"),
        _NULL_BYTE_CASES,
        ids=(
            "client-pseudonym-null-byte",
            "case-title-null-byte",
            "outcomegoal-title-null-byte",
            "milestone-title-null-byte",
        ),
    )
    def test_value_rejected(self, request, model, field, form_builder, required_defaults, fixture_deps):
        if form_builder is not None:
            form = form_builder(request, NULL_BYTE)
            if not form.is_valid():
                return  # Form fängt es ab — gut.

        defaults = required_defaults | {
            key: request.getfixturevalue(fixture_name) for key, fixture_name in fixture_deps.items()
        }
        with pytest.raises((DataError, IntegrityError, ValueError, ValidationError)):
            with transaction.atomic():
                model.objects.create(**defaults, **{field: NULL_BYTE})


# =========================================================================
# 4. WHITESPACE-TRIM (Django CharField, strip=True)
# =========================================================================


@pytest.mark.django_db
class TestWhitespaceTrim:
    """Django CharField hat per Default ``strip=True`` — führende/anhängende
    Whitespaces werden in ``cleaned_data`` entfernt.
    """

    @pytest.mark.parametrize(
        ("form_builder", "field", "expected"),
        _WHITESPACE_TRIM_FORM_CASES,
        ids=(
            "client-pseudonym-whitespace-trim",
            "case-title-whitespace-trim",
            "episode-title-whitespace-trim",
            "workitem-title-whitespace-trim",
        ),
    )
    def test_form_value_trimmed(self, request, form_builder, field, expected):
        form = form_builder(request, WHITESPACE_PADDED)
        assert_form_valid(form)
        assert_clean_value(form, field, expected)


# =========================================================================
# 5. NUR-WHITESPACE (entspricht leerem Pflichtfeld nach strip)
# =========================================================================


@pytest.mark.django_db
class TestWhitespaceOnlyRejected:
    """Bei ``strip=True`` wird reiner Whitespace zu Empty-String — Pflichtfelder
    müssen das als ``required``-Fehler melden.
    """

    @pytest.mark.parametrize(
        ("form_builder", "field", "expected"),
        _WHITESPACE_ONLY_FORM_CASES,
        ids=(
            "client-pseudonym-whitespace-only",
            "case-title-whitespace-only",
            "episode-title-whitespace-only",
            "workitem-title-whitespace-only",
        ),
    )
    def test_form_value_rejected(self, request, form_builder, field, expected):
        form = form_builder(request, ONLY_WHITESPACE)
        assert form.is_valid() is expected
        assert field in form.errors
