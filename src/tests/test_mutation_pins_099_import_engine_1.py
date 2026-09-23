"""Pin-Tests der Welle L (0.99) — Vollmutations-Survivors in
``core.services.import_engine`` (Batch ``import_engine-1``).

Refs https://github.com/anlaufstelle/app/issues/2174.
Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Vollmutations-Lauf auf main@7125fb79 (frischer Mutantenbaum), Batch
``import_engine-1`` (49 Survivor-Mutanten in ``core.services.import_engine``:
``_pending_import_upload_bytes`` 4, ``_binary_kind`` 8, ``x_store_import_upload``
2, ``available_targets`` 4, ``_coerce_time`` 13, ``_coerce_datetime`` 2,
``_import_client_row`` 2, ``_import_event_row`` 4, ``_classify_cell_error`` 1,
``_classify_validation_error`` 1, ``_lock_job`` 4, ``probe_import`` 1,
``commit_import`` 1, ``abort_import`` 1, ``cleanup_stale_import_jobs`` 1).
Andere Funktionen/Module dieser Datei gehören anderen Batches und werden hier
nicht angefasst; ``test_mutation_pins_099_forms_import_data.py`` bleibt
unveraendert.

Vier Achsen dieses Batches:

* **Kontingent-Zaehler** (``_pending_import_upload_bytes``): Schleifen-
  Kontrollfluss (``continue``/``break``) und Akkumulation ueber einer nach
  ``ImportJob.Meta.ordering = ["-created_at"]`` LEGAL geordneten Query —
  keine beobachtete, sondern eine vom Modell erzwungene Reihenfolge (Standard-
  block 9(3)). Ein Unterzaehlen hebelt das Speicherkontingent aus (#2129/N-5,
  Mehr-Mandanten-Fairness) — Prio 1.
* **Binaererkennung** (``_binary_kind``): Grenzwert der RIFF-Laengenpruefung
  und der ``errors=``-Parameter des Subtyp-Decodes sind Teil der
  #2128-Abwehr gegen Bild-/Office-Uploads im Import-Pfad — bindend Prio 1.
  Die reine Fallback-Beschriftung ("RIFF") bei leerem Subtyp
  ist dagegen nur Berichtstext (Prio 3).
* **Zeit-Koerzierung** (``_coerce_time``): der Fall-Through von
  ``time.fromisoformat`` auf die manuellen Formate (``"9:30"`` ohne
  fuehrende Null) und die Code-/Spalten-Angabe des resultierenden
  ``_CellError`` bei einer wirklich unparsebaren Uhrzeit (``"25:99"``).
* **Lock-Synchronisierung** (``_lock_job``): verlorene Felder nach dem
  Zeilen-Lock sind der Stale-Instanz-Fehlerfamilie zuzurechnen (frühere
  Befunde zu Import-Zuordnung ohne Re-Validierung) — Prio 1, inklusive des
  gecachten ``document_type``-Deskriptors: Django invalidiert dessen Cache
  zwar schon selbst, WENN sich ``document_type_id`` aendert
  (``ForeignKeyDeferredAttribute.__set__``) — aber nicht, wenn nur die
  referenzierte Zeile selbst sich aendert (parallele Bearbeitung desselben
  Dokumentationstyps) und ``document_type_id`` gleich bleibt; genau diese
  Luecke schliesst die ``pop()``-Zeile.

``.get(key, "")``-Default-Mutationen in ``_import_client_row``/
``_import_event_row`` sind ``equivalent``: der Rueckgabewert wird
unmittelbar per ``if not <wert>: raise`` geprueft, bevor er je gelesen wird —
``""``/``None``/impliziertes ``None`` sind an dieser toten Verzweigung
ununterscheidbar (verifiziert per Diff, Abschnitt 3 im Bericht). Ebenso
``available_targets``: das entfernte ``required=False`` entspricht dem
``@dataclass``-Feld-Default von ``ImportTarget.required`` — derselbe Wert,
kein Unterschied. ``cast(...)`` in den beiden ``_classify_*``-Helfern ist zur
Laufzeit die Identitaetsfunktion (``typing.cast`` ignoriert das erste
Argument). ``timezone.make_aware(parsed, None)`` entspricht laut
Django-Quelltext exakt ``timezone.make_aware(parsed, get_current_timezone())``
(``timezone=None`` loest intern denselben Default aus). ``probe_import``s
entferntes ``commit=False`` ist der Funktionsdefault von ``run_import``.
``abort_import``s ``update_fields``-Verlust ist equivalent (Save bleibt
INNERHALB der Lock-Klammer, ``updated_at`` steht in der Liste, kein
abweichender Zwischenzustand — alle drei Bedingungen dafuer negativ);
``commit_import``s Verlust dagegen ist ``test``, weil dieser
Save NACH dem ``with transaction.atomic()``-Block liegt (Lost-Update-Fenster).

Konventionen wie ``test_mutation_pins_099_audit_1.py``: Zusicherungen
vergleichen EXAKT (``==``), nie per Teilstring; ``pytestmark`` setzt
``django_db`` + ``_encryption_key`` modulweit (der Mutantenbaum hat keine
``.env`` und damit keinen ``ENCRYPTION_KEY``); Fixtures aus
``src/tests/conftest.py``, eigene lokale ``media_root``-Fixture wie in
``test_import_engine.py``. Jeder Test nennt seine Mutanten in der Docstring.
"""

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, DocumentType, ImportJob, Settings
from core.services import import_engine

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _set_created_at(job: ImportJob, when) -> None:
    """Erzwingt die ``Meta.ordering``-massgebliche Sortierspalte deterministisch."""
    ImportJob.objects.filter(pk=job.pk).update(created_at=when)


def _client_csv_upload(name: str = "import.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"Pseudonym\nIMP-01\n", content_type="text/csv")


def _oversized_csv_upload(marker: str, payload_bytes: int = 600_000) -> SimpleUploadedFile:
    row_bytes = 100_000
    rows = [f"{marker}-{index};" + "x" * row_bytes for index in range(payload_bytes // row_bytes)]
    content = ("Pseudonym;Notizen\n" + "\n".join(rows) + "\n").encode("utf-8")
    return SimpleUploadedFile(f"{marker}.csv", content, content_type="text/csv")


# ============================================================================
# _pending_import_upload_bytes — Kontingent-Zaehler (4 Mutanten)
# ============================================================================


class TestPendingImportUploadBytes:
    """Iterationsreihenfolge ist die vom Modell erzwungene ``-created_at``-Ordnung
    (``ImportJob.Meta.ordering``); kein ``.order_by()`` in der Produktions-
    funktion setzt sie zurueck. Die Tests erzwingen sie ueber explizite
    ``created_at``-Zeitstempel statt ueber eine beobachtete Einfuegefolge.
    """

    def test_no_open_runs_returns_zero(self, facility, media_root):
        """Pin: ``x__pending_import_upload_bytes__mutmut_14`` — ``total = 0`` ->
        ``total = 1``. Ohne offene Laeufe ist die Summe exakt ``0``, nicht ``1``.
        """
        assert import_engine._pending_import_upload_bytes(facility) == 0

    def test_skips_blank_storage_filenames_without_aborting_the_loop(self, facility, admin_user, media_root):
        """Pin: ``x__pending_import_upload_bytes__mutmut_16`` — ``continue`` ->
        ``break`` beim leeren Dateinamen.

        Lauf A (neuester Zeitstempel, zuerst iteriert) hat KEINEN Dateinamen und
        muss uebersprungen werden, nicht die Schleife beenden — sonst zaehlt
        Lauf B (aeltere, echte Datei) nie mit.
        """
        now = timezone.now()
        without_file = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="",
        )
        _set_created_at(without_file, now)
        ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="real.enc",
        )
        _set_created_at(
            ImportJob.objects.for_facility(facility).get(storage_filename="real.enc"), now - timedelta(minutes=1)
        )
        import_dir = import_engine._import_dir(facility)
        import_dir.mkdir(parents=True, exist_ok=True)
        (import_dir / "real.enc").write_bytes(b"x" * 4321)

        assert import_engine._pending_import_upload_bytes(facility) == 4321

    def test_skips_missing_files_without_aborting_the_loop(self, facility, admin_user, media_root):
        """Pin: ``x__pending_import_upload_bytes__mutmut_20`` — ``continue`` ->
        ``break`` bei ``FileNotFoundError``.

        Lauf A (neuester Zeitstempel) referenziert eine Datei, die NICHT mehr
        auf der Disk liegt; der fehlende Stat darf nicht die Schleife
        abbrechen, sonst zaehlt Lauf B (aeltere, echte Datei) nie mit.
        """
        now = timezone.now()
        missing = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="missing.enc",
        )
        _set_created_at(missing, now)
        present = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="present.enc",
        )
        _set_created_at(present, now - timedelta(minutes=1))
        import_dir = import_engine._import_dir(facility)
        import_dir.mkdir(parents=True, exist_ok=True)
        (import_dir / "present.enc").write_bytes(b"y" * 777)

        assert import_engine._pending_import_upload_bytes(facility) == 777

    def test_sums_every_open_runs_file_size(self, facility, admin_user, media_root):
        """Pin: ``x__pending_import_upload_bytes__mutmut_17`` — ``total += size``
        -> ``total = size`` (verliert die Akkumulation, behaelt nur die letzte
        verarbeitete Groesse).
        """
        now = timezone.now()
        newer = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="newer.enc",
        )
        _set_created_at(newer, now)
        older = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            storage_filename="older.enc",
        )
        _set_created_at(older, now - timedelta(minutes=1))
        import_dir = import_engine._import_dir(facility)
        import_dir.mkdir(parents=True, exist_ok=True)
        (import_dir / "newer.enc").write_bytes(b"a" * 111)
        (import_dir / "older.enc").write_bytes(b"b" * 222)

        assert import_engine._pending_import_upload_bytes(facility) == 333


# ============================================================================
# _binary_kind — #2128-Abwehr gegen Bild-/Office-Uploads (6 Mutanten killbar)
# ============================================================================


class TestBinaryKind:
    """RIFF-Zweig: Laengengrenze ``>= 12`` und ``errors=``-Parameter des
    Subtyp-Decodes sind Teil der Binaererkennung, keine Kosmetik.
    """

    def test_len_exactly_12_boundary_still_recognizes_riff(self):
        """Pin: ``x__binary_kind__mutmut_7``/``_8`` — ``len(raw) >= 12`` ->
        ``> 12``/``>= 13``.

        Bei GENAU 12 Bytes greift die RIFF-Erkennung noch (``"WAVE"``); beide
        Grenzwert-Mutanten schlagen an genau dieser Laenge fehl und fallen auf
        den NUL-Byte-Zweig zurueck (``"Binaerdatei"``).
        """
        raw = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE"
        assert len(raw) == 12
        assert import_engine._binary_kind(raw) == "WAVE"

    def test_replace_errors_handler_survives_invalid_ascii_bytes(self):
        """Pin: ``x__binary_kind__mutmut_13``/``_18``/``_19`` — ``errors="replace"``
        entfernt bzw. durch ungueltige Handler-Namen (``"XXreplaceXX"``/
        ``"REPLACE"``) ersetzt.

        Byte ``0xFF`` an Offset 8 ist in ASCII nicht dekodierbar. Original
        ersetzt es fail-safe (``"\\ufffdABC"``); alle drei Mutanten werfen
        ``UnicodeDecodeError``/``LookupError`` statt eines Ergebnisses.
        """
        raw = b"RIFF" + b"\x00\x00\x00\x00" + bytes([0xFF, 0x41, 0x42, 0x43])
        assert import_engine._binary_kind(raw) == "�ABC"

    def test_riff_fallback_label_when_subtype_is_blank(self):
        """Pin: ``x__binary_kind__mutmut_20``/``_21`` — Fallback ``"RIFF"`` ->
        ``"XXRIFFXX"``/``"riff"``.

        Vier Leerzeichen an Offset 8 dekodieren zu einem nach ``.strip()``
        leeren String — der ``or "RIFF"``-Fallback greift; beide Varianten
        aendern den Wortlaut.
        """
        raw = b"RIFF" + b"\x00\x00\x00\x00" + b"    "
        assert import_engine._binary_kind(raw) == "RIFF"


# ============================================================================
# store_import_upload — Audit-Akteur bei erschoepftem Kontingent (1 Mutant)
# ============================================================================


class TestStoreImportUploadQuotaAudit:
    def test_quota_exceeded_audit_entry_keeps_the_real_actor(self, facility, admin_user, media_root, settings):
        """Pin: ``x_store_import_upload__mutmut_29`` — ``enforce_storage_quota(...,
        user=user, ...)`` -> ``user=None``.

        Original: die ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile traegt den
        tatsaechlich hochladenden Akteur (Rechenschaftspflicht, Prio 1/Audit).
        Mutant: der Akteur geht verloren, die Zeile wird anonym.
        """
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
        Settings.objects.update_or_create(facility=facility, defaults={"max_storage_mb": 1})
        import_engine.store_import_upload(
            facility,
            admin_user,
            _oversized_csv_upload("QUOTA-FIRST"),
            target_type=ImportJob.TargetType.CLIENT,
        )

        with pytest.raises(ValidationError, match="Speicherkontingent der Einrichtung erschöpft"):
            import_engine.store_import_upload(
                facility,
                admin_user,
                _oversized_csv_upload("QUOTA-SECOND"),
                target_type=ImportJob.TargetType.CLIENT,
            )

        entry = AuditLog.objects.get(facility=facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert entry.user_id == admin_user.pk


# ============================================================================
# _coerce_time — Fall-Through auf die manuellen Formate (13 Mutanten)
# ============================================================================


class TestCoerceTime:
    def test_time_without_leading_zero_falls_back_to_the_manual_formats(self):
        """Pin: ``x__coerce_time__mutmut_1``/``_2``/``_3``/``_4``/``_5``/``_6``/``_7``.

        ``"9:30"`` (keine fuehrende Null) scheitert an ``time.fromisoformat``
        und muss ueber ``_parse_with_formats``/``_TIME_FORMATS`` aufgeloest
        werden. Jeder dieser Mutanten zerstoert genau diesen Fall-Through-Pfad:
        ``_1`` erzwingt ``fromisoformat(None)`` (TypeError statt Ergebnis),
        ``_2``/``_5``/``_6`` unterschlagen den ``_parse_with_formats``-Aufruf
        oder seine Argumente (``TypeError``/``None``), ``_3``/``_4`` ersetzen
        ``raw``/``_TIME_FORMATS`` durch ``None`` (``TypeError`` in der
        Schleife), ``_7`` invertiert ``if parsed is None`` und wirft einen
        ``_CellError`` statt das erfolgreich geparste Ergebnis zurueckzugeben.
        """
        assert import_engine._coerce_time("9:30", "Uhrzeit") == "09:30:00"

    def test_unparseable_time_raises_the_exact_code_and_column(self):
        """Pin: ``x__coerce_time__mutmut_8``/``_9``/``_10``/``_11``/``_12``/``_13``.

        ``"25:99"`` ist weder ISO noch ueber ``_TIME_FORMATS`` parsebar
        (``parsed`` bleibt ``None``) — Original wirft ``_CellError("invalid_time",
        "uhrzeit")``. Die Mutanten unterschlagen Code (``_8``/``_12``/``_13``)
        oder Spalte (``_9``/``_10``/``_11``) des Fehlers.
        """
        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._coerce_time("25:99", "uhrzeit")
        assert excinfo.value.code == "invalid_time"
        assert excinfo.value.column == "uhrzeit"


# ============================================================================
# _lock_job — Synchronisierung aus der gesperrten Zeile (4 Mutanten)
# ============================================================================


class TestLockJobSynchronisation:
    """Stale-Instanz-Familie: der Wizard laeuft ueber mehrere Requests, jede
    View haelt einen eigenen, moeglicherweise veralteten Snapshot. Verlorene
    Felder nach dem Lock reissen genau die Luecke wieder auf, die ``_lock_job``
    laut Docstring schliessen soll.
    """

    def test_lock_takes_original_filename_encrypted_from_the_locked_row(self, facility, admin_user):
        """Pin: ``x__lock_job__mutmut_9`` — ``job.original_filename_encrypted =
        locked.original_filename_encrypted`` -> ``= None``.

        Original: der veraltete Snapshot wird durch den verschluesselten
        DB-Stand ersetzt. Mutant: ``None`` — ein spaeterer ``commit_import``
        saehe keinen Originaldateinamen mehr, obwohl einer gespeichert ist.
        """
        job = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            status=ImportJob.Status.UPLOADED,
            storage_filename="abc.enc",
            original_filename_encrypted={"__encrypted__": True, "value": "geheim"},
        )
        stale = ImportJob.objects.get(pk=job.pk)
        stale.original_filename_encrypted = {"stale": True}

        with transaction.atomic():
            import_engine._lock_job(stale)

        assert stale.original_filename_encrypted == {"__encrypted__": True, "value": "geheim"}

    def test_lock_refreshes_the_cached_document_type_relation(self, facility, admin_user, doc_type_contact):
        """Pin: ``x__lock_job__mutmut_12``/``_15``/``_16`` —
        ``job._state.fields_cache.pop("document_type", None)`` ->
        ``pop(None, None)``/``pop("XXdocument_typeXX", None)``/
        ``pop("DOCUMENT_TYPE", None)``.

        Djangos eigener ``document_type_id``-Setter (``ForeignKeyDeferredAttribute
        .__set__``) verwirft den FK-Cache bereits von sich aus, SOBALD sich
        ``document_type_id`` aendert — die explizite ``pop()``-Zeile ist daher
        nur fuer den Fall entscheidend, dass die REFERENZIERTE Zeile sich
        aendert, waehrend ``document_type_id`` GLEICH bleibt (z. B. eine
        parallele Bearbeitung des Dokumentationstyps waehrend der laufende
        Import-Request noch die alte Instanz im Speicher haelt). Original: das
        gecachte Objekt wird verworfen, der naechste Zugriff liest den
        FRISCHEN Namen. Alle drei Mutanten treffen den Cache-Schluessel nicht
        (falscher Schluessel bzw. No-Op) — der Name bleibt veraltet.
        """
        job = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type_contact,
            status=ImportJob.Status.MAPPED,
            storage_filename="abc.enc",
        )
        stale = ImportJob.objects.get(pk=job.pk)
        assert stale.document_type.name == "Kontakt"  # Cache fuellen, document_type_id bleibt unveraendert

        DocumentType.objects.filter(pk=doc_type_contact.pk).update(name="Kontakt (umbenannt)")

        with transaction.atomic():
            import_engine._lock_job(stale)

        assert stale.document_type.name == "Kontakt (umbenannt)"


# ============================================================================
# commit_import — Feldliste NACH der Lock-Klammer (1 Mutant)
# ============================================================================


class TestCommitImportUpdateFields:
    """Der finale ``.save(update_fields=[...])`` in ``commit_import`` laeuft
    bewusst AUSSERHALB der ``with transaction.atomic(): _lock_job(job); ...``
    -Klammer (Kommentar im Quelltext: „Bewusst NACH dem atomic-Block"). In
    diesem Fenster kann eine andere Verarbeitung dieselbe Zeile aendern — ein
    Verlust der Feldliste (volles ``save()``) ueberschriebe das mit dem
    veralteten In-Memory-Stand (Lost Update).
    """

    def test_commit_does_not_clobber_a_concurrent_mapping_change(self, facility, admin_user, media_root, monkeypatch):
        """Pin: ``x_commit_import__mutmut_35`` — ``update_fields=[...]`` ->
        ``update_fields=None``.
        """
        job = import_engine.store_import_upload(
            facility, admin_user, _client_csv_upload(), target_type=ImportJob.TargetType.CLIENT
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, admin_user, commit=False)

        concurrent_mapping = {
            "columns": job.mapping_json.get("columns", {}),
            "delimiter": "SENTINEL-KONKURRIERENDE-AENDERUNG",
            "encoding": job.mapping_json.get("encoding", ""),
        }
        original_delete_import_file = import_engine.delete_import_file

        def _delete_and_simulate_concurrent_write(target_job):
            original_delete_import_file(target_job)
            ImportJob.objects.filter(pk=target_job.pk).update(mapping_json=concurrent_mapping)

        monkeypatch.setattr(import_engine, "delete_import_file", _delete_and_simulate_concurrent_write)

        import_engine.commit_import(job, admin_user)

        job.refresh_from_db()
        assert job.mapping_json == concurrent_mapping
