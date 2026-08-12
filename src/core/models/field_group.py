"""Feldgruppe — visuelle Gliederung der Felder eines Dokumentationstyps."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.document_type import DocumentType
from core.models.mixins import SluggedModel
from core.models.slug import _SLUG_RE


class FieldGroup(SluggedModel):
    """Visuelle Gruppierung von Feldern innerhalb eines Dokumentationstyps.

    M0 (Refs #1056): erlaubt es, die Felder eines DocumentType im Admin-UI und
    im Formular in benannte, optional einklappbare Abschnitte zu gliedern. Kein
    eigener ``facility``-FK — der Scope ergibt sich transitiv aus
    ``document_type.facility`` (wie bei :class:`DocumentTypeField`), RLS via
    JOIN-Policy (Migration 0111).

    M2/#1713 (globale Block-Sequenz): ``sort_order`` ist die Position des
    **Gruppen-Blocks** in EINER globalen Sequenz je DocumentType, die sich
    Gruppen und ungruppierte Felder teilen — ein ungruppiertes
    :class:`DocumentTypeField` (``field_group IS NULL``) trägt in derselben
    Sequenz seine eigene Position (siehe dort). Es gibt keinen fixen „Ohne
    Gruppe"-Block mehr; freie Felder können zwischen Gruppen liegen. Die
    kanonische Reihenfolge liefert ``core.services.config.iter_blocks_in_order``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.ForeignKey(
        DocumentType,
        on_delete=models.CASCADE,
        related_name="field_groups",
        verbose_name=_("Dokumentationstyp"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    slug = models.SlugField(
        max_length=120,
        verbose_name=_("Slug"),
        help_text=_("Stabiler Identifier — wird im M2-Paketformat referenziert. Nach Erstellung unveränderbar."),
    )
    sort_order = models.IntegerField(default=0, verbose_name=_("Sortierung"))
    is_collapsible = models.BooleanField(default=False, verbose_name=_("Einklappbar"))

    class Meta:
        verbose_name = _("Feldgruppe")
        verbose_name_plural = _("Feldgruppen")
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "slug"],
                name="unique_documenttype_fieldgroup_slug",
            ),
            models.UniqueConstraint(
                fields=["document_type", "name"],
                name="unique_documenttype_fieldgroup_name",
            ),
        ]

    def slug_sibling_queryset(self):
        """Slug ist je Dokumentationstyp eindeutig."""
        return FieldGroup.objects.filter(document_type=self.document_type).exclude(pk=self.pk)

    def clean(self):
        super().clean()
        if self.slug and not _SLUG_RE.fullmatch(self.slug):
            raise ValidationError({"slug": _("Nur a-z, 0-9 und Bindestriche erlaubt.")})

    def __str__(self):
        return f"{self.document_type.name} → {self.name}"
