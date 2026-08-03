"""Pytest fixtures for Anlaufstelle test suite."""

import os

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
    FieldCondition,
    FieldGroup,
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


def xdist_role_suffix() -> str:
    """Worker-Suffix fuer cluster-globale Postgres-Rollennamen unter ``-n auto``.

    Postgres-Rollen sind **cluster-global** — parallele pytest-xdist-Worker
    teilen sich den Rollen-Namespace, obwohl jeder Worker seine eigene Test-DB
    hat. Ohne Suffix kollidieren die ``CREATE ROLE``/``DROP ROLE`` zweier Worker
    (``role already exists`` / ``cannot drop role``). Alle ``transaction=True``-
    Tests, die statische Testrollen anlegen, haengen daher diesen Suffix an ihre
    Rollennamen. Generalisiert das Worker-Erkennungsmuster aus
    ``src/tests/e2e/conftest.py::_get_worker_info`` (Refs #1479, TST-03).

    Seriell (kein xdist, ``PYTEST_XDIST_WORKER`` leer oder ``master``): leerer
    Suffix, damit die Rollennamen unveraendert bleiben.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    if not worker or worker == "master":
        return ""
    return f"_{worker}"


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
def doc_type_client_profile(facility):
    """Personen-Dokumentationstyp (M2 P4b, Refs #1058) mit je einem normalen und
    einem verschlüsselten Feld.

    Pendant zu ``doc_type_contact`` für ``entity_type=client``: ``Client.data_json``
    wird gegen genau diese Feldmenge whitelistet, ``wohnsituation`` bleibt Klartext
    (Residue-Proben), ``diagnose`` ist ``is_encrypted=True`` und muss nach
    ``Client.save()`` als Fernet-Token in der DB liegen.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personenprofil",
    )

    ft_wohnsituation = FieldTemplate.objects.create(
        facility=facility,
        name="Wohnsituation",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_diagnose = FieldTemplate.objects.create(
        facility=facility,
        name="Diagnose",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_wohnsituation, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_diagnose, sort_order=1)

    return doc_type


@pytest.fixture
def doc_type_case_form(facility):
    """Fall-Dokumentationstyp (M2 P5, Refs #1058) mit je einem normalen und
    einem verschlüsselten Feld.

    Pendant zu ``doc_type_client_profile`` für ``entity_type=case``:
    ``Case.data_json`` wird gegen genau diese Feldmenge whitelistet,
    ``anlass`` bleibt Klartext (Residue-Proben), ``verlauf`` ist
    ``is_encrypted=True`` und muss nach ``Case.save()`` als Fernet-Token in
    der DB liegen.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CASE,
        name="Fallformular",
    )

    ft_anlass = FieldTemplate.objects.create(
        facility=facility,
        name="Anlass",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_verlauf = FieldTemplate.objects.create(
        facility=facility,
        name="Verlauf",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        is_encrypted=True,
    )

    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_anlass, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_verlauf, sort_order=1)

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
def foreign_doc_type_client(second_facility):
    """Personen-Dokumentationstyp auf ``second_facility`` (M2 P4b, Refs #1058).

    Spiegel zu ``doc_type_client_profile`` für die Cross-Facility-Probe: der Typ
    ist inhaltlich gültig (``entity_type=client``), gehört aber zur falschen
    Einrichtung — der Service-Guard muss ihn ablehnen.
    """
    return DocumentType.objects.create(
        facility=second_facility,
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CLIENT,
        name="Personenprofil (fremd)",
    )


@pytest.fixture
def foreign_doc_type_case(second_facility):
    """Fall-Dokumentationstyp auf ``second_facility`` (M2 P5, Refs #1058).

    Spiegel zu ``doc_type_case_form`` für die Cross-Facility-Probe: der Typ
    ist inhaltlich gültig (``entity_type=case``), gehört aber zur falschen
    Einrichtung — der Service-Guard muss ihn ablehnen.
    """
    return DocumentType.objects.create(
        facility=second_facility,
        category=DocumentType.Category.ADMIN,
        entity_type=DocumentType.EntityType.CASE,
        name="Fallformular (fremd)",
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


# ---- Config-CRUD-Fixtures (M0, Refs #1056) -------------------------------
# FieldTemplate/FieldGroup/DocumentTypeField der Haupt-facility für die
# Config-CRUD-Matrix (P5) sowie die interaktive Feld-Zuordnung (P6/P7); die
# foreign_*-Spiegel auf second_facility/foreign_doc_type dienen den IDOR-Proben.


@pytest.fixture
def field_template(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Feldvorlage (Config)",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def doc_type_field(doc_type_contact, facility):
    """Eine dedizierte Feld-Zuordnung auf doc_type_contact (zusätzlich zu den
    beiden Fixture-Feldern), mit stabilem, eigenem FieldTemplate."""
    ft = FieldTemplate.objects.create(
        facility=facility,
        name="Feld (Config-Zuordnung)",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    return DocumentTypeField.objects.create(
        document_type=doc_type_contact,
        field_template=ft,
        sort_order=2,
    )


@pytest.fixture
def field_group(doc_type_contact):
    return FieldGroup.objects.create(
        document_type=doc_type_contact,
        name="Feldgruppe (Config)",
    )


@pytest.fixture
def foreign_field_template(second_facility):
    return FieldTemplate.objects.create(
        facility=second_facility,
        name="Feldvorlage (fremd)",
        field_type=FieldTemplate.FieldType.TEXT,
    )


@pytest.fixture
def foreign_field_group(foreign_doc_type):
    return FieldGroup.objects.create(
        document_type=foreign_doc_type,
        name="Feldgruppe (fremd)",
    )


@pytest.fixture
def foreign_doc_type_field(foreign_doc_type, foreign_field_template):
    return DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=foreign_field_template,
        sort_order=0,
    )


# ---- FieldCondition-Fixtures (M1 P6, Refs #1057) --------------------------
# Referenz-Bedingungen für die AuthZ-/IDOR-Matrix (config_condition_update/
# _delete). Die Haupt-facility-Variante hängt sich an die beiden Bestandsfelder
# von ``doc_type_contact`` (dauer/number, notiz/textarea); die foreign-Variante
# legt eigenständig zwei Felder+DTFs an ``foreign_doc_type`` an, weil
# ``foreign_doc_type`` selbst keine Felder trägt und ``foreign_doc_type_field``
# nur EIN DTF liefert (eine Bedingung braucht zwei).


@pytest.fixture
def field_condition(doc_type_contact):
    dauer = doc_type_contact.fields.get(field_template__slug="dauer")
    notiz = doc_type_contact.fields.get(field_template__slug="notiz")
    return FieldCondition.objects.create(
        target_field=notiz,
        depends_on_field=dauer,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=10,
        group_index=0,
    )


@pytest.fixture
def foreign_field_condition(foreign_doc_type, second_facility):
    ft_source = FieldTemplate.objects.create(
        facility=second_facility,
        name="Fremd-Quelle",
        field_type=FieldTemplate.FieldType.NUMBER,
    )
    ft_target = FieldTemplate.objects.create(
        facility=second_facility,
        name="Fremd-Ziel",
        field_type=FieldTemplate.FieldType.TEXTAREA,
    )
    source = DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=ft_source,
        sort_order=0,
    )
    target = DocumentTypeField.objects.create(
        document_type=foreign_doc_type,
        field_template=ft_target,
        sort_order=1,
    )
    return FieldCondition.objects.create(
        target_field=target,
        depends_on_field=source,
        operator=FieldCondition.Operator.GREATER_THAN,
        value=5,
        group_index=0,
    )
