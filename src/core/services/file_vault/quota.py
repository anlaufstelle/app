"""Speicherkontingent je Einrichtung (Refs #2129, Befund N-5).

Warum ueberhaupt
================

Die Groessenbegrenzung des File-Vaults galt bis hierher ausschliesslich **pro
Datei** (:func:`~core.services.file_vault.policy.enforce_upload_size`:
``min(Settings.max_file_size_mb, FILE_VAULT_MAX_UPLOAD_BYTES)``). Eine Summe je
Einrichtung wurde nirgends gebildet. Betriebsseitig gab es nur den globalen
Disk-Check in ``/health/`` (``HEALTH_DISK_REQUIRED``) — der **meldet**, wenn
die Platte volllaeuft, verhindert nichts und kennt keine Einrichtung.

Im Mehr-Mandanten-Betrieb (eine Instanz, mehrere Einrichtungen — das
RLS-Zielbild) konnte damit **eine** Einrichtung mit lauter legitimen 10-MB-
Uploads den gemeinsamen ``MEDIA_ROOT`` fuellen; die Folge (Upload-Fehler,
Backup-Fehler, ggf. Postgres-WAL auf gemeinsamem Volume) traf **alle**
Mandanten. Verfuegbarkeits- und Isolationsthema nach Art. 32 DSGVO, kein
Vertraulichkeitsleck.

Ein Rechner fuer zwei Fragen
============================

:func:`facility_storage_usage` beantwortet die Durchsetzungsfrage („passt diese
Datei noch?") **und** die Anzeigefrage („wie voll ist die Einrichtung?"). Das
ist Absicht: eine Kachel, die eine andere Zahl nennt als die Regel dahinter,
ist schlimmer als keine Kachel (dieselbe Regel wie in
:mod:`core.services.hubs`).

Was zaehlt
==========

**Alle** ``Attachment``-Zeilen der Einrichtung, **inklusive Papierkorb** und
inklusive abgeloester Vorversionen: soft-geloescht heisst nur „aus der Sicht
der Fachkraft entfernt", die ``.enc`` liegt bis zur physischen Tilgung
(Owner-Loeschung/Anonymisierung bzw. ``purge_attachment_chain``) weiter auf der
Platte und belegt Platz. Die Anzeige weist den Papierkorb-Anteil deshalb
gesondert aus — er ist der Hebel, mit dem eine volle Einrichtung wieder Luft
bekommt. ``ImportJob``-Quelldateien zaehlen **nicht** mit: sie sind temporaer
und haengen an einem eigenen Modell.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings as django_settings
from django.db.models import Q, Sum

from core.models.attachment import Attachment
from core.models.settings import Settings

_BYTES_PER_MB = 1024 * 1024

#: Ab diesem Fuellstand (Prozent) warnen Verwaltungs-Hub und Leitungs-Dashboard.
WARN_THRESHOLD_PERCENT = 90


@dataclass(frozen=True)
class StorageUsage:
    """Belegung und wirksames Limit einer Einrichtung — in Bytes.

    Die ``*_mb``-Eigenschaften runden kaufmaennisch auf ganze Megabyte; sie
    sind fuer Meldungstexte und Kacheln gedacht (dasselbe Format wie in
    ``enforce_upload_size``). Wer exakt rechnen will, nimmt die Byte-Werte.
    ``limit_bytes is None`` heisst **unbegrenzt** — dann gibt es weder eine
    Prozentzahl noch eine Warnschwelle.
    """

    used_bytes: int
    trash_bytes: int
    limit_bytes: int | None

    @property
    def used_mb(self) -> int:
        return round(self.used_bytes / _BYTES_PER_MB)

    @property
    def trash_mb(self) -> int:
        return round(self.trash_bytes / _BYTES_PER_MB)

    @property
    def limit_mb(self) -> int | None:
        return None if self.limit_bytes is None else round(self.limit_bytes / _BYTES_PER_MB)

    @property
    def percent_used(self) -> int | None:
        """Fuellstand in Prozent — ``None`` bei unbegrenztem Kontingent."""
        if not self.limit_bytes:
            return None
        return round(self.used_bytes * 100 / self.limit_bytes)

    @property
    def is_near_limit(self) -> bool:
        percent = self.percent_used
        return percent is not None and percent >= WARN_THRESHOLD_PERCENT

    def fits(self, additional_bytes: int) -> bool:
        """Passt eine weitere Datei dieser Groesse noch hinein?

        Der Grenzwert ist **inklusiv**: genau aufgehen darf noch.
        """
        if self.limit_bytes is None:
            return True
        return self.used_bytes + additional_bytes <= self.limit_bytes


def _configured_limit_bytes(facility) -> int | None:
    """Das wirksame Limit: das **striktere** von Einrichtungs-Setting und
    Betreiber-Backstop; ``None``, wenn beide fehlen.

    Fail-closed wie ``enforce_allowed_file_types``: fehlt die ``Settings``-Row
    ganz, gilt nicht „unbegrenzt", sondern der Backstop. ``0`` ist an beiden
    Stellen die ausdrueckliche Abschaltung („kein Limit an dieser Stelle") —
    ein Kontingent von 0 MB waere sinnlos, es liesse gar keinen Upload mehr zu.

    ``objects.get(facility=…)`` statt ``for_facility``: dasselbe Muster wie
    ``enforce_upload_size``/``enforce_allowed_file_types`` — ``Settings`` ist
    ein 1:1-Satz je Einrichtung, kein zu scopender Bestand (Refs #1834-Guard
    zielt auf ``objects.filter(facility=…)``).
    """
    try:
        facility_mb = Settings.objects.get(facility=facility).max_storage_mb or None
    except Settings.DoesNotExist:
        facility_mb = None
    backstop_mb = getattr(django_settings, "FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB", 0) or None

    candidates = [mb for mb in (facility_mb, backstop_mb) if mb]
    if not candidates:
        return None
    return min(candidates) * _BYTES_PER_MB


def facility_storage_usage(facility) -> StorageUsage:
    """Belegung und wirksames Kontingent einer Einrichtung (2 Queries).

    Eine Aggregat-Query fuer Gesamt- und Papierkorb-Summe (bedingtes ``Sum``),
    eine fuer das Einrichtungs-Setting — die Kachel darf die Uebersichtsseiten
    nicht teurer machen.
    """
    totals = Attachment.objects.for_facility(facility).aggregate(
        used=Sum("file_size"),
        trash=Sum("file_size", filter=Q(deleted_at__isnull=False)),
    )
    return StorageUsage(
        used_bytes=totals["used"] or 0,
        trash_bytes=totals["trash"] or 0,
        limit_bytes=_configured_limit_bytes(facility),
    )
