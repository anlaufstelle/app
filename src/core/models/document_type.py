"""Document types and field templates."""

import re
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.constants import CONTACT_STAGE_CHOICES
from core.models.managers import FacilityScopedManager

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class DocumentType(models.Model):
    """Configurable document type (e.g. contact, crisis intervention)."""

    class Category(models.TextChoices):
        CONTACT = "contact", _("Kontakt")
        SERVICE = "service", _("Leistung")
        ADMIN = "admin", _("Verwaltung")
        NOTE = "note", _("Notiz")

    class Sensitivity(models.TextChoices):
        NORMAL = "normal", _("Normal")
        ELEVATED = "elevated", _("Erhöht")
        HIGH = "high", _("Hoch")

    class SystemType(models.TextChoices):
        BAN = "ban", _("Hausverbot")
        CONTACT = "contact", _("Kontakt")
        CRISIS = "crisis", _("Krisengespräch")
        MEDICAL = "medical", _("Medizinische Versorgung")
        NEEDLE_EXCHANGE = "needle_exchange", _("Spritzentausch")
        ACCOMPANIMENT = "accompaniment", _("Begleitung")
        COUNSELING = "counseling", _("Beratungsgespräch")
        REFERRAL = "referral", _("Vermittlung")
        NOTE = "note", _("Notiz")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="document_types",
        verbose_name=_("Einrichtung"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.CONTACT,
        verbose_name=_("Kategorie"),
        help_text=_("Gruppiert Dokumentationstypen für Filter und Auswertungen"),
    )
    sensitivity = models.CharField(
        max_length=20,
        choices=Sensitivity.choices,
        default=Sensitivity.NORMAL,
        verbose_name=_("Sensibilität"),
        help_text=_("Steuert Zugriffsrechte: ELEVATED/HIGH erfordern höhere Berechtigungen"),
    )
    min_contact_stage = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=CONTACT_STAGE_CHOICES,
        verbose_name=_("Mindest-Kontaktstufe"),
        help_text=_("Mindest-Kontaktstufe des Klientel für diesen Dokumentationstyp"),
    )
    retention_days = models.IntegerField(
        blank=True,
        null=True,
        verbose_name=_("Aufbewahrungsfrist (Tage)"),
    )
    icon = models.CharField(max_length=50, blank=True, verbose_name=_("Icon"))
    color = models.CharField(max_length=20, blank=True, verbose_name=_("Farbe"))
    description = models.TextField(blank=True, default="", verbose_name=_("Beschreibung"))
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    sort_order = models.IntegerField(default=0, verbose_name=_("Sortierung"))
    system_type = models.CharField(
        max_length=30,
        choices=SystemType.choices,
        null=True,
        blank=True,
        verbose_name=_("Systemtyp"),
        help_text=_("Interner Typ für systemgesteuerte Logik (Hausverbot, Export etc.)"),
    )

    class Meta:
        verbose_name = _("Dokumentationstyp")
        verbose_name_plural = _("Dokumentationstypen")
        ordering = ["sort_order", "name"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            try:
                orig = DocumentType.objects.get(pk=self.pk)
                if orig.system_type and orig.system_type != self.system_type:
                    raise ValidationError(_("system_type kann nach Erstellung nicht geändert werden."))
            except DocumentType.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.facility.name} — {self.name}"


class FieldTemplate(models.Model):
    """Template for an input field within a document type."""

    class FieldType(models.TextChoices):
        TEXT = "text", _("Text")
        TEXTAREA = "textarea", _("Textbereich")
        NUMBER = "number", _("Zahl")
        DATE = "date", _("Datum")
        TIME = "time", _("Uhrzeit")
        BOOLEAN = "boolean", _("Ja/Nein")
        SELECT = "select", _("Auswahl")
        MULTI_SELECT = "multi_select", _("Mehrfachauswahl")
        FILE = "file", _("Datei")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="field_templates",
        verbose_name=_("Einrichtung"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    slug = models.SlugField(
        max_length=120,
        verbose_name=_("Slug"),
        help_text=_("Stabiler Identifier — wird als Key in data_json verwendet. Nach Erstellung unveränderbar."),
    )
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
        default=FieldType.TEXT,
        verbose_name=_("Feldtyp"),
    )
    is_required = models.BooleanField(default=False, verbose_name=_("Pflichtfeld"))
    is_encrypted = models.BooleanField(default=False, verbose_name=_("Verschlüsselt"))
    sensitivity = models.CharField(
        max_length=20,
        choices=DocumentType.Sensitivity.choices,
        blank=True,
        default="",
        verbose_name=_("Sensibilität"),
        help_text=_("Feld-Level Sichtbarkeit. Leer = erbt vom Dokumentationstyp."),
    )
    options_json = models.JSONField(
        blank=True,
        default=list,
        verbose_name=_("Optionen (JSON)"),
    )
    default_value = models.TextField(
        blank=True,
        default="",
        verbose_name=_("Default-Wert"),
        help_text=_(
            "Wird beim Neu-Anlegen eines Ereignisses vorgeblendet. "
            "Leer lassen = kein Default. Für SELECT/MULTI_SELECT: Option-Slug(s) "
            "(mehrere durch Komma trennen). Für FILE nicht unterstützt."
        ),
    )
    statistics_category = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Statistik-Kategorie"),
    )
    help_text = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_("Hilfetext"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Aktiv"),
        help_text=_(
            "Deaktivierte Feldvorlagen werden in Formularen nicht mehr angezeigt. "
            "Bestehende Werte in Events bleiben erhalten (Soft-Delete-Alternative zum Hard-Delete)."
        ),
    )

    class Meta:
        verbose_name = _("Feldvorlage")
        verbose_name_plural = _("Feldvorlagen")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "name"],
                name="unique_facility_fieldtemplate_name",
            ),
            models.UniqueConstraint(
                fields=["facility", "slug"],
                name="unique_facility_fieldtemplate_slug",
            ),
        ]

    def _generate_unique_slug(self):
        """Generate a unique slug within the facility."""
        base = slugify(self.name.lower().translate(_UMLAUT_MAP))
        if not base:
            raise ValidationError(_("Name ergibt keinen gültigen Slug."))
        slug = base
        counter = 2
        while FieldTemplate.objects.filter(facility=self.facility, slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def clean(self):
        super().clean()
        if self.slug and not _SLUG_RE.fullmatch(self.slug):
            raise ValidationError({"slug": _("Nur a-z, 0-9 und Bindestriche erlaubt.")})
        self._validate_default_value()

    def _validate_default_value(self):
        raw = (self.default_value or "").strip()
        if not raw:
            return
        ft = self.FieldType
        if self.field_type == ft.FILE:
            raise ValidationError({"default_value": _("Für Datei-Felder ist kein Default-Wert zulässig.")})
        if self.field_type == ft.NUMBER:
            try:
                int(raw)
            except ValueError:
                raise ValidationError({"default_value": _("Default-Wert muss eine ganze Zahl sein.")}) from None
        elif self.field_type == ft.DATE:
            from datetime import date

            try:
                date.fromisoformat(raw)
            except ValueError:
                raise ValidationError(
                    {"default_value": _("Default-Wert muss ein ISO-Datum sein (YYYY-MM-DD).")}
                ) from None
        elif self.field_type == ft.TIME:
            from datetime import time

            try:
                time.fromisoformat(raw)
            except ValueError:
                raise ValidationError(
                    {"default_value": _("Default-Wert muss eine ISO-Uhrzeit sein (HH:MM oder HH:MM:SS).")}
                ) from None
        elif self.field_type == ft.BOOLEAN:
            if raw.lower() not in {"true", "false", "1", "0"}:
                raise ValidationError({"default_value": _("Default-Wert muss 'true' oder 'false' sein.")})
        elif self.field_type in (ft.SELECT, ft.MULTI_SELECT):
            active = {o["slug"] for o in (self.options_json or []) if o.get("is_active", True) and "slug" in o}
            values = [v.strip() for v in raw.split(",")] if self.field_type == ft.MULTI_SELECT else [raw]
            for v in values:
                if v not in active:
                    raise ValidationError(
                        {"default_value": _("Default-Wert '%(value)s' ist kein aktiver Options-Slug.") % {"value": v}}
                    )

    _SLUG_RETRY_LIMIT = 3

    def save(self, *args, **kwargs):
        # FILE fields are always encrypted at rest
        if self.field_type == self.FieldType.FILE:
            self.is_encrypted = True

        if self._state.adding:
            auto_slug = not self.slug
            if auto_slug:
                self.slug = self._generate_unique_slug()
            elif not _SLUG_RE.fullmatch(self.slug):
                raise ValidationError(_("Slug ist nicht gültig (nur a-z, 0-9, Bindestriche)."))
            if auto_slug:
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
                super().save(*args, **kwargs)
                return
        else:
            try:
                orig = FieldTemplate.objects.get(pk=self.pk)
                if orig.slug and orig.slug != self.slug:
                    raise ValidationError(_("Slug kann nach Erstellung nicht geändert werden."))
            except FieldTemplate.DoesNotExist:
                pass
            super().save(*args, **kwargs)

    @property
    def choices(self):
        """Options as (value, label) tuples. Schema: [{"slug": "x", "label": "X"}]."""
        if not self.options_json:
            return []
        return [(o["slug"], o["label"]) for o in self.options_json if o.get("is_active", True)]

    def get_default_initial(self):
        """Cast ``default_value`` (String) in den passenden Python-Typ fürs Form-Initial.

        Gibt ``None`` zurück, wenn kein Default gesetzt ist oder der Wert
        für den Feldtyp nicht parsbar ist (fail-safe — kein User-Fehler).
        """
        raw = (self.default_value or "").strip()
        if not raw:
            return None
        ft = self.FieldType
        try:
            if self.field_type == ft.NUMBER:
                return int(raw)
            if self.field_type == ft.DATE:
                from datetime import date

                return date.fromisoformat(raw)
            if self.field_type == ft.TIME:
                from datetime import time

                return time.fromisoformat(raw)
            if self.field_type == ft.BOOLEAN:
                return raw.lower() in {"true", "1"}
            if self.field_type == ft.MULTI_SELECT:
                return [v.strip() for v in raw.split(",") if v.strip()]
            if self.field_type == ft.FILE:
                return None
            return raw
        except (ValueError, TypeError):
            return None

    def __str__(self):
        return f"{self.facility.name} — {self.name} ({self.get_field_type_display()})"


class DocumentTypeField(models.Model):
    """Association of a field template with a document type."""

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

    def __str__(self):
        return f"{self.document_type.name} → {self.field_template.name}"
