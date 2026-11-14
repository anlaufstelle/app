"""Shipped ban types name a person: packages, event service and bulk seed.

A ban document type (``system_type: ban``) in the default, beratungsstelle and
awareness packages carries ``min_contact_stage: identified``. A new ban event
therefore needs a person (a pseudonym is enough); the bulk seed never creates
one without. Existing anonymous ban events stay editable and deactivatable,
because the contact-stage check runs only when an event is created.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Client, DocumentType, Event
from core.packages.catalog import list_packages, parse_catalog_package
from core.packages.loader import load_package
from core.seed.events import seed_events_bulk
from core.seed.users import seed_users
from core.services.events.crud import _validate_contact_stage, create_event, update_event

IDENTIFIED = Client.ContactStage.IDENTIFIED

BAN_TYPES_OF_SHIPPED_PACKAGES = [
    ("default", "hausverbot"),
    ("beratungsstelle", "hausverbot"),
    ("awareness", "orga-meldung"),
]


def _data_for_ban() -> dict[str, Any]:
    return {"grund": "Test reason"}


# ---------------------------------------------------------------------------
# Shipped packages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("package", "slug"), BAN_TYPES_OF_SHIPPED_PACKAGES)
def test_shipped_ban_types_require_a_person(package, slug):
    by_slug = {item.slug: item for item in parse_catalog_package(package).document_types}

    assert by_slug[slug].system_type == "ban"
    assert by_slug[slug].min_contact_stage == "identified"


@pytest.mark.parametrize("package", list_packages())
def test_every_shipped_ban_type_has_a_minimum_stage(package):
    ban_types = [item for item in parse_catalog_package(package).document_types if item.system_type == "ban"]

    assert all(item.min_contact_stage for item in ban_types)


# ---------------------------------------------------------------------------
# Event service: creating needs a person, existing anonymous bans stay usable
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEventService:
    @pytest.fixture(params=BAN_TYPES_OF_SHIPPED_PACKAGES, ids=lambda item: item[1])
    def ban_type(self, request, facility):
        package, slug = request.param
        load_package(facility, parse_catalog_package(package))
        return DocumentType.objects.get(facility=facility, slug=slug)

    def test_anonymous_ban_is_refused(self, facility, staff_user, ban_type):
        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=ban_type,
                occurred_at=timezone.now(),
                data_json=_data_for_ban(),
                client=None,
                is_anonymous=True,
            )

        assert "Anonyme Kontakte sind bei diesem Dokumentationstyp nicht erlaubt" in str(excinfo.value)

    def test_ban_without_a_person_is_refused(self, facility, staff_user, ban_type):
        with pytest.raises(ValidationError) as excinfo:
            create_event(
                facility=facility,
                user=staff_user,
                document_type=ban_type,
                occurred_at=timezone.now(),
                data_json=_data_for_ban(),
                client=None,
                is_anonymous=False,
            )

        assert "Für diesen Dokumentationstyp muss eine Person ausgewählt werden" in str(excinfo.value)
        assert not Event.objects.filter(document_type=ban_type).exists()

    def test_ban_for_a_pseudonym_only_person_is_accepted(self, facility, staff_user, ban_type, client_identified):
        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=ban_type,
            occurred_at=timezone.now(),
            data_json=_data_for_ban(),
            client=client_identified,
        )

        assert event.client_id == client_identified.pk
        assert event.is_anonymous is False

    @staticmethod
    def _legacy_anonymous_ban(facility, staff_user, ban_type):
        """An anonymous ban that predates the rule, inserted past ``save()``."""
        return Event.objects.bulk_create(
            [
                Event(
                    facility=facility,
                    document_type=ban_type,
                    occurred_at=timezone.now(),
                    data_json={"grund": "Legacy reason", "aktiv": True},
                    created_by=staff_user,
                    is_anonymous=True,
                )
            ]
        )[0]

    def test_existing_anonymous_ban_stays_editable_and_deactivatable(self, facility, staff_user, ban_type):
        legacy = self._legacy_anonymous_ban(facility, staff_user, ban_type)

        update_event(legacy, staff_user, {"grund": "Corrected reason", "aktiv": True})
        update_event(legacy, staff_user, {"grund": "Corrected reason", "aktiv": False})

        legacy.refresh_from_db()
        assert legacy.is_anonymous is True
        assert legacy.client_id is None
        assert legacy.data_json["grund"] == "Corrected reason"
        assert legacy.data_json["aktiv"] is False

    def test_existing_anonymous_ban_can_be_saved_again_directly(self, facility, staff_user, ban_type):
        legacy = self._legacy_anonymous_ban(facility, staff_user, ban_type)

        legacy.data_json = {"grund": "Saved again", "aktiv": False}
        legacy.save()
        legacy.save(update_fields=["data_json"])

        legacy.refresh_from_db()
        assert legacy.client_id is None
        assert legacy.data_json["aktiv"] is False

    def test_a_new_ban_event_without_a_person_is_refused_below_the_service(self, facility, staff_user, ban_type):
        with pytest.raises(ValidationError):
            Event.objects.create(
                facility=facility,
                document_type=ban_type,
                occurred_at=timezone.now(),
                data_json=_data_for_ban(),
                created_by=staff_user,
                is_anonymous=True,
            )

        assert not Event.objects.filter(document_type=ban_type).exists()

    def test_a_new_ban_event_with_a_person_can_be_inserted_directly(
        self, facility, staff_user, ban_type, client_identified
    ):
        event = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=ban_type,
            occurred_at=timezone.now(),
            data_json=_data_for_ban(),
            created_by=staff_user,
        )

        assert event.client_id == client_identified.pk

    def test_the_service_demands_a_person_for_a_ban_type_without_a_stage(self, facility, staff_user):
        """The person rule hangs on the system type, not on the type's own stage."""
        stageless_ban = DocumentType(facility=facility, name="Stageless", slug="stageless", system_type="ban")

        for client, is_anonymous in ((None, True), (None, False)):
            with pytest.raises(ValidationError):
                _validate_contact_stage(stageless_ban, client, is_anonymous)

    def test_other_types_without_a_stage_stay_anonymous_capable(self, facility, staff_user):
        plain = DocumentType.objects.create(facility=facility, name="Plain contact")

        event = create_event(
            facility=facility,
            user=staff_user,
            document_type=plain,
            occurred_at=timezone.now(),
            data_json={},
            client=None,
            is_anonymous=False,
        )

        assert event.is_anonymous is True


# ---------------------------------------------------------------------------
# Bulk seed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(("package", "slug"), BAN_TYPES_OF_SHIPPED_PACKAGES)
def test_seed_creates_no_ban_event_without_a_person(facility, package, slug):
    users = seed_users(facility, 0)
    clients = [
        Client.objects.create(facility=facility, pseudonym=f"Seed-{i}", contact_stage=IDENTIFIED, created_by=users[0])
        for i in range(8)
    ]
    load_package(facility, parse_catalog_package(package))
    DocumentType.objects.filter(facility=facility).exclude(slug=slug).delete()

    random.seed(2786)
    created = seed_events_bulk(
        facility, users, clients, {"events_per_facility": 120, "zeitraum_days": 90}, package=package
    )

    ban_events = Event.objects.filter(facility=facility, document_type__slug=slug)
    assert created == 120
    assert ban_events.count() == 120
    assert not ban_events.filter(client__isnull=True).exists()
    assert not ban_events.filter(is_anonymous=True).exists()
