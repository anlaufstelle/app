"""Follow-Up-Tests für Mutation-Survivors in
``core.services.audit.diff`` (Refs #1388).

``changed_fields_vs_db`` bildet den Feld-Diff gegen den DB-Stand für die
Audit-Erfassung (Refs #1613, Threat-Model TB6 Repudiation): ohne korrekten
Diff bleibt ``changed_fields`` leer und ``*_UPDATE`` wird nie geschrieben. Der
Voll-Lauf 2026-07-16 liess zwei echte Verhaltens-Survivors stehen: den
„Zeile (noch) nicht in der DB → alle Felder gelten als geändert"-Pfad und die
FK-Reduktion auf ``value.pk`` nur für echte Modell-Objekte (roher PK aus dem
Offline-Replay bleibt, wie er ist).

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from core.models import Event
from core.services.audit.diff import apply_allowlisted_update, changed_fields_vs_db


@pytest.mark.django_db
class TestChangedFieldsVsDb:
    def test_missing_row_marks_all_fields_changed(self, facility, doc_type_contact):
        """Killt ``changed_fields_vs_db__mutmut_13``:
        ``return list(fields.keys())`` → ``return list(None)``.

        Ist die Zeile (noch) nicht in der DB — neuer Datensatz oder
        Offline-Replay vor dem Insert — gelten ALLE übergebenen Felder als
        geändert. Der Mutant ``list(None)`` würde hier mit ``TypeError``
        abbrechen; kein Bestandstest deckte den ``row is None``-Zweig ab.
        """
        # Unsaved-Instanz: pk (UUID) ist gesetzt, aber keine DB-Zeile vorhanden.
        ghost = Event(
            facility=facility,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        changed = changed_fields_vs_db(ghost, {"is_deleted": True, "data_json": {"x": 1}}, lock=False)
        assert sorted(changed) == ["data_json", "is_deleted"]

    def test_raw_fk_pk_is_compared_without_dereferencing(self, facility, doc_type_contact, client_identified):
        """Killt ``changed_fields_vs_db__mutmut_17``: die FK-Reduktion
        ``value.pk if is_relation and value is not None and hasattr(value, "pk")
        else value`` → ``… and value is not None or hasattr(value, "pk") …``.

        Ein FK-Wert, der als ROHER PK (UUID/int, Offline-Replay) statt als
        Modell-Objekt kommt, hat kein ``.pk`` — er muss unverändert
        durchgereicht werden. Die ``and→or``-Mutation zwingt den ``value.pk``-
        Zweig auch für den rohen PK und bricht mit ``AttributeError`` ab.
        Aufbau: ``client`` steht in der DB auf ``client_identified``; wir
        vergleichen gegen dessen ROHEN PK → kein Diff, kein Dereferenzieren.
        """
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={},
        )
        changed = changed_fields_vs_db(event, {"client": client_identified.pk}, lock=False)
        assert changed == []  # roher PK == DB-Stand → keine Änderung, kein value.pk-Crash


@pytest.mark.django_db
class TestApplyAllowlistedUpdate:
    """Gemeinsame, sicherheitsrelevante Update-Choreografie (Refs #1833)."""

    def test_rejects_unknown_field_before_diff_and_save(self, case_open):
        """Die Mass-Assignment-Grenze laeuft vor jedem DB-Diff und Write."""
        from core.services.audit import diff as diff_module

        with (
            patch.object(diff_module, "changed_fields_vs_db", wraps=changed_fields_vs_db) as diff_spy,
            patch.object(case_open, "save", wraps=case_open.save) as save_spy,
            pytest.raises(ValueError, match=r"^Feld 'status' darf nicht aktualisiert werden\.$"),
        ):
            apply_allowlisted_update(
                case_open,
                {"status": "closed"},
                allowed_fields={"title", "description"},
            )

        diff_spy.assert_not_called()
        save_spy.assert_not_called()

    def test_diffs_against_db_when_instance_is_already_mutated(self, case_open):
        """ModelForm-_post_clean darf den Audit-Diff nicht verschwinden lassen."""
        case_open.title = "Bereits in-memory gesetzt"

        changed_fields = apply_allowlisted_update(
            case_open,
            {"title": "Bereits in-memory gesetzt"},
            allowed_fields={"title"},
        )

        assert changed_fields == ["title"]
        case_open.refresh_from_db()
        assert case_open.title == "Bereits in-memory gesetzt"

    def test_saves_only_supplied_fields_plus_updated_at_in_order(self, case_open):
        fields = {
            "title": "Neuer Titel",
            "description": "Neue Beschreibung",
        }

        with patch.object(case_open, "save", wraps=case_open.save) as save_spy:
            changed_fields = apply_allowlisted_update(
                case_open,
                fields,
                allowed_fields={"title", "description"},
            )

        assert changed_fields == ["title", "description"]
        save_spy.assert_called_once_with(update_fields=["title", "description", "updated_at"])

    def test_before_save_hook_runs_after_assignment_and_before_write(self, case_open):
        observed = []

        with patch.object(case_open, "save", wraps=case_open.save) as save_spy:

            def before_save(instance, changed_fields):
                assert instance.title == "Neuer Titel"
                assert changed_fields == ["title"]
                save_spy.assert_not_called()
                observed.append("before_save")

            apply_allowlisted_update(
                case_open,
                {"title": "Neuer Titel"},
                allowed_fields={"title"},
                before_save=before_save,
            )

        assert observed == ["before_save"]
        save_spy.assert_called_once_with(update_fields=["title", "updated_at"])

    def test_empty_fields_still_diff_and_bump_updated_at(self, case_open):
        """Altsemantik: auch ein leeres Mapping speichert ``updated_at``."""
        from core.services.audit import diff as diff_module

        fields = {}
        with (
            patch.object(diff_module, "changed_fields_vs_db", return_value=[]) as diff_spy,
            patch.object(case_open, "save", wraps=case_open.save) as save_spy,
        ):
            changed_fields = apply_allowlisted_update(
                case_open,
                fields,
                allowed_fields={"title"},
            )

        assert changed_fields == []
        diff_spy.assert_called_once_with(case_open, fields)
        save_spy.assert_called_once_with(update_fields=["updated_at"])
