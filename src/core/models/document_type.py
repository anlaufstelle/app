"""Dokumentationstyp — konfigurierbarer Typ fuer Ereignisse/Personen/Faelle."""

import uuid
from contextlib import contextmanager

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.constants import CONTACT_STAGE_CHOICES
from core.models.managers import FacilityScopedManager
from core.models.mixins import SluggedModel
from core.models.report_key import ReportKeyMixin


class DocumentType(ReportKeyMixin, SluggedModel):
    """Configurable document type (e.g. contact, crisis intervention)."""

    class Sensitivity(models.TextChoices):
        NORMAL = "normal", _("Normal")
        ELEVATED = "elevated", _("Erhöht")
        HIGH = "high", _("Hoch")

    class SystemType(models.TextChoices):
        # E4 (Refs #1850, Konzept #1847): nur noch die beiden
        # Verhaltens-Schluessel. Die sieben reinen Berichtswerte (contact,
        # medical, needle_exchange, accompaniment, counseling, referral, note)
        # sind entfernt — die Berichts-Zuordnung traegt seit E3 (Refs #1849)
        # allein ``report_key``. Migration ``0147`` nullt die Altwerte.
        BAN = "ban", _("Hausverbot")
        CRISIS = "crisis", _("Krisengespräch")

    class EntityType(models.TextChoices):
        # M2 (Refs #1058): auf welche Entität sich Dokumente dieses Typs
        # beziehen. Bestand + Default = EVENT (heutiges, event-only Verhalten).
        EVENT = "event", _("Ereignis")
        CLIENT = "client", _("Person")
        CASE = "case", _("Fall")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="document_types",
        verbose_name=_("Einrichtung"),
    )
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    slug = models.SlugField(
        max_length=120,
        # blank=True NUR für die Validierung: full_clean() auf ungespeicherten
        # Instanzen muss ohne Slug durchlaufen (Bestandsmuster, z. B.
        # test_contact_stage) — save() generiert ihn bei _state.adding immer,
        # und der Immutabilitäts-Guard + DB-Trigger verhindern spätere Leerung.
        blank=True,
        verbose_name=_("Slug"),
        help_text=_(
            "Stabiler Identifier — wird als Lookup-Key im M2-Paketformat verwendet. Nach Erstellung unveränderbar."
        ),
    )
    sensitivity = models.CharField(
        max_length=20,
        choices=Sensitivity.choices,
        default=Sensitivity.NORMAL,
        verbose_name=_("Sensibilität"),
        help_text=_(
            "Steuert, wer die Inhalte sehen darf. Ob ein Feld verschlüsselt gespeichert wird, "
            "legt die Feldvorlage fest."
        ),
    )
    min_contact_stage = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=CONTACT_STAGE_CHOICES,
        verbose_name=_("Mindest-Kontaktstufe"),
        help_text=_(
            "Legt fest, wie viel über eine Person bekannt sein muss, damit "
            "dieser Dokumentationstyp verwendet werden darf. Bleibt das Feld "
            "leer, ist der Typ auch für anonyme Kontakte ohne zugeordnete "
            "Person nutzbar."
        ),
    )
    retention_days = models.IntegerField(
        blank=True,
        null=True,
        # Security N8 (Refs #1445): wie bei Settings.retention_*_days — 0/negativ
        # erzeugt in core.retention.strategies einen Cutoff >= jetzt. None (keine
        # Frist) bleibt zulaessig; gesetzte Werte muessen >= 1 sein.
        validators=[MinValueValidator(1)],
        verbose_name=_("Höchstfrist (verkürzt nur)"),
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
        # E4 (Refs #1850): Der Hilfetext trennte bisher die drei Rollen der
        # neun Werte. Uebrig sind die beiden Verhaltens-Schluessel — der Text
        # sagt jetzt, was sie tun, und verweist fuer die Berichts-Zuordnung
        # auf das Feld, das sie seit E3 (Refs #1849) allein traegt.
        help_text=_(
            "Stabile Kennung für Bann-Logik und Krisen-Highlight. Nach Erstellung unveränderbar. "
            "Beide Werte steuern App-Verhalten: „Hausverbot“ speist Banner, Zeitstrom-Filter und Übergabe, "
            "„Krisengespräch“ die Krisen-Hervorhebung. Für die Zuordnung zu einem Bericht ist der "
            "Berichtsschlüssel zuständig, nicht dieses Feld."
        ),
    )
    tally_enabled = models.BooleanField(default=False, verbose_name=_("In Strichliste anbieten"))
    entity_type = models.CharField(
        max_length=20,
        choices=EntityType.choices,
        default=EntityType.EVENT,
        verbose_name=_("Entitätstyp"),
        help_text=_("Auf welche Entität sich Dokumente dieses Typs beziehen. Nach Erstellung unveränderbar."),
    )

    class Meta:
        verbose_name = _("Dokumentationstyp")
        verbose_name_plural = _("Dokumentationstypen")
        ordering = ["sort_order", "name"]
        constraints = [
            # Snapshot-Lookup in services/dashboard/snapshot.py rekonstruiert
            # DocumentType ueber (facility, name) — ohne UniqueConstraint
            # koennten zwei gleichnamige DocumentTypes den Lookup mit
            # MultipleObjectsReturned auf einen 500er fallen lassen.
            # Refs #434, Refs #733; seit #1749 (ADR-040) ohne den frueheren
            # Zweitschluessel ``category``: gleichnamige Typen einer
            # Einrichtung sind fuer Nutzerinnen ohnehin nicht unterscheidbar.
            models.UniqueConstraint(
                fields=["facility", "name"],
                name="unique_facility_documenttype_name",
            ),
            # M2 (Refs #1058): slug ist der M2-Paketformat-Lookup-Key —
            # DocumentType ist nicht soft-deletable, daher ohne Condition
            # (anders als z. B. Client/Case-Uniques auf aktive Zeilen).
            models.UniqueConstraint(
                fields=["facility", "slug"],
                name="unique_facility_documenttype_slug",
            ),
            # Security N8 (Refs #1445): gesetzte retention_days muessen >= 1 sein
            # (None = keine Frist bleibt erlaubt).
            models.CheckConstraint(
                condition=models.Q(retention_days__isnull=True) | models.Q(retention_days__gte=1),
                name="documenttype_retention_days_min_1",
            ),
        ]

    def slug_sibling_queryset(self):
        """Slug ist je Facility eindeutig (Muster FieldTemplate)."""
        return DocumentType.objects.filter(facility=self.facility).exclude(pk=self.pk)

    def clean(self):
        from core.services.dynamic_protection import validate_configuration_facility

        super().clean()
        validate_configuration_facility(self, self.facility_id)

    @contextmanager
    def _save_context(self):
        """Serialize shared slug persistence and sensitivity rewrites (Refs #2525)."""
        from core.services.dynamic_protection import (
            configuration_write,
            rewrite_dynamic_records,
            validate_configuration_facility,
        )

        validate_configuration_facility(self, self.facility_id)
        with configuration_write(self.facility_id):
            previous = DocumentType.objects.for_facility(self.facility_id).filter(pk=self.pk).first()
            yield
            if previous is not None and previous.sensitivity != self.sensitivity:
                rewrite_dynamic_records([self])

    def delete(self, *args, **kwargs):
        from core.services.dynamic_protection import configuration_write

        with configuration_write(self.facility_id):
            return super().delete(*args, **kwargs)

    def _check_immutable_fields(self, orig):
        # E3 (Refs #1849): Der ``report_key``-Guard wird explizit aufgerufen
        # (Vertrag von ``ReportKeyMixin``) — er steht vor den beiden
        # bestehenden, weil er die haeufigere Bearbeitung betrifft.
        self._check_report_key_immutable(orig)
        if orig.system_type and orig.system_type != self.system_type:
            raise ValidationError(_("system_type kann nach Erstellung nicht geändert werden."))
        if orig.entity_type != self.entity_type:
            raise ValidationError(_("entity_type kann nach Erstellung nicht geändert werden."))

    def __str__(self):
        return f"{self.facility.name} — {self.name}"
