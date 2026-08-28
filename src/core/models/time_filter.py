"""Time filters for shift schedules."""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models.managers import FacilityScopedManager


class TimeFilter(models.Model):
    """Time window (e.g. morning, evening, night shift) for a facility."""

    objects = FacilityScopedManager()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="time_filters",
        verbose_name=_("Einrichtung"),
    )
    label = models.CharField(max_length=100, verbose_name=_("Bezeichnung"))
    start_time = models.TimeField(
        verbose_name=_("Startzeit"),
        help_text=_("Schichtbeginn (z.B. 08:00)"),
    )
    end_time = models.TimeField(
        verbose_name=_("Endzeit"),
        help_text=_("Schichtende (z.B. 16:00). Bei Nachtschicht: Ende nach Mitternacht möglich"),
    )
    # Refs #2068 (e): ``is_default`` ist ersatzlos entfallen. Das Feld wurde
    # ausschliesslich vom Startbestand geschrieben und an keiner Stelle
    # gelesen; ``current_tally_context`` faellt ausserhalb jeder Schicht auf
    # den vollen Tag zurueck, nicht auf die markierte Schicht. Der help_text
    # („wird vorausgewählt, wenn keine aktive Schicht erkannt wird")
    # versprach damit ein Verhalten, das es nie gab. Eine echte
    # Default-Schicht-Schicht waere ein Feature und braucht einen eigenen
    # Entwurf — sie hier stillschweigend nachzubauen hiesse, aus einer
    # Bereinigung eine Verhaltensaenderung zu machen.
    is_active = models.BooleanField(default=True, verbose_name=_("Aktiv"))
    sort_order = models.IntegerField(default=0, verbose_name=_("Sortierung"))

    class Meta:
        verbose_name = _("Zeitfilter")
        verbose_name_plural = _("Zeitfilter")
        ordering = ["sort_order", "start_time"]

    def __str__(self):
        return f"{self.label} ({self.start_time:%H:%M}–{self.end_time:%H:%M})"

    def covers_time(self, dt):
        """Check whether a point in time falls within this time window.

        Supports midnight overlap: when start > end,
        the condition is t >= start OR t <= end.
        """
        t = dt.time() if hasattr(dt, "time") else dt
        if self.start_time <= self.end_time:
            return self.start_time <= t <= self.end_time
        # Midnight logic (e.g. 22:00-08:00)
        return t >= self.start_time or t <= self.end_time
