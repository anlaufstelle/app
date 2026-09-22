"""Refs #2291: ``/health/`` und der Systembereich leiten den Backup-Zustand
aus DERSELBEN Quelle ab.

``core/views/health.py::_check_backup_age`` scannte das Backup-Verzeichnis
selbst (jüngste ``*.sql.gz.enc``-Datei), während
``core/services/system/health.py::last_backup_info`` die Grundlage der Anzeige
im Systembereich und der Compliance-Zeile ist. Zwei Ableitungen über dasselbe
Verzeichnis heißt: zwei Anzeigen, die einander widersprechen können — genau
das, was Betreiber im Fehlerfall in die Irre führt.

Der Guard pinnt die gemeinsame Ableitung, nicht eine bestimmte Heuristik: was
``last_backup_info`` als letztes Backup gelten lässt, gilt auch für
``/health/`` — und umgekehrt.
"""

import pytest

from core.services.system.health import last_backup_info
from core.views.health import _check_backup_age

pytestmark = pytest.mark.django_db


class TestHealthBackupAgeSharesTheSystemDerivation:
    def test_complete_backup_is_seen_identically_by_both_paths(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc").write_bytes(b"x")

        info = last_backup_info()
        age = _check_backup_age()

        assert info is not None
        assert age == info["age_hours"]

    def test_partial_artifact_is_judged_identically_by_both_paths(self, tmp_path, settings):
        """Teilprodukt (hier: ein leeres Manifest ohne Dump) — die beiden
        Anzeigen dürfen sich nicht widersprechen. WELCHES Urteil richtig ist,
        entscheidet ``last_backup_info``; ``/health/`` folgt ihm."""
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "manifest.json").write_text("", encoding="utf-8")

        info = last_backup_info()
        age = _check_backup_age()

        assert (info is None) == (age is None), (
            "/health/ und der Systembereich bewerten dasselbe Backup-Verzeichnis "
            f"unterschiedlich: last_backup_info={info!r}, _check_backup_age={age!r} (Refs #2291)"
        )
        if info is not None:
            assert age == info["age_hours"]

    def test_health_has_no_second_derivation(self, tmp_path, settings, monkeypatch):
        """Kernzusage: ``_check_backup_age`` rechnet nicht selbst. Sagt die
        gemeinsame Funktion „kein Backup", sagt ``/health/`` dasselbe — auch
        wenn im Verzeichnis eine Dump-Datei liegt."""
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "anlaufstelle_2026-01-01.sql.gz.enc").write_bytes(b"x")
        monkeypatch.setattr("core.views.health.last_backup_info", lambda: None, raising=False)

        assert _check_backup_age() is None

    def test_missing_directory_stays_none(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path / "gibt-es-nicht"
        assert _check_backup_age() is None
