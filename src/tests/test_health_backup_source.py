"""Refs #2291: ``/health/`` und der Systembereich leiten den Backup-Zustand
aus DERSELBEN Quelle ab.

``core/views/health.py::_check_backup_age`` scannte das Backup-Verzeichnis
selbst (jüngste ``*.sql.gz.enc``-Datei), während
``core/services/system/health.py::last_backup_info`` die Grundlage der Anzeige
im Systembereich und der Compliance-Zeile ist. Zwei Ableitungen über dasselbe
Verzeichnis heißt: zwei Anzeigen, die einander widersprechen können — genau
das, was Betreiber im Fehlerfall in die Irre führt.

Der Guard pinnt die gemeinsame Ableitung, **nicht** eine bestimmte Heuristik:
was ``last_backup_info`` als letztes Backup gelten lässt, gilt auch für
``/health/`` — und umgekehrt. Deshalb sind die Urteile über Teilprodukte als
Deckungsgleichheit formuliert und nicht hart verdrahtet: der Guard gilt so
sowohl auf einem Stand, auf dem ``last_backup_info`` noch jede Datei zählt,
als auch auf dem Stand mit der Satz-Prüfung (Manifest + ``.hmac`` + die
referenzierten Artefakte) — dort ist das gemeinsame Urteil über ein
Teilprodukt „kein Backup".

Die Satz-Fixture unten folgt dem Muster der Backup-Satz-Tests
(``test_ops_backup_success_marker.py``): vollständig veröffentlichter Satz aus
signiertem Manifest, Dump und dessen Sidecar. Sie erfüllt damit **beide**
Lesarten von „Backup vorhanden" und ist auf beiden Ständen ein Treffer.
"""

import pytest

from core.services.system.health import last_backup_info
from core.views.health import _check_backup_age

pytestmark = pytest.mark.django_db

#: Pflichttabellen der Satzkennung (Vertrag aus ``scripts/ops/lib/backup-set.sh``).
_SET_TABLES = (
    "core_facility",
    "core_client",
    "core_event",
    "core_workitem",
    "core_auditlog",
    "core_attachment",
)

_TIMESTAMP = "2026-09-12_020000"


def _publish_complete_set(backup_dir, timestamp=_TIMESTAMP):
    """Ein vollständig veröffentlichter Backup-Satz (nur Datenbank, kein Medienteil).

    Signaturen und Hashes werden nicht geprüft (der HMAC-Schlüssel liegt nicht
    im web-Container) — die Sidecar-Dateien müssen nur existieren und dürfen
    nicht leer sein. Ohne Medien-Hash im Manifest gehört kein Medienarchiv zum
    Satz.
    """
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


class TestHealthBackupAgeSharesTheSystemDerivation:
    def test_complete_backup_is_seen_identically_by_both_paths(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        _publish_complete_set(tmp_path)

        info = last_backup_info()
        age = _check_backup_age()

        assert info is not None, "Ein vollständig veröffentlichter Satz muss als Backup gelten."
        assert age == info["age_hours"]

    def test_partial_artifact_is_judged_identically_by_both_paths(self, tmp_path, settings):
        """Teilprodukt (hier: ein leeres Manifest ohne Dump) — die beiden
        Anzeigen dürfen sich nicht widersprechen. WELCHES Urteil richtig ist,
        entscheidet ``last_backup_info``; ``/health/`` folgt ihm."""
        settings.BACKUP_DIR = tmp_path
        (tmp_path / "anlaufstelle_2026-01-01.manifest").write_text("", encoding="utf-8")

        info = last_backup_info()
        age = _check_backup_age()

        assert (info is None) == (age is None), (
            "/health/ und der Systembereich bewerten dasselbe Backup-Verzeichnis "
            f"unterschiedlich: last_backup_info={info!r}, _check_backup_age={age!r} (Refs #2291)"
        )
        if info is not None:
            assert age == info["age_hours"]

    def test_dump_without_manifest_is_judged_identically(self, tmp_path, settings):
        """Ein Dump ohne veröffentlichtes Manifest ist kein Satz.

        Auf dem Stand mit der Satz-Prüfung lautet das gemeinsame Urteil beider
        Pfade ``None``; geprüft wird hier die Deckungsgleichheit, damit der
        Guard kein Urteil vorwegnimmt, das die gemeinsame Funktion fällt.
        """
        settings.BACKUP_DIR = tmp_path
        daily = tmp_path / "daily"
        daily.mkdir()
        (daily / f"anlaufstelle_{_TIMESTAMP}.sql.gz.enc").write_bytes(b"dump")

        info = last_backup_info()
        age = _check_backup_age()

        assert (info is None) == (age is None), (
            "Dump ohne Manifest: /health/ und der Systembereich urteilen unterschiedlich "
            f"(last_backup_info={info!r}, _check_backup_age={age!r}, Refs #2291)"
        )
        if info is not None:
            assert age == info["age_hours"]

    def test_health_has_no_second_derivation(self, tmp_path, settings, monkeypatch):
        """Kernzusage: ``_check_backup_age`` rechnet nicht selbst. Sagt die
        gemeinsame Funktion „kein Backup", sagt ``/health/`` dasselbe — auch
        wenn im Verzeichnis ein vollständiger Satz liegt."""
        settings.BACKUP_DIR = tmp_path
        _publish_complete_set(tmp_path)
        monkeypatch.setattr("core.views.health.last_backup_info", lambda: None, raising=False)

        assert _check_backup_age() is None

    def test_missing_directory_stays_none(self, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path / "gibt-es-nicht"
        assert _check_backup_age() is None
