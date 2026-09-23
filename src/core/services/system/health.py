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
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from core.services import ops_thresholds

logger = logging.getLogger(__name__)

# Refs #1872: ein Backup gilt als "stale", sobald die WARNING-Schwelle aus
# ``core.services.ops_thresholds`` gerissen ist (36 h = 1,5 x Tagesintervall
# des Host-Cron). Frueher stand die 36 hier als eigene Zahl und widersprach
# den 24/72 h des Compliance-Checks auf **derselben** Backup-Zeile.
BACKUP_STALE_THRESHOLD_HOURS = ops_thresholds.BACKUP_WARN_HOURS


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


# Pflichttabellen des Zeilenzahl-Manifests aus ``scripts/ops/backup.sh``.
# Ein Manifest ohne diese Zeilen beschreibt keinen auswertbaren Satz.
BACKUP_SET_TABLES = (
    "core_facility",
    "core_client",
    "core_event",
    "core_workitem",
    "core_auditlog",
    "core_attachment",
)

_SHA256_HEX_LENGTH = 64
MAX_BACKUP_MANIFEST_BYTES = 1024 * 1024
MAX_BACKUP_MANIFESTS = 256


def _parse_backup_manifest(manifest: Path) -> dict[str, str] | None:
    """Manifest als ``key=value``-Abbildung oder ``None`` bei Unlesbarkeit."""
    try:
        if manifest.stat().st_size > MAX_BACKUP_MANIFEST_BYTES:
            logger.warning(
                "system_health: backup manifest exceeds %d bytes: %s",
                MAX_BACKUP_MANIFEST_BYTES,
                manifest,
            )
            return None
        raw = manifest.read_text(encoding="utf-8")
    except Exception:
        logger.exception("system_health: backup manifest unreadable: %s", manifest)
        return None
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator:
            return None
        values[key] = value
    return values


def _backup_manifest_candidates(target_path: Path) -> Iterator[Path]:
    """Yield a bounded set of manifests from root and retention directories."""
    candidates = [*target_path.glob("*.manifest"), *target_path.glob("*/*.manifest")]
    candidates = [
        manifest
        for manifest in candidates
        if not any(part.startswith(".") for part in manifest.relative_to(target_path).parts)
    ]
    candidates.sort(key=lambda manifest: manifest.name, reverse=True)
    if len(candidates) > MAX_BACKUP_MANIFESTS:
        logger.warning(
            "system_health: backup manifest candidate limit %d reached in %s",
            MAX_BACKUP_MANIFESTS,
            target_path,
        )
    yield from candidates[:MAX_BACKUP_MANIFESTS]


def _is_complete_backup_set(manifest: Path) -> bool:
    """True, wenn das Manifest einen vollstaendig veroeffentlichten Satz beschreibt.

    Vertrag (Teilmenge von ``scripts/ops/lib/backup-set.sh``): nicht-leeres
    Manifest MIT vorhandenem ``.hmac``-Sidecar, Satzkennung, Dump-Hash und den
    Zeilenzahlen aller Pflichttabellen; die referenzierten Artefakte liegen
    daneben. Geprueft wird nur die EXISTENZ des Sidecars, nicht die Signatur,
    und keine Hashes: der HMAC-Schluessel liegt nicht im web-Container, und die
    Funktion laeuft im Request-Cycle und darf keine Gigabytes lesen. Signatur
    und Hashes pruefen ``backup.sh --verify`` und ``restore.sh``.
    """
    if not manifest.is_file() or manifest.stat().st_size == 0:
        return False
    if not manifest.with_name(f"{manifest.name}.hmac").is_file():
        return False
    values = _parse_backup_manifest(manifest)
    if values is None:
        return False
    if any(f"table_{table}" not in values for table in BACKUP_SET_TABLES):
        return False
    timestamp = values.get("backup_timestamp", "")
    db_sha256 = values.get("db_sha256", "")
    if not timestamp or len(db_sha256) != _SHA256_HEX_LENGTH:
        return False

    base = manifest.with_suffix("")
    required = [base.with_name(f"{base.name}.sql.gz.enc")]
    if values.get("media_sha256"):
        required.append(base.with_name(f"{base.name}_media.tar.gz.enc"))
    for artifact in required:
        if not artifact.is_file() or artifact.stat().st_size == 0:
            return False
        sidecar = artifact.with_name(f"{artifact.name}.hmac")
        if not sidecar.is_file() or sidecar.stat().st_size == 0:
            return False
    if values.get("hwm_sha256"):
        anchor = manifest.with_name(f"audit_hwm-{timestamp}.json")
        if not anchor.is_file() or anchor.stat().st_size == 0:
            return False
    return True


def last_backup_info(backup_dir: str | os.PathLike | None = None) -> dict | None:
    """Liefert Infos zum juengsten VOLLSTAENDIGEN Backup-Satz oder ``None``.

    Refs #2291: Frueher galt die juengste beliebige Datei im Backup-Verzeichnis
    als Sicherung — auch das leere Manifest, das ein gescheiterter Lauf
    hinterliess. Bewertet wird jetzt ausschliesslich ein veroeffentlichter Satz:
    signiertes Manifest plus die darin referenzierten Artefakte. Gibt es keinen,
    ist der Zustand ``None`` (= fehlend/unbekannt), nie „gesund".

    Returns dict mit ``path`` (Manifestpfad), ``mtime`` (timezone-aware
    datetime), ``age_hours`` (float oder ``None``), ``in_future`` (bool) und
    ``is_stale`` (bool).

    Refs #2342: Ein Zeitstempel in der **Zukunft** (falsch gestellte Systemuhr,
    Uhrensprung, per ``touch`` oder aus einem Archiv gesetzter Zeitstempel)
    ergaebe ein negatives Alter. ``is_stale`` vergleicht gegen eine positive
    Schwelle und bliebe damit ``False`` — eine Sicherung mit unbekanntem Alter
    galt als die frischeste ueberhaupt. Ein negatives Alter ist kein Messwert:
    ``age_hours`` ist dann ``None``, ``in_future`` ist ``True`` und
    ``is_stale`` ist ``True`` (nie gruen). Die drei Anzeigen — Systembereich,
    Compliance-Karte und ``/health/`` — lesen diesen einen Vertrag.
    """
    target = backup_dir if backup_dir is not None else getattr(settings, "BACKUP_DIR", None)
    if not target:
        return None
    target_path = Path(target)
    if not target_path.exists() or not target_path.is_dir():
        return None
    try:
        # Backup-Skripte legen pro Retention-Stufe genau eine Unterordner-Ebene
        # an. Arbeitsverzeichnisse laufender/abgebrochener Laeufe beginnen mit
        # einem Punkt und zaehlen nie als Satz. Anzahl und Tiefe bleiben
        # begrenzt, weil die Auswertung im Request-Cycle laeuft.
        candidates = [
            manifest for manifest in _backup_manifest_candidates(target_path) if _is_complete_backup_set(manifest)
        ]
    except Exception:
        logger.exception("system_health: backup directory listing failed for %s", target_path)
        return None
    if not candidates:
        return None

    try:
        # Die Auswahl der juengsten Datei und ihre Auswertung gehoeren in
        # denselben Schutz wie das Listing: zwischen Filter und ``stat`` kann
        # die Rotation des Backup-Skripts die Datei entfernen. Diese Funktion
        # laeuft im Request-Cycle und darf ihn nie kippen (Refs #2342).
        youngest = max(candidates, key=lambda p: p.stat().st_mtime)
        mtime_ts = youngest.stat().st_mtime
        mtime = datetime.fromtimestamp(mtime_ts, tz=UTC)
    except Exception:
        logger.exception("system_health: backup timestamp evaluation failed in %s", target_path)
        return None
    now = datetime.now(tz=UTC)
    age = now - mtime
    age_hours = round(age / timedelta(hours=1), 1)
    if age_hours < 0:
        return {
            "path": str(youngest),
            "mtime": mtime,
            "age_hours": None,
            "in_future": True,
            "is_stale": True,
        }
    return {
        "path": str(youngest),
        "mtime": mtime,
        "age_hours": age_hours,
        "in_future": False,
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
        # liegt im Repo-Root, also eine Ebene darueber (im Container:
        # ``/pyproject.toml``, seit Refs #1504 mit ins finale Image kopiert).
        pyproject_path = Path(settings.BASE_DIR).parent / "pyproject.toml"
        with pyproject_path.open("rb") as fh:
            data = tomllib.load(fh)
        app_version = data.get("project", {}).get("version") or os.environ.get("APP_VERSION", "unknown")
    except FileNotFoundError:
        # Refs #1504: legitimer Fallback-Pfad (z.B. Deploys auf einem
        # Image-Stand vor der pyproject-COPY) -- kein Fehlerzustand, daher
        # kein ERROR-Log-Rauschen bei jedem Health-Call.
        logger.info("system_health: pyproject.toml not found, falling back to APP_VERSION env")
        app_version = os.environ.get("APP_VERSION", "unknown")
    except Exception:
        logger.exception("system_health: pyproject.toml read failed")
        app_version = os.environ.get("APP_VERSION", "unknown")
    return {
        "python_version": python_version,
        "django_version": django_version,
        "app_version": app_version,
    }
