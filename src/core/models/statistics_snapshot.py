"""Monthly statistics snapshots per facility."""

from uuid import uuid4

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Value
from django.utils.translation import gettext_lazy as _

from .managers import FacilityScopedManager


class StatisticsSnapshot(models.Model):
    """Stores pre-computed monthly statistics for a facility."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    facility = models.ForeignKey(
        "core.Facility",
        on_delete=models.CASCADE,
        related_name="statistics_snapshots",
    )
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    data = models.JSONField(default=dict)
    jugendamt_data = models.JSONField(default=dict)
    # M5 P3 (Refs #1061): backend-unabhängiger Nachfolger von ``jugendamt_data``.
    # Ein Eintrag je Reporting-Backend mit ``snapshot_enabled``; der Wert ist das
    # **rohe** Aggregat aus ``DatasetBackend.build_dataset(...).summary`` (ohne
    # ``prepare_export``-Transformationen wie die k-Anon-Suppression, die nur den
    # Export-/Panel-Pfad betreffen). ``jugendamt_data`` wird bis zum Ende der
    # Übergangsphase parallel weitergeschrieben (Dual-Write) und ist die
    # Rückfallebene für Snapshots vor Migration 0122.
    # ``db_default`` zusätzlich zum Python-``default``: Django verwirft den
    # DB-Default sonst direkt nach dem ``ADD COLUMN`` (Migration 0121). Rollt
    # jemand nur den App-Code auf den Stand vor M5 zurück und lässt das Schema
    # stehen — der Standard-Rollback einer additiven Migration —, schreibt der
    # Alt-Code neue Monats-Snapshots ohne diese Spalte und liefe ohne DB-Default
    # in eine NOT-NULL-Verletzung.
    backend_data = models.JSONField(
        default=dict,
        db_default=Value({}, models.JSONField()),
        help_text=_("Map Backend-Slug → Roh-Aggregat des jeweiligen Reporting-Backends."),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = FacilityScopedManager()

    class Meta:
        verbose_name = _("Statistik-Snapshot")
        verbose_name_plural = _("Statistik-Snapshots")
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["facility", "year", "month"],
                name="unique_snapshot_per_month",
            ),
        ]

    def __str__(self):
        return f"{self.facility} — {self.year}/{self.month:02d}"
