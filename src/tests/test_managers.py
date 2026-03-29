"""Tests für FacilityScopedManager."""

import pytest

from core.models import Client, Facility, Organization


@pytest.fixture
def two_facilities(db):
    org = Organization.objects.create(name="TestOrg")
    f1 = Facility.objects.create(organization=org, name="Facility A")
    f2 = Facility.objects.create(organization=org, name="Facility B")
    return f1, f2


@pytest.mark.django_db
class TestFacilityScopedManager:
    def test_for_facility_filters_correctly(self, two_facilities):
        f1, f2 = two_facilities
        c1 = Client.objects.create(facility=f1, pseudonym="Client-A")
        c2 = Client.objects.create(facility=f2, pseudonym="Client-B")

        result = Client.objects.for_facility(f1)
        assert list(result) == [c1]

        result = Client.objects.for_facility(f2)
        assert list(result) == [c2]

    def test_all_returns_everything(self, two_facilities):
        f1, f2 = two_facilities
        Client.objects.create(facility=f1, pseudonym="Client-A")
        Client.objects.create(facility=f2, pseudonym="Client-B")

        assert Client.objects.all().count() == 2

    def test_for_facility_is_chainable(self, two_facilities):
        f1, _ = two_facilities
        Client.objects.create(facility=f1, pseudonym="Alpha")
        Client.objects.create(facility=f1, pseudonym="Beta")

        result = Client.objects.for_facility(f1).filter(pseudonym="Alpha")
        assert result.count() == 1
        assert result.first().pseudonym == "Alpha"
