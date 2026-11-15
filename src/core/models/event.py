"""Documentation event -- core object of the Anlaufstelle."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, router
from django.utils.translation import gettext_lazy as _

from core.models.managers import EventManager
from core.models.mixins import DynamicDataEncryptionMixin

# Prefix of the error the ban person trigger of migration 0199 raises. The
# message carries it because a trigger has no constraint name of its own.
BAN_PERSON_TRIGGER_TAG = "event_ban_requires_person"


def is_ban_person_violation(exc) -> bool:
    """Whether this ``IntegrityError`` comes from the ban person trigger."""
    return isinstance(exc, IntegrityError) and BAN_PERSON_TRIGGER_TAG in str(exc)


class Event(DynamicDataEncryptionMixin):
    """A single documentation event (contact, service, note, etc.)."""

    objects = EventManager()

    class CreatedVia(models.TextChoices):
        """Nicht editierbare Entstehungsprovenienz fuer eng begrenzte Flows."""

        STANDARD = "standard", _("Standard")
        TALLY = "tally", _("Strichliste")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Einrichtung"),
    )
    client = models.ForeignKey(
        "core.Client",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Person"),
    )
    document_type = models.ForeignKey(
        "core.DocumentType",
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("Dokumentationstyp"),
    )
    case = models.ForeignKey(
        "core.Case",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Fall"),
    )
    episode = models.ForeignKey(
        "core.Episode",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("Episode"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_events",
        verbose_name=_("Erstellt von"),
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
    idempotency_payload_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        default=None,
        editable=False,
        verbose_name=_("Idempotenz-Payload-Hash"),
        help_text=_(
            "SHA-256-Fingerabdruck des fachlichen Request-Payloads — erkennt "
            "abweichende Wiederverwendung eines Idempotenz-Schlüssels auch nach Cache-Verlust."
        ),
    )
    created_via = models.CharField(
        max_length=16,
        choices=CreatedVia.choices,
        default=CreatedVia.STANDARD,
        editable=False,
        verbose_name=_("Erstellt über"),
    )
    occurred_at = models.DateTimeField(verbose_name=_("Zeitpunkt"))
    data_json = models.JSONField(default=dict, verbose_name=_("Daten (JSON)"))
    # Refs #827 (C-60): expliziter Suchindex statt JSONB-icontains.
    # Enthaelt nur unverschluesselte Felder mit Default-Sensitivity (ohne
    # ELEVATED/HIGH). Wird im create_event/update_event-Pfad gepflegt und
    # ueber einen GIN-trgm-Index angefragt — in services/search.py.
    search_text = models.TextField(default="", blank=True, verbose_name=_("Suchindex"))
    is_anonymous = models.BooleanField(default=False, verbose_name=_("Anonym"))
    is_deleted = models.BooleanField(default=False, verbose_name=_("Gelöscht"))
    # Refs #2157: Papierkorb-Metadaten in der Form von ``SoftDeletableModel``
    # (``models/mixins.py``), aber bewusst OHNE Umstellung auf das Mixin: seine
    # ``soft_delete()``/``restore()``-Methoden schreiben ein volles ``save()``,
    # waehrend ``soft_delete_event`` mit ``update_fields``-Praezision gegen ein
    # paralleles Merge arbeitet (``services/events/crud.py``). Ohne die beiden
    # Felder konnte die Art.-15-Auskunft bei Ereignissen im Papierkorb weder
    # Loeschzeitpunkt noch Ausloeser ausweisen, obwohl sie beides bei Person und
    # Fall liefert. Vor 0.96 geloeschte Ereignisse tragen ``NULL`` — es gibt
    # bewusst keinen Backfill (die loeschende Person ist aus dem Bestand nicht
    # rekonstruierbar).
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
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))

    class Meta:
        verbose_name = _("Ereignis")
        verbose_name_plural = _("Ereignisse")
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["client", "-occurred_at"]),
            models.Index(fields=["document_type", "-occurred_at"]),
            models.Index(fields=["facility", "-occurred_at"]),
            # Nahezu alle Event-Queries filtern is_deleted=False, daher
            # Composite-Index (facility, is_deleted, -occurred_at) für
            # Timeline-/Listen-Queries. Refs #638.
            models.Index(fields=["facility", "is_deleted", "-occurred_at"], name="event_facility_del_occ_idx"),
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
                name="event_idem_key_per_user_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.document_type} – {self.occurred_at:%d.%m.%Y %H:%M}"

    def _is_certain_personless_insert(self, save_kwargs) -> bool:
        """Whether the pending ``save()`` certainly writes a new row without a person.

        Decided from the arguments alone, so no query runs: ``force_insert``
        and an instance that was never stored (``_state.adding``, or no primary
        key) INSERT. Everything else is left out on purpose: ``force_update``
        and ``update_fields`` never insert, and a plain ``save()`` of a loaded
        instance is an UPDATE whose INSERT fallback (primary key replaced, or
        the row deleted meanwhile) only the database sees at the moment it
        happens. The trigger ``event_ban_requires_person_insert`` (migration
        ``0199``) refuses that INSERT, and ``event_ban_requires_person_update``
        refuses an UPDATE that turns a row into a personless ban.
        """
        if save_kwargs.get("force_update") or save_kwargs.get("update_fields") is not None:
            return False
        if not (save_kwargs.get("force_insert") or self._state.adding or self.pk is None):
            return False
        if self.get_deferred_fields():
            # ``dynamic_data_write`` refuses an implicit save of a deferred
            # instance before any query; reading a deferred ``client_id`` here
            # would query first.
            return False
        return self.client_id is None

    def _stored_type_is_ban(self, save_kwargs) -> bool:
        """Read the system type of the stored type row, not of a loaded instance.

        A ``DocumentType`` instance loaded earlier can still carry an old system
        type after another save made the type a ban.
        """
        from core.models.document_type import DocumentType

        if self.document_type_id is None:
            return False
        using = save_kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        system_type = (
            DocumentType._base_manager.using(using)
            .filter(pk=self.document_type_id)
            .values_list("system_type", flat=True)
            .first()
        )
        return system_type == DocumentType.SystemType.BAN

    def save(self, *args, **kwargs):
        from core.services.dynamic_protection import dynamic_data_write

        update_fields = kwargs.get("update_fields")
        if self._is_certain_personless_insert(kwargs) and self._stored_type_is_ban(kwargs):
            # A new ban names a person. This check only gives a readable error;
            # the database triggers of migration 0199 carry the guarantee.
            raise ValidationError(
                _(
                    "Für diesen Dokumentationstyp muss eine Person ausgewählt werden, "
                    "da eine Mindest-Kontaktstufe vorausgesetzt wird."
                )
            )
        with dynamic_data_write(self, update_fields):
            if update_fields is None or "data_json" in update_fields:
                self._encrypt_sensitive_fields()
                if update_fields is not None and "search_text" not in update_fields:
                    from core.services.dynamic_data import _is_file_marker

                    current = (
                        Event.objects.for_facility(self.facility_id)
                        .filter(pk=self.pk)
                        .values_list("data_json", flat=True)
                        .first()
                    )
                    # Refs #2525: file-marker-only writes cannot affect the index.
                    # Preserve independent index writes while still encrypting
                    # stale source values and indexing every non-marker change.
                    before = {key: value for key, value in (current or {}).items() if not _is_file_marker(value)}
                    after = {key: value for key, value in (self.data_json or {}).items() if not _is_file_marker(value)}
                    if current is None or before != after:
                        kwargs["update_fields"] = {*update_fields, "search_text"}
            super().save(*args, **kwargs)
