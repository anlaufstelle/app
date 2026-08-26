"""Mutation-Followup-Tests für ``core.services.config`` — Restlücken #1998.

Ergänzt die bestehenden Suiten ``test_config_service.py`` (Config-Service:
Audit + Reorder-Mechanik) und ``test_field_types_registry.py`` (Field-Type-
Registry) um gezielt gegen den Mutmut-Report von #1998 geschriebene Tests —
je Fall dokumentiert der Docstring, welche konkrete Mutation/Schwächung er
killt.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import RequestFactory

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    FieldGroup,
    FieldTemplate,
)
from core.services.config import (
    add_field_to_doctype,
    conflicting_report_key_assignment,
    delete_field_group,
    move_field,
    reorder_blocks,
    save_document_type,
    save_field_template,
)


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (Muster test_config_service.py)."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


# ---------------------------------------------------------------------------
# Posten 2 — block_order.move_field: Ownership-Check des dtf_id (Refs #1998)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMoveFieldDtfOwnership:
    def test_foreign_dtf_id_raises(self, req, facility, doc_type_contact):
        """Der eigene Ownership-Check von ``move_field``
        (``DocumentTypeField.objects.filter(pk=dtf_id, document_type=doc_type)``)
        war bislang komplett ungetestet — nur der Gruppen-Ownership-Check in
        ``_move_field_into_group`` hatte einen Test (``test_cross_doctype_group_raises``).

        ``ordered_dtf_ids`` wird so gewählt, dass sie GENAU der erwarteten
        Zielmenge entspricht (Ziel-Gruppe leer + das fremde Feld) — würde der
        ``document_type=doc_type``-Teil des Filters entfernt, fände ``move_field``
        das fremde Feld trotzdem, verschöbe es unbemerkt in eine Gruppe eines
        FREMDEN Dokumentationstyps und würfe KEINEN ValueError mehr.
        """
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer Typ")
        ft = FieldTemplate.objects.create(facility=facility, name="Fremdfeld", field_type="text")
        foreign_dtf = DocumentTypeField.objects.create(document_type=other_dt, field_template=ft, sort_order=0)
        target_grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Ziel")
        with pytest.raises(ValueError):
            move_field(req, doc_type_contact, foreign_dtf.id, target_grp.id, ordered_dtf_ids=[foreign_dtf.id])


# ---------------------------------------------------------------------------
# Posten 3 — mutations.save_field_template: E-J-Konflikt über die VOLLE
# Aufrufkette (nicht nur conflicting_report_key_assignment direkt, Refs #1998)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveFieldTemplateReportKeyConflict:
    def test_setting_report_key_that_collides_with_active_binding_raises(self, req, facility, doc_type_contact):
        """``save_field_template`` verdrahtet ``conflicting_report_key_assignment``
        selbst (E-J, der „vierte Weg" der Doppelbindung) — bislang war nur die
        Prüf-Funktion direkt getestet (``test_reporting_field_cardinality.py::
        test_service_weist_den_nachtraeglichen_schluessel_ab``), NIE der
        Verdrahtungs-Pfad in ``save_field_template`` selbst. ``doc_type_contact``
        hat bereits ein aktiv gebundenes Feld ("Dauer", report_key=
        "dauer_minuten"); das zweite Feld ("Notiz") bekommt über
        ``save_field_template`` denselben Schlüssel nachgetragen — muss
        ValidationError werfen, OHNE zu speichern.
        """
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        form = modelform_factory(FieldTemplate, fields=["name", "field_type", "report_key"])(
            instance=notiz,
            data={"name": notiz.name, "field_type": notiz.field_type, "report_key": "dauer_minuten"},
        )
        assert form.is_valid(), form.errors
        with pytest.raises(ValidationError):
            save_field_template(req, form, creating=False, facility=facility)
        notiz.refresh_from_db()
        assert notiz.report_key in (None, "")


# ---------------------------------------------------------------------------
# Posten 4 — mutations.conflicting_report_key_assignment: drei gezielte Pfade
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConflictingReportKeyAssignmentPaths:
    def test_field_template_itself_carries_the_checked_key_is_excluded(self, facility, doc_type_contact):
        """(a) Selbst-Ausschluss-Pfad: trägt ``field_template`` selbst schon den
        zu prüfenden ``report_key`` (der häufige Re-Save-Fall — Name ändern,
        Schlüssel unverändert lassen), darf die Funktion sich nicht selbst als
        Konfliktpartner finden (``.exclude(pk=field_template.pk)``)."""
        dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert conflicting_report_key_assignment(dauer, "dauer_minuten", is_active=True) is None

    def test_active_field_with_different_report_key_is_not_a_conflict(self, facility, doc_type_contact):
        """(b) report_key-Filter-Pfad: ein AKTIVES Feld hängt am selben Typ,
        trägt aber einen ANDEREN Schlüssel — die Funktion darf es nicht als
        Konflikt melden (ein Mutant, der den ``report_key=report_key``-Filter
        entfernt, fände "Dauer" fälschlich als Bestand, obwohl der Schlüssel
        gar nicht übereinstimmt)."""
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        assert conflicting_report_key_assignment(notiz, "ein_anderer_schluessel", is_active=True) is None

    def test_inactive_field_with_same_report_key_is_not_a_conflict(self, facility, doc_type_contact):
        """(c) is_active-Filter-Pfad: ein INAKTIVES Feld mit demselben
        ``report_key`` hängt am Zieltyp — die interne FieldTemplate-Abfrage
        filtert bewusst auf ``is_active=True`` (das Backend liest nur aktive
        Vorlagen). Die Fixture-eigene "Dauer" (aktiv, report_key=
        "dauer_minuten") wird dafür deaktiviert, damit die einzige verbliebene
        Vorlage mit diesem Schlüssel INAKTIV ist — sonst kollidiert der Test
        mit der Bestands-Bindung von "Dauer" statt der Inaktiv-Vorlage."""
        dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        dauer.is_active = False
        dauer.save(update_fields=["is_active"])
        ft_inactive = FieldTemplate.objects.create(
            facility=facility,
            name="Dauer (inaktiv)",
            field_type="number",
            report_key="dauer_minuten",
            is_active=False,
        )
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft_inactive, sort_order=5)
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        assert conflicting_report_key_assignment(notiz, "dauer_minuten", is_active=True) is None
