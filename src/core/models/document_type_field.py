"""Zuordnung einer Feldvorlage zu einem Dokumentationstyp."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.document_type import DocumentType
from core.models.field_group import FieldGroup
from core.models.field_template import FieldTemplate


class DocumentTypeField(models.Model):
    """Association of a field template with a document type.

    M2/#1713 (globale Block-Sequenz): ``sort_order`` hat zwei Bedeutungen je nach
    ``field_group``:

    * ``field_group IS NULL`` (freies Feld) → Position des **Feld-Blocks** in der
      globalen Block-Sequenz des DocumentType (dieselbe Sequenz wie
      :attr:`FieldGroup.sort_order`);
    * ``field_group`` gesetzt → Position **innerhalb** der Gruppe (0..n-1).

    Die kanonische, interleavte Reihenfolge liefert
    ``core.services.config.iter_blocks_in_order`` /
    ``iter_fields_in_block_order``; Board, Offline-Bundle und Feed-Vorschau
    nutzen sie direkt. Das Event-Formular (``grouped_fields()``) wendet
    dieselbe Semantik auf BoundFields an (eigene Implementierung, per Tests
    gegen die Iteratoren-Semantik fixiert).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.ForeignKey(
        DocumentType,
        on_delete=models.CASCADE,
        related_name="fields",
        verbose_name=_("Dokumentationstyp"),
    )
    field_template = models.ForeignKey(
        FieldTemplate,
        on_delete=models.CASCADE,
        related_name="document_type_fields",
        verbose_name=_("Feldvorlage"),
    )
    field_group = models.ForeignKey(
        FieldGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fields",
        verbose_name=_("Feldgruppe"),
    )
    sort_order = models.IntegerField(default=0, verbose_name=_("Sortierung"))

    class Meta:
        verbose_name = _("Dokumentationstyp-Feld")
        verbose_name_plural = _("Dokumentationstyp-Felder")
        ordering = ["sort_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "field_template"],
                name="unique_doctype_field",
            ),
        ]

    def clean(self):
        from core.services.dynamic_protection import validate_configuration_facility

        super().clean()
        if self.document_type_id:
            validate_configuration_facility(
                self, self.document_type.facility_id, facility_lookup="document_type__facility_id"
            )
        # M0 (Refs #1056): Eine zugeordnete Feldgruppe muss zum selben
        # Dokumentationstyp gehoeren — sonst wuerde ein Feld unter einer Gruppe
        # eines fremden DocumentType (ggf. sogar fremder Facility) einsortiert.
        if self.field_group_id and self.field_group.document_type_id != self.document_type_id:
            raise ValidationError({"field_group": _("Die Feldgruppe muss zum selben Dokumentationstyp gehören.")})

    def save(self, *args, **kwargs):
        from core.services.dynamic_protection import (
            configuration_write,
            rewrite_dynamic_records,
            validate_configuration_facility,
        )

        validate_configuration_facility(
            self, self.document_type.facility_id, facility_lookup="document_type__facility_id"
        )
        with configuration_write(self.document_type.facility_id):
            previous = type(self).objects.filter(pk=self.pk).first()
            if (
                FieldTemplate.objects.for_facility(self.document_type.facility_id)
                .filter(pk=self.field_template_id)
                .values_list("pk", flat=True)
                .first()
                is None
            ):
                raise ValidationError(_("Die Feldvorlage gehört zu einer anderen Einrichtung."))
            super().save(*args, **kwargs)
            if previous is None or (previous.document_type_id, previous.field_template_id) != (
                self.document_type_id,
                self.field_template_id,
            ):
                targets = [self.document_type]
                if previous is not None and previous.document_type_id != self.document_type_id:
                    targets.append(previous.document_type)
                rewrite_dynamic_records(targets)

    def delete(self, *args, **kwargs):
        from core.services.dynamic_protection import configuration_write, rewrite_dynamic_records

        target = self.document_type
        with configuration_write(target.facility_id):
            result = super().delete(*args, **kwargs)
            rewrite_dynamic_records([target])
            return result

    def __str__(self):
        return f"{self.document_type.name} → {self.field_template.name}"
