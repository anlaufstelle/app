"""Pytest fixtures for Anlaufstelle test suite."""

import pytest
from django.utils import timezone

# E2E-Tests nur collecten wenn playwright + requests installiert sind
try:
    import playwright  # noqa: F401
    import requests  # noqa: F401
except ImportError:
    collect_ignore_glob = ["e2e/*"]

from core.models import (
    Case,
    Client,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    Facility,
    FieldTemplate,
    Milestone,
    Organization,
    OutcomeGoal,
    Settings,
    User,
    WorkItem,
)


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Testorg")


@pytest.fixture
def facility(organization):
    return Facility.objects.create(organization=organization, name="Teststelle")


@pytest.fixture
def admin_user(facility):
    user = User.objects.create_user(
        username="testadmin",
        role=User.Role.FACILITY_ADMIN,
        facility=facility,
        is_superuser=True,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def lead_user(facility):
    user = User.objects.create_user(
        username="testlead",
        role=User.Role.LEAD,
        facility=facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def staff_user(facility):
    user = User.objects.create_user(
        username="teststaff",
        role=User.Role.STAFF,
        facility=facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def assistant_user(facility):
    user = User.objects.create_user(
        username="testassistant",
        role=User.Role.ASSISTANT,
        facility=facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def super_admin_user(db):
    """Refs #867: SUPER_ADMIN ohne Facility-Bindung (Persona Jonas).

    Bewusst ohne ``facility``-Bindung — der super_admin bedient die gesamte
    Installation. ``is_superuser=False``, weil die Rolle nicht mit Djangos
    Auth-Superuser-Flag gleichgesetzt werden darf (RBAC-Property entscheidet).
    """
    user = User.objects.create_user(
        username="testsuperadmin",
        role=User.Role.SUPER_ADMIN,
        facility=None,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def client_identified(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Test-ID-01",
        created_by=staff_user,
    )


@pytest.fixture
def client_qualified(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym="Test-QU-01",
        created_by=staff_user,
    )


@pytest.fixture
def doc_type_contact(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.CONTACT,
        name="Kontakt",
    )

    ft_dauer = FieldTemplate.objects.create(
        facility=facility,
        name="Dauer",
        field_type=FieldTemplate.FieldType.NUMBER,
    )
    ft_notiz = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_dauer, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz, sort_order=1)

    return doc_type


@pytest.fixture
def doc_type_crisis(facility):
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.SERVICE,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        name="Krisengespräch",
    )

    ft_notiz_krise = FieldTemplate.objects.create(
        facility=facility,
        name="Notiz (Krise)",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
        sensitivity="high",
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_notiz_krise, sort_order=0)

    return doc_type


@pytest.fixture
def sample_event(facility, client_identified, doc_type_contact, staff_user):
    return Event.objects.create(
        facility=facility,
        client=client_identified,
        document_type=doc_type_contact,
        occurred_at=timezone.now(),
        data_json={"dauer": 15, "notiz": "Testnotiz"},
        created_by=staff_user,
    )


@pytest.fixture
def sample_workitem(facility, client_identified, staff_user):
    return WorkItem.objects.create(
        facility=facility,
        client=client_identified,
        created_by=staff_user,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Test-Aufgabe",
    )


@pytest.fixture
def other_facility(organization):
    return Facility.objects.create(organization=organization, name="Andere Stelle")


@pytest.fixture
def second_facility(organization):
    return Facility.objects.create(organization=organization, name="Zweite Stelle")


@pytest.fixture
def second_facility_user(second_facility):
    user = User.objects.create_user(
        username="seconduser",
        role=User.Role.STAFF,
        facility=second_facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def case_open(facility, client_identified, staff_user):
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Offener Fall",
        status=Case.Status.OPEN,
        created_by=staff_user,
    )


@pytest.fixture
def case_closed(facility, client_identified, staff_user):
    return Case.objects.create(
        facility=facility,
        client=client_identified,
        title="Geschlossener Fall",
        status=Case.Status.CLOSED,
        closed_at=timezone.now(),
        created_by=staff_user,
    )


@pytest.fixture
def episode(case_open, staff_user):
    return Episode.objects.create(
        case=case_open,
        title="Test-Episode",
        started_at=timezone.now().date(),
        created_by=staff_user,
    )


@pytest.fixture
def settings_obj(facility):
    return Settings.objects.create(
        facility=facility,
        retention_anonymous_days=90,
        retention_identified_days=365,
        retention_qualified_days=3650,
    )


@pytest.fixture
def outcome_goal(case_open, staff_user):
    return OutcomeGoal.objects.create(
        case=case_open,
        title="Stabile Wohnsituation",
        created_by=staff_user,
    )


@pytest.fixture
def milestone(outcome_goal):
    return Milestone.objects.create(
        goal=outcome_goal,
        title="Erstgespräch geführt",
    )
