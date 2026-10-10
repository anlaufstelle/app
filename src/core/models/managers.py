"""Custom managers for facility scoping."""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _


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

    def _protect_dynamic_batch(self, objects, *, fields=None):
        from core.models import DocumentType
        from core.services.dynamic_protection import lock_dynamic_protection_many
        from core.services.events.fields import compute_event_search_text

        lock_dynamic_protection_many({obj.facility_id for obj in objects})
        if fields is not None:
            current_types = dict(
                self.filter(pk__in=[obj.pk for obj in objects])
                .order_by("facility_id", "pk")
                .select_for_update(of=("self",))
                .values_list("pk", "document_type_id")
            )
            if "document_type" not in fields and "document_type_id" not in fields:
                for obj in objects:
                    if obj.pk in current_types:
                        obj.document_type_id = current_types[obj.pk]
        for obj in objects:
            if (
                obj.data_json
                and obj.document_type_id is not None
                and not DocumentType.objects.for_facility(obj.facility_id).filter(pk=obj.document_type_id).exists()
            ):
                raise ValidationError(_("Dynamische Daten benötigen einen Dokumentationstyp derselben Einrichtung."))
            obj._encrypt_sensitive_fields()
            if hasattr(obj, "search_text"):
                obj.search_text = compute_event_search_text(obj.data_json, obj.document_type)

    def bulk_create(self, objs, *args, **kwargs):
        objects = list(objs)
        if not objects or not hasattr(objects[0], "_encrypt_sensitive_fields"):
            return super().bulk_create(objects, *args, **kwargs)
        with transaction.atomic(using=self.db):
            self._protect_dynamic_batch(objects)
            if kwargs.get("update_conflicts") and hasattr(objects[0], "search_text"):
                update_fields = list(kwargs.get("update_fields") or ())
                if "data_json" in update_fields and "search_text" not in update_fields:
                    kwargs["update_fields"] = [*update_fields, "search_text"]
            return super().bulk_create(objects, *args, **kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        objects = list(objs)
        fields = list(fields)
        if (
            not objects
            or not hasattr(objects[0], "_encrypt_sensitive_fields")
            or not {"data_json", "search_text", "document_type", "document_type_id"}.intersection(fields)
        ):
            return super().bulk_update(objects, fields, *args, **kwargs)
        with transaction.atomic(using=self.db):
            self._protect_dynamic_batch(objects, fields=fields)
            if {"document_type", "document_type_id"}.intersection(fields) and "data_json" not in fields:
                fields.append("data_json")
            if hasattr(objects[0], "search_text") and "search_text" not in fields:
                fields.append("search_text")
            return super().bulk_update(objects, fields, *args, **kwargs)

    def update_or_create(self, defaults=None, create_defaults=None, **kwargs):
        if self.model.__name__ not in {"DocumentType", "FieldTemplate"}:
            return super().update_or_create(defaults=defaults, create_defaults=create_defaults, **kwargs)
        from core.services.dynamic_protection import lock_dynamic_protection_many

        facility = kwargs.get("facility", kwargs.get("facility_id"))
        if facility is None:
            for values in (create_defaults or {}, defaults or {}):
                facility = values.get("facility", values.get("facility_id"))
                if facility is not None:
                    break
        facility_ids = (
            [getattr(facility, "pk", facility)]
            if facility is not None
            else self.filter(**kwargs).values_list("facility_id", flat=True).distinct()
        )
        with transaction.atomic(using=self.db):
            lock_dynamic_protection_many(facility_ids, exclusive=True)
            return super().update_or_create(defaults=defaults, create_defaults=create_defaults, **kwargs)


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
