"""Tests für den Config-Service (M0, Refs #1056).

Der Config-Service kapselt alle Mutationen an der Formular-Konfiguration
(DocumentType, FieldTemplate, FieldGroup, DocumentTypeField) samt Audit-Spur
(ADR-007: Views dünn, Mutationen + Audit im Service). Getestet werden je
Mutationstyp der Audit-Eintrag inkl. ``detail``-Inhalt, die Lösch-Blocker
(referenzierende Events/Attachments/Feld-Zuordnungen) sowie die atomaren
Reorder-Operationen mit strikter ID-Set-Validierung.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import RequestFactory
from django.utils import timezone

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    Event,
    EventAttachment,
    FieldGroup,
    FieldTemplate,
)
from core.services.config import (
    ConfigDeleteBlocked,
    add_field_to_doctype,
    delete_document_type,
    delete_field_group,
    delete_field_template,
    iter_blocks_in_order,
    iter_fields_in_block_order,
    move_field,
    remove_field_from_doctype,
    reorder_blocks,
    reorder_fields,
    save_document_type,
    save_field_group,
    save_field_template,
    set_field_template_active,
)


@pytest.fixture
def req(staff_user, facility):
    """Request mit authentifiziertem User + Facility-Scope (wie FacilityScopeMiddleware)."""
    request = RequestFactory().post("/")
    request.user = staff_user
    request.current_facility = facility
    return request


def _make_event(facility, client, doc_type, staff_user, data_json=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=timezone.now(),
        data_json=data_json or {},
        created_by=staff_user,
    )


def _make_attachment(event, field_template):
    return EventAttachment.objects.create(
        event=event,
        field_template=field_template,
        storage_filename="abcd1234.enc",
        original_filename_encrypted={"ct": "x"},
        file_size=1,
        mime_type="application/pdf",
    )


# ---------------------------------------------------------------------------
# DocumentType — save (create/update) + delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveDocumentType:
    def _form_cls(self):
        return modelform_factory(DocumentType, fields=["facility", "name", "category"])

    def test_create_writes_config_create_audit(self, req, facility):
        form = self._form_cls()(data={"facility": facility.pk, "name": "Neuer Typ", "category": "contact"})
        assert form.is_valid(), form.errors
        obj = save_document_type(req, form, creating=True)
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(obj.pk)).latest("timestamp")
        assert entry.detail["name"] == "Neuer Typ"
        assert isinstance(entry.detail["changed_fields"], list)
        assert "name" in entry.detail["changed_fields"]

    def test_update_writes_config_update_audit_with_changed_fields(self, req, doc_type_contact, facility):
        form = self._form_cls()(
            instance=doc_type_contact,
            data={"facility": facility.pk, "name": "Umbenannt", "category": doc_type_contact.category},
        )
        assert form.is_valid(), form.errors
        save_document_type(req, form, creating=False)
        entry = AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_UPDATE, target_id=str(doc_type_contact.pk)
        ).latest("timestamp")
        assert entry.detail["changed_fields"] == ["name"]


@pytest.mark.django_db
class TestDeleteDocumentType:
    def test_blocked_by_referencing_events(self, req, facility, client_identified, doc_type_contact, staff_user):
        _make_event(facility, client_identified, doc_type_contact, staff_user)
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_document_type(req, doc_type_contact)
        assert exc.value.reasons
        assert any("Ereignis" in r for r in exc.value.reasons)
        assert DocumentType.objects.filter(pk=doc_type_contact.pk).exists()
        assert not AuditLog.objects.filter(
            action=AuditLog.Action.CONFIG_DELETE, target_id=str(doc_type_contact.pk)
        ).exists()

    def test_success_without_references(self, req, facility):
        dt = DocumentType.objects.create(facility=facility, name="Wegwerf", category="contact")
        pk = str(dt.pk)  # Django nullt obj.pk beim delete() → vorher festhalten.
        delete_document_type(req, dt)
        assert not DocumentType.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).exists()


# ---------------------------------------------------------------------------
# FieldTemplate — save/create, activate toggle, delete-blocker
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveFieldTemplate:
    def test_create_sets_facility_and_audits(self, req, facility):
        form = modelform_factory(FieldTemplate, fields=["name", "field_type"])(
            data={"name": "Via Service", "field_type": "text"}
        )
        assert form.is_valid(), form.errors
        ft = save_field_template(req, form, creating=True, facility=facility)
        assert ft.facility == facility
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(ft.pk)).latest("timestamp")
        assert entry.detail["name"] == "Via Service"

    def test_set_active_toggle_audits_is_active(self, req, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Toggle", field_type="text", is_active=True)
        set_field_template_active(req, ft, active=False)
        ft.refresh_from_db()
        assert ft.is_active is False
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_UPDATE, target_id=str(ft.pk)).latest("timestamp")
        assert entry.detail["changed_fields"] == ["is_active"]


@pytest.mark.django_db
class TestDeleteFieldTemplate:
    def test_blocked_by_doctype_field_reference(self, req, facility, doc_type_contact):
        ft = FieldTemplate.objects.create(facility=facility, name="Feld A", field_type="text")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft)
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_field_template(req, ft)
        assert len(exc.value.reasons) == 1
        assert FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_blocked_by_attachment_reference(self, req, facility, client_identified, doc_type_contact, staff_user):
        ft = FieldTemplate.objects.create(facility=facility, name="Feld Anhang", field_type="file")
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user)
        _make_attachment(ev, ft)
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_field_template(req, ft)
        assert len(exc.value.reasons) == 1
        assert any("anhang" in r.lower() or "anhänge" in r.lower() for r in exc.value.reasons)

    def test_blocked_by_event_data_signal(self, req, facility, client_identified, doc_type_contact, staff_user):
        ft = FieldTemplate.objects.create(facility=facility, name="Feld Daten", field_type="text")
        _make_event(facility, client_identified, doc_type_contact, staff_user, data_json={ft.slug: "wert"})
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_field_template(req, ft)
        assert len(exc.value.reasons) == 1

    def test_blocked_by_all_three_reasons_combined(
        self, req, facility, client_identified, doc_type_contact, staff_user
    ):
        ft = FieldTemplate.objects.create(facility=facility, name="Feld Kombi", field_type="file")
        DocumentTypeField.objects.create(document_type=doc_type_contact, field_template=ft)
        ev = _make_event(facility, client_identified, doc_type_contact, staff_user, data_json={ft.slug: "wert"})
        _make_attachment(ev, ft)
        with pytest.raises(ConfigDeleteBlocked) as exc:
            delete_field_template(req, ft)
        assert len(exc.value.reasons) == 3
        assert FieldTemplate.objects.filter(pk=ft.pk).exists()

    def test_success_without_references(self, req, facility):
        ft = FieldTemplate.objects.create(facility=facility, name="Frei", field_type="text")
        pk = str(ft.pk)  # Django nullt obj.pk beim delete() → vorher festhalten.
        delete_field_template(req, ft)
        assert not FieldTemplate.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=pk).exists()


# ---------------------------------------------------------------------------
# FieldGroup — save + delete (SET_NULL: Felder werden gruppenlos)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFieldGroup:
    def test_create_sets_doctype_and_audits(self, req, doc_type_contact):
        form = modelform_factory(FieldGroup, fields=["name"])(data={"name": "Abschnitt A"})
        assert form.is_valid(), form.errors
        grp = save_field_group(req, form, doc_type=doc_type_contact, creating=True)
        assert grp.document_type == doc_type_contact
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(grp.pk)).exists()

    def test_delete_makes_fields_groupless_not_deleted(self, req, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Gruppe X")
        grp_pk = str(grp.pk)  # Django nullt obj.pk beim delete() → vorher festhalten.
        dtf = doc_type_contact.fields.first()
        dtf.field_group = grp
        dtf.save(update_fields=["field_group"])
        delete_field_group(req, grp)
        assert not FieldGroup.objects.filter(pk=grp_pk).exists()
        dtf.refresh_from_db()
        assert dtf.field_group is None
        assert DocumentTypeField.objects.filter(pk=dtf.pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=grp_pk).exists()


# ---------------------------------------------------------------------------
# DocumentTypeField — add (sort_order=max+1 je Scope) / remove
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAddRemoveField:
    def test_add_ungrouped_appends_at_max_plus_one(self, req, facility, doc_type_contact):
        # doc_type_contact hat bereits 2 ungruppierte Felder (sort 0, 1).
        ft = FieldTemplate.objects.create(facility=facility, name="Neu", field_type="text")
        dtf = add_field_to_doctype(req, doc_type_contact, ft)
        assert dtf.sort_order == 2
        assert dtf.field_group is None
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_CREATE, target_id=str(dtf.pk)).exists()

    def test_add_into_group_scopes_sort_order(self, req, facility, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G")
        ft_a = FieldTemplate.objects.create(facility=facility, name="A", field_type="text")
        ft_b = FieldTemplate.objects.create(facility=facility, name="B", field_type="text")
        dtf_a = add_field_to_doctype(req, doc_type_contact, ft_a, field_group=grp)
        dtf_b = add_field_to_doctype(req, doc_type_contact, ft_b, field_group=grp)
        # Trotz 2 ungruppierter Bestandsfelder startet die Gruppe bei 0.
        assert dtf_a.sort_order == 0
        assert dtf_b.sort_order == 1

    def test_add_duplicate_raises(self, req, facility, doc_type_contact):
        existing_ft = FieldTemplate.objects.get(facility=facility, name="Dauer")
        with pytest.raises(ValidationError):
            add_field_to_doctype(req, doc_type_contact, existing_ft)

    def test_add_with_foreign_group_raises_and_creates_nothing(self, req, facility, doc_type_contact):
        # Befund A (Refs #1056): eine FieldGroup aus einem FREMDEN DocumentType
        # darf nicht durchrutschen — .create() ruft full_clean() nie auf, der
        # DocumentTypeField.clean()-Guard greift also nicht. Der Service muss die
        # Zugehörigkeit selbst prüfen (Fehlerklasse konsistent zu move_field →
        # ValueError für HTTP 400) und darf keinen DTF anlegen.
        other_dt = DocumentType.objects.create(facility=facility, name="Fremd-Typ", category="contact")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremde Gruppe")
        ft = FieldTemplate.objects.create(facility=facility, name="Neu-Fremd", field_type="text")
        before = DocumentTypeField.objects.filter(document_type=doc_type_contact).count()
        with pytest.raises(ValueError):
            add_field_to_doctype(req, doc_type_contact, ft, field_group=foreign_grp)
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact).count() == before
        assert not DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=ft).exists()

    def test_add_duplicate_translates_integrity_error(self, req, facility, doc_type_contact, monkeypatch):
        # Befund B (Refs #1056): die Doppelprüfung ist check-then-create. Fällt der
        # exists()-Vorabtreffer aus (paralleles Add desselben Paars), schlägt die
        # UniqueConstraint beim create() zu. Ohne Übersetzung wäre das eine
        # unbehandelte IntegrityError (HTTP 500) statt der im Docstring
        # versprochenen ValidationError. Wir erzwingen den Race, indem wir den
        # exists()-Vorabtreffer auf False zwingen (QuerySet.exists am Modell
        # gestubbt, kein Proxy) — dann muss das create() die IntegrityError in
        # eine ValidationError übersetzen (transaction-sicher, sonst vergiftet der
        # Fehler die umgebende Test-Transaktion).
        existing_ft = FieldTemplate.objects.get(facility=facility, name="Dauer")
        assert DocumentTypeField.objects.filter(document_type=doc_type_contact, field_template=existing_ft).exists()
        from django.db.models.query import QuerySet

        monkeypatch.setattr(QuerySet, "exists", lambda self: False)
        with pytest.raises(ValidationError):
            add_field_to_doctype(req, doc_type_contact, existing_ft)

    def test_remove_field_audits_config_delete(self, req, doc_type_contact):
        dtf = doc_type_contact.fields.first()
        pk = dtf.pk
        remove_field_from_doctype(req, dtf)
        assert not DocumentTypeField.objects.filter(pk=pk).exists()
        assert AuditLog.objects.filter(action=AuditLog.Action.CONFIG_DELETE, target_id=str(pk)).exists()


# ---------------------------------------------------------------------------
# Globale Block-Sequenz (#1713) — Block-Reorder / Feld-Reorder / Move / Helfer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReorderBlocks:
    def test_reorder_mixed_blocks_renumbers_and_audits_once(self, req, doc_type_contact):
        # doc_type_contact: 2 freie Felder (Blockpos 0,1) + 1 Gruppe (Blockpos 2).
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=2)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        before = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).count()
        # Neue Reihenfolge: Gruppe zuerst, dann freies Feld 1, dann freies Feld 0.
        reorder_blocks(
            req,
            doc_type_contact,
            [("group", grp.id), ("field", free[1].id), ("field", free[0].id)],
        )
        grp.refresh_from_db()
        free[0].refresh_from_db()
        free[1].refresh_from_db()
        assert grp.sort_order == 0
        assert free[1].sort_order == 1
        assert free[0].sort_order == 2
        after = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).count()
        assert after == before + 1
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).latest("timestamp")
        assert entry.detail["operation"] == "block_order"

    def test_incomplete_field_set_raises_and_leaves_db_unchanged(self, req, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=2)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        with pytest.raises(ValueError):
            # Ein freies Feld fehlt in der Blockliste → 400.
            reorder_blocks(req, doc_type_contact, [("group", grp.id), ("field", free[0].id)])
        grp.refresh_from_db()
        free[0].refresh_from_db()
        free[1].refresh_from_db()
        assert (free[0].sort_order, free[1].sort_order, grp.sort_order) == (0, 1, 2)

    def test_foreign_group_id_raises(self, req, doc_type_contact):
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        with pytest.raises(ValueError):
            reorder_blocks(
                req,
                doc_type_contact,
                [("group", uuid4()), ("field", free[0].id), ("field", free[1].id)],
            )


@pytest.mark.django_db
class TestReorderFields:
    def test_reorder_within_group(self, req, facility, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="RF")
        ft1 = FieldTemplate.objects.create(facility=facility, name="RF-1", field_type="text")
        ft2 = FieldTemplate.objects.create(facility=facility, name="RF-2", field_type="text")
        d1 = add_field_to_doctype(req, doc_type_contact, ft1, field_group=grp)
        d2 = add_field_to_doctype(req, doc_type_contact, ft2, field_group=grp)
        reorder_fields(req, doc_type_contact, grp.id, [d2.id, d1.id])
        d1.refresh_from_db()
        d2.refresh_from_db()
        assert d2.sort_order == 0
        assert d1.sort_order == 1
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).latest("timestamp")
        assert entry.detail["operation"] == "field_order"

    def test_foreign_dtf_id_raises(self, req, facility, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="RF")
        ft1 = FieldTemplate.objects.create(facility=facility, name="RF-1", field_type="text")
        d1 = add_field_to_doctype(req, doc_type_contact, ft1, field_group=grp)
        with pytest.raises(ValueError):
            reorder_fields(req, doc_type_contact, grp.id, [d1.id, uuid4()])


@pytest.mark.django_db
class TestMoveField:
    def test_move_into_group_sets_fk_and_compacts_sequence(self, req, doc_type_contact):
        # Ein freies Feld (Blockpos 0) wandert in eine Gruppe (Blockpos 2) →
        # Sequenz kompaktiert: die Lücke des Ex-Blocks verschwindet.
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst", sort_order=2)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        moved, stays = free[0], free[1]
        move_field(req, doc_type_contact, moved.id, grp_dst.id, ordered_dtf_ids=[moved.id])
        moved.refresh_from_db()
        stays.refresh_from_db()
        grp_dst.refresh_from_db()
        assert moved.field_group_id == grp_dst.id
        assert moved.sort_order == 0  # Innen-Position der Zielgruppe
        # Kompaktierte Top-Level-Blöcke: das verbliebene freie Feld + die Gruppe.
        assert (stays.sort_order, grp_dst.sort_order) == (0, 1)
        entry = AuditLog.objects.filter(action=AuditLog.Action.CONFIG_REORDER).latest("timestamp")
        assert entry.detail["operation"] == "move_field"

    def test_move_into_populated_group_orders_correctly(self, req, facility, doc_type_contact):
        grp_dst = FieldGroup.objects.create(document_type=doc_type_contact, name="Dst2")
        ft_x = FieldTemplate.objects.create(facility=facility, name="X", field_type="text")
        dtf_x = add_field_to_doctype(req, doc_type_contact, ft_x, field_group=grp_dst)
        dtf_move = doc_type_contact.fields.filter(field_group__isnull=True).first()
        move_field(req, doc_type_contact, dtf_move.id, grp_dst.id, ordered_dtf_ids=[dtf_x.id, dtf_move.id])
        dtf_x.refresh_from_db()
        dtf_move.refresh_from_db()
        assert dtf_x.sort_order == 0
        assert dtf_move.sort_order == 1
        assert dtf_move.field_group_id == grp_dst.id

    def test_move_to_top_level_position_persists(self, req, doc_type_contact):
        # Feld sitzt in einer Gruppe; es wird per Blockliste ZWISCHEN Gruppe und
        # freies Feld an Top-Level gezogen.
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="Src", sort_order=0)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        moved = free[0]
        moved.field_group = grp
        moved.sort_order = 0
        moved.save(update_fields=["field_group", "sort_order"])
        # Restliche Top-Level-Blöcke jetzt: Gruppe (0), freies Feld free[1] (1).
        other_free = free[1]
        # Zielreihenfolge: Gruppe, moved (frei), other_free.
        move_field(
            req,
            doc_type_contact,
            moved.id,
            None,
            ordered_blocks=[("group", grp.id), ("field", moved.id), ("field", other_free.id)],
        )
        moved.refresh_from_db()
        grp.refresh_from_db()
        other_free.refresh_from_db()
        assert moved.field_group_id is None
        assert (grp.sort_order, moved.sort_order, other_free.sort_order) == (0, 1, 2)

    def test_cross_doctype_group_raises(self, req, facility, doc_type_contact):
        other_dt = DocumentType.objects.create(facility=facility, name="Anderer", category="contact")
        foreign_grp = FieldGroup.objects.create(document_type=other_dt, name="Fremd")
        dtf = doc_type_contact.fields.first()
        with pytest.raises(ValueError):
            move_field(req, doc_type_contact, dtf.id, foreign_grp.id, ordered_dtf_ids=[dtf.id])


@pytest.mark.django_db
class TestAddRemoveBlockSequence:
    def test_add_ungrouped_goes_to_end_of_global_sequence(self, req, facility, doc_type_contact):
        # Vorlage: 2 freie Felder (0,1) + eine Gruppe an Blockposition 2.
        FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=2)
        ft = FieldTemplate.objects.create(facility=facility, name="Neu", field_type="text")
        dtf = add_field_to_doctype(req, doc_type_contact, ft)
        assert dtf.field_group_id is None
        # Ans Ende der GLOBALEN Sequenz (max Blockposition 2) + 1 = 3.
        assert dtf.sort_order == 3

    def test_remove_ungrouped_compacts_sequence(self, req, doc_type_contact):
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=2)
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        # Das erste freie Feld (Blockpos 0) entfernen → Sequenz kompaktiert.
        remove_field_from_doctype(req, free[0])
        free[1].refresh_from_db()
        grp.refresh_from_db()
        assert (free[1].sort_order, grp.sort_order) == (0, 1)


@pytest.mark.django_db
class TestDeleteFieldGroupBlockSequence:
    def test_delete_group_places_inner_fields_at_group_position(self, req, facility, doc_type_contact):
        from core.services.config import delete_field_group

        # Layout: freies Feld A (Blockpos 0), Gruppe G (Blockpos 1) mit zwei
        # Innen-Feldern, freies Feld B (Blockpos 2).
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        a, b = free[0], free[1]
        a.sort_order = 0
        a.save(update_fields=["sort_order"])
        b.sort_order = 2
        b.save(update_fields=["sort_order"])
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=1)
        ft1 = FieldTemplate.objects.create(facility=facility, name="Innen 1", field_type="text")
        ft2 = FieldTemplate.objects.create(facility=facility, name="Innen 2", field_type="text")
        inner1 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft1, field_group=grp, sort_order=0
        )
        inner2 = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft2, field_group=grp, sort_order=1
        )

        delete_field_group(req, grp)

        for obj in (a, b, inner1, inner2):
            obj.refresh_from_db()
        # Die Ex-Gruppen-Felder stehen jetzt an der Gruppenposition (Blockpos 1,2),
        # das nachfolgende freie Feld B rutscht auf 3.
        assert a.field_group_id is None and a.sort_order == 0
        assert inner1.field_group_id is None and inner1.sort_order == 1
        assert inner2.field_group_id is None and inner2.sort_order == 2
        assert b.field_group_id is None and b.sort_order == 3


@pytest.mark.django_db
class TestBlockOrderHelpers:
    def test_iter_fields_in_block_order_interleaves(self, req, facility, doc_type_contact):
        # freies Feld (Blockpos 0), Gruppe (Blockpos 1) mit einem Feld, freies Feld (Blockpos 2).
        free = list(doc_type_contact.fields.filter(field_group__isnull=True).order_by("sort_order"))
        a, b = free[0], free[1]
        a.sort_order = 0
        a.save(update_fields=["sort_order"])
        b.sort_order = 2
        b.save(update_fields=["sort_order"])
        grp = FieldGroup.objects.create(document_type=doc_type_contact, name="G", sort_order=1)
        ft = FieldTemplate.objects.create(facility=facility, name="Innen", field_type="text")
        inner = DocumentTypeField.objects.create(
            document_type=doc_type_contact, field_template=ft, field_group=grp, sort_order=0
        )
        ordered = iter_fields_in_block_order(doc_type_contact)
        assert [d.pk for d in ordered] == [a.pk, inner.pk, b.pk]
        # iter_blocks_in_order spiegelt dieselbe Reihenfolge (Gruppe dazwischen).
        blocks = iter_blocks_in_order(doc_type_contact)
        kinds = [(blk["kind"], blk.get("group").pk if blk["kind"] == "group" else blk["field"].pk) for blk in blocks]
        assert kinds == [("field", a.pk), ("group", grp.pk), ("field", b.pk)]
