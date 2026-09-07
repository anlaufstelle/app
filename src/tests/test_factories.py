"""Selbsttest fuer ``tests._factories`` — prueft die drei Factories gegen
die echten Modelle (Refs #1839 E5b). Kein Ersatz fuer die Fach-Tests, nur
ein Nachweis, dass die duennen Wrapper genau ein Objekt mit den erwarteten
Feldern anlegen.
"""

from django.utils import timezone

from core.models import Client, DocumentType, Event
from tests._factories import make_client, make_document_type, make_event


def test_make_document_type_setzt_pflichtfelder_und_reicht_overrides_durch(facility):
    doc_type = make_document_type(
        facility=facility, name="Krise", report_key="crisis", sensitivity=DocumentType.Sensitivity.HIGH
    )

    assert doc_type.facility_id == facility.id
    assert doc_type.name == "Krise"
    assert doc_type.report_key == "crisis"
    assert doc_type.sensitivity == DocumentType.Sensitivity.HIGH
    assert DocumentType.objects.filter(pk=doc_type.pk).count() == 1


def test_make_client_setzt_stabile_felder(facility, admin_user):
    client = make_client(facility=facility, pseudonym="Fabrik-Test", created_by=admin_user)

    assert client.facility_id == facility.id
    assert client.pseudonym == "Fabrik-Test"
    assert client.created_by_id == admin_user.id
    assert Client.objects.filter(pk=client.pk).count() == 1


def test_make_client_reicht_contact_stage_durch(facility, admin_user):
    client = make_client(
        facility=facility,
        pseudonym="Fabrik-Test-2",
        created_by=admin_user,
        contact_stage=Client.ContactStage.IDENTIFIED,
    )

    assert client.contact_stage == Client.ContactStage.IDENTIFIED


def test_make_event_legt_genau_ein_objekt_mit_kernfeldern_an(facility, admin_user):
    doc_type = make_document_type(facility=facility, name="Kontakt")
    client = make_client(facility=facility, pseudonym="Fabrik-Event-Klientel", created_by=admin_user)
    occurred_at = timezone.now()

    event = make_event(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=occurred_at,
        data_json={"notiz": "Fabrik-Test"},
        created_by=admin_user,
    )

    assert event.facility_id == facility.id
    assert event.client_id == client.id
    assert event.document_type_id == doc_type.id
    assert event.occurred_at == occurred_at
    assert event.data_json == {"notiz": "Fabrik-Test"}
    assert event.created_by_id == admin_user.id
    assert Event.objects.filter(pk=event.pk).count() == 1
