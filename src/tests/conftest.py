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
    AuditLog,
    Case,
    Client,
    DeletionRequest,
    DocumentType,
    DocumentTypeField,
    Episode,
    Event,
    Facility,
    FieldTemplate,
    LegalHold,
    Milestone,
    Organization,
    OutcomeGoal,
    RetentionProposal,
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
        # Refs #1271: facility_admin ist kein Django-superuser (Least-Privilege);
        # spiegelt Seed/Prod. ``is_staff`` bleibt fuer den Admin-Site-Login.
        is_staff=True,
        # Refs #1053: spiegelt die Backfill-Migration — bestehende
        # Admins/Leitungen tragen das Vier-Augen-Genehmiger-Recht.
        can_confirm_deletion=True,
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
        can_confirm_deletion=True,
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


# ---- AuthZ-Matrix-Fixtures (Refs #1055) ---------------------------------
# Eigene Facility-1-Objekte für Matrix-Zellen + Spiegel-Objekte in
# second_facility für IDOR-Probes (erwartet 404, kein Existenz-Leak).

PDF_MINIMAL = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_attachment(fac, event, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from core.services.file_vault import store_encrypted_file

    ft = FieldTemplate.objects.create(facility=fac, name="Anhang (AuthZ)", field_type=FieldTemplate.FieldType.FILE)
    upload = SimpleUploadedFile("authz.pdf", PDF_MINIMAL, content_type="application/pdf")
    return store_encrypted_file(fac, upload, ft, event, user)


@pytest.fixture
def client_trashed(facility, staff_user):
    return Client.objects.create(
        facility=facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Trash-01",
        created_by=staff_user,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def case_event(case_open, sample_event):
    """Hängt sample_event an case_open (mutiert sample_event in place)."""
    sample_event.case = case_open
    sample_event.save(update_fields=["case"])
    return sample_event


@pytest.fixture
def authz_attachment(facility, sample_event, staff_user):
    return _make_attachment(facility, sample_event, staff_user)


@pytest.fixture
def audit_entry(facility, staff_user, client_identified):
    return AuditLog.objects.create(
        facility=facility,
        user=staff_user,
        action=AuditLog.Action.EXPORT,
        target_type="Client",
        target_id=str(client_identified.pk),
        detail={},
    )


@pytest.fixture
def retention_proposal(facility, sample_event):
    return RetentionProposal.objects.create(
        facility=facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=sample_event.pk,
        deletion_due_at=timezone.localdate(),
        retention_category="anonymous",
    )


@pytest.fixture
def legal_hold(facility, sample_event, lead_user):
    return LegalHold.objects.create(
        facility=facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="AuthZ-Matrix-Test",
        created_by=lead_user,
    )


@pytest.fixture
def deletion_request(facility, sample_event, staff_user):
    return DeletionRequest.objects.create(
        facility=facility,
        target_type="Event",
        target_id=sample_event.pk,
        reason="AuthZ-Matrix-Test",
        requested_by=staff_user,
    )


# ---- Fremde Facility (IDOR-Ziele) ----------------------------------------


@pytest.fixture
def foreign_staff(second_facility):
    user = User.objects.create_user(
        username="authz_foreign_staff",
        role=User.Role.STAFF,
        facility=second_facility,
        is_staff=True,
    )
    user.set_password("testpass123")
    user.save()
    return user


@pytest.fixture
def foreign_client(second_facility, foreign_staff):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Fremd-01",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_client_trashed(second_facility, foreign_staff):
    return Client.objects.create(
        facility=second_facility,
        contact_stage=Client.ContactStage.IDENTIFIED,
        pseudonym="Fremd-Trash-01",
        created_by=foreign_staff,
        is_deleted=True,
        deleted_at=timezone.now(),
    )


@pytest.fixture
def foreign_case(second_facility, foreign_client, foreign_staff):
    return Case.objects.create(
        facility=second_facility,
        client=foreign_client,
        title="Fremder Fall",
        status=Case.Status.OPEN,
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_case_closed(second_facility, foreign_client, foreign_staff):
    return Case.objects.create(
        facility=second_facility,
        client=foreign_client,
        title="Fremder geschlossener Fall",
        status=Case.Status.CLOSED,
        closed_at=timezone.now(),
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_doc_type(second_facility):
    return DocumentType.objects.create(
        facility=second_facility,
        category=DocumentType.Category.CONTACT,
        name="Kontakt (fremd)",
    )


@pytest.fixture
def foreign_event(second_facility, foreign_client, foreign_doc_type, foreign_staff):
    return Event.objects.create(
        facility=second_facility,
        client=foreign_client,
        document_type=foreign_doc_type,
        occurred_at=timezone.now(),
        data_json={},
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_case_event(foreign_case, foreign_event):
    """Hängt foreign_event an foreign_case (mutiert foreign_event in place)."""
    foreign_event.case = foreign_case
    foreign_event.save(update_fields=["case"])
    return foreign_event


@pytest.fixture
def foreign_episode(foreign_case, foreign_staff):
    return Episode.objects.create(
        case=foreign_case,
        title="Fremde Episode",
        started_at=timezone.now().date(),
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_goal(foreign_case, foreign_staff):
    return OutcomeGoal.objects.create(
        case=foreign_case,
        title="Fremdes Ziel",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_milestone(foreign_goal):
    return Milestone.objects.create(goal=foreign_goal, title="Fremder Meilenstein")


@pytest.fixture
def foreign_workitem(second_facility, foreign_client, foreign_staff):
    return WorkItem.objects.create(
        facility=second_facility,
        client=foreign_client,
        created_by=foreign_staff,
        item_type=WorkItem.ItemType.TASK,
        status=WorkItem.Status.OPEN,
        title="Fremde Aufgabe",
    )


@pytest.fixture
def foreign_attachment(second_facility, foreign_event, foreign_staff):
    return _make_attachment(second_facility, foreign_event, foreign_staff)


@pytest.fixture
def foreign_audit_entry(second_facility, foreign_staff, foreign_client):
    return AuditLog.objects.create(
        facility=second_facility,
        user=foreign_staff,
        action=AuditLog.Action.EXPORT,
        target_type="Client",
        target_id=str(foreign_client.pk),
        detail={},
    )


@pytest.fixture
def foreign_retention_proposal(second_facility, foreign_event):
    return RetentionProposal.objects.create(
        facility=second_facility,
        target_type=RetentionProposal.TargetType.EVENT,
        target_id=foreign_event.pk,
        deletion_due_at=timezone.localdate(),
        retention_category="anonymous",
    )


@pytest.fixture
def foreign_legal_hold(second_facility, foreign_event, foreign_staff):
    return LegalHold.objects.create(
        facility=second_facility,
        target_type="Event",
        target_id=foreign_event.pk,
        reason="AuthZ-IDOR-Test",
        created_by=foreign_staff,
    )


@pytest.fixture
def foreign_deletion_request(second_facility, foreign_event, foreign_staff):
    return DeletionRequest.objects.create(
        facility=second_facility,
        target_type="Event",
        target_id=foreign_event.pk,
        reason="AuthZ-IDOR-Test",
        requested_by=foreign_staff,
    )
