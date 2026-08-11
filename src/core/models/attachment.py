"""Encrypted file attachments — owner-generisch (Event, Person, Fall).

Bis M24 (Refs #1738) hingen Anhaenge als ``EventAttachment`` ausschliesslich am
Ereignis. Seit ADR-039 traegt ``Attachment`` eine eigene ``facility`` und genau
EINEN Owner (``event`` | ``client`` | ``case``, DB-gepinnt via CheckConstraint);
die RLS-Policy ist der direkte Spaltenvergleich (Migration 0131) statt des
frueheren Joins ueber ``core_event``.

Sicherheitskontrakt (ADR-039 §2): ``facility`` wird IMMER aus ``owner.facility``
abgeleitet — nie als freier Parameter entgegengenommen. Durchgesetzt im
Service-Layer (``file_vault``), gepinnt per Unit-Test.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import AttachmentManager

#: Reihenfolge = Anzeige-/Dispatch-Reihenfolge (Event zuerst: Bestandspfad).
OWNER_FIELDS = ("event", "client", "case")


def owner_field_name(owner):
    """FK-Feldname fuer eine Owner-Instanz (``Event``/``Client``/``Case``).

    Zentraler Dispatch-Helfer fuer den owner-generischen Service-Layer —
    ``ValueError`` fuer alles, was kein zulaessiger Owner ist (fail-loud statt
    stillem Fehlscoping).
    """
    model_name = owner._meta.model_name
    if model_name not in OWNER_FIELDS:
        raise ValueError(f"Kein zulaessiger Attachment-Owner: {owner._meta.label} (erwartet: {OWNER_FIELDS})")
    return model_name


class Attachment(models.Model):
    """An encrypted file attached to a dynamic FILE field of its owner."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("Einrichtung"),
    )
    # Genau EIN Owner-FK ist gesetzt (CheckConstraint ``attachment_exactly_one_owner``).
    event = models.ForeignKey(
        "core.Event",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name=_("Event"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name=_("Person"),
    )
    case = models.ForeignKey(
        "core.Case",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="attachments",
        verbose_name=_("Fall"),
    )
    # Versions-Kette (Stufe B, Refs #622): Ein ``entry_id`` identifiziert eine
    # Kette von Attachments, die dieselbe logische Datei über ihre Replace-
    # History hinweg repräsentiert. Für Stufe-A-Einträge (1 Datei pro Feld)
    # wird eine eigene ``entry_id`` pro Kette in der Data-Migration gesetzt.
    entry_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        verbose_name=_("Entry-ID (Versionskette)"),
    )
    sort_order = models.IntegerField(
        default=0,
        verbose_name=_("Sortierung"),
        help_text=_("Reihenfolge der Einträge innerhalb eines Feldes (0-indexed)."),
    )
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Soft-deleted am"),
        help_text=_("Markiert den Eintrag als vom User entfernt. Physischer Delete erst beim Owner-Delete/Anonymize."),
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
        help_text=_("Wird verschlüsselt gespeichert."),
    )
    file_size = models.PositiveIntegerField(
        verbose_name=_("Dateigröße (Bytes)"),
    )
    mime_type = models.CharField(
        max_length=100,
        verbose_name=_("MIME-Typ"),
    )
    detected_mime = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("Verifizierter MIME-Typ"),
        help_text=_(
            "Beim Upload per libmagic erkannter (verifizierter) Typ. Maßgeblich "
            "beim Download; der browser-gemeldete mime_type dient nur als Fallback "
            "für Bestandsanhänge ohne erkannten Typ (Refs #1274)."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name=_("Erstellt von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))

    # Versionierung (Refs #587, Stufe A): statt eine alte Datei beim Ersetzen
    # zu löschen, markieren wir sie als superseded. Pro (owner, field_template)
    # bleibt die jeweils neueste Version `is_current=True`; Vorgänger zeigen
    # per `superseded_by` auf ihren Nachfolger und zusammen bilden sie die
    # Versionskette. Disk-Cleanup erst beim Owner-Delete/Anonymize.
    is_current = models.BooleanField(
        default=True,
        verbose_name=_("Aktuelle Version"),
    )
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="prior_versions",
        verbose_name=_("Ersetzt durch"),
    )
    superseded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Ersetzt am"),
    )

    objects = AttachmentManager()

    class Meta:
        verbose_name = _("Dateianhang")
        verbose_name_plural = _("Dateianhänge")
        ordering = ["-created_at"]
        constraints = [
            # Genau ein Owner — DB-Backstop zusaetzlich zur Service-Ebene;
            # greift auch bei direktem .save() und Roh-SQL (ADR-039 §1).
            models.CheckConstraint(
                condition=(
                    (models.Q(event__isnull=False) & models.Q(client__isnull=True) & models.Q(case__isnull=True))
                    | (models.Q(event__isnull=True) & models.Q(client__isnull=False) & models.Q(case__isnull=True))
                    | (models.Q(event__isnull=True) & models.Q(client__isnull=True) & models.Q(case__isnull=False))
                ),
                name="attachment_exactly_one_owner",
            ),
        ]
        indexes = [
            # Uebersichts-Query (Datei-Liste je Facility, neueste zuerst);
            # FK-Indexe legt Django automatisch an (ADR-039 §4).
            models.Index(fields=["facility", "-created_at"], name="attachment_facility_ca_idx"),
        ]

    @property
    def owner_type(self):
        """``"event"`` | ``"client"`` | ``"case"`` — Branch auf ``*_id`` (kein Fetch)."""
        for name in OWNER_FIELDS:
            if getattr(self, f"{name}_id") is not None:
                return name
        return None  # nur waehrend Konstruktion moeglich; DB-Zeilen haben immer einen Owner

    @property
    def owner_id(self):
        owner_type = self.owner_type
        return getattr(self, f"{owner_type}_id") if owner_type else None

    @property
    def owner(self):
        """Owner-Instanz — laedt erst hier (Branch vorher via ``*_id``, kein N+1)."""
        owner_type = self.owner_type
        return getattr(self, owner_type) if owner_type else None

    def __str__(self):
        return f"Attachment {self.storage_filename} → {self.owner_type} {self.owner_id}"
