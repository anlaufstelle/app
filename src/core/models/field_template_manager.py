"""Reject option updates that bypass instance validation."""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager, FacilityScopedQuerySet


def _reject_options_bulk_write(fields):
    if "options_json" in fields:
        raise ValidationError(
            {
                "options_json": _(
                    "Optionsänderungen über update, bulk_update oder Upsert sind nicht zulässig. "
                    "Bitte die Feldvorlage einzeln speichern, damit verwendete Slugs geprüft werden."
                )
            }
        )


class FieldTemplateQuerySet(FacilityScopedQuerySet):
    """Keep ordinary bulk updates while rejecting unsafe option mutations.

    This guards supported ORM writes, not raw SQL or concurrent reference writes.
    """

    def update(self, **kwargs):
        _reject_options_bulk_write(kwargs)
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, batch_size=None):
        fields = tuple(fields)
        _reject_options_bulk_write(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if update_fields is not None:
            update_fields = tuple(update_fields)
        if update_conflicts:
            _reject_options_bulk_write(update_fields or ())
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class FieldTemplateManager(FacilityScopedManager):
    """Expose the guarded queryset without changing facility scoping."""

    def get_queryset(self):
        return FieldTemplateQuerySet(self.model, using=self._db, hints=self._hints)
