"""Follow-Up-Tests für Mutation-Survivors in
``core.services.file_vault.cleanup`` (Refs #1388).

``cleanup_orphan_storage_files`` ist der periodische Cron-Helfer, der
verwaiste ``.enc``-Dateien (ohne ``Attachment``-Record) löscht (#662).
Sicherheitsrelevant, weil er direkt Dateien im Vault ``unlink``t: ein Fehler
im Altersschutz oder im Zähler löscht entweder frische, noch nicht
registrierte Uploads (Datenverlust) oder meldet eine falsche Bilanz.

Der Voll-Lauf 2026-07-16 liess u. a. das ``cutoff``-Vorzeichen, die
Zähler-Arithmetik und den „Media-Root fehlt"-Frühausstieg als Survivor stehen.

Jeder Test benennt die konkret gekillte Mutation.
"""

from __future__ import annotations

import os
import time

import pytest

from core.services.file_vault.cleanup import cleanup_orphan_storage_files


def _write_enc(directory, name, *, age_seconds):
    """Lege eine ``.enc``-Datei mit definiertem Alter (mtime) an."""
    path = directory / name
    path.write_bytes(b"ciphertext")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


@pytest.mark.django_db
class TestCleanupOrphanStorageFiles:
    def test_deletes_old_orphans_keeps_recent(self, settings, tmp_path):
        """Killt gleich mehrere Survivors am Alters-Cutoff und Zähler:

        - ``mutmut_7``: ``cutoff = time.time() - min_age_seconds`` →
          ``+ min_age_seconds`` (Cutoff in die Zukunft → auch die FRISCHE,
          evtl. noch nicht registrierte Datei würde gelöscht → Datenverlust).
        - ``mutmut_18``: ``deleted = 0`` → ``deleted = 1`` (Bilanz-Offset).
        - ``mutmut_28``: ``deleted += 1`` → ``deleted += 2`` (Bilanz-Inflation).

        Aufbau: eine alte Waise (2 h) und eine frische Waise (jetzt) bei
        Default-Schwelle 1 h. Echt: nur die alte fällt, Rückgabe 1, frische
        bleibt. Mutant_7: beide fallen.
        """
        settings.MEDIA_ROOT = str(tmp_path)
        old = _write_enc(tmp_path, "old-orphan.enc", age_seconds=7200)
        recent = _write_enc(tmp_path, "recent-orphan.enc", age_seconds=0)

        deleted = cleanup_orphan_storage_files()  # Default min_age_seconds=3600

        assert deleted == 1  # mutmut_18 (→2) + mutmut_28 (→2)
        assert not old.exists()
        assert recent.exists()  # mutmut_7: frische Datei darf NICHT gelöscht werden

    def test_return_counts_every_deletion(self, settings, tmp_path):
        """Killt ``mutmut_26``: ``deleted += 1`` → ``deleted = 1``.

        Bei ZWEI gelöschten Waisen muss die Rückgabe 2 sein; ``deleted = 1``
        klemmt sie bei 1 fest. (Bei nur einer Löschung wären beide Varianten
        ununterscheidbar — daher zwei Waisen.)
        """
        settings.MEDIA_ROOT = str(tmp_path)
        _write_enc(tmp_path, "a-orphan.enc", age_seconds=7200)
        _write_enc(tmp_path, "b-orphan.enc", age_seconds=7200)

        assert cleanup_orphan_storage_files() == 2

    def test_missing_media_root_returns_zero(self, settings, tmp_path):
        """Killt ``mutmut_5``: ``return 0`` → ``return 1`` im
        ``if not media_root.exists()``-Zweig.

        Existiert das Media-Root nicht (frische Instanz/Fehlkonfiguration),
        darf nichts gescannt und 0 gemeldet werden — nicht fälschlich 1.
        """
        settings.MEDIA_ROOT = str(tmp_path / "does-not-exist")
        assert cleanup_orphan_storage_files() == 0
