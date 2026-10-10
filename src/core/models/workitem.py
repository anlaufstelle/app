"""Work items and deletion requests."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.constants import ApprovalStatus
from core.models.managers import FacilityScopedManager
from core.models.mixins import SoftDeletableModel


class WorkItem(SoftDeletableModel):
    """Work item or hint for the team."""

    class ItemType(models.TextChoices):
        HINT = "hint", _("Hinweis")
        TASK = "task", _("Aufgabe")

    class Status(models.TextChoices):
        OPEN = "open", _("Offen")
        IN_PROGRESS = "in_progress", _("In Bearbeitung")
        DONE = "done", _("Erledigt")
        DISMISSED = "dismissed", _("Verworfen")

    class Priority(models.TextChoices):
        NORMAL = "normal", _("Normal")
        IMPORTANT = "important", _("Wichtig")
        URGENT = "urgent", _("Dringend")

    class Recurrence(models.TextChoices):
        NONE = "none", _("Keine")
        WEEKLY = "weekly", _("Wöchentlich")
        MONTHLY = "monthly", _("Monatlich")
        QUARTERLY = "quarterly", _("Vierteljährlich")
        YEARLY = "yearly", _("Jährlich")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="work_items",
        verbose_name=_("Einrichtung"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="work_items",
        verbose_name=_("Person"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Refs #1347 (DAT-04): SET_NULL statt CASCADE — eine harte
        # User-Loeschung darf die fachliche Aufgabenhistorie nicht mit sich
        # reissen. Der Ersteller-Bezug faellt auf NULL, die Aufgabe bleibt
        # erhalten (analog Case.created_by/Event.created_by).
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_work_items",
        verbose_name=_("Erstellt von"),
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_work_items",
        verbose_name=_("Zugewiesen an"),
    )
    item_type = models.CharField(
        max_length=10,
        choices=ItemType.choices,
        default=ItemType.TASK,
        verbose_name=_("Typ"),
        help_text=_("Aufgabe = aktiv zu erledigen, Hinweis = informativ."),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        verbose_name=_("Status"),
        help_text=_("Offen = noch nicht begonnen, In Bearbeitung = aktiv, Erledigt = abgeschlossen"),
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.NORMAL,
        verbose_name=_("Priorität"),
        help_text=_("Dringend = sofortige Bearbeitung, Wichtig = zeitnah, Normal = regulär"),
    )
    title = models.CharField(max_length=200, verbose_name=_("Titel"))
    description = models.TextField(
        blank=True,
        verbose_name=_("Beschreibung"),
        help_text=_(
            "Frei-Text zur Aufgabe. Dieses Feld wird nicht verschlüsselt "
            "gespeichert — bitte keine Klarnamen und keine besonders "
            "schützenswerten Angaben zu Gesundheit, Sucht, Herkunft, Religion "
            "oder Sexualität (besondere Kategorien personenbezogener Daten, "
            "Art. 9 DSGVO)."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Abgeschlossen am"),
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Zu erledigen bis"),
    )
    remind_at = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("Wiedervorlage am"),
        help_text=_("Optional. Wann soll die Aufgabe wieder aufpoppen?"),
    )
    recurrence = models.CharField(
        max_length=20,
        choices=Recurrence.choices,
        default=Recurrence.NONE,
        verbose_name=_("Wiederholung"),
        help_text=_("Bei Erledigung wird automatisch eine Folgeaufgabe mit neuer Frist erstellt."),
    )
    recurrence_duplicated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Folgeaufgabe erstellt am"),
        help_text=_(
            "Zeitpunkt, an dem für dieses Item bereits eine wiederkehrende Folgeaufgabe erzeugt "
            "wurde. Verhindert doppelte Duplikate beim erneuten Setzen auf 'Erledigt'."
        ),
    )
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        default=None,
        editable=False,
        verbose_name=_("Idempotenz-Schlüssel"),
        help_text=_(
            "X-Idempotency-Key des Offline-Replays — persistenter DB-Backstop "
            "gegen Duplikate bei Cache-Verlust oder parallelen Replays (Review R5/R6)."
        ),
    )

    class Meta:
        verbose_name = _("Arbeitsauftrag")
        verbose_name_plural = _("Arbeitsaufträge")
        ordering = ["-created_at"]
        # WorkItemInboxView filtert facility + status, sortiert nach
        # priority_order + -created_at. Refs #638.
        indexes = [
            models.Index(fields=["facility", "status", "-created_at"], name="workitem_facility_status_idx"),
        ]
        constraints = [
            # Review R5/R6: persistenter Backstop gegen Duplikat-Replays, wenn
            # der Cache-Dedup ausfaellt (Eviction/Neustart) oder zwei Replays
            # das get-then-set-Fenster gleichzeitig passieren. Partiell, damit
            # Nicht-Offline-Writes (idempotency_key IS NULL) unbeschraenkt
            # bleiben.
            models.UniqueConstraint(
                fields=["created_by", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="workitem_idem_key_per_user_uniq",
            ),
        ]

    def __str__(self):
        return self.title


class DeletionRequest(models.Model):
    """Deletion request (four-eyes principle)."""

    #: Geteilt mit ``RectificationRequest`` (Refs #1834) — Alias, damit
    #: ``DeletionRequest.Status.PENDING`` weiter funktioniert.
    Status = ApprovalStatus

    class TargetType(models.TextChoices):
        EVENT = "Event", _("Event")
        CLIENT = "Client", _("Person")
        # Refs #1818 (ADR-044): Art. 17 kannte bis hierher nur die beiden
        # Extreme — einzelnes Ereignis oder ganze Person. Ein Betroffener, der
        # genau einen Fall geloescht haben will, zwang die Einrichtung damit zur
        # Alles-oder-nichts-Entscheidung. Der Wert ist bewusst der Modellname
        # (wie ``Event``/``Client``), damit ``target_id`` ohne Zusatzwissen
        # aufloesbar bleibt.
        CASE = "Case", _("Fall")

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="deletion_requests",
        verbose_name=_("Einrichtung"),
    )
    target_type = models.CharField(max_length=100, choices=TargetType.choices, verbose_name=_("Zieltyp"))
    target_id = models.UUIDField(verbose_name=_("Ziel-ID"))
    # Refs #1876 (ADR-045): Merge-Ziel eines Zusammenfuehrungs-Antrags. KEIN
    # eigenes Request-Modell: der Vier-Augen-Weg (PENDING-Unique, Requester-!=-
    # Reviewer-Constraint, Antragsliste, Idempotenz) ist mit dem Loeschantrag
    # identisch — die Quelle IST ``target_id``, es fehlte nur das Ziel. Ein
    # gesetztes ``merge_into`` macht aus dem Loeschantrag einen Merge-Antrag;
    # entschieden wird er ausschliesslich ueber ``approve_client_merge``/
    # ``reject_client_merge`` (Guards in ``services/client/main.py``).
    # PROTECT wie ``Client.merged_into``: Personen werden redigiert, nie hart
    # geloescht — der Antrags-Nachweis darf nicht still zerfallen.
    merge_into = models.ForeignKey(
        "core.Client",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_merge_requests",
        verbose_name=_("Zusammenführungs-Ziel"),
    )
    stage_downgrade = models.BooleanField(default=False, verbose_name=_("Herabstufungsantrag"))
    reason = models.TextField(verbose_name=_("Begründung"))
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name=_("Status"),
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Refs #1347 (DAT-04): SET_NULL statt CASCADE — der 4-Augen-Nachweis
        # (wer hat den Loeschantrag gestellt) darf eine harte User-Loeschung
        # ueberleben; nur der Personenbezug faellt weg, der Antrag bleibt.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deletion_requests",
        verbose_name=_("Beantragt von"),
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_deletion_requests",
        verbose_name=_("Geprüft von"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Geprüft am"),
    )

    class Meta:
        verbose_name = _("Löschantrag")
        verbose_name_plural = _("Löschanträge")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stage_downgrade=False) | models.Q(target_type="Client", merge_into__isnull=True),
                name="deletion_request_downgrade_client_only",
            ),
            models.CheckConstraint(
                condition=~models.Q(requested_by=models.F("reviewed_by")),
                name="deletion_request_different_reviewer",
            ),
            # Refs #1876: ein Merge-Antrag darf nicht auf seine eigene Quelle
            # zeigen (analog ``deletion_request_different_reviewer`` — der
            # Service prueft zuerst, die DB haelt die Invariante fest).
            models.CheckConstraint(
                condition=models.Q(merge_into__isnull=True) | ~models.Q(merge_into=models.F("target_id")),
                name="deletion_request_merge_target_differs",
            ),
            # #530: prevent duplicate PENDING requests for the same target.
            # Partial unique index — closed (APPROVED/REJECTED) records
            # remain unconstrained so re-requests after rejection are allowed.
            models.UniqueConstraint(
                fields=["facility", "target_type", "target_id"],
                condition=models.Q(status="pending"),
                name="unique_pending_deletion_request",
            ),
        ]

    def __str__(self):
        return f"Löschantrag {self.target_type} ({self.get_status_display()})"
