"""Reusable abstract model mixins."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SoftDeletableModel(models.Model):
    """Abstract base for aggregates that support reversible soft-delete.

    Provides ``is_deleted``, ``deleted_at``, ``deleted_by`` plus
    ``soft_delete()`` and ``restore()`` convenience methods. Combine with
    :class:`core.models.managers.SoftDeletableFacilityScopedManager` to
    filter soft-deleted rows out of the default queryset.
    """

    is_deleted = models.BooleanField(default=False, verbose_name=_("Gelöscht"))
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Gelöscht am"),
    )
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Gelöscht von"),
    )

    class Meta:
        abstract = True

    def soft_delete(self, user=None):
        """Mark this object as deleted; preserve in DB for audit/restore."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])

    def restore(self):
        """Reverse a soft-delete."""
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by"])


class DynamicDataEncryptionMixin(models.Model):
    """Abstrakte Basis fuer Modelle mit einem ``document_type``-gesteuerten,
    verschluesselbaren ``data_json``-Feld (P4a, Refs #1058).

    Buendelt die Feldverschluesselungs-Logik, die zuvor 1:1 in
    ``Event._encrypt_sensitive_fields`` lag — generalisiert fuer kuenftige
    Modelle mit derselben Form (z. B. Client/Case in spaeteren M2-Phasen).

    **Voraussetzungen** (vom aufrufenden Modell zu erfuellen):

    - ``self.document_type`` (FK) bzw. ``self.document_type_id`` — verweist
      auf ein Objekt mit ``.fields.filter(field_template__is_encrypted=True)
      .values_list("field_template__slug", flat=True)`` (aktuell
      ``DocumentType``).
    - ``self.data_json`` — ein ``dict``-wertiges JSONField mit den
      Rohdaten, deren als ``is_encrypted`` markierte Slugs verschluesselt
      werden sollen.

    **Kein ``save()``-Override im Mixin** (Entscheid E5): MRO-Kollisionen
    mit :class:`SoftDeletableModel` und anderen Mixins sollen nicht durch
    unsichtbare Aufrufreihenfolge entstehen. Jedes Modell ruft
    ``self._encrypt_sensitive_fields()`` explizit in seinem eigenen
    ``save()`` auf, bevor ``super().save()`` folgt — ein Bugfix-Ort fuer
    die krypto-nahe Logik selbst, aber transparente Aufrufreihenfolge je
    Modell.
    """

    class Meta:
        abstract = True

    def _encrypt_sensitive_fields(self):
        """Encrypt fields that are marked as encrypted in the field template."""
        from core.services.file_vault import encrypt_field, is_encrypted_value

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
                # Skip file attachment markers — they are metadata, not user data.
                # Beide Formate berücksichtigen: Stufe A (__file__) und Stufe B
                # (__files__, Refs #622).
                if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
                    continue
                self.data_json[key] = encrypt_field(value)
