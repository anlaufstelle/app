"""Art.-18-Sperrvermerk: Einrichtungsgrenze beim Setzen und Aufheben.

``restrict_processing`` und ``lift_processing_restriction`` laden die Person
unter ``select_for_update`` neu — bisher per ``get(pk=…)`` ohne
Einrichtungsfilter, und ohne die Einrichtung der handelnden Person zu prüfen.
Das Aufheben ist ein Vier-Augen-Schritt; wie in den übrigen Vier-Augen-Services
gehört die Einrichtungsgrenze in den Service, nicht nur in die View.

Über HTTP tragen View-Scoping und Datenbank-Zeilensicherheit; im Test ist die
Zeilensicherheit nicht aktiv, der Service ist also die einzige Grenze — wie in
``test_four_eyes_approve_facility_guard.py``. Abgewiesen wird ohne jede
Änderung an der Person und ohne Audit-Zeile.
"""

import pytest
from django.core.exceptions import ValidationError

from core.models import AuditLog, Client, User
from core.services.client.restriction import lift_processing_restriction, restrict_processing

pytestmark = pytest.mark.django_db

_FREMD = "Person gehört zu einer anderen Einrichtung."
_GRUND = Client.RestrictionReason.ACCURACY_DISPUTED
_SPALTEN = (
    "processing_restricted",
    "processing_restriction_reason",
    "processing_restricted_at",
    "processing_restricted_by_id",
    "updated_at",
)


@pytest.fixture
def foreign_staff(second_facility):
    return User.objects.create_user(
        username="fremdefachkraft-einschraenkung",
        role=User.Role.STAFF,
        facility=second_facility,
        is_staff=True,
    )


@pytest.fixture
def foreign_lead(second_facility):
    return User.objects.create_user(
        username="fremdeleitung-einschraenkung",
        role=User.Role.LEAD,
        facility=second_facility,
        is_staff=True,
    )


def _snapshot(client):
    return Client.objects.filter(pk=client.pk).values(*_SPALTEN).get()


def _assert_refused(callable_, client):
    vorher = _snapshot(client)
    audit_vorher = AuditLog.objects.count()
    with pytest.raises(ValidationError) as excinfo:
        callable_()
    assert excinfo.value.messages == [_FREMD]
    assert _snapshot(client) == vorher
    assert AuditLog.objects.count() == audit_vorher


class TestRestrict:
    def test_foreign_actor_is_refused(self, client_identified, foreign_staff):
        _assert_refused(lambda: restrict_processing(client_identified, foreign_staff, reason=_GRUND), client_identified)

    def test_instance_claiming_the_actors_facility_is_refused(self, client_identified, foreign_staff):
        """Der Sperr-Lookup selbst ist gescopet: eine Instanz, deren
        ``facility_id`` im Speicher auf die Einrichtung der handelnden Person
        zeigt, öffnet keine fremde Zeile."""
        client_identified.facility_id = foreign_staff.facility_id
        _assert_refused(lambda: restrict_processing(client_identified, foreign_staff, reason=_GRUND), client_identified)

    def test_actor_without_facility_is_refused(self, client_identified, staff_user):
        """Fail-closed: ohne Einrichtung keine Einschränkung."""
        staff_user.facility = None
        _assert_refused(lambda: restrict_processing(client_identified, staff_user, reason=_GRUND), client_identified)

    def test_foreign_state_is_not_revealed(self, client_identified, staff_user, foreign_staff):
        """Auch die Idempotenz-Abkürzung (bereits eingeschränkt) greift erst
        nach der Einrichtungsgrenze."""
        restrict_processing(client_identified, staff_user, reason=_GRUND)
        _assert_refused(lambda: restrict_processing(client_identified, foreign_staff, reason=_GRUND), client_identified)

    def test_same_facility_still_restricts(self, client_identified, staff_user):
        result = restrict_processing(client_identified, staff_user, reason=_GRUND)
        assert result.processing_restricted is True
        assert _snapshot(client_identified)["processing_restricted_by_id"] == staff_user.pk


class TestLift:
    @pytest.fixture
    def restricted(self, client_identified, staff_user):
        restrict_processing(client_identified, staff_user, reason=_GRUND)
        return client_identified

    def test_foreign_lead_is_refused(self, restricted, foreign_lead):
        _assert_refused(lambda: lift_processing_restriction(restricted, foreign_lead), restricted)

    def test_instance_claiming_the_actors_facility_is_refused(self, restricted, foreign_lead):
        restricted.facility_id = foreign_lead.facility_id
        _assert_refused(lambda: lift_processing_restriction(restricted, foreign_lead), restricted)

    def test_actor_without_facility_is_refused(self, restricted, lead_user):
        lead_user.facility = None
        _assert_refused(lambda: lift_processing_restriction(restricted, lead_user), restricted)

    def test_foreign_state_is_not_revealed(self, client_identified, foreign_lead):
        """Nicht eingeschränkte fremde Person: Einrichtungsmeldung, nicht die
        Zustandsmeldung."""
        _assert_refused(lambda: lift_processing_restriction(client_identified, foreign_lead), client_identified)

    def test_same_facility_lead_still_lifts(self, restricted, lead_user):
        result = lift_processing_restriction(restricted, lead_user)
        assert result.processing_restricted is False
        assert _snapshot(restricted)["processing_restricted"] is False
