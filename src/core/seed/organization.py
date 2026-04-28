"""Organization and facility creation."""

from core.models import Facility, Organization


def seed_organization(name: str = "Anlaufstelle") -> Organization:
    """Return (creating if needed) the single demo organization."""
    org, _ = Organization.objects.get_or_create(name=name)
    return org


def seed_facility(org: Organization, name: str) -> Facility:
    """Return (creating if needed) a facility for the given organization."""
    facility, _ = Facility.objects.get_or_create(organization=org, name=name)
    return facility
