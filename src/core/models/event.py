"""Documentation event -- core object of the Anlaufstelle."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import EventManager


class Event(models.Model):
    """A single documentation event (contact, service, note, etc.)."""

    objects = EventManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Einrichtung"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Klientel"),
    )
    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("Dokumentationstyp"),
    )
    case = models.ForeignKey(
        "core.Case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Fall"),
    )
    episode = models.ForeignKey(
        "core.Episode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Episode"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
        verbose_name=_("Erstellt von"),
    )
    occurred_at = models.DateTimeField(verbose_name=_("Zeitpunkt"))
    data_json = models.JSONField(default=dict, verbose_name=_("Daten (JSON)"))
    is_anonymous = models.BooleanField(default=False, verbose_name=_("Anonym"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("Gelöscht"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))

    class Meta:
        verbose_name = _("Ereignis")
        verbose_name_plural = _("Ereignisse")
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["client", "-occurred_at"]),
            models.Index(fields=["document_type", "-occurred_at"]),
            models.Index(fields=["facility", "-occurred_at"]),
            # Nahezu alle Event-Queries filtern is_deleted=False, daher
            # Composite-Index (facility, is_deleted, -occurred_at) für
            # Timeline-/Listen-Queries. Refs #638.
            models.Index(fields=["facility", "is_deleted", "-occurred_at"], name="event_facility_del_occ_idx"),
        ]

    def __str__(self):
        return f"{self.document_type} – {self.occurred_at:%d.%m.%Y %H:%M}"

    def save(self, *args, **kwargs):
        self._encrypt_sensitive_fields()
        super().save(*args, **kwargs)

    def _encrypt_sensitive_fields(self):
        """Encrypt fields that are marked as encrypted in the field template."""
        from core.services.encryption import encrypt_field, is_encrypted_value

        if not self.document_type_id or not self.data_json:
            return

        if not hasattr(self, "_cached_encrypted_slugs"):
            self._cached_encrypted_slugs = set(
                self.document_type.fields.filter(
                    field_template__is_encrypted=True,
                ).values_list("field_template__slug", flat=True)
            )
        encrypted_field_names = self._cached_encrypted_slugs
        for key in encrypted_field_names:
            value = self.data_json.get(key)
            if value and not is_encrypted_value(value):
                # Skip file attachment markers — they are metadata, not user data
                if isinstance(value, dict) and value.get("__file__"):
                    continue
                self.data_json[key] = encrypt_field(value)
