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
        from core.services.option_usage import validate_option_references

        lock_dynamic_protection_many({obj.facility_id for obj in objects})
        current_rows = {}
        if fields is not None:
            current_rows = {
                row["pk"]: row
                for row in self.filter(pk__in=[obj.pk for obj in objects])
                .order_by("facility_id", "pk")
                .select_for_update(of=("self",))
                .values("pk", "facility_id", "document_type_id", "data_json")
            }
            if "document_type" not in fields and "document_type_id" not in fields:
                for obj in objects:
                    if obj.pk in current_rows:
                        obj.document_type_id = current_rows[obj.pk]["document_type_id"]
        for obj in objects:
            if (
                obj.data_json
                and obj.document_type_id is not None
                and not DocumentType.objects.for_facility(obj.facility_id).filter(pk=obj.document_type_id).exists()
            ):
                raise ValidationError(_("Dynamische Daten benötigen einen Dokumentationstyp derselben Einrichtung."))
            if fields is None or {"data_json", "document_type", "document_type_id"}.intersection(fields):
                current = current_rows.get(obj.pk)
                validate_option_references(
                    obj.facility_id,
                    obj.data_json,
                    previous_data=current["data_json"]
                    if current and current["facility_id"] == obj.facility_id
                    else None,
                )
            obj._encrypt_sensitive_fields()
            if hasattr(obj, "search_text"):
                obj.search_text = compute_event_search_text(obj.data_json, obj.document_type)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts and hasattr(self.model, "_encrypt_sensitive_fields"):
            raise ValueError("bulk_create with update_conflicts is not supported for protected dynamic data")
        options = {
            "batch_size": batch_size,
            "ignore_conflicts": ignore_conflicts,
            "update_conflicts": update_conflicts,
            "update_fields": update_fields,
            "unique_fields": unique_fields,
        }
        objects = list(objs)
        if not objects or not hasattr(objects[0], "_encrypt_sensitive_fields"):
            return super().bulk_create(objects, **options)
        with transaction.atomic(using=self.db):
            self._protect_dynamic_batch(objects)
            return super().bulk_create(objects, **options)

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


class ClientQuerySet(FacilityScopedQuerySet):
    """Prevent bulk writes from bypassing the four-eyes contact-stage workflow."""

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_conflicts and "contact_stage" in (update_fields or ()):
            raise ValidationError(_("Kontaktstufen dürfen nicht über einen Bulk-Upsert geändert werden."))
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def bulk_update(self, objs, fields, batch_size=None):
        if "contact_stage" not in fields:
            return super().bulk_update(objs, fields, batch_size=batch_size)
        # Keep every batch in the same global person-lock order.
        objs = sorted(objs, key=lambda obj: str(obj.pk))
        # Django uses savepoint=False internally; contain a refused batch here.
        with transaction.atomic(using=self.db):
            return super().bulk_update(objs, fields, batch_size=batch_size)

    def update(self, **kwargs):
        if "contact_stage" not in kwargs:
            return super().update(**kwargs)
        stage = kwargs["contact_stage"]
        expression = stage if hasattr(stage, "resolve_expression") else models.Value(stage)
        with transaction.atomic(using=self.db):
            # Use a plain outer query to lock person rows without joined children.
            locked_ids = list(
                self.model._base_manager.using(self.db)
                .filter(pk__in=self.values("pk"))
                .order_by("pk")
                .select_for_update()
                .values_list("pk", flat=True)
            )
            if (
                self.filter(pk__in=locked_ids, contact_stage="qualified", events__isnull=False)
                .annotate(requested_stage=expression)
                .exclude(requested_stage=models.F("contact_stage"))
                .exists()
            ):
                raise ValidationError(
                    _("Bei vorhandenen Ereignissen ist ein Herabstufungsantrag mit Vier-Augen-Freigabe erforderlich.")
                )
            return super().update(**kwargs)


class ClientManager(FacilityScopedManager):
    """Facility scoping plus the guard for bulk contact-stage changes."""

    def get_queryset(self):
        return ClientQuerySet(self.model, using=self._db)


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
