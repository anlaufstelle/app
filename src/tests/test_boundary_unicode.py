"""Boundary-Tests für Unicode/Emoji/RTL/Null-Byte/Whitespace in user-input.

Refs Welle 4 (#927), Master #922.

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


# =========================================================================
# 1. EMOJI / UNICODE
# =========================================================================


@pytest.mark.django_db
class TestEmojiAccepted:
    """Emoji und erweitertes Unicode müssen im UTF-8-Stack durchlaufen."""

    def test_client_pseudonym_with_emoji(self, facility):
        """Pseudonym mit Emoji-Suffix wird unverändert akzeptiert."""
        form = _client_form(facility, EMOJI)
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", EMOJI)

    def test_client_pseudonym_with_supplementary_plane_emoji(self, facility):
        """Emoji aus der Supplementary Plane (U+1F680) wird akzeptiert."""
        form = _client_form(facility, EMOJI_2)
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", EMOJI_2)

    def test_case_title_with_emoji(self, facility, client_identified):
        """Case-Titel mit Emoji-Inhalt ist valide."""
        form = _case_form(facility, client_identified, EMOJI)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == EMOJI

    def test_episode_title_with_emoji(self, facility):
        """Episode-Titel akzeptiert Emoji."""
        form = _episode_form(EMOJI)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == EMOJI

    def test_workitem_title_with_emoji(self, facility):
        """WorkItem-Titel akzeptiert Emoji."""
        form = _workitem_form(facility, EMOJI_2)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == EMOJI_2

    def test_outcomegoal_title_with_emoji_persists(self, case_open, staff_user):
        """OutcomeGoal mit Emoji im Titel persistiert verlustfrei."""
        goal = OutcomeGoal.objects.create(case=case_open, title=EMOJI, created_by=staff_user)
        goal.full_clean()  # Model-Layer-Validierung
        goal.refresh_from_db()
        assert goal.title == EMOJI

    def test_milestone_title_with_emoji_persists(self, outcome_goal):
        """Milestone mit Emoji im Titel persistiert verlustfrei."""
        milestone = Milestone.objects.create(goal=outcome_goal, title=EMOJI_2)
        milestone.full_clean()
        milestone.refresh_from_db()
        assert milestone.title == EMOJI_2


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

    def test_client_pseudonym_with_rtl_override_is_accepted(self, facility):
        """RTL-Override im Pseudonym wird unverändert akzeptiert (current behavior)."""
        form = _client_form(facility, RTL_OVERRIDE)
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", RTL_OVERRIDE)

    def test_case_title_with_rtl_override_is_accepted(self, facility, client_identified):
        """RTL-Override im Case-Titel wird unverändert akzeptiert."""
        form = _case_form(facility, client_identified, RTL_OVERRIDE)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == RTL_OVERRIDE

    def test_episode_title_with_rtl_override_is_accepted(self):
        """RTL-Override im Episode-Titel wird unverändert akzeptiert."""
        form = _episode_form(RTL_OVERRIDE)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == RTL_OVERRIDE

    def test_workitem_title_with_rtl_override_is_accepted(self, facility):
        """RTL-Override im WorkItem-Titel wird unverändert akzeptiert."""
        form = _workitem_form(facility, RTL_OVERRIDE)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == RTL_OVERRIDE


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

    def test_client_pseudonym_null_byte_rejected(self, facility):
        """Null-Byte im Pseudonym wird abgelehnt — Form invalid oder DB-Fehler."""
        form = _client_form(facility, NULL_BYTE)
        if not form.is_valid():
            return  # Form fängt es ab — gut.
        # Form akzeptiert, aber save() muss scheitern.
        with pytest.raises((DataError, IntegrityError, ValueError, ValidationError)):
            with transaction.atomic():
                Client.objects.create(
                    facility=facility,
                    pseudonym=NULL_BYTE,
                    contact_stage=Client.ContactStage.IDENTIFIED,
                )

    def test_case_title_null_byte_rejected(self, facility, client_identified, staff_user):
        """Null-Byte im Case-Titel wird abgelehnt."""
        form = _case_form(facility, client_identified, NULL_BYTE)
        if not form.is_valid():
            return
        with pytest.raises((DataError, IntegrityError, ValueError, ValidationError)):
            with transaction.atomic():
                Case.objects.create(
                    facility=facility,
                    client=client_identified,
                    title=NULL_BYTE,
                    status=Case.Status.OPEN,
                    created_by=staff_user,
                )

    def test_outcomegoal_title_null_byte_rejected_at_save(self, case_open, staff_user):
        """Direkter Model-Save mit Null-Byte muss scheitern (DB-Layer)."""
        with pytest.raises((DataError, IntegrityError, ValueError, ValidationError)):
            with transaction.atomic():
                OutcomeGoal.objects.create(
                    case=case_open,
                    title=NULL_BYTE,
                    created_by=staff_user,
                )

    def test_milestone_title_null_byte_rejected_at_save(self, outcome_goal):
        """Milestone-Save mit Null-Byte muss scheitern (DB-Layer)."""
        with pytest.raises((DataError, IntegrityError, ValueError, ValidationError)):
            with transaction.atomic():
                Milestone.objects.create(goal=outcome_goal, title=NULL_BYTE)


# =========================================================================
# 4. WHITESPACE-TRIM (Django CharField, strip=True)
# =========================================================================


@pytest.mark.django_db
class TestWhitespaceTrim:
    """Django CharField hat per Default ``strip=True`` — führende/anhängende
    Whitespaces werden in ``cleaned_data`` entfernt.
    """

    def test_client_pseudonym_whitespace_trimmed(self, facility):
        """Pseudonym wird in cleaned_data getrimmt."""
        form = _client_form(facility, WHITESPACE_PADDED)
        assert_form_valid(form)
        assert_clean_value(form, "pseudonym", "Test")

    def test_case_title_whitespace_trimmed(self, facility, client_identified):
        """Case-Titel wird in cleaned_data getrimmt."""
        form = _case_form(facility, client_identified, WHITESPACE_PADDED)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == "Test"

    def test_episode_title_whitespace_trimmed(self):
        """Episode-Titel wird in cleaned_data getrimmt."""
        form = _episode_form(WHITESPACE_PADDED)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == "Test"

    def test_workitem_title_whitespace_trimmed(self, facility):
        """WorkItem-Titel wird in cleaned_data getrimmt."""
        form = _workitem_form(facility, WHITESPACE_PADDED)
        assert_form_valid(form)
        assert form.cleaned_data["title"] == "Test"


# =========================================================================
# 5. NUR-WHITESPACE (entspricht leerem Pflichtfeld nach strip)
# =========================================================================


@pytest.mark.django_db
class TestWhitespaceOnlyRejected:
    """Bei ``strip=True`` wird reiner Whitespace zu Empty-String — Pflichtfelder
    müssen das als ``required``-Fehler melden.
    """

    def test_client_pseudonym_whitespace_only_rejected(self, facility):
        """Nur-Whitespace-Pseudonym wird als Pflichtfeld-Fehler abgelehnt."""
        form = _client_form(facility, ONLY_WHITESPACE)
        assert not form.is_valid()
        assert "pseudonym" in form.errors

    def test_case_title_whitespace_only_rejected(self, facility, client_identified):
        """Nur-Whitespace-Case-Titel wird als Pflichtfeld-Fehler abgelehnt."""
        form = _case_form(facility, client_identified, ONLY_WHITESPACE)
        assert not form.is_valid()
        assert "title" in form.errors

    def test_episode_title_whitespace_only_rejected(self):
        """Nur-Whitespace-Episode-Titel wird als Pflichtfeld-Fehler abgelehnt."""
        form = _episode_form(ONLY_WHITESPACE)
        assert not form.is_valid()
        assert "title" in form.errors

    def test_workitem_title_whitespace_only_rejected(self, facility):
        """Nur-Whitespace-WorkItem-Titel wird als Pflichtfeld-Fehler abgelehnt."""
        form = _workitem_form(facility, ONLY_WHITESPACE)
        assert not form.is_valid()
        assert "title" in form.errors
