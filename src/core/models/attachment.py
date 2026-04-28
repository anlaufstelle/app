"""Encrypted file attachments for events."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class EventAttachment(models.Model):
    """An encrypted file attached to a specific field of an event."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "core.Event",
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("Event"),
    )
    field_template = models.ForeignKey(
        "core.FieldTemplate",
        on_delete=models.PROTECT,
        related_name="attachments",
        verbose_name=_("Feldvorlage"),
    )
    storage_filename = models.CharField(
        max_length=255,
        verbose_name=_("Speicherdateiname"),
        help_text=_("UUID-basierter Dateiname auf Disk (z.B. abcd1234.enc)"),
    )
    original_filename_encrypted = models.JSONField(
        verbose_name=_("Originaldateiname (verschlüsselt)"),
        help_text=_("Verschlüsselt via encrypt_field()"),
    )
    file_size = models.PositiveIntegerField(
        verbose_name=_("Dateigröße (Bytes)"),
    )
    mime_type = models.CharField(
        max_length=100,
        verbose_name=_("MIME-Typ"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name=_("Erstellt von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))

    class Meta:
        verbose_name = _("Dateianhang")
        verbose_name_plural = _("Dateianhänge")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Attachment {self.storage_filename} → Event {self.event_id}"
