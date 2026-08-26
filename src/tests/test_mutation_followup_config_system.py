"""Mutation-Followup-Tests für ``core.services.config`` — Restlücken #1998.

Ergänzt die bestehenden Suiten ``test_config_service.py`` (Config-Service:
Audit + Reorder-Mechanik) und ``test_field_types_registry.py`` (Field-Type-
Registry) um gezielt gegen den Mutmut-Report von #1998 geschriebene Tests —
je Fall dokumentiert der Docstring, welche konkrete Mutation/Schwächung er
killt.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import RequestFactory

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    FieldCondition,
    FieldGroup,
    FieldTemplate,
)
from core.services.config import (
    _next_block_position,
    _validate_id_set,
    add_field_to_doctype,
    conflicting_report_key_assignment,
    delete_document_type,
    delete_field_condition,
    delete_field_group,
    delete_field_template,
    move_field,
    remove_field_from_doctype,
    reorder_blocks,
    reorder_fields,
    save_document_type,
    save_field_group,
    save_field_template,
    update_field_condition,
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


# ---------------------------------------------------------------------------
# Posten 5 — mutations.save_document_type: previous_sensitivity-Spy (Refs #1998)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveDocumentTypeBanRevisionWiring:
    def test_sensitivity_change_calls_bump_with_correct_previous_value(self, req, facility):
        """``save_document_type`` berechnet ``previous_sensitivity`` VOR dem
        ``form.save()`` (``form.initial.get("sensitivity", "")``) und reicht
        genau diesen alten Wert an
        ``bump_ban_revision_for_document_type_change`` weiter — bislang war
        dieser Verdrahtungs-Pfad komplett ungetestet: die Admin-Fallback-Seite
        (``test_zeitstrom_ban_watch.py::TestDocTypeAdminWriteBoundary``) läuft
        ausdrücklich NICHT über ``save_document_type``, und sonst importiert
        nur ``mutations.py`` selbst diese Funktion.

        Spy als MODUL-Attribut-Stub (``core.services.system.ban_revision``),
        NICHT über einen Proxy — ``save_document_type`` importiert die
        Funktion lazy per ``from ... import ...`` bei jedem Aufruf, liest das
        Modul-Attribut also zur Aufrufzeit neu ein.
        """
        from core.services.system import ban_revision as ban_revision_module

        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Hausverbot Spy",
            system_type=DocumentType.SystemType.BAN,
            sensitivity=DocumentType.Sensitivity.NORMAL,
        )
        calls = []

        def _spy(document_type, changed_fields, *, previous_sensitivity):
            calls.append((document_type, changed_fields, previous_sensitivity))

        monkeypatch_target = "bump_ban_revision_for_document_type_change"
        original = getattr(ban_revision_module, monkeypatch_target)
        setattr(ban_revision_module, monkeypatch_target, _spy)
        try:
            form_cls = modelform_factory(DocumentType, fields=["facility", "name", "sensitivity"])
            form = form_cls(
                instance=doc_type,
                data={
                    "facility": facility.pk,
                    "name": doc_type.name,
                    "sensitivity": DocumentType.Sensitivity.HIGH,
                },
            )
            assert form.is_valid(), form.errors
            save_document_type(req, form, creating=False)
        finally:
            setattr(ban_revision_module, monkeypatch_target, original)

        assert len(calls) == 1
        called_obj, called_changed_fields, called_previous = calls[0]
        assert called_obj.pk == doc_type.pk
        assert "sensitivity" in called_changed_fields
        # Der VORHERIGE Wert (NORMAL), nicht der neue (HIGH) — genau die
        # Unterscheidung, die ``form.initial`` VOR dem Save festhaelt.
        assert called_previous == DocumentType.Sensitivity.NORMAL


# ---------------------------------------------------------------------------
# Posten 7 — delete_field_group / _move_field_to_top_level / reorder_blocks:
# ZWEI FieldGroups am selben DocumentType (Refs #1998)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTwoFieldGroupsBlockSequence:
    def test_delete_one_of_two_groups_keeps_other_groups_recompacted_position(
        self, req, facility, doc_type_contact
    ):
        """``test_delete_group_places_inner_fields_at_group_position``
        (test_config_service.py) hatte nur EINE FieldGroup im Bestand und
        testete damit nie den Zweig, der eine ANDERE (nicht gelöschte) Gruppe
        unangetastet in ``new_order`` übernimmt
        (``elif block["kind"] == "group": new_order.append(...)``). Die
        gelöschte Gruppe bekommt hier ZWEI Innen-Felder, damit die
        verbleibende Gruppe nach dem Löschen eine ANDERE Position hat als
        vorher (reine Passthrough-Kopie ihrer alten sort_order würde sonst
        zufällig auch stimmen).

        Layout vorher: Dauer (frei, 0), G1 (1, 2 Innen-Felder), G2 (2),
        Notiz (frei, 3). Nach dem Löschen von G1: Dauer(0), Innen1(1),
        Innen2(2), G2(3, VERSCHOBEN von 2), Notiz(4).
        """
        dauer = FieldTemplate.objects.get(facility=facility, name="Dauer")
        notiz = FieldTemplate.objects.get(facility=facility, name="Notiz")
        dauer_dtf = doc_type_contact.fields.get(field_template=dauer)
        notiz_dtf = doc_type_contact.fields.get(field_template=notiz)
        dauer_dtf.sort_order = 0
        dauer_dtf.save(update_fields=["sort_order"])
        notiz_dtf.sort_order = 3
        notiz_dtf.save(update_fields=["sort_order"])

        grp1 = FieldGroup.objects.create(document_type=doc_type_contact, name="G1", sort_order=1)
        grp2 = FieldGroup.objects.create(document_type=doc_type_contact, name="G2", sort_order=2)
        ft_i1 = FieldTemplate.objects.create(facility=facility, name="Innen 1", field_type="text")
        ft_i2 = FieldTemplate.objects.create(facility=facility, name="Innen 2", field_type="text")
        inner1 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_i1, field_group=grp1, sort_order=0
        )
        inner2 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft_i2, field_group=grp1, sort_order=1
        )

        delete_field_group(req, grp1)

        for obj in (dauer_dtf, inner1, inner2, grp2, notiz_dtf):
            obj.refresh_from_db()
        assert dauer_dtf.field_group_id is None and dauer_dtf.sort_order == 0
        assert inner1.field_group_id is None and inner1.sort_order == 1
        assert inner2.field_group_id is None and inner2.sort_order == 2
        # G2 war vorher an Position 2 — durch die ZWEI Innen-Felder von G1
        # rückt sie jetzt auf Position 3 (nicht bloß unverändert übernommen).
        assert grp2.sort_order == 3
        assert notiz_dtf.field_group_id is None and notiz_dtf.sort_order == 4

    def test_reorder_blocks_with_two_groups_positions_both_independently(self, req, doc_type_contact):
        """``test_reorder_mixed_blocks_renumbers_and_audits_once``
        (test_config_service.py) hatte nur EINE Gruppe. Mit ZWEI Gruppen in
        ``ordered_blocks`` muss jede unabhängig ihre eigene Zielposition
        bekommen — nicht beide dieselbe (z. B. durch eine ID-Set-Prüfung, die
        Gruppen nur als Menge, nicht positionsgenau behandelt)."""
        grp1 = FieldGroup.objects.create(document_type=doc_type_contact, name="R1", sort_order=2)
        grp2 = FieldGroup.objects.create(document_type=doc_type_contact, name="R2", sort_order=3)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))

        reorder_blocks(
            req,
            doc_type_contact,
            [("group", grp2.id), ("field", free[0].id), ("group", grp1.id), ("field", free[1].id)],
        )

        grp1.refresh_from_db()
        grp2.refresh_from_db()
        free[0].refresh_from_db()
        free[1].refresh_from_db()
        assert (grp2.sort_order, free[0].sort_order, grp1.sort_order, free[1].sort_order) == (0, 1, 2, 3)

    def test_move_to_top_level_with_second_group_keeps_its_own_position(self, req, doc_type_contact):
        """``test_move_to_top_level_position_persists`` (test_config_service.py)
        hatte nur EINE Gruppe. Mit einer ZWEITEN Gruppe im Bestand muss
        ``_move_field_to_top_level`` beide Gruppen-IDs korrekt gegen
        ``existing_group_ids`` validieren UND beiden ihre je eigene Zielposition
        aus ``ordered_blocks`` zuweisen."""
        grp_src = FieldGroup.objects.create(document_type=doc_type_contact, name="Quelle", sort_order=0)
        grp_other = FieldGroup.objects.create(document_type=doc_type_contact, name="Andere", sort_order=1)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        moved = free[0]
        moved.field_group = grp_src
        moved.sort_order = 0
        moved.save(update_fields=["field_group", "sort_order"])
        other_free = free[1]

        # Ziel-Top-Level-Reihenfolge: moved (frei), grp_other, grp_src, other_free.
        move_field(
            req,
            doc_type_contact,
            moved.id,
            None,
            ordered_blocks=[
                ("field", moved.id),
                ("group", grp_other.id),
                ("group", grp_src.id),
                ("field", other_free.id),
            ],
        )

        moved.refresh_from_db()
        grp_other.refresh_from_db()
        grp_src.refresh_from_db()
        other_free.refresh_from_db()
        assert moved.field_group_id is None
        assert (moved.sort_order, grp_other.sort_order, grp_src.sort_order, other_free.sort_order) == (0, 1, 2, 3)


# ---------------------------------------------------------------------------
# Posten 8 — block_order._validate_id_set: gleiche Länge, andere Mitgliedsmenge
# ---------------------------------------------------------------------------


class TestValidateIdSetSameLengthDifferentMembership:
    def test_same_length_but_different_membership_raises(self):
        """Die bisherigen Aufrufer (``reorder_blocks``/``reorder_fields``-Tests)
        deckten immer nur LÄNGEN-Abweichungen ab (fehlendes/fremdes Element ändert
        auch die Länge). Der Fall „gleiche Länge, andere Menge"
        (``ordered=[A,B]``, ``existing={A,C}``) prüft den ``set(requested) !=
        existing``-Teil der Bedingung isoliert — ein Mutant, der ``or`` durch
        ``and`` ersetzt, würde bei gleicher Länge NICHT mehr raisen."""
        with pytest.raises(ValueError):
            _validate_id_set(["A", "B"], {"A", "C"})


# ---------------------------------------------------------------------------
# Posten 9 — block_order._next_block_position: leerer DocumentType (Refs #1998)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestNextBlockPositionEmptyDocType:
    def test_zero_for_first_element_when_doc_type_has_no_blocks(self, facility):
        """Off-by-one-Basisfall: ein DocumentType OHNE jegliche Blöcke (weder
        Gruppen noch ungruppierte Felder) muss ``0`` für das ERSTE Element
        liefern. Bestehende Tests (``test_add_ungrouped_goes_to_end_of_global_
        sequence``, ``test_new_group_appends_to_end_of_global_sequence``)
        nutzen alle ``doc_type_contact`` MIT bereits vorhandenen Feldern —
        der leere Basisfall (``candidates == []`` → ``return 0``) war
        ungetestet."""
        doc_type = DocumentType.objects.create(facility=facility, name="Leer")
        assert _next_block_position(doc_type) == 0


# ---------------------------------------------------------------------------
# Posten 12 — generischer Parametrized-Test: CONFIG_*-Audit trägt korrektes
# target_type/detail (Refs #1998, ~35 Survivor über zwölf Funktionen)
# ---------------------------------------------------------------------------
#
# Jeder Fall ruft die Mutation DIREKT auf der Service-Schicht auf (nicht über
# den View/HTTP-Layer) und liefert ``(AuditLog-Eintrag, erwarteter target_type,
# erwartete detail-Teilmenge)``. Die DELETE-Funktionen setzen ``target_type``
# EXPLIZIT (das Objekt existiert nach ``.delete()`` nicht mehr, ``pk`` wird
# genullt) — diese ``detail``-Inhalte waren in den bestehenden Suiten teils
# gar nicht geprüft (nur die reine Existenz des Audit-Eintrags).


def _case_add_field_to_doctype(req, facility, doc_type_contact):
    ft = FieldTemplate.objects.create(facility=facility, name="Param-Add", field_type="text")
    dtf = add_field_to_doctype(req, doc_type_contact, ft)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(dtf.pk)).latest("timestamp")
    return entry, "DocumentTypeField", {"name": "Param-Add", "changed_fields": []}


def _case_delete_document_type(req, facility, doc_type_contact):
    dt = DocumentType.objects.create(facility=facility, name="Param-Delete-DT")
    pk = str(dt.pk)
    delete_document_type(req, dt)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
    return entry, "DocumentType", {"name": "Param-Delete-DT"}


def _case_save_field_template(req, facility, doc_type_contact):
    form = modelform_factory(FieldTemplate, fields=["name", "field_type"])(
        data={"name": "Param-FT", "field_type": "text"}
    )
    assert form.is_valid(), form.errors
    ft = save_field_template(req, form, creating=True, facility=facility)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(ft.pk)).latest("timestamp")
    return entry, "FieldTemplate", {"name": "Param-FT"}


def _case_save_field_group(req, facility, doc_type_contact):
    form = modelform_factory(FieldGroup, fields=["name"])(data={"name": "Param-Group"})
    assert form.is_valid(), form.errors
    grp = save_field_group(req, form, doc_type=doc_type_contact, creating=True)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(grp.pk)).latest("timestamp")
    return entry, "FieldGroup", {"name": "Param-Group"}


def _case_delete_field_group(req, facility, doc_type_contact):
    grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Param-Delete-Group")
    pk = str(grp.pk)
    delete_field_group(req, grp)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
    return entry, "FieldGroup", {"name": "Param-Delete-Group"}


def _case_remove_field_from_doctype(req, facility, doc_type_contact):
    ft = FieldTemplate.objects.create(facility=facility, name="Param-Remove", field_type="text")
    dtf = DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft, sort_order=9)
    pk = str(dtf.pk)
    remove_field_from_doctype(req, dtf)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
    return entry, "DocumentTypeField", {"name": "Param-Remove"}


def _case_delete_field_template(req, facility, doc_type_contact):
    ft = FieldTemplate.objects.create(facility=facility, name="Param-Delete-FT", field_type="text")
    pk = str(ft.pk)
    delete_field_template(req, ft)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
    return entry, "FieldTemplate", {"name": "Param-Delete-FT"}


def _case_reorder_blocks(req, facility, doc_type_contact):
    free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
    reorder_blocks(req, doc_type_contact, [("field", free[1].id), ("field", free[0].id)])
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CONFIG_REORDER, target_id=str(doc_type_contact.pk)
    ).latest("timestamp")
    return entry, "DocumentType", {"operation": "block_order"}


def _case_reorder_fields(req, facility, doc_type_contact):
    grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Param-RF")
    ft1 = FieldTemplate.objects.create(facility=facility, name="RF-P1", field_type="text")
    ft2 = FieldTemplate.objects.create(facility=facility, name="RF-P2", field_type="text")
    d1 = add_field_to_doctype(req, doc_type_contact, ft1, field_group=grp)
    d2 = add_field_to_doctype(req, doc_type_contact, ft2, field_group=grp)
    reorder_fields(req, doc_type_contact, grp.id, [d2.id, d1.id])
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CONFIG_REORDER, target_id=str(doc_type_contact.pk)
    ).latest("timestamp")
    return entry, "DocumentType", {"operation": "field_order"}


def _case_move_field(req, facility, doc_type_contact):
    grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Param-Move-Dst")
    free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
    moved = free[0]
    move_field(req, doc_type_contact, moved.id, grp.id, ordered_dtf_ids=[moved.id])
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CONFIG_REORDER, target_id=str(doc_type_contact.pk)
    ).latest("timestamp")
    return entry, "DocumentType", {"operation": "move_field"}


def _case_update_field_condition(req, facility, doc_type_contact):
    dauer = doc_type_contact.fields.get(field_template__slug="dauer")
    notiz = doc_type_contact.fields.get(field_template__slug="notiz")
    condition = FieldCondition.objects.create(
        target_field=notiz,
        depends_on_field=dauer,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )
    update_field_condition(
        req,
        condition,
        target_field=notiz,
        depends_on_field=dauer,
        operator=FieldCondition.Operator.LESS_THAN,
        value=20,
        group_index=0,
    )
    entry = AuditLog.objects.filter(
        action=AuditLog.Action.CONFIG_UPDATE, target_id=str(condition.pk)
    ).latest("timestamp")
    return entry, "FieldCondition", {"operator": "less_than", "target_slug": "notiz", "source_slug": "dauer"}


def _case_delete_field_condition(req, facility, doc_type_contact):
    dauer = doc_type_contact.fields.get(field_template__slug="dauer")
    notiz = doc_type_contact.fields.get(field_template__slug="notiz")
    condition = FieldCondition.objects.create(
        target_field=notiz,
        depends_on_field=dauer,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )
    pk = str(condition.pk)
    delete_field_condition(req, condition)
    entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).latest("timestamp")
    return entry, "FieldCondition", {"operator": "greater_than", "target_slug": "notiz", "source_slug": "dauer"}


_CONFIG_AUDIT_CASES = {
    "add_field_to_doctype": _case_add_field_to_doctype,
    "delete_document_type": _case_delete_document_type,
    "save_field_template": _case_save_field_template,
    "save_field_group": _case_save_field_group,
    "delete_field_group": _case_delete_field_group,
    "remove_field_from_doctype": _case_remove_field_from_doctype,
    "delete_field_template": _case_delete_field_template,
    "reorder_blocks": _case_reorder_blocks,
    "reorder_fields": _case_reorder_fields,
    "move_field": _case_move_field,
    "update_field_condition": _case_update_field_condition,
    "delete_field_condition": _case_delete_field_condition,
}


@pytest.mark.django_db
@pytest.mark.parametrize("case_name", sorted(_CONFIG_AUDIT_CASES))
def test_config_audit_target_type_and_detail(case_name, req, facility, doc_type_contact):
    """Generischer Test über zwölf Config-Mutationen: jede muss einen
    Audit-Eintrag mit korrektem ``target_type`` UND inhaltlich korrektem
    ``detail`` schreiben (Refs #1998). Ruft die Service-Funktion DIREKT auf
    (nicht über den View/HTTP-Layer) — u. a. für die DELETE-Funktionen und
    die ``FieldCondition``-Mutationen war der ``detail``-Inhalt bislang nicht
    (oder nur über den View-Layer) geprüft.
    """
    entry, expected_target_type, expected_detail_subset = _CONFIG_AUDIT_CASES[case_name](req, facility, doc_type_contact)
    assert entry.target_type == expected_target_type
    for key, expected_value in expected_detail_subset.items():
        assert entry.detail[key] == expected_value, f"{case_name}: detail[{key!r}] != {expected_value!r}"
