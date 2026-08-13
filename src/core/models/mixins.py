"""Reusable abstract model mixins."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models.slug import _SLUG_RE, generate_unique_slug


class SluggedModel(models.Model):
    """Abstrakte Basis für Modelle mit generiertem, unveränderbarem ``slug``.

    Refs #1834: ``DocumentType``, ``FieldTemplate`` und ``FieldGroup`` trugen
    dieselbe Slug-Save-Mechanik je einmal — inklusive eigener
    ``_SLUG_RETRY_LIMIT``-Konstante, eigener ``_generate_unique_slug``-Kopie
    und dreifach identischem Immutabilitäts-Guard samt Fehlertext. Hier steht
    sie einmal; die ``SlugField``-Definitionen bleiben bewusst lokal in den
    Modellen (unterschiedliche ``blank``/``help_text``) — deshalb braucht das
    Zusammenführen keine Migration.

    **Vertrag der Subklassen:**

    * ``slug``/``name``-Felder lokal definieren,
    * :meth:`slug_sibling_queryset` implementieren (Pflicht-Hook, kein
      stiller Default-Scope),
    * optional :meth:`_check_new_slug` (zusätzliche Prüfung des fertigen
      Slugs beim Anlegen, z. B. reservierte Formularfeld-Namen) und
      :meth:`_check_immutable_fields` (weitere Unveränderbarkeits-Guards
      beim Update, z. B. ``system_type``/``entity_type``).

    Modelle mit eigener Save-Normalisierung (``FieldTemplate``: FILE/HIGH ⇒
    ``is_encrypted``) behalten ein schlankes ``save()``, das die
    Normalisierung erledigt und dann ``super().save()`` aufruft.
    """

    _SLUG_RETRY_LIMIT = 3

    class Meta:
        abstract = True

    def slug_sibling_queryset(self):
        """Queryset, innerhalb dessen der Slug eindeutig sein muss.

        Pflicht-Hook: liefert den Kollisions-Scope (``facility=…`` bzw.
        ``document_type=…``) **inklusive** ``.exclude(pk=self.pk)``.
        """
        raise NotImplementedError("SluggedModel-Subklassen müssen slug_sibling_queryset() implementieren.")

    def _generate_unique_slug(self):
        return generate_unique_slug(self.name, self.slug_sibling_queryset())

    def _check_new_slug(self):
        """Hook: zusätzliche Prüfung des fertigen Slugs beim Anlegen (Default: keine)."""

    def _check_immutable_fields(self, orig):
        """Hook: weitere Unveränderbarkeits-Guards beim Update (Default: keine)."""

    def save(self, *args, **kwargs):
        if self._state.adding:
            auto_slug = not self.slug
            if auto_slug:
                self.slug = self._generate_unique_slug()
            elif not _SLUG_RE.fullmatch(self.slug):
                raise ValidationError(_("Slug ist nicht gültig (nur a-z, 0-9, Bindestriche)."))
            self._check_new_slug()
            if not auto_slug:
                super().save(*args, **kwargs)
                return
            # Race-Condition (paralleles Anlegen desselben Namens): der
            # Unique-Constraint schlägt zu, der Slug wird neu generiert.
            # Nur für auto-generierte Slugs — ein explizit gesetzter Slug
            # darf nicht hinter dem Rücken der Aufruferin umbenannt werden.
            for attempt in range(self._SLUG_RETRY_LIMIT):
                try:
                    with transaction.atomic():
                        super().save(*args, **kwargs)
                    return
                except IntegrityError:
                    if attempt >= self._SLUG_RETRY_LIMIT - 1:
                        raise
                    self.slug = self._generate_unique_slug()
        else:
            orig = type(self)._default_manager.filter(pk=self.pk).first()
            if orig:
                self._check_immutable_fields(orig)
                if orig.slug and orig.slug != self.slug:
                    raise ValidationError(_("Slug kann nach Erstellung nicht geändert werden."))
            super().save(*args, **kwargs)


class SoftDeletableModel(models.Model):
    """Abstract base for aggregates that support reversible soft-delete.

    Provides ``is_deleted``, ``deleted_at``, ``deleted_by`` plus
    ``soft_delete()`` and ``restore()`` convenience methods. Combine with
    :class:`core.models.managers.FacilityScopedManager` — its
    ``.active()``/``.deleted()`` queryset methods filter soft-deleted rows
    (the manager itself does not filter by default, consistent with the
    ``Event`` pattern).
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

        # Refs #1058 (Review-Haertung): Cache als ``(document_type_id, slugs)``-
        # Tupel statt nacktem Slug-Set. Ohne den Typ im Cache-Schluessel
        # verschluesselte ein zweiter save() derselben In-Memory-Instanz nach
        # einem document_type-Wechsel weiter nach der Feldliste des ALTEN Typs
        # (latenter Bug, betrifft Event/Client/Case).
        cached = getattr(self, "_cached_encrypted_slugs", None)
        if cached is None or cached[0] != self.document_type_id:
            cached = (
                self.document_type_id,
                set(
                    self.document_type.fields.filter(
                        field_template__is_encrypted=True,
                    ).values_list("field_template__slug", flat=True)
                ),
            )
            self._cached_encrypted_slugs = cached
        encrypted_field_names = cached[1]
        for key in encrypted_field_names:
            value = self.data_json.get(key)
            if value and not is_encrypted_value(value):
                # Skip file attachment markers — they are metadata, not user data.
                # Beide Formate berücksichtigen: Stufe A (__file__) und Stufe B
                # (__files__, Refs #622).
                if isinstance(value, dict) and (value.get("__file__") or value.get("__files__")):
                    continue
                self.data_json[key] = encrypt_field(value)
