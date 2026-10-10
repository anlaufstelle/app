"""Change history for events."""

import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _


class EventHistory(models.Model):
    """Append-only change log for an event.

    Records are immutable once created. Updates and deletions are prevented
    at the application level via overridden save() and delete() methods.
    """

    class Action(models.TextChoices):
        CREATE = "create", _("Erstellt")
        UPDATE = "update", _("Aktualisiert")
        DELETE = "delete", _("Gelöscht")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        "core.Event",
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name=_("Ereignis"),
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_changes",
        verbose_name=_("Geändert von"),
    )
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Geändert am"))
    action = models.CharField(
        max_length=10,
        choices=Action.choices,
        verbose_name=_("Aktion"),
    )
    data_before = models.JSONField(null=True, blank=True, verbose_name=_("Daten vorher"))
    data_after = models.JSONField(null=True, blank=True, verbose_name=_("Daten nachher"))
    field_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Snapshot der Feld-Labels, Sensitivität und Verschlüsselungsstatus "
            "zum Zeitpunkt der Änderung für revisionsfeste Darstellung."
        ),
    )

    # ADR-050 D1.4 (Refs #2065): Wann wurde diese Zeile inhaltlich geleert?
    # Traegt drei Aufgaben zugleich — Idempotenz-Filter des Sweeps, Praedikat
    # des Teilindex (D6) und Fristanker der zweiten Stufe (D8/M-7). Aus
    # ``changed_at`` laesst sich „wie lange steht die Zeile schon leer" nicht
    # beantworten.
    redacted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Redigiert am"),
    )

    class Meta:
        verbose_name = _("Ereignis-Historie")
        verbose_name_plural = _("Ereignis-Historien")
        ordering = ["-changed_at"]
        indexes = [
            # ADR-050 D6: Teilindex fuer den altersbasierten Sweep. ``changed_at``
            # hatte bisher gar keinen Index (Migration 0006 indiziert nur
            # ``core_event``), jede Altersabfrage war ein Seq-Scan. Das
            # ``redacted_at IS NULL``-Praedikat ist mit dem Zeitstempel aus D1.4
            # unstrittig immutable; der Index schrumpft mit jedem Lauf, weil
            # erledigte Zeilen herausfallen.
            models.Index(
                fields=["changed_at"],
                condition=models.Q(redacted_at__isnull=True),
                name="eventhistory_unredacted_age",
            ),
        ]

    def save(self, *args, **kwargs):
        """Prevent updates — only inserts are allowed."""
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("EventHistory records are append-only and cannot be updated.")
        from core.models import FieldTemplate
        from core.services.dynamic_protection import lock_dynamic_protection
        from core.services.events.history_diff import is_redaction_marker
        from core.services.file_vault import encrypt_field, is_encrypted_value

        payloads = (self.data_before, self.data_after)
        # Empty and redacted history carries no values needing protection.
        if not any(isinstance(payload, dict) and payload and not is_redaction_marker(payload) for payload in payloads):
            return super().save(*args, **kwargs)
        with transaction.atomic():
            lock_dynamic_protection(self.event.facility_id)
            encrypted_slugs = set(
                FieldTemplate.objects.for_facility(self.event.facility_id)
                .filter(is_encrypted=True)
                .values_list("slug", flat=True)
            )
            encrypted_slugs.update(
                slug
                for slug, metadata in self.field_metadata.items()
                if isinstance(metadata, dict) and metadata.get("is_encrypted") is True
            )
            for payload in payloads:
                if not isinstance(payload, dict) or is_redaction_marker(payload):
                    continue
                for slug in encrypted_slugs:
                    value = payload.get(slug)
                    if value is None or value == "" or is_encrypted_value(value):
                        continue
                    if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
                        continue
                    payload[slug] = encrypt_field(value)
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Prevent deletion of history records."""
        raise ValueError("EventHistory records are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.event} – {self.get_action_display()} ({self.changed_at:%d.%m.%Y %H:%M})"
