"""Modell-Tests fuer ``RectificationRequest`` (M4 Strang C, ADR-041, Refs #1060).

Deckt die drei Invarianten ab, die NUR die Datenbank halten kann und die daher
nicht in den Service-Tests aufgehen:

* partieller Unique-Index — hoechstens EIN offener Antrag je
  (Einrichtung, Person, Feldpfad),
* CheckConstraint gegen Selbst-Review (Vier-Augen-Prinzip, Praezedenz
  ``DeletionRequest``),
* ``SET_NULL`` auf beiden Personenbezuegen — der Vier-Augen-Nachweis
  ueberlebt eine harte User-Loeschung (Delta 13 des Plan-Updates).

Die Cross-Tenant-Probe laeuft — wie ``TestDeletionRequestRLSIsolation`` in
``test_rls.py`` — unter der NOSUPERUSER-Rolle, weil die ``facility_isolation``-
Policy sonst per Postgres-Default bypasst wuerde.
"""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction

from core.models import Client, RectificationRequest
from core.services.file_vault import encrypt_field
from tests.test_rls_functional import as_rls_role, rls_test_role  # noqa: F401


def _make(facility, client, user, **kw):
    defaults = {
        "facility": facility,
        "target_client": client,
        "field_path": "pseudonym",
        "old_value": encrypt_field('"alt"'),
        "new_value": encrypt_field('"neu"'),
        "reason": encrypt_field("Schreibfehler laut Vorsprache"),
        "requested_by": user,
    }
    defaults.update(kw)
    return RectificationRequest.objects.create(**defaults)


@pytest.mark.django_db
class TestPendingUniqueness:
    def test_second_pending_for_same_field_path_is_rejected(self, facility, client_identified, staff_user):
        _make(facility, client_identified, staff_user)
        with pytest.raises(IntegrityError), transaction.atomic():
            _make(facility, client_identified, staff_user)

    def test_second_pending_for_another_field_path_is_allowed(self, facility, client_identified, staff_user):
        _make(facility, client_identified, staff_user)
        _make(facility, client_identified, staff_user, field_path="notes")
        assert RectificationRequest.objects.for_facility(facility).count() == 2

    def test_closed_request_does_not_block_a_new_one(self, facility, client_identified, staff_user, lead_user):
        _make(
            facility,
            client_identified,
            staff_user,
            status=RectificationRequest.Status.REJECTED,
            reviewed_by=lead_user,
        )
        _make(facility, client_identified, staff_user)
        assert RectificationRequest.objects.for_facility(facility).count() == 2


@pytest.mark.django_db
class TestFourEyesConstraint:
    def test_self_review_is_rejected_by_the_database(self, facility, client_identified, staff_user):
        with pytest.raises(IntegrityError), transaction.atomic():
            _make(
                facility,
                client_identified,
                staff_user,
                status=RectificationRequest.Status.APPROVED,
                reviewed_by=staff_user,
            )

    def test_open_request_without_reviewer_is_allowed(self, facility, client_identified, staff_user):
        rr = _make(facility, client_identified, staff_user)
        assert rr.reviewed_by is None
        assert rr.status == RectificationRequest.Status.PENDING

    def test_foreign_reviewer_is_allowed(self, facility, client_identified, staff_user, lead_user):
        rr = _make(
            facility,
            client_identified,
            staff_user,
            status=RectificationRequest.Status.APPROVED,
            reviewed_by=lead_user,
        )
        assert rr.reviewed_by == lead_user


@pytest.mark.django_db
class TestPersonReferencesSurviveUserDeletion:
    def test_requested_by_and_reviewed_by_are_set_null(self, facility, client_identified, staff_user, lead_user):
        rr = _make(
            facility,
            client_identified,
            staff_user,
            status=RectificationRequest.Status.APPROVED,
            reviewed_by=lead_user,
        )
        staff_user.delete()
        lead_user.delete()
        rr.refresh_from_db()
        assert rr.requested_by is None
        assert rr.reviewed_by is None
        # Der Antrag selbst (und damit der Art.-5(2)-Nachweis) bleibt bestehen.
        assert RectificationRequest.objects.filter(pk=rr.pk).exists()


@pytest.mark.django_db
class TestFacilityScoping:
    def test_manager_scopes_to_facility(
        self, facility, second_facility, client_identified, foreign_client, staff_user, foreign_staff
    ):
        own = _make(facility, client_identified, staff_user)
        _make(second_facility, foreign_client, foreign_staff)
        scoped = list(RectificationRequest.objects.for_facility(facility))
        assert scoped == [own]


@pytest.mark.django_db(transaction=True)
class TestRectificationRequestRLSIsolation:
    """Cross-Tenant-Probe unter der NOSUPERUSER-Rolle (Muster ``test_rls.py``).

    Ein Berichtigungsantrag traegt Personenbezug (``target_client``) und die
    verschluesselten Wertpaare — schon seine blosse Existenz ueber die
    Mandantengrenze hinweg waere ein Metadaten-Leak.
    """

    def setup_method(self):
        if connection.vendor != "postgresql":
            pytest.skip("RLS requires PostgreSQL")

    def test_request_invisible_across_facility(
        self,
        rls_test_role,  # noqa: F811
        facility,
        second_facility,
        staff_user,
        second_facility_user,
    ):
        client_a = Client.objects.create(
            facility=facility,
            pseudonym="A-Client-Rect",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=staff_user,
        )
        client_b = Client.objects.create(
            facility=second_facility,
            pseudonym="B-Client-Rect",
            contact_stage=Client.ContactStage.IDENTIFIED,
            created_by=second_facility_user,
        )
        rr_a = _make(facility, client_a, staff_user)
        rr_b = _make(second_facility, client_b, second_facility_user)

        with as_rls_role(rls_test_role, facility_id=facility.pk) as cur:
            cur.execute("SELECT id::text FROM core_rectificationrequest")
            ids_a = {uuid.UUID(row[0]) for row in cur.fetchall()}
        assert rr_a.pk in ids_a, "Einrichtung A sieht den eigenen Antrag nicht — Policy zu strikt."
        assert rr_b.pk not in ids_a, f"Cross-Tenant-Leak nach A: {ids_a}"

        with as_rls_role(rls_test_role, facility_id=second_facility.pk) as cur:
            cur.execute("SELECT id::text FROM core_rectificationrequest")
            ids_b = {uuid.UUID(row[0]) for row in cur.fetchall()}
        assert rr_b.pk in ids_b
        assert rr_a.pk not in ids_b, f"Cross-Tenant-Leak nach B: {ids_b}"
