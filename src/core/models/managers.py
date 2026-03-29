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
