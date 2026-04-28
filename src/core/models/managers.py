"""Custom managers for facility scoping."""

from django.db import models


class FacilityScopedQuerySet(models.QuerySet):
    """QuerySet with .for_facility() convenience method."""

    def for_facility(self, facility):
        """Return only objects belonging to the given facility."""
        return self.filter(facility=facility)


class FacilityScopedManager(models.Manager):
    """Manager that uses FacilityScopedQuerySet."""

    def get_queryset(self):
        return FacilityScopedQuerySet(self.model, using=self._db)

    def for_facility(self, facility):
        return self.get_queryset().for_facility(facility)


class EventQuerySet(FacilityScopedQuerySet):
    """QuerySet with sensitivity-based visibility filter for events."""

    def visible_to(self, user):
        """Return only events whose DocumentType.sensitivity the user may see.

        Single source of truth for the sensitivity-based event visibility used
        in client/case timelines, the Zeitstrom feed, and handover summaries.
        Without this gate, lower roles see metadata (DocumentType name,
        timestamp, client link) of events they aren't supposed to know exist.
        """
        from core.services.sensitivity import allowed_sensitivities_for_user

        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(document_type__sensitivity__in=allowed_sensitivities_for_user(user))


class EventManager(FacilityScopedManager):
    """Manager exposing :meth:`EventQuerySet.visible_to` and facility scoping."""

    def get_queryset(self):
        return EventQuerySet(self.model, using=self._db)

    def visible_to(self, user):
        return self.get_queryset().visible_to(user)
