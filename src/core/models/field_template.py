"""Feldvorlage — Definition eines dynamischen Eingabefeldes."""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.document_type import DocumentType
from core.models.managers import FacilityScopedManager
from core.models.mixins import SluggedModel
from core.models.report_key import ReportKeyMixin
from core.models.slug import _RESERVED_SLUGS, _SLUG_RE


class FieldTemplate(ReportKeyMixin, SluggedModel):
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

    def slug_sibling_queryset(self):
        """Slug ist je Facility eindeutig."""
        return FieldTemplate.objects.filter(facility=self.facility).exclude(pk=self.pk)

    def clean(self):
        super().clean()
        if self.slug and not _SLUG_RE.fullmatch(self.slug):
            raise ValidationError({"slug": _("Nur a-z, 0-9 und Bindestriche erlaubt.")})
        # Refs #1726: reservierter Slug (fester Formularfeld-Name) — nur beim
        # Anlegen, damit hypothetische Bestands-Vorlagen mit reserviertem Slug
        # (Migrations-Inventur meldet sie, benennt aber nicht um) weiter
        # bearbeitbar bleiben; save() ist der Backstop für ``.create()``.
        if self._state.adding and self.slug in _RESERVED_SLUGS:
            raise ValidationError(
                {"slug": _("Der Slug „%(slug)s“ kollidiert mit einem festen Formularfeld.") % {"slug": self.slug}}
            )
        self._validate_default_value()
        self._validate_high_sensitivity_requires_encryption()

    def _validate_high_sensitivity_requires_encryption(self):
        # Refs #733: HIGH-Sensitivity-Felder muessen
        # verschluesselt sein — sonst landet Art.-9-relevanter Inhalt im
        # Klartext-JSONB und entgegen der Sensitivity-Logik im Backup.
        if self.sensitivity == DocumentType.Sensitivity.HIGH and not self.is_encrypted:
            raise ValidationError({"is_encrypted": _("Felder mit Sensibilität=Hoch müssen verschlüsselt sein.")})

    def _validate_default_value(self):
        """Refs #907: delegiert an die Field-Type-Registry.

        Pre-Check ``allows_default=False`` (Datei) raised einen klaren
        Fehler, bevor der typspezifische Validator drankommt.
        """
        from core.services.system import get_spec

        raw = (self.default_value or "").strip()
        if not raw:
            return
        spec = get_spec(self.field_type)
        if not spec.allows_default:
            raise ValidationError({"default_value": _("Für Datei-Felder ist kein Default-Wert zulässig.")})
        spec.validate_default(raw, {"options_json": self.options_json})

    def _check_immutable_fields(self, orig):
        # E3 (Refs #1849): Vertrag von ``ReportKeyMixin`` — leer→gesetzt bleibt
        # erlaubt, gesetzt→anders/weg nicht. Die Feldvorlage hatte bis hier gar
        # keinen Unveraenderbarkeits-Guard (nur der Slug ueber ``SluggedModel``).
        self._check_report_key_immutable(orig)

    def _check_new_slug(self):
        # Refs #1726: Backstop für Nicht-Form-Pfade (``.create()``/Seed/Bulk,
        # die clean() umgehen) — greift für explizite UND auto-generierte
        # Slugs, damit kein dynamisches Feld einen festen Formularfeld-Namen
        # belegt.
        if self.slug in _RESERVED_SLUGS:
            raise ValidationError(
                _("Der Slug „%(slug)s“ kollidiert mit einem festen Formularfeld.") % {"slug": self.slug}
            )

    def save(self, *args, **kwargs):
        """Verschlüsselungs-Normalisierung; die Slug-Mechanik liegt in ``SluggedModel``."""
        # FILE fields are always encrypted at rest
        if self.field_type == self.FieldType.FILE:
            self.is_encrypted = True

        # Refs #1270 (T5): HIGH⇒verschlüsselt-Invariante auf save()-Ebene
        # erzwingen. clean()/full_clean() (ModelForm) blockiert HIGH ohne
        # Verschlüsselung bereits mit einer ValidationError (UX-Feedback); ein
        # direktes .save() (Seed/Bulk/Migration) umgeht das aber. Wie beim
        # FILE-Zwang oben setzen wir is_encrypted hier als sichere Backstop-
        # Maßnahme, damit Art.-9-relevanter Klartext nicht über einen
        # Nicht-Form-Pfad ins JSONB/Backup gerät.
        if self.sensitivity == DocumentType.Sensitivity.HIGH:
            self.is_encrypted = True

        super().save(*args, **kwargs)

    @property
    def choices(self):
        """Options as (value, label) tuples. Schema: [{"slug": "x", "label": "X"}]."""
        if not self.options_json:
            return []
        return [(o["slug"], o["label"]) for o in self.options_json if o.get("is_active", True)]

    def get_default_initial(self):
        """Cast ``default_value`` (String) in den passenden Python-Typ fürs Form-Initial.

        Refs #907: delegiert an die Field-Type-Registry —
        jeder ``parse_default``-Helper ist fail-safe (liefert ``None``
        bei nicht-parsbarem Input).
        """
        from core.services.system import get_spec

        raw = (self.default_value or "").strip()
        if not raw:
            return None
        return get_spec(self.field_type).parse_default(raw)

    def __str__(self):
        return f"{self.facility.name} — {self.name} ({self.get_field_type_display()})"
