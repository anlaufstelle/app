"""Organization and facility."""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


class Organization(models.Model):
    """Parent organization that operates one or more facilities."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))

    class Meta:
        verbose_name = _("Organisation")
        verbose_name_plural = _("Organisationen")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Facility(models.Model):
    """Individual facility within an organization."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="facilities",
        verbose_name=_("Organisation"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    description = models.TextField(blank=True, verbose_name=_("Beschreibung"))
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))

    class Meta:
        verbose_name = _("Einrichtung")
        verbose_name_plural = _("Einrichtungen")
        ordering = ["name"]

    def __str__(self):
        return self.name
