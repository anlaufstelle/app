"""Document types and field templates."""

import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from core.constants import CONTACT_STAGE_CHOICES
from core.models.managers import FacilityScopedManager

_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def slug_base(name: str, max_length: int = 120) -> str:
    """Normalisierter Slug-Stamm aus ``name`` — leer, wenn keiner entsteht.

    ``slugify`` allein genügt nicht (Final-Review D, Refs #1058): es lässt
    Unterstriche durch (``\\w``) und die Truncation auf ``max_length`` kann auf
    einem Bindestrich enden. Beides verletzt :data:`_SLUG_RE` und wäre damit im
    Paketformat nicht referenzierbar. Deshalb: ``_`` → ``-``, Mehrfach-
    Bindestriche kollabieren, Truncation, danach Rand-Bindestriche strippen.

    Leerer Rückgabewert = der Name ergibt keinen Slug (reine Satzzeichen,
    nicht-lateinische Schrift); die Aufrufer entscheiden über Fehler oder
    Fallback.
    """
    base = slugify(name.lower().translate(_UMLAUT_MAP)).replace("_", "-")
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base[:max_length].strip("-")


# Feste Formularfeld-Namen, unter denen dynamische Felder eines Dokumentationstyps
# NACKT im selben ``<form>`` gerendert werden (M2, Refs #1726). Ein
# FieldTemplate-Slug, der einen dieser Namen trifft, überschriebe beim Absenden
# das gleichnamige feste Feld (POST-Kollision/Datenverlust) — die Reserved-Liste
# blockt das an ALLEN Schichten: Config-Form (``validate_slugifiable_name``),
# Modell (``FieldTemplate.clean``/``save``) und Paket-Schema
# (``_parse_field_template``). Bewusst hier neben ``slug_base``, damit alle
# Schichten dieselbe Liste importieren (DRY).
#
# Unterstrich-Namen (``contact_stage``, ``document_type``, …) sind über
# ``slug_base``/``_SLUG_RE`` heute als NEUE Slugs unerreichbar (``_`` → ``-``):
# sie stehen hier TROTZDEM defensiv drin — für Bestandsdaten (direkt gesetzte
# Slugs) und Zukunftssicherheit (falls sich das Slug-Format ändert). Gegen eine
# ``slug_base``-Ausgabe matchen sie schlicht nie, schaden aber auch nicht; der
# Modell-Backstop und die Migrations-Inventur (Refs #1730) vergleichen dagegen
# den ROHEN Slug und profitieren von den Unterstrich-Einträgen.
_RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        # ClientForm (core/forms/clients.py)
        "pseudonym",
        "contact_stage",
        "age_cluster",
        "notes",
        "document_type",
        # CaseForm (core/forms/cases.py) — ``document_type`` bereits oben
        "client",
        "title",
        "description",
        "lead_user",
        # EventMetaForm (core/forms/events.py) — ``document_type``/``client`` oben
        "occurred_at",
        "case",
        # Generische POST-Schlüssel in denselben ``<form>``s
        "csrfmiddlewaretoken",
        "expected_updated_at",
        "_save_and_new",
    }
)


def generate_unique_slug(name: str, siblings_qs: models.QuerySet, max_length: int = 120) -> str:
    """Generiert einen Slug aus ``name``, eindeutig innerhalb ``siblings_qs``.

    Modulweiter Helfer (M2, Refs #1058) — extrahiert aus den vormals fast
    identischen Kopien ``FieldTemplate._generate_unique_slug``/
    ``FieldGroup._generate_unique_slug``. ``siblings_qs`` muss bereits auf den
    Kollisions-Scope gefiltert sein (z. B. ``facility=…`` bzw.
    ``document_type=…``) und das eigene Objekt per ``.exclude(pk=…)``
    ausschließen — dieser Helfer filtert hier nur noch auf ``slug=``.
    Truncation auf ``max_length`` VOR dem Counter-Suffix, damit der
    Suffix das Limit des Ziel-``SlugField`` nie sprengt. Normalisierung des
    Stamms: :func:`slug_base` (Refs #1058) — auch die gekürzte Variante vor dem
    Suffix wird von Rand-Bindestrichen befreit, sonst entstünde ``…--2``.
    """
    base = slug_base(name, max_length)
    if not base:
        raise ValidationError(_("Name ergibt keinen gültigen Slug."))
    slug = base
    counter = 2
    while siblings_qs.filter(slug=slug).exists():
        suffix = f"-{counter}"
        slug = f"{base[: max_length - len(suffix)].strip('-')}{suffix}"
        counter += 1
    return slug


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
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.CONTACT,
        verbose_name=_("Kategorie"),
        help_text=_(
            "Reine UI-Gruppierung für Statistik-Anzeige und Admin-Filter. "
            "Beeinflusst weder Export noch Bann-/Krisen-Logik — dafür ist "
            "der Systemtyp zuständig."
        ),
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
        help_text=_("Mindest-Kontaktstufe der Person für diesen Dokumentationstyp"),
    )
    retention_days = models.IntegerField(
        blank=True,
        null=True,
        # Security N8 (Refs #1445): wie bei Settings.retention_*_days — 0/negativ
        # erzeugt in core.retention.strategies einen Cutoff >= jetzt. None (keine
        # Frist) bleibt zulaessig; gesetzte Werte muessen >= 1 sein.
        validators=[MinValueValidator(1)],
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
        # Refs #1748 (RFC, Option O1): Der zweite Satz trennt die drei Rollen
        # der Werteliste. Ohne ihn liest sich das Dropdown wie neun
        # gleichwertige Verhaltensschalter — tatsaechlich tragen nur „ban" und
        # „crisis" App-Logik, sechs Werte sind reine Bericht-Opt-ins und „note"
        # wirkt exportseitig wie „kein Systemtyp".
        help_text=_(
            "Stabile Kennung für Bann-Logik, Krisen-Highlight und Sachbericht-Export. Nach Erstellung unveränderbar. "
            "Nur „Hausverbot“ und „Krisengespräch“ steuern App-Logik; die übrigen Werte ordnen den Typ ausschließlich "
            "dem Beispiel-Sachbericht zu, „Notiz“ schließt ihn davon aus (wie ohne Systemtyp)."
        ),
    )
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
            # Snapshot-Lookup in services/snapshot.py rekonstruiert
            # DocumentType ueber (facility, name, category) — ohne
            # UniqueConstraint koennten zwei DocumentTypes mit gleichem
            # Tripel den Lookup mit MultipleObjectsReturned auf einen
            # 500er fallen lassen. Refs #434, Refs #733.
            models.UniqueConstraint(
                fields=["facility", "name", "category"],
                name="unique_facility_documenttype_name_category",
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

    _SLUG_RETRY_LIMIT = 3

    def _generate_unique_slug(self):
        """Slug innerhalb der Facility eindeutig generieren (Muster FieldTemplate)."""
        return generate_unique_slug(self.name, DocumentType.objects.filter(facility=self.facility).exclude(pk=self.pk))

    def save(self, *args, **kwargs):
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
            orig = DocumentType.objects.filter(pk=self.pk).first()
            if orig:
                if orig.system_type and orig.system_type != self.system_type:
                    raise ValidationError(_("system_type kann nach Erstellung nicht geändert werden."))
                if orig.entity_type != self.entity_type:
                    raise ValidationError(_("entity_type kann nach Erstellung nicht geändert werden."))
                if orig.slug and orig.slug != self.slug:
                    raise ValidationError(_("Slug kann nach Erstellung nicht geändert werden."))
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
        return generate_unique_slug(self.name, FieldTemplate.objects.filter(facility=self.facility).exclude(pk=self.pk))

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

    _SLUG_RETRY_LIMIT = 3

    def save(self, *args, **kwargs):
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

        if self._state.adding:
            auto_slug = not self.slug
            if auto_slug:
                self.slug = self._generate_unique_slug()
            elif not _SLUG_RE.fullmatch(self.slug):
                raise ValidationError(_("Slug ist nicht gültig (nur a-z, 0-9, Bindestriche)."))
            # Refs #1726: Backstop für Nicht-Form-Pfade (``.create()``/Seed/Bulk,
            # die clean() umgehen) — greift für explizite UND auto-generierte
            # Slugs, damit kein dynamisches Feld einen festen Formularfeld-Namen
            # belegt.
            if self.slug in _RESERVED_SLUGS:
                raise ValidationError(
                    _("Der Slug „%(slug)s“ kollidiert mit einem festen Formularfeld.") % {"slug": self.slug}
                )
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
            orig = FieldTemplate.objects.filter(pk=self.pk).first()
            if orig and orig.slug and orig.slug != self.slug:
                raise ValidationError(_("Slug kann nach Erstellung nicht geändert werden."))
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


class FieldGroup(models.Model):
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

    def _generate_unique_slug(self):
        """Generate a unique slug within the document type."""
        return generate_unique_slug(
            self.name, FieldGroup.objects.filter(document_type=self.document_type).exclude(pk=self.pk)
        )

    def clean(self):
        super().clean()
        if self.slug and not _SLUG_RE.fullmatch(self.slug):
            raise ValidationError({"slug": _("Nur a-z, 0-9 und Bindestriche erlaubt.")})

    _SLUG_RETRY_LIMIT = 3

    def save(self, *args, **kwargs):
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
            orig = FieldGroup.objects.filter(pk=self.pk).first()
            if orig and orig.slug and orig.slug != self.slug:
                raise ValidationError(_("Slug kann nach Erstellung nicht geändert werden."))
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document_type.name} → {self.name}"


class DocumentTypeField(models.Model):
    """Association of a field template with a document type.

    M2/#1713 (globale Block-Sequenz): ``sort_order`` hat zwei Bedeutungen je nach
    ``field_group``:

    * ``field_group IS NULL`` (freies Feld) → Position des **Feld-Blocks** in der
      globalen Block-Sequenz des DocumentType (dieselbe Sequenz wie
      :attr:`FieldGroup.sort_order`);
    * ``field_group`` gesetzt → Position **innerhalb** der Gruppe (0..n-1).

    Die kanonische, interleavte Reihenfolge liefert
    ``core.services.config.iter_blocks_in_order`` /
    ``iter_fields_in_block_order``; Board, Offline-Bundle und Feed-Vorschau
    nutzen sie direkt. Das Event-Formular (``grouped_fields()``) wendet
    dieselbe Semantik auf BoundFields an (eigene Implementierung, per Tests
    gegen die Iteratoren-Semantik fixiert).
    """

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
    field_group = models.ForeignKey(
        FieldGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fields",
        verbose_name=_("Feldgruppe"),
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

    def clean(self):
        super().clean()
        # M0 (Refs #1056): Eine zugeordnete Feldgruppe muss zum selben
        # Dokumentationstyp gehoeren — sonst wuerde ein Feld unter einer Gruppe
        # eines fremden DocumentType (ggf. sogar fremder Facility) einsortiert.
        if self.field_group_id and self.field_group.document_type_id != self.document_type_id:
            raise ValidationError({"field_group": _("Die Feldgruppe muss zum selben Dokumentationstyp gehören.")})

    def __str__(self):
        return f"{self.document_type.name} → {self.field_template.name}"
