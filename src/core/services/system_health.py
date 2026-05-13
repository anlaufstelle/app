"""System-Health-Checks fuer den Superadmin-Systembereich (Refs #871).

Schmale, defensiv geschriebene Funktionen, die das Dashboard-Card mit
Status-Indikatoren befuellen. Alle Funktionen sind so geschrieben, dass
sie unter keinen Umstaenden den Request-Cycle kippen — bei Fehlern
liefern sie ``False`` / leere Werte und schlucken Exceptions.

Bewusst Read-Only und ohne externe Side-Effects: ein Render des
Dashboard darf nichts veraendern.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

logger = logging.getLogger(__name__)

# Heuristik: ein Backup gilt als "stale", wenn es aelter als 36h ist.
# 24h waere zu strikt fuer Off-by-one-Stunden bei Cron-Schedules,
# 48h zu lasch fuer einen Daily-Backup-Loop. 36h gibt einen
# vollen Tag plus Sicherheitsmarge.
BACKUP_STALE_THRESHOLD_HOURS = 36


def check_database() -> bool:
    """Returns True if the database connection is reachable.

    Kuerzeste Variante via ``connection.ensure_connection()``. Eine
    Exception bedeutet, dass die DB nicht erreichbar ist — aus Sicht
    der Health-Card ist das ein roter Punkt.
    """
    try:
        connection.ensure_connection()
        return True
    except Exception:
        logger.exception("system_health: database check failed")
        return False


def pending_migrations() -> list[tuple[str, str]]:
    """List of (app_label, migration_name) tuples for unapplied migrations.

    Reuse: Django's ``MigrationExecutor.migration_plan`` returns the same
    Set, das ``manage.py migrate`` als naechstes anwenden wuerde.
    """
    try:
        executor = MigrationExecutor(connection)
        loader: MigrationLoader = executor.loader
        targets = loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        # plan = [(Migration, backwards: bool), ...]. Wir wollen nur
        # forward-pending Migrations als (app_label, name)-Tuple.
        return [(migration.app_label, migration.name) for migration, backwards in plan if not backwards]
    except Exception:
        logger.exception("system_health: pending_migrations check failed")
        return []


def disk_usage(path: str | os.PathLike | None = None) -> dict:
    """Disk-Usage fuer ``path`` (Default: ``settings.BASE_DIR``).

    Returns a dict with keys ``total_gb``, ``used_gb``, ``free_gb`` and
    ``percent_used`` (0-100). Bei Fehler -> alle Werte ``None``.
    """
    target = Path(path) if path is not None else Path(settings.BASE_DIR)
    try:
        usage = shutil.disk_usage(target)
        total = usage.total
        used = usage.used
        free = usage.free
        percent_used = round(used / total * 100, 1) if total > 0 else 0.0
        return {
            "total_gb": round(total / (1024**3), 1),
            "used_gb": round(used / (1024**3), 1),
            "free_gb": round(free / (1024**3), 1),
            "percent_used": percent_used,
        }
    except Exception:
        logger.exception("system_health: disk_usage check failed for %s", target)
        return {
            "total_gb": None,
            "used_gb": None,
            "free_gb": None,
            "percent_used": None,
        }


def last_backup_info(backup_dir: str | os.PathLike | None = None) -> dict | None:
    """Liefert Infos zum juengsten Backup-File oder ``None``.

    Heuristik: jueneste Datei (rekursiv) im konfigurierten
    ``settings.BACKUP_DIR``. Wenn weder Argument noch Setting verfuegbar
    sind oder das Verzeichnis leer ist, gibt es kein Backup.

    Returns dict mit ``path`` (str), ``mtime`` (timezone-aware datetime),
    ``age_hours`` (float) und ``is_stale`` (bool: aelter als
    ``BACKUP_STALE_THRESHOLD_HOURS``).
    """
    target = backup_dir if backup_dir is not None else getattr(settings, "BACKUP_DIR", None)
    if not target:
        return None
    target_path = Path(target)
    if not target_path.exists() or not target_path.is_dir():
        return None
    try:
        # Rekursiv alle Dateien — Backup-Skripte legen oft pro Datum
        # eigene Subdirs an (z.B. ``backups/2026/05/...``).
        candidates = [p for p in target_path.rglob("*") if p.is_file()]
    except Exception:
        logger.exception("system_health: backup directory listing failed for %s", target_path)
        return None
    if not candidates:
        return None

    youngest = max(candidates, key=lambda p: p.stat().st_mtime)
    mtime_ts = youngest.stat().st_mtime
    mtime = datetime.fromtimestamp(mtime_ts, tz=UTC)
    now = datetime.now(tz=UTC)
    age = now - mtime
    age_hours = round(age / timedelta(hours=1), 1)
    return {
        "path": str(youngest),
        "mtime": mtime,
        "age_hours": age_hours,
        "is_stale": age_hours > BACKUP_STALE_THRESHOLD_HOURS,
    }


def app_versions() -> dict:
    """Versionsinfos fuer Python, Django und die Anlaufstelle-App.

    ``app_version`` wird aus ``pyproject.toml`` gelesen; bei Fehlern
    Fallback auf den ``APP_VERSION`` ENV-Wert (analog Health-Endpoint)
    oder ``"unknown"``.
    """
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    django_version = django.get_version()
    app_version: str
    try:
        # ``settings.BASE_DIR`` zeigt auf ``src/``. ``pyproject.toml``
        # liegt im Repo-Root, also eine Ebene darueber.
        pyproject_path = Path(settings.BASE_DIR).parent / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)
        app_version = data.get("project", {}).get("version") or os.environ.get("APP_VERSION", "unknown")
    except Exception:
        logger.exception("system_health: pyproject.toml read failed")
        app_version = os.environ.get("APP_VERSION", "unknown")
    return {
        "python_version": python_version,
        "django_version": django_version,
        "app_version": app_version,
    }
