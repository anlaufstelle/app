"""RectificationRequest — interner Art.-16-Berichtigungsworkflow (ADR-041)."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class RectificationRequest(models.Model):
    """Antrag auf Berichtigung eines Personenfelds (Vier-Augen-Prinzip, Refs #1060).

    [ADR-041](../../../docs/adr/041-berichtigungsworkflow-intern.md) bildet den
    **internen** Pruefweg ab, den [ADR-013](../../../docs/adr/013-dsgvo-art16-no-selfservice.md)
    organisatorisch festgelegt hat: Antragsteller ist **immer** eine Fachkraft
    (``staff``+), nie die betroffene Person; Leitung (``facility_lead``/
    ``facility_admin``) genehmigt oder lehnt ab. Angewendet wird eine genehmigte
    Berichtigung ausschliesslich ueber ``update_client`` — siehe
    :mod:`core.services.client.rectification`.

    **Wertformat (bewusst zweistufig):** ``old_value``/``new_value`` tragen den
    Feldwert erst **JSON-serialisiert** (``json.dumps``) und dann ueber
    :func:`~core.services.file_vault.encrypt_field` Fernet-verschluesselt. Die
    JSON-Stufe ist noetig, weil ``data_json``-Felder nicht nur Text tragen
    koennen (Zahl, Bool, Mehrfachauswahl-Liste) — ``encrypt_field`` selbst
    ``str()``-t stumpf und wuerde ``["a", "b"]`` als Python-Repr zurueckgeben,
    das ``update_client`` nicht mehr typrichtig einsetzen koennte. Die
    Verschluesselung ist die AEUSSERE Stufe, damit in der Spalte nie Klartext
    liegt. Gespeichert wird das Marker-Dict von ``encrypt_field``
    (``{"__encrypted__": true, "value": "gAAAA…"}``) — deshalb ``JSONField``,
    exakt wie ``Attachment.original_filename_encrypted``.

    ``reason``/``review_note`` sind ebenfalls verschluesselt (ohne JSON-Stufe,
    sie sind immer Freitext): die fachliche Begruendung eines
    Berichtigungsverlangens benennt regelmaessig genau den strittigen
    Sachverhalt und traegt damit Personenbezug.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Ausstehend")
        APPROVED = "approved", _("Genehmigt")
        REJECTED = "rejected", _("Abgelehnt")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="rectification_requests",
        verbose_name=_("Einrichtung"),
    )
    target_client = models.ForeignKey(
        "core.Client",
        on_delete=models.CASCADE,
        related_name="rectification_requests",
        verbose_name=_("Betroffene Person"),
    )
    field_path = models.CharField(
        max_length=150,
        verbose_name=_("Feldpfad"),
        help_text=_("Stammdatenfeld (z.B. pseudonym) oder dynamisches Feld (data_json.<slug>)."),
    )
    old_value = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Alter Wert (verschlüsselt)"),
        help_text=_("Wird als JSON verschlüsselt gespeichert."),
    )
    new_value = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Neuer Wert (verschlüsselt)"),
        help_text=_("Wird als JSON verschlüsselt gespeichert."),
    )
    reason = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Begründung (verschlüsselt)"),
        help_text=_("Wird verschlüsselt gespeichert."),
    )
    review_note = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Prüfvermerk (verschlüsselt)"),
        help_text=_("Wird verschlüsselt gespeichert."),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("Status"),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Delta 13 (Refs #1060), Praezedenz ``DeletionRequest`` (Refs #1347):
        # SET_NULL statt CASCADE — der Vier-Augen-Nachweis (wer hat beantragt,
        # wer geprueft) muss eine harte User-Loeschung ueberleben; nur der
        # Personenbezug faellt weg, der Antrag bleibt als Rechenschaftsbeleg.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rectification_requests",
        verbose_name=_("Beantragt von"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_rectification_requests",
        verbose_name=_("Geprüft von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Geprüft am"))

    class Meta:
        verbose_name = _("Berichtigungsantrag")
        verbose_name_plural = _("Berichtigungsanträge")
        ordering = ["-created_at"]
        constraints = [
            # Vier-Augen-Prinzip DB-seitig (Muster ``DeletionRequest``). Der
            # ``IS NULL``-Zweig ist explizit, obwohl ``NOT (a = b)`` bei NULL
            # ohnehin zu NULL (= erfuellt) auswertet — die Absicht soll im
            # Schema lesbar sein und nicht von SQL-Dreiwertlogik abhaengen.
            models.CheckConstraint(
                condition=~models.Q(requested_by=models.F("reviewed_by")) | models.Q(reviewed_by__isnull=True),
                name="rectification_request_different_reviewer",
            ),
            # Hoechstens EIN offener Antrag je (Einrichtung, Person, Feldpfad):
            # zwei gleichzeitig laufende Berichtigungen desselben Feldes ergeben
            # widerspruechliche Endzustaende (wer zuletzt genehmigt, gewinnt).
            # Partiell, damit abgeschlossene (genehmigte/abgelehnte) Antraege
            # eine erneute Beantragung nicht blockieren — analog
            # ``unique_pending_deletion_request``.
            models.UniqueConstraint(
                fields=["facility", "target_client", "field_path"],
                condition=models.Q(status="pending"),
                name="unique_pending_rectification_request",
            ),
        ]

    def __str__(self):
        return f"Berichtigungsantrag {self.field_path} ({self.get_status_display()})"
