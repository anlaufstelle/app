"""ShiftHandoverNote — manuelle Freitext-Notiz zur Schichtuebergabe (Refs #2042)."""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class ShiftHandoverNote(models.Model):
    """Die subjektive Notiz der abgebenden Kraft an die Folgeschicht.

    Die Uebergabe-Ansicht ist bis hierher rein auto-generiert (Kennzahlen,
    Krisen, Hausverbote, dringende Aufgaben). Was die abgebende Kraft
    *einschaetzt* — „B war heute sehr belastbar, bitte nachfassen" — hatte
    keinen Ort; #1139
    entfernte die alte Aufgabenliste bewusst und liess diese Luecke offen.

    **Ein Datensatz je Schicht.** Die Schicht ist das Paar
    (``shift_date``, ``time_filter``): das lokale Kalenderdatum des
    Schichtbeginns und das konfigurierte Zeitfenster der Einrichtung. Ohne
    konfiguriertes Zeitfenster (``time_filter = NULL``) ist die Schicht der
    Kalendertag — genau die Fallunterscheidung, die
    :func:`core.services.events.feed.get_time_range` schon fuer Feed,
    Strichliste und Uebergabe trifft. Der ``UniqueConstraint`` traegt deshalb
    ``nulls_distinct=False``: sonst liesse Postgres beliebig viele
    Ganztags-Notizen desselben Tages nebeneinander zu.

    **Verschluesselung (Muster ``RectificationRequest.reason``, ADR-041).**
    ``note`` ist ein ``JSONField``, das das Marker-Dict von
    :func:`core.services.file_vault.encrypt_field`
    (``{"__encrypted__": true, "value": "gAAAA…"}``) traegt — ohne die
    JSON-Zwischenstufe der Wertpaare, weil die Notiz immer Freitext ist. Der
    ``DynamicDataEncryptionMixin`` passt hier strukturell nicht: er haengt an
    einem ``document_type``-FK und dessen ``is_encrypted``-Feldflags, die eine
    Schichtnotiz nicht hat.

    Ver- und Entschluesselt wird ausschliesslich im Service
    (:mod:`core.services.case.handover_note`) — wie bei
    ``RectificationRequest`` gibt es bewusst keinen ``save()``-Override und
    keinen Modul-Import von ``core.services.file_vault`` hier (Zirkelfreiheit
    der Model-Schicht).

    **Kein Personenbezug im Schema.** Es gibt keinen Client-/Case-FK: die
    Notiz gehoert der Schicht, nicht einer Person. Der Freitext *kann*
    Personen nennen — deshalb ist er verschluesselt, rollenbeschraenkt
    (``staff``+) und faellt unter eine eigene, kurze Loeschfrist
    (``Settings.retention_handover_notes_days``). Er ist bewusst **nicht**
    Teil des Art.-15-Personenexports und **nicht** Teil der Suche
    (``docs/datenschutz.md`` § Uebergabenotiz).

    ``__str__`` nennt nur Schicht und Datum — nie den Text (Refs #1751:
    Freitext gehoert nicht in Repr, Admin-Listen oder Logs).
    """

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="shift_handover_notes",
        verbose_name=_("Einrichtung"),
    )
    shift_date = models.DateField(
        verbose_name=_("Schichtdatum"),
        help_text=_("Lokales Kalenderdatum des Schichtbeginns."),
    )
    time_filter = models.ForeignKey(
        "core.TimeFilter",
        # Refs #2042 Befund E-02): **PROTECT**, nicht CASCADE.
        # ``CASCADE`` loeschte die Notizen eines Zeitfensters physisch mit —
        # am auditierten Loeschpfad und an der Aufbewahrungsfrist vorbei, ohne
        # eine einzige ``HANDOVER_NOTE_DELETED``-Zeile. Genau die stille
        # Vernichtung von Uebergabewissen, die die Rechenschaftspflicht
        # (Art. 5 Abs. 2 DSGVO) ausschliessen soll.
        # ``SET_NULL`` scheidet aus: ``time_filter IS NULL`` ist die
        # **Identitaet** der Ganztags-Schicht (``nulls_distinct=False``) —
        # zwei verwaiste Schichtnotizen desselben Tages fielen auf dieselbe
        # Zeile zusammen und verletzten den UniqueConstraint.
        # Praktische Folge, bewusst so: ein Zeitfenster mit Notizen ist erst
        # loeschbar, wenn deren Frist abgelaufen ist. Der Django-Admin meldet
        # die Schutzverletzung kontrolliert.
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="handover_notes",
        verbose_name=_("Schicht"),
    )
    note = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("Notiz (verschlüsselt)"),
        help_text=_("Wird verschlüsselt gespeichert."),
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # Bestandsmuster der Autorenfelder (``Event.created_by``): die Notiz
        # ueberlebt die Loeschung ihrer Verfasserin, nur der Personenbezug
        # faellt weg.
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authored_handover_notes",
        verbose_name=_("Verfasst von"),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_handover_notes",
        verbose_name=_("Zuletzt geändert von"),
    )
    # -- Eingefrorene Fenstergrenzen (Refs #2042, Befund E-01) --------
    #
    # Die drei Instants werden bei der ANLAGE aus der damals gueltigen
    # Dienstplan-Konfiguration berechnet und danach nie mehr angefasst. Vorher
    # rechnete jeder Zugriff das Fenster aus den *gegenwaertig aktiven*
    # Zeitfiltern neu — wer den Spaetdienst deaktivierte oder seine Zeiten
    # verschob, riss damit das Editierfenster einer laengst abgeschlossenen
    # Fruehschicht wieder auf. „Danach schreibgeschuetzt" war also keine
    # Zusage, sondern eine Momentaufnahme der Konfiguration.
    #
    # ``window_start``/``window_end`` sind das Schichtfenster selbst (Anzeige,
    # Nachvollziehbarkeit), ``editable_until`` das Ende der FOLGE-Schicht und
    # damit die einzige Grenze, gegen die Schreiben und Loeschen pruefen.
    # Aenderungen an Zeitfiltern wirken dadurch ausdruecklich **nicht**
    # rueckwirkend.
    window_start = models.DateTimeField(verbose_name=_("Schichtbeginn"))
    window_end = models.DateTimeField(verbose_name=_("Schichtende"))
    editable_until = models.DateTimeField(verbose_name=_("Bearbeitbar bis"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Erstellt am"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Aktualisiert am"))

    class Meta:
        verbose_name = _("Übergabenotiz")
        verbose_name_plural = _("Übergabenotizen")
        ordering = ["-shift_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "shift_date", "time_filter"],
                name="uniq_handover_note_per_shift",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["facility", "shift_date"], name="handovernote_fac_date_idx"),
        ]

    def __str__(self):
        label = self.time_filter.label if self.time_filter_id else "Ganzer Tag"
        return f"Übergabenotiz {label} {self.shift_date:%d.%m.%Y}"
