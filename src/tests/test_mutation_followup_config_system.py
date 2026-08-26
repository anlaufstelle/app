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
