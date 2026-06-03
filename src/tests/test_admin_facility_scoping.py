"""A2.2 — facility-FK zentral im FacilityScopedAdminMixin scopen + erzwingen.

Refs #1021 (Tracker #1016, Workstream A2.2).

A2.1 hat die facility-Begrenzung lokal im UserAdmin ergaenzt. A2.2 zieht sie in
den gemeinsamen ``FacilityScopedAdminMixin`` (Single Source of Truth), sodass
JEDER facility-gescopte Admin (Client, Case, Event, ...) sie erbt:

- ``formfield_for_foreignkey('facility')`` -> non-super sieht nur die eigene
  Facility; der ModelChoiceField-Queryset validiert die geposteten PK
  serverseitig.
- ``save_model`` -> erzwingt ``obj.facility`` serverseitig (Defense-in-Depth
  gegen gefaelschte POSTs), unabhaengig von der per-ModelAdmin-Definition.

Getestet ueber ``ClientAdmin`` (ein Mixin-Konsument OHNE eigene Overrides), um zu
beweisen, dass die Logik zentral und nicht user-spezifisch ist.
"""

from __future__ import annotations

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from core.admin.clients import ClientAdmin
from core.admin_site import anlaufstelle_admin_site
from core.models import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def rf():
    return RequestFactory()


def _request(rf, user, method="get"):
    """Admin-Request analog zur FacilityScopeMiddleware (setzt current_facility)."""
    request = getattr(rf, method)("/")
    request.user = user
    request.current_facility = getattr(user, "facility", None)
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _admin():
    return ClientAdmin(Client, anlaufstelle_admin_site)


class TestFacilityFieldScopeCentral:
    def test_facility_admin_facility_choices_limited_to_own(self, rf, admin_user, facility, other_facility):
        facility_field = Client._meta.get_field("facility")
        formfield = _admin().formfield_for_foreignkey(facility_field, _request(rf, admin_user))
        facilities = list(formfield.queryset)
        assert facility in facilities
        assert other_facility not in facilities

    def test_super_admin_facility_choices_unrestricted(self, rf, super_admin_user, facility, other_facility):
        facility_field = Client._meta.get_field("facility")
        formfield = _admin().formfield_for_foreignkey(facility_field, _request(rf, super_admin_user))
        facilities = list(formfield.queryset)
        assert facility in facilities
        assert other_facility in facilities


class TestSaveModelForcesFacility:
    def test_facility_admin_save_forces_own_facility(self, rf, admin_user, facility, other_facility, client_identified):
        """Gefaelschte fremde facility wird serverseitig auf die eigene ueberschrieben."""
        client_identified.facility = other_facility  # forged
        _admin().save_model(_request(rf, admin_user, method="post"), client_identified, form=None, change=True)
        assert client_identified.facility == facility
        client_identified.refresh_from_db()
        assert client_identified.facility == facility

    def test_super_admin_save_keeps_chosen_facility(self, rf, super_admin_user, other_facility, client_identified):
        """super_admin behaelt die freie Facility-Wahl (kein Forcing)."""
        client_identified.facility = other_facility
        _admin().save_model(_request(rf, super_admin_user, method="post"), client_identified, form=None, change=True)
        assert client_identified.facility == other_facility
        client_identified.refresh_from_db()
        assert client_identified.facility == other_facility
