"""Smoke-Tests: prüfen dass die Grundkonfiguration funktioniert."""

import pytest


@pytest.mark.django_db
def test_fixture_chain(facility, admin_user, doc_type_contact, sample_event):
    """Alle Kern-Fixtures lassen sich gemeinsam instanziieren."""
    assert facility.name == "Teststelle"
    assert admin_user.role == "admin"
    assert admin_user.is_superuser is True
    assert doc_type_contact.category == "contact"
    assert sample_event.data_json["dauer"] == 15


@pytest.mark.django_db
def test_user_roles(admin_user, lead_user, staff_user, assistant_user):
    """Alle 4 Rollen sind korrekt zugewiesen."""
    assert admin_user.is_admin is True
    assert lead_user.is_lead_or_admin is True
    assert staff_user.is_staff_or_above is True
    assert assistant_user.role == "assistant"


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
