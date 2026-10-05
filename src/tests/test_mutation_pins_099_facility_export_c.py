"""Pin-Tests der Welle L (0.99) — Vollmutation #2174/#2193, Batch ``facility_export-c``.

Refs #2174,
Refs #2193.

Modul: ``core.services.system.facility_export`` (Familie ``services.system``).
Das Modul ist auf vier Batches ``facility_export-a``/``-b``/``-c``/``-d``
verteilt (andere Funktionen, andere Testdateien; a und b laufen parallel) — diese
Datei deckt ausschließlich die ``facility_export-c``-Batch ab: 65
Survivor-Mutanten aus den fünf Entity-Serialisierern ``_serialize_episode``
(15), ``_serialize_field_template`` (14), ``_serialize_goal`` (14),
``_serialize_field_group`` (11) und ``_serialize_milestone`` (11). Andere
Funktionen desselben Moduls (``_build_entities``, ``_build_configuration``,
``_serialize_event``, ``_serialize_client``, ``_serialize_case``,
``_gather_attachments``, ``_serialize_workitem``, ``_serialize_document_type``,
``_serialize_attachment``, ``build_facility_export``,
``_serialize_field_condition``, ``_serialize_facility``,
``_attachment_sha256``, ``_decrypt_data_json``, ``_uuid_str``) gehören den
Schwester-Batches a/b/d in eigenen Testdateien — bewusst nicht gelesen,
nicht angefasst.

Alle 65 Mutanten dieser Batch sind mechanische Dict-Key-Umbenennungen
(mutmut-String-Marker ``XX…XX``/Großschreibung) oder Argument-Ersetzungen
durch ``None`` an ``_isoformat(...)``/``_uuid_str(...)``/``str(...)``-Aufrufen
— keine Verzweigungslogik, kein Kontrollfluss. Die vollständige Triage
(Klasse ``test``/``skip``/``equivalent``/``bug`` je Mutante, Begründung,
Prio) steht in
``reports/m-scratch/pins-facility_export-c/triage-099-facility_export-c.json``.

Jeder Pin ruft den privaten Serialisierer DIREKT mit einer Modellinstanz auf,
deren Felder bewusst NICHT auf den Modell-Defaults stehenbleiben (insbesondere
alle nullable Datums-/FK-Felder sind belegt: ``Episode.ended_at``,
``OutcomeGoal.achieved_at``, ``Milestone.completed_at`` sind sonst ``None``),
und vergleicht das Ergebnis per EXAKTER Dict-Gleichheit gegen das erwartete
Dict — das killt sowohl jede Schlüssel-Umbenennung (Schlüsselmenge weicht ab)
als auch jede ``_isoformat(None)``/``_uuid_str(None)``/``str(None)``-
Verstümmelung (Wert weicht trotz gesetztem Original vom erwarteten Wert ab)
in einem einzigen Assert je Funktion.

``_encryption_key`` liegt modulweit an (Wellen-Konvention): der
Mutantenbaum (``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit
keinen ``ENCRYPTION_KEY``; ohne die Fixture wären Pins, deren Modelle
verschlüsselte Felder anfassen, aus dem FALSCHEN Grund rot (Lehre 112).
Keiner der fünf Serialisierer dieser Batch fasst ``data_json``/
Verschlüsselung an, die Fixture liegt trotzdem modulweit an.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import Episode, FieldGroup, FieldTemplate, Milestone, OutcomeGoal
from core.models.document_type import DocumentType
from core.services.system.facility_export import (
    _serialize_episode,
    _serialize_field_group,
    _serialize_field_template,
    _serialize_goal,
    _serialize_milestone,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# _serialize_episode — vollstaendiger Dict-Vergleich
# (x__serialize_episode__mutmut_7..21)
# ---------------------------------------------------------------------------


class TestSerializeEpisode:
    def test_full_dict_with_all_optional_fields_set(self, case_open, staff_user):
        """Killt core.services.system.facility_export.x__serialize_episode__mutmut_7
        bis __mutmut_21 (Schlüssel title/description/started_at/ended_at/
        created_by_id/created_at umbenannt, bzw. started_at/ended_at auf
        ``_isoformat(None)`` und created_by_id auf ``_uuid_str(None)``
        gestutzt): ``ended_at`` ist bewusst gesetzt (Modell-Default ``None``),
        damit ``_isoformat(episode.ended_at)`` sich von der
        ``_isoformat(None)``-Mutante unterscheidet; ``created_by`` ist gesetzt,
        damit ``_uuid_str(...)`` sich von ``_uuid_str(None)`` unterscheidet."""
        started = timezone.now().date()
        ended = started + timedelta(days=3)
        episode = Episode.objects.create(
            case=case_open,
            title="Kriseninterventionsphase",
            description="Akute Stabilisierung nach Zuweisung.",
            started_at=started,
            ended_at=ended,
            created_by=staff_user,
        )

        result = _serialize_episode(episode)

        assert result == {
            "id": str(episode.pk),
            "case_id": str(case_open.pk),
            "title": "Kriseninterventionsphase",
            "description": "Akute Stabilisierung nach Zuweisung.",
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "created_by_id": str(staff_user.pk),
            "created_at": episode.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# _serialize_field_template — vollstaendiger Dict-Vergleich
# (x__serialize_field_template__mutmut_4/5/14/15/18/19/20/21/22/23/26/27/28/29)
# ---------------------------------------------------------------------------


class TestSerializeFieldTemplate:
    def test_full_dict_with_all_optional_fields_set(self, facility):
        """Killt core.services.system.facility_export.x__serialize_field_template__mutmut_4,
        __mutmut_5, __mutmut_14, __mutmut_15, __mutmut_18, __mutmut_19,
        __mutmut_20, __mutmut_21, __mutmut_22, __mutmut_23, __mutmut_26,
        __mutmut_27, __mutmut_28, __mutmut_29 (Schlüssel name/is_required/
        sensitivity/options_json/default_value/help_text/is_active
        umbenannt): jedes betroffene Feld trägt hier einen gesetzten,
        vom jeweiligen Modell-Default abweichenden Wert (``is_required``,
        ``sensitivity``, ``options_json``, ``default_value``, ``help_text``,
        ``is_active`` sind sonst ``False``/``""``/``[]``/``""``/``""``/
        ``True``), ein exakter Dict-Vergleich macht jede Umbenennung als
        fehlenden/zusätzlichen Schlüssel sichtbar."""
        field_template = FieldTemplate.objects.create(
            facility=facility,
            name="Belastungsstufe",
            field_type=FieldTemplate.FieldType.NUMBER,
            min_value=1,
            max_value=5,
            is_required=True,
            is_encrypted=True,
            sensitivity=DocumentType.Sensitivity.HIGH,
            options_json=["niedrig", "mittel", "hoch"],
            default_value="mittel",
            report_key="belastung",
            help_text="1 (gering) bis 5 (akut).",
            is_active=False,
        )

        result = _serialize_field_template(field_template)

        assert result == {
            "id": str(field_template.pk),
            "name": "Belastungsstufe",
            "slug": field_template.slug,
            "field_type": FieldTemplate.FieldType.NUMBER,
            "min_value": 1,
            "max_value": 5,
            "is_required": True,
            "is_encrypted": True,
            "sensitivity": DocumentType.Sensitivity.HIGH,
            "options_json": ["niedrig", "mittel", "hoch"],
            "default_value": "mittel",
            "report_key": "belastung",
            "help_text": "1 (gering) bis 5 (akut).",
            "is_active": False,
        }


# ---------------------------------------------------------------------------
# _serialize_goal — vollstaendiger Dict-Vergleich
# (x__serialize_goal__mutmut_7..20)
# ---------------------------------------------------------------------------


class TestSerializeGoal:
    def test_full_dict_with_achieved_at_and_milestone_set(self, case_open, staff_user):
        """Killt core.services.system.facility_export.x__serialize_goal__mutmut_7
        bis __mutmut_20 (Schlüssel title/description/is_achieved/achieved_at/
        created_by_id/created_at umbenannt, bzw. achieved_at auf
        ``_isoformat(None)`` und created_by_id auf ``_uuid_str(None)``
        gestutzt): ``achieved_at`` ist bewusst gesetzt (Modell-Default
        ``None``), ``created_by`` ist gesetzt — beide unterscheiden die
        echten Werte von den ``None``-Mutanten. ``milestones`` wird über den
        unmutierten ``_serialize_milestone`` erwartet (dessen eigene
        Mutanten trägt Batch ``facility_export-c`` separat, s. u.)."""
        achieved = timezone.now().date()
        goal = OutcomeGoal.objects.create(
            case=case_open,
            title="Stabile Wohnsituation",
            description="Eigene Wohnung ohne akute Obdachlosigkeit.",
            is_achieved=True,
            achieved_at=achieved,
            created_by=staff_user,
        )
        milestone = Milestone.objects.create(goal=goal, title="Wohnung besichtigt", sort_order=1)

        result = _serialize_goal(goal)

        assert result == {
            "id": str(goal.pk),
            "case_id": str(case_open.pk),
            "title": "Stabile Wohnsituation",
            "description": "Eigene Wohnung ohne akute Obdachlosigkeit.",
            "is_achieved": True,
            "achieved_at": achieved.isoformat(),
            "created_by_id": str(staff_user.pk),
            "created_at": goal.created_at.isoformat(),
            "milestones": [_serialize_milestone(milestone)],
        }


# ---------------------------------------------------------------------------
# _serialize_field_group — vollstaendiger Dict-Vergleich
# (x__serialize_field_group__mutmut_4..14)
# ---------------------------------------------------------------------------


class TestSerializeFieldGroup:
    def test_full_dict(self, doc_type_contact):
        """Killt core.services.system.facility_export.x__serialize_field_group__mutmut_4
        bis __mutmut_14 (Schlüssel document_type_id/name/slug/sort_order/
        is_collapsible umbenannt, bzw. document_type_id auf ``str(None)``
        gestutzt): ``sort_order``/``is_collapsible`` tragen vom Modell-
        Default (0/False) abweichende Werte, ``document_type_id`` verweist
        auf eine echte Instanz — ein exakter Dict-Vergleich fängt jede
        Umbenennung und die ``str(None)``-Mutante in einer Assertion."""
        field_group = FieldGroup.objects.create(
            document_type=doc_type_contact,
            name="Sozialdaten",
            sort_order=3,
            is_collapsible=True,
        )

        result = _serialize_field_group(field_group)

        assert result == {
            "id": str(field_group.pk),
            "document_type_id": str(doc_type_contact.pk),
            "name": "Sozialdaten",
            "slug": field_group.slug,
            "sort_order": 3,
            "is_collapsible": True,
        }


# ---------------------------------------------------------------------------
# _serialize_milestone — vollstaendiger Dict-Vergleich
# (x__serialize_milestone__mutmut_4..14)
# ---------------------------------------------------------------------------


class TestSerializeMilestone:
    def test_full_dict_with_completed_at_set(self, outcome_goal):
        """Killt core.services.system.facility_export.x__serialize_milestone__mutmut_4
        bis __mutmut_14 (Schlüssel title/is_completed/completed_at/
        sort_order/created_at umbenannt, bzw. completed_at auf
        ``_isoformat(None)`` gestutzt): ``completed_at`` ist bewusst gesetzt
        (Modell-Default ``None``), ``is_completed``/``sort_order`` tragen
        vom Default abweichende Werte."""
        completed = timezone.now().date()
        milestone = Milestone.objects.create(
            goal=outcome_goal,
            title="Erstkontakt dokumentiert",
            is_completed=True,
            completed_at=completed,
            sort_order=2,
        )

        result = _serialize_milestone(milestone)

        assert result == {
            "id": str(milestone.pk),
            "title": "Erstkontakt dokumentiert",
            "is_completed": True,
            "completed_at": completed.isoformat(),
            "sort_order": 2,
            "created_at": milestone.created_at.isoformat(),
        }
