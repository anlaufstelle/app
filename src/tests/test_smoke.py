"""Smoke-Tests: prüfen dass die Grundkonfiguration funktioniert."""

import pytest

from core.models import User


@pytest.mark.django_db
def test_fixture_chain(facility, admin_user, doc_type_contact, sample_event):
    """Alle Kern-Fixtures lassen sich gemeinsam instanziieren."""
    assert facility.name == "Teststelle"
    # Refs #867: ``admin_user`` ist seit dem 5-Rollen-Refactor ein
    # FACILITY_ADMIN ("facility_admin"), nicht mehr "admin".
    assert admin_user.role == User.Role.FACILITY_ADMIN
    assert admin_user.is_superuser is True
    assert doc_type_contact.category == "contact"
    assert sample_event.data_json["dauer"] == 15


@pytest.mark.django_db
def test_user_roles(admin_user, lead_user, staff_user, assistant_user):
    """Alle 4 Rollen sind korrekt zugewiesen."""
    assert admin_user.is_facility_admin is True
    assert lead_user.is_lead_or_admin is True
    assert staff_user.is_staff_or_above is True
    assert assistant_user.role == "assistant"


@pytest.mark.django_db
def test_super_admin_role_smoke(facility):
    """Refs #867: SUPER_ADMIN-Rolle ist eine eigenstaendige Top-Rolle.

    Smoke-Check, dass ``is_super_admin`` True liefert und gleichzeitig
    ``is_facility_admin`` False ist — die Rollen schliessen sich aus
    (jeder User hat genau eine Rolle).
    """
    super_admin = User.objects.create_user(
        username="testsuper",
        role=User.Role.SUPER_ADMIN,
        facility=None,
    )
    assert super_admin.is_super_admin is True
    assert super_admin.is_facility_admin is False
    assert super_admin.role == User.Role.SUPER_ADMIN


@pytest.mark.django_db
def test_client_stages(client_identified, client_qualified):
    """Beide Kontaktstufen sind korrekt."""
    assert client_identified.contact_stage == "identified"
    assert client_qualified.contact_stage == "qualified"


@pytest.mark.django_db
def test_encrypted_field_template(doc_type_crisis):
    """Krisengespräch hat ein verschlüsseltes Feld."""
    encrypted_fields = doc_type_crisis.fields.filter(field_template__is_encrypted=True)
    assert encrypted_fields.count() == 1
    assert encrypted_fields.first().field_template.name == "Notiz (Krise)"
