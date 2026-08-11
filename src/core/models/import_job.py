"""ImportJob — persistierter Wizard-State des CSV-Imports (M4, Refs #1060).

Der Import laeuft ueber mehrere Requests (Hochladen → Zuordnen → Probelauf →
Uebernehmen) und muss jederzeit abbrechbar sein. Der Zustand liegt deshalb in
der DB statt in der Session: nur so ist er nach einem Abbruch/Timeout noch
auffindbar (Stale-Cleanup, :func:`core.services.import_engine.cleanup_stale_import_jobs`)
und nur so bleibt eine PII-freie Spur des Laufs erhalten.

**Statusmaschine** (``Status``)::

    UPLOADED ──apply_mapping──> MAPPED ──run_import(commit=False)──> VALIDATED
        │                          │                                    │
        │                          │                       commit_import │ (nur bei 0 Fehlern)
        │                          │                                    ▼
        └──────────── abort_import ┴────────────────────────────> COMMITTED
                                   │
                                   └──────────────────────────────> ABORTED

* ``UPLOADED``  — verschluesselte Quelldatei liegt auf Disk, keine Zuordnung.
* ``MAPPED``    — Spalten→Ziel-Zuordnung validiert und persistiert.
* ``VALIDATED`` — Probelauf gelaufen; ``result_json`` traegt Zusammenfassung
  und Fehlerliste. Ohne Fehler ist der Uebernehmen-Schritt freigeschaltet.
* ``COMMITTED`` / ``ABORTED`` — Endzustaende. In BEIDEN ist die
  Upload-Datei geloescht (Issue-Vorgabe: Import-Uploads sind PII).

Ein erneuter Probelauf ist aus ``MAPPED`` und ``VALIDATED`` heraus moeglich
(idempotent); die Endzustaende sind terminal.

**PII-Kontrakt:** In der Zeile stehen NUR der verschluesselte Originalname
(``original_filename_encrypted``) und strukturelle Metadaten. ``mapping_json``
traegt Spaltenueberschriften und Ziel-Slugs, ``result_json`` ausschliesslich
Zeilen-/Spaltennummern, Fehlercodes und feste Meldungstexte — nie Zellwerte
(gepinnt in ``src/tests/test_import_engine.py``).
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class ImportJob(models.Model):
    """Ein CSV-Importlauf einer Einrichtung."""

    class TargetType(models.TextChoices):
        # Werte identisch zu ``DocumentType.EntityType`` — ``clean()`` und die
        # Engine vergleichen beide direkt, ohne Uebersetzungstabelle.
        CLIENT = "client", _("Personen")
        EVENT = "event", _("Ereignisse")

    class Status(models.TextChoices):
        UPLOADED = "uploaded", _("Hochgeladen")
        MAPPED = "mapped", _("Zugeordnet")
        VALIDATED = "validated", _("Geprüft")
        COMMITTED = "committed", _("Übernommen")
        ABORTED = "aborted", _("Abgebrochen")

    #: Endzustaende — ab hier sind Datei und Statuswechsel erledigt.
    FINAL_STATUSES = (Status.COMMITTED, Status.ABORTED)

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="import_jobs",
        verbose_name=_("Einrichtung"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="import_jobs",
        verbose_name=_("Angelegt von"),
    )
    target_type = models.CharField(
        max_length=20,
        choices=TargetType.choices,
        verbose_name=_("Zieltyp"),
        help_text=_("Ob die Zeilen der Datei Personen oder Ereignisse anlegen."),
    )
    document_type = models.ForeignKey(
        "core.DocumentType",
        # PROTECT wie bei ``Client.document_type``: die Feld-Semantik der
        # Zuordnung (Slug → Label/Verschluesselung) darf nicht unbemerkt
        # wegbrechen, solange ein Lauf sie referenziert.
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="import_jobs",
        verbose_name=_("Dokumentationstyp"),
        help_text=_("Liefert die Zusatzfelder als Zuordnungsziele. Für Ereignisse Pflicht."),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
        verbose_name=_("Status"),
    )
    storage_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Speicherdateiname"),
        help_text=_("UUID-basierter Dateiname der verschlüsselten Quelldatei; leer nach Übernahme/Abbruch."),
    )
    original_filename_encrypted = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Originaldateiname (verschlüsselt)"),
        help_text=_("Wird verschlüsselt gespeichert — der Dateiname kann einen Personenbezug tragen."),
    )
    mapping_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Zuordnung (JSON)"),
        help_text=_("{'columns': {Spalte: Ziel}, 'delimiter': ';', 'encoding': 'utf-8-sig'}"),
    )
    result_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("Ergebnis (JSON)"),
        help_text=_("{'summary': {...}, 'errors': [{'row','column','code','message'}]} — ohne Zellwerte."),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))

    class Meta:
        verbose_name = _("Importlauf")
        verbose_name_plural = _("Importläufe")
        ordering = ["-created_at"]
        indexes = [
            # Listen-Query der Einrichtung (neueste zuerst) + Stale-Cleanup,
            # das je Facility nach alten, nicht abgeschlossenen Laeufen sucht.
            models.Index(fields=["facility", "-created_at"], name="importjob_facility_ca_idx"),
        ]

    def clean(self):
        super().clean()
        self._validate_document_type()

    def _validate_document_type(self):
        """Der Typ muss zur Einrichtung UND zum Zieltyp passen.

        Ohne den ersten Guard liesse sich ueber die Feldmenge eines fremden
        Typs die Konfiguration einer anderen Einrichtung ausspaehen; ohne den
        zweiten liefe der Lauf gegen ein Feldset, das die Ziel-Entitaet gar
        nicht kennt (``create_client``/``create_event`` verwerfen die Werte
        dann still).
        """
        if self.document_type_id is None:
            if self.target_type == self.TargetType.EVENT:
                raise ValidationError(
                    {"document_type": _("Für den Ereignis-Import ist ein Dokumentationstyp erforderlich.")}
                )
            return
        if self.document_type.facility_id != self.facility_id:
            raise ValidationError({"document_type": _("Der Dokumentationstyp gehört zu einer anderen Einrichtung.")})
        if self.document_type.entity_type != self.target_type:
            raise ValidationError({"document_type": _("Der Dokumentationstyp passt nicht zum gewählten Zieltyp.")})

    @property
    def is_final(self):
        """``True``, sobald der Lauf uebernommen oder abgebrochen wurde."""
        return self.status in self.FINAL_STATUSES

    @property
    def error_count(self):
        """Anzahl der Zeilenfehler des letzten Probelaufs (0 ohne Ergebnis)."""
        return len((self.result_json or {}).get("errors", []))

    def __str__(self):
        return f"Import {self.get_target_type_display()} ({self.get_status_display()}) — {self.facility_id}"
