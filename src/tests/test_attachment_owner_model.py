"""Owner-Generik des ``Attachment``-Modells (M24, Refs #1738, ADR-039 §1/§3/§4).

Prueft die Modell-Ebene der dreistufigen Migrationskette 0129–0131:
genau-ein-Owner-Constraint, Owner-Helfer und den ``deleted_at``-basierten
Manager (``FacilityScopedManager`` wuerde ``is_deleted`` vererben — das Feld
existiert am Attachment nicht, ADR-039 §3).
"""

import uuid

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Attachment, FieldTemplate
from core.models.attachment import OWNER_FIELDS, owner_field_name


def _attachment_kwargs(facility):
    ft = FieldTemplate.objects.create(
        facility=facility,
        name=f"Anhang {uuid.uuid4().hex[:6]}",
        field_type=FieldTemplate.FieldType.FILE,
    )
    return {
        "facility": facility,
        "field_template": ft,
        "storage_filename": f"{uuid.uuid4().hex}.enc",
        "original_filename_encrypted": {"v": 1, "data": "x"},
        "file_size": 1,
        "mime_type": "application/pdf",
    }


@pytest.mark.django_db
class TestExactlyOneOwner:
    def test_event_owned_erlaubt(self, facility, sample_event):
        att = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility))
        assert att.pk is not None

    def test_client_owned_erlaubt(self, facility, client_identified):
        att = Attachment.objects.create(client=client_identified, **_attachment_kwargs(facility))
        assert att.pk is not None

    def test_case_owned_erlaubt(self, facility, case_open):
        att = Attachment.objects.create(case=case_open, **_attachment_kwargs(facility))
        assert att.pk is not None

    def test_ohne_owner_verboten(self, facility):
        with pytest.raises(IntegrityError), transaction.atomic():
            Attachment.objects.create(**_attachment_kwargs(facility))

    @pytest.mark.parametrize("owner_combo", ["event_client", "event_case", "client_case", "alle_drei"])
    def test_mehrfach_owner_verboten(self, facility, sample_event, client_identified, case_open, owner_combo):
        """Alle vier illegalen Mehrfach-Kombinationen (G2-Befund: die drei
        Disjunkte des Constraints sind copy-paste-anfaellig — faellt ein
        ``__isnull=True``-Term weg, muss genau eine dieser Zeilen rot werden)."""
        combos = {
            "event_client": {"event": sample_event, "client": client_identified},
            "event_case": {"event": sample_event, "case": case_open},
            "client_case": {"client": client_identified, "case": case_open},
            "alle_drei": {"event": sample_event, "client": client_identified, "case": case_open},
        }
        with pytest.raises(IntegrityError), transaction.atomic():
            Attachment.objects.create(**combos[owner_combo], **_attachment_kwargs(facility))


@pytest.mark.django_db
class TestOwnerHelpers:
    def test_owner_type_und_owner(self, facility, sample_event, client_identified, case_open):
        kwargs = _attachment_kwargs(facility)
        by_owner = {
            "event": Attachment.objects.create(event=sample_event, **kwargs),
            "client": Attachment.objects.create(client=client_identified, **_attachment_kwargs(facility)),
            "case": Attachment.objects.create(case=case_open, **_attachment_kwargs(facility)),
        }
        expected = {"event": sample_event, "client": client_identified, "case": case_open}
        for owner_type, att in by_owner.items():
            assert att.owner_type == owner_type
            assert att.owner_id == expected[owner_type].pk
            assert att.owner == expected[owner_type]

    def test_owner_field_name_je_owner(self, sample_event, client_identified, case_open):
        assert owner_field_name(sample_event) == "event"
        assert owner_field_name(client_identified) == "client"
        assert owner_field_name(case_open) == "case"

    def test_owner_field_name_lehnt_fremde_modelle_ab(self, facility):
        with pytest.raises(ValueError):
            owner_field_name(facility)

    def test_owner_fields_konstante(self):
        assert OWNER_FIELDS == ("event", "client", "case")


@pytest.mark.django_db
class TestAttachmentManager:
    def test_active_und_deleted_auf_deleted_at(self, facility, sample_event):
        live = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility))
        gone = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility))
        gone.deleted_at = timezone.now()
        gone.save(update_fields=["deleted_at"])

        assert set(Attachment.objects.active()) == {live}
        assert set(Attachment.objects.deleted()) == {gone}

    def test_reverse_manager_erbt_attachment_queryset(self, facility, sample_event):
        """``event.attachments.active()`` muss den ``deleted_at``-Override nutzen —
        der geerbte ``is_deleted``-Filter des FacilityScopedQuerySet waere ein
        ``FieldError`` (ADR-039 §3)."""
        att = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility))
        assert set(sample_event.attachments.active()) == {att}

    def test_for_facility_scoping(self, facility, second_facility, sample_event):
        att = Attachment.objects.create(event=sample_event, **_attachment_kwargs(facility))
        assert set(Attachment.objects.for_facility(facility)) == {att}
        assert Attachment.objects.for_facility(second_facility).count() == 0
