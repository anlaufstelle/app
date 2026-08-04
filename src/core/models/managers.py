"""Custom managers for facility scoping."""

from django.db import models


class FacilityScopedQuerySet(models.QuerySet):
    """QuerySet with .for_facility() and soft-delete convenience methods."""

    def for_facility(self, facility):
        """Return only objects belonging to the given facility."""
        return self.filter(facility=facility)

    def active(self):
        """Return only non-soft-deleted rows.

        Convenience method for models using
        :class:`core.models.mixins.SoftDeletableModel`. Equivalent to
        ``.filter(is_deleted=False)``.
        """
        return self.filter(is_deleted=False)

    def deleted(self):
        """Return only soft-deleted rows (for restore/papierkorb views)."""
        return self.filter(is_deleted=True)


class FacilityScopedManager(models.Manager):
    """Manager that uses FacilityScopedQuerySet.

    Does not filter soft-deleted rows by default — consistent with the
    existing :class:`Event` pattern. Use ``.active()`` on querysets to
    exclude soft-deleted rows in list views and forms.
    """

    def get_queryset(self):
        return FacilityScopedQuerySet(self.model, using=self._db)

    def for_facility(self, facility):
        return self.get_queryset().for_facility(facility)

    def active(self):
        return self.get_queryset().active()

    def deleted(self):
        return self.get_queryset().deleted()


class AttachmentQuerySet(FacilityScopedQuerySet):
    """QuerySet fuer ``Attachment`` — Soft-Delete via ``deleted_at``.

    ``Attachment`` hat kein ``is_deleted``-Feld (SoftDeletableModel), sondern
    den Versionsketten-Marker ``deleted_at`` (Refs #622). Ohne diese Overrides
    vererbte ``FacilityScopedQuerySet.active()`` den ``is_deleted``-Filter auch
    auf den Reverse-Manager (``event.attachments.active()``) → ``FieldError``
    (ADR-039 §3).
    """

    def active(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class AttachmentManager(FacilityScopedManager):
    """Manager fuer ``Attachment`` (facility-Scoping + ``deleted_at``-Semantik)."""

    def get_queryset(self):
        return AttachmentQuerySet(self.model, using=self._db)


class EventQuerySet(FacilityScopedQuerySet):
    """QuerySet with sensitivity-based visibility filter for events."""

    def visible_to(self, user):
        """Return only events whose DocumentType.sensitivity the user may see.

        Single source of truth for the sensitivity-based event visibility used
        in client/case timelines, the Zeitstrom feed, and handover summaries.
        Without this gate, lower roles see metadata (DocumentType name,
        timestamp, client link) of events they aren't supposed to know exist.
        """
        from core.services.compliance import allowed_sensitivities_for_user

        if user is None or not getattr(user, "is_authenticated", False):
            return self.none()
        return self.filter(document_type__sensitivity__in=allowed_sensitivities_for_user(user))


class EventManager(FacilityScopedManager):
    """Manager exposing :meth:`EventQuerySet.visible_to` and facility scoping."""

    def get_queryset(self):
        return EventQuerySet(self.model, using=self._db)

    def visible_to(self, user):
        return self.get_queryset().visible_to(user)
