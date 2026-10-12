"""Ein Backup-Zeitstempel in der Zukunft ist kein Messwert (Refs #2342).

``last_backup_info`` rechnet ``jetzt - mtime``. Liegt der Zeitstempel in der
Zukunft — falsch gestellte Systemuhr, Uhrensprung, per ``touch`` gesetzter
Zeitstempel, aus einem Archiv wiederhergestellte Datei —, wird das Alter
**negativ**. ``is_stale`` vergleicht gegen eine positive Schwelle und bleibt
damit ``False``, die Compliance-Karte meldete „Backup ist -87294h alt" und
stand auf **ok**: Eine Sicherung, deren Alter unbekannt ist, galt als die
frischeste ueberhaupt.

Gedeckt wird deshalb:

- Zukunfts-``mtime`` ⇒ eigener, **nicht gruener** Zustand in allen drei
  Ableitungen (Systembereich, Compliance-Karte, ``/health/``), Meldung ohne
  negative Zahl.
- Derselbe Vertrag in allen dreien — ``/health/`` rechnet nicht selbst.
- Die Zusage „kippt nie den Request-Cycle": verschwindet die juengste Datei
  zwischen Auswahl und Auswertung, gibt es kein Traceback.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from django.core.cache import cache

from core.services.compliance._types import ComplianceStatus
from core.services.compliance.backup import _backup_checks
from core.services.system.health import last_backup_info

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """/health/ caches the backup signal; a value cached by an earlier test would hide this file's sets."""
    cache.clear()
    yield
    cache.clear()


_SET_TABLES = (
    "core_facility",
    "core_client",
    "core_event",
    "core_workitem",
    "core_auditlog",
    "core_attachment",
)
_TIMESTAMP = "2026-09-12_020000"
#: Zehn Jahre — deutlich jenseits jeder Zeitzonen-/NTP-Drift.
_FUTURE_SECONDS = 10 * 365 * 24 * 3600


def _publish_complete_set(backup_dir: Path, timestamp: str = _TIMESTAMP) -> Path:
    daily = backup_dir / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    dump = daily / f"anlaufstelle_{timestamp}.sql.gz.enc"
    dump.write_bytes(b"dump")
    dump.with_name(f"{dump.name}.hmac").write_text("a" * 64 + "\n", encoding="utf-8")
    manifest = daily / f"anlaufstelle_{timestamp}.manifest"
    manifest.write_text(
        "".join(f"table_{table}=1\n" for table in _SET_TABLES)
        + f"backup_timestamp={timestamp}\n"
        + f"db_sha256={'b' * 64}\n",
        encoding="utf-8",
    )
    manifest.with_name(f"{manifest.name}.hmac").write_text("c" * 64 + "\n", encoding="utf-8")
    return manifest


def _set_future_mtime(manifest: Path) -> None:
    future = time.time() + _FUTURE_SECONDS
    os.utime(manifest, (future, future))


class TestFutureTimestampIsItsOwnState:
    def test_system_derivation_reports_the_future_instead_of_a_negative_age(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        _set_future_mtime(_publish_complete_set(tmp_path))

        info = last_backup_info()

        assert info is not None
        assert info["in_future"] is True, "Der Zustand muss benennbar sein, nicht nur ein Vorzeichen"
        assert info["age_hours"] is None, "Ein negatives Alter ist kein Messwert"
        assert info["is_stale"] is True, "Ein unbekanntes Alter darf nie als frisch gelten"

    def test_compliance_card_is_not_green_and_shows_no_negative_number(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        _set_future_mtime(_publish_complete_set(tmp_path))

        checks = [check for check in _backup_checks() if check.key == "backup_age"]

        assert len(checks) == 1
        check = checks[0]
        assert check.status is not ComplianceStatus.OK, "Ein unbekanntes Alter ist kein gruener Zustand"
        assert "-" not in check.message.replace("—", ""), f"Keine negative Zahl in der Meldung: {check.message!r}"
        assert "Zukunft" in check.message
        assert check.action_hint, "Der Betreiber braucht einen Handgriff (Systemuhr/Zeitstempel)"

    def test_health_endpoint_follows_the_same_contract(self, tmp_path, settings, client, django_user_model):
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_DETAIL_TOKEN = "token-fuer-den-detail-payload-mit-genug-entropie"
        _set_future_mtime(_publish_complete_set(tmp_path))

        response = client.get("/health/", headers={"X-Health-Token": settings.HEALTH_DETAIL_TOKEN})

        data = response.json()
        assert data["backup"] == "future", data
        assert data["last_backup_age_hours"] is None, "Kein negativer Messwert im Monitoring-Payload"
        assert data["status"] != "ok", "Ein unbekanntes Backup-Alter darf die Ampel nicht gruen lassen"

    def test_shared_derivation_has_no_age_for_the_future_case(self, tmp_path, settings):
        """Der gemeinsame Service liefert für die Zukunft keinen Messwert."""
        settings.BACKUP_DIR = tmp_path
        _set_future_mtime(_publish_complete_set(tmp_path))

        info = last_backup_info()

        assert info["age_hours"] is None


class TestNormalAgesAreUnchanged:
    """Regressionsschutz: der gemessene Normalfall bleibt, wie er war."""

    def test_fresh_backup_stays_ok(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        _publish_complete_set(tmp_path)

        info = last_backup_info()
        checks = [check for check in _backup_checks() if check.key == "backup_age"]

        assert info["in_future"] is False
        assert info["age_hours"] >= 0
        assert info["is_stale"] is False
        assert checks[0].status is ComplianceStatus.OK

    def test_old_backup_stays_stale(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        manifest = _publish_complete_set(tmp_path)
        old = time.time() - 10 * 24 * 3600
        os.utime(manifest, (old, old))

        info = last_backup_info()

        assert info["in_future"] is False
        assert info["is_stale"] is True
        assert info["age_hours"] > 0


class TestListingNeverBreaksTheRequestCycle:
    def test_vanishing_youngest_manifest_is_no_traceback(self, tmp_path, settings, monkeypatch):
        """Die Datei verschwindet zwischen Auswahl und Auswertung.

        Das Backup-Skript rotiert waehrend des laufenden Requests — genau das
        Fenster zwischen dem Filter und dem ``stat`` der juengsten Datei. Die
        Funktion sagt zu, den Request-Cycle nie zu kippen; ohne den Fix liegt
        ``stat``/``fromtimestamp`` ausserhalb des ``try``-Blocks.
        """
        settings.BACKUP_DIR = tmp_path
        manifest = _publish_complete_set(tmp_path)
        import core.services.system.health as health_module

        original = health_module._is_complete_backup_set

        def vanishing(candidate: Path) -> bool:
            result = original(candidate)
            if result:
                candidate.unlink(missing_ok=True)
            return result

        monkeypatch.setattr(health_module, "_is_complete_backup_set", vanishing)

        assert last_backup_info() is None, "Ein Rotationsfenster ist kein Serverfehler"
        assert not manifest.exists()
