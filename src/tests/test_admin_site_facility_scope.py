"""Facility-Scoping-Tests fuer ModelAdmin.get_queryset() (Refs #785)."""

from __future__ import annotations

import pytest

from core.models import Client, Facility


@pytest.fixture
def other_facility(organization):
    return Facility.objects.create(organization=organization, name="Andere Stelle")


@pytest.fixture
def client_in_other_facility(other_facility, staff_user):
    return Client.objects.create(
        facility=other_facility,
        contact_stage=Client.ContactStage.QUALIFIED,
        pseudonym="Other-QU-99",
        created_by=staff_user,
    )


@pytest.mark.django_db
class TestFacilityAdminScope:
    """facility_admin sieht nur Daten der eigenen Facility."""

    def test_facility_admin_sees_only_own_clients(self, client, admin_user, client_qualified, client_in_other_facility):
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/core/client/", follow=False)
        assert response.status_code == 200
        # Eigener Client sichtbar:
        assert client_qualified.pseudonym.encode() in response.content
        # Fremder Client NICHT sichtbar:
        assert client_in_other_facility.pseudonym.encode() not in response.content

    def test_facility_admin_cannot_change_other_facility_client(self, client, admin_user, client_in_other_facility):
        """Direct-Access auf fremde Client-PK ist blockiert (queryset filtert).

        Django-Admin redirected (302) zu /admin-mgmt/ mit Error-Message
        ``"client object with primary key '...' was not found"`` —
        Django's _get_obj_does_not_exist_redirect-Pattern.
        """
        client.force_login(admin_user)
        url = f"/admin-mgmt/core/client/{client_in_other_facility.pk}/change/"
        response = client.get(url, follow=False)
        # Either 404 (object not found via get_queryset) or 302 to admin-index
        # (Django's "doesn't exist"-redirect with message). Beides bedeutet
        # "Zugriff verweigert".
        assert response.status_code in (302, 404), (
            f"Expected 302 or 404 for cross-facility access, got {response.status_code}"
        )
        if response.status_code == 302:
            assert "/admin-mgmt/" in response["Location"]
            assert str(client_in_other_facility.pk) not in response["Location"]


@pytest.mark.django_db
class TestSuperAdminScope:
    """super_admin sieht alle Facilities."""

    def test_super_admin_sees_clients_across_facilities(
        self, client, super_admin_user, client_qualified, client_in_other_facility
    ):
        client.force_login(super_admin_user)
        response = client.get("/admin-mgmt/core/client/", follow=False)
        assert response.status_code == 200
        # Beide Clients sichtbar:
        assert client_qualified.pseudonym.encode() in response.content
        assert client_in_other_facility.pseudonym.encode() in response.content


# ---------------------------------------------------------------------------
# Attachment-Admin (M24/AP2, Refs #1738)
# ---------------------------------------------------------------------------


def _attachment(facility, owner_kwargs):
    """Anhang-Zeile ohne File-Vault (kein libmagic/Encryption-Key noetig)."""
    import uuid

    from core.models import Attachment, FieldTemplate

    ft = FieldTemplate.objects.create(
        facility=facility,
        name=f"Anhang {uuid.uuid4().hex[:6]}",
        field_type=FieldTemplate.FieldType.FILE,
    )
    return Attachment.objects.create(
        facility=facility,
        field_template=ft,
        storage_filename=f"{uuid.uuid4().hex}.enc",
        original_filename_encrypted={"v": 1, "data": "x"},
        file_size=1,
        mime_type="application/pdf",
        **owner_kwargs,
    )


@pytest.mark.django_db
class TestAttachmentAdminScope:
    """G2-Befund (Refs #1738): das frueher genutzte ``event__facility``-Scoping
    haette personen-/falleigene Anhaenge komplett verborgen — der Admin scopt
    seit ADR-039 ueber die eigene ``facility``-Spalte."""

    def test_facility_admin_sieht_personen_eigenen_anhang(self, client, admin_user, facility, client_identified):
        attachment = _attachment(facility, {"client": client_identified})
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/core/attachment/", follow=False)
        assert response.status_code == 200
        assert attachment.storage_filename.encode() in response.content

    def test_facility_admin_sieht_fall_eigenen_anhang(self, client, admin_user, facility, case_open):
        attachment = _attachment(facility, {"case": case_open})
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/core/attachment/", follow=False)
        assert response.status_code == 200
        assert attachment.storage_filename.encode() in response.content
        # Owner-Spalte weist die Zuordnung aus, ohne die Instanz nachzuladen.
        assert str(case_open.pk).encode() in response.content

    def test_fremde_facility_bleibt_unsichtbar(self, client, admin_user, other_facility, staff_user):
        from core.models import Client as ClientModel

        foreign_person = ClientModel.objects.create(
            facility=other_facility,
            contact_stage=ClientModel.ContactStage.IDENTIFIED,
            pseudonym="Fremd-AT-01",
            created_by=staff_user,
        )
        attachment = _attachment(other_facility, {"client": foreign_person})
        client.force_login(admin_user)
        response = client.get("/admin-mgmt/core/attachment/", follow=False)
        assert response.status_code == 200
        assert attachment.storage_filename.encode() not in response.content
