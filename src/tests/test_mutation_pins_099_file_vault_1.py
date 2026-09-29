"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — Vollmutation-Survivor-Triage
fuer den Batch ``file_vault-1``: ``core.services.file_vault.storage``,
``core.services.file_vault.cleanup`` und ``core.services.file_vault.rotation``
(verschluesselter Dateispeicher, Waisen-Bereinigung, Schluesselrotation).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-file_vault-1.json`` (Triage
``triage-099-file_vault-1.json``). 68 Survivor, davon 57 als ``test``
klassifiziert und hier gepinnt (24 Pin-Tests, mehrere Mutanten je Pin;
11 ``equivalent`` sind in der Triage-Datei begruendet, kein Pin; 0 ``skip`` —
nach der Zweitreview-Nacharbeit gilt fuer Logging-Mutanten: Meldungs-/
Argument-/Textverlust ist ``test`` (exakte ``caplog``-Assertion auf
``record.getMessage()``), nur reine XX-Marker-/Gross-Klein-Varianten waeren
``skip`` gewesen — hier toetet der Exakt-Vergleich sie ohnehin mit).

``_encryption_key`` liegt modulweit an (Lehre 112): der Mutantenbaum hat
keine ``.env`` und damit keinen ``ENCRYPTION_KEY``.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from core.models import Client, DocumentType, Event, FieldTemplate, ShiftHandoverNote
from core.models.mfa import EncryptedTOTPDevice
from core.services.file_vault import (
    StagedUpload,
    cleanup_orphan_storage_files,
    delete_attachment_file,
    encrypt_field,
    get_attachment_path,
    get_original_filename,
    prepare_encrypted_upload,
    safe_decrypt,
    soft_delete_attachment_chain,
    store_encrypted_file,
)
from core.services.file_vault import cleanup as cleanup_module
from core.services.file_vault import rotation as rotation_module
from core.services.file_vault.rotation import (
    _reencrypt_data_json,
    _reencrypt_events,
    _reencrypt_shift_handover_notes,
    _rewrap_one_totp_device,
    _rotate_dynamic_json_model,
)
from core.services.file_vault.storage import _commit_staged_upload

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

# Echte PDF-Magic-Bytes (Refs #610): ``store_encrypted_file``/``prepare_encrypted_upload``
# lehnen Uploads ab, deren Inhalt nicht zum deklarierten Typ passt.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _make_event(facility):
    """Minimale Event-Owner-Instanz (Pflichtfeld ``document_type``)."""
    dt = DocumentType.objects.create(facility=facility, name="Doku")
    return Event.objects.create(facility=facility, document_type=dt, occurred_at=timezone.now())


def _handover_window(shift_date):
    """Pflicht-Fensterspalten einer Schichtnotiz (Refs #2042, E-01) — fuer die
    Rotation ohne fachliche Bedeutung, aber inzwischen nicht mehr
    nullbar (analog ``test_reencrypt_fields.py::_handover_window``)."""
    return {
        "window_start": timezone.make_aware(datetime.combine(shift_date, dt_time.min)),
        "window_end": timezone.make_aware(datetime.combine(shift_date, dt_time.max)),
        "editable_until": timezone.make_aware(datetime.combine(shift_date + timedelta(days=1), dt_time.max)),
    }


def _make_note(facility, shift_date, note):
    """Schichtnotiz mit gueltigem Pflicht-Fenster; ``shift_date`` steuert
    ueber ``Meta.ordering = ["-shift_date", "-created_at"]`` die
    Iterationsreihenfolge in ``_reencrypt_shift_handover_notes``."""
    return ShiftHandoverNote.objects.create(
        facility=facility,
        shift_date=shift_date,
        note=note,
        **_handover_window(shift_date),
    )


def _write_enc(directory, name, *, age_seconds):
    """Lege eine ``.enc``-Datei mit definiertem Alter (mtime) an."""
    path = directory / name
    path.write_bytes(b"ciphertext")
    mtime = time.time() - age_seconds
    os.utime(path, (mtime, mtime))
    return path


class _FrozenTime:
    """Ersetzt das Modul-Attribut ``time`` in ``cleanup.py`` durch eine feste Uhr
    (Regel: eine beobachtete Reihenfolge/ein beobachteter Zeitpunkt ohne
    erzwungene Kontrolle ist kein Aequivalenzbeweis — hier wird die Uhr
    deshalb komplett eingefroren statt auf Systemzeit-Toleranzen zu hoffen)."""

    def __init__(self, value):
        self._value = value

    def time(self):
        return self._value


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — prepare_encrypted_upload
# ---------------------------------------------------------------------------


def test_prepare_encrypted_upload_verschluckt_den_original_fehler_bei_verschluesselung_nicht(
    monkeypatch, settings, tmp_path, facility, staff_user
):
    """Pins ``xǁprepare_encrypted_uploadǁ__mutmut_84``/``_85``: die Aufraeum-Zeile
    im ``except``-Block (``output_path.unlink(missing_ok=True)``) faengt eine
    NICHT existierende Datei ab, wenn ``encrypt_file`` VOR jedem Disk-Write
    scheitert. Mit ``missing_ok=None``/``False`` wirft dieses Aufraeumen selbst
    ein ``FileNotFoundError`` und verschluckt die eigentliche Verschluesselungs-
    Fehlermeldung — der Aufrufer saehe die falsche Ausnahme."""
    settings.MEDIA_ROOT = str(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("boom-verschluesselung-marker")

    monkeypatch.setattr("core.services.file_vault.storage.encrypt_file", boom)
    uploaded = SimpleUploadedFile("a.pdf", PDF_HEADER, content_type="application/pdf")

    with pytest.raises(RuntimeError, match="boom-verschluesselung-marker"):
        prepare_encrypted_upload(facility, uploaded, staff_user)


def test_prepare_encrypted_upload_staged_defaults_bleiben_leer_statt_platzhalter(
    monkeypatch, settings, tmp_path, facility, staff_user
):
    """Pins ``xǁprepare_encrypted_uploadǁ__mutmut_99`` (``detected_mime or ""``
    -> ``or "XXXX"``), ``_101``/``_102`` (``content_type or
    "application/octet-stream"`` -> XX-Marker/Grossschreibung): liefert
    ``enforce_magic_bytes`` (hier direkt kontrolliert) einen leeren erkannten
    MIME und der Upload keinen ``content_type``, muessen die Staging-Defaults
    GENAU leer bzw. GENAU der dokumentierte Fallback sein — kein Platzhalter-
    text landet in den Feldern, die spaeter als Attachment-Zeile persistiert
    werden."""
    settings.MEDIA_ROOT = str(tmp_path)
    monkeypatch.setattr("core.services.file_vault.storage.enforce_magic_bytes", lambda *a, **k: "")
    uploaded = SimpleUploadedFile("leer.pdf", PDF_HEADER, content_type="")

    staged = prepare_encrypted_upload(facility, uploaded, staff_user)

    assert staged.detected_mime == ""
    assert staged.content_type == "application/octet-stream"


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — _commit_staged_upload (privat)
# ---------------------------------------------------------------------------


def test_commit_staged_upload_auditiert_per_default_ohne_expliziten_aufruf(monkeypatch, tmp_path, facility, staff_user):
    """Pins ``xǁ_commit_staged_uploadǁ__mutmut_1``: der Default von ``audit``
    kippt von ``True`` auf ``False``. Der EINZIGE Aufrufer
    (``commit_staged_upload``) gibt ``audit`` zwar immer explizit durch, ein
    direkter Aufruf der privaten Funktion (wie hier) haengt jedoch am
    dokumentierten Default — der muss die Audit-Zeile schreiben."""
    event = _make_event(facility)
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    staged = StagedUpload(
        storage_name="phantom-audit.enc",
        output_path=tmp_path / "phantom-audit.enc",
        detected_mime="application/pdf",
        file_size=3,
        original_name="a.pdf",
        content_type="application/pdf",
    )
    captured = {"called": False}

    def fake_audit_attachment_upload(owner, field_template, attachment, staged, user, *, mode):
        captured["called"] = True

    monkeypatch.setattr("core.services.file_vault.audit.audit_attachment_upload", fake_audit_attachment_upload)

    _commit_staged_upload(event, ft, staged, staff_user)  # kein audit= angegeben -> Default

    assert captured["called"] is True


def test_commit_staged_upload_detected_mime_bleibt_leer_statt_platzhalter(tmp_path, facility, staff_user):
    """Pins ``xǁ_commit_staged_uploadǁ__mutmut_44`` (``staged.detected_mime or
    ""`` -> ``or "XXXX"``): ein leerer erkannter MIME darf NICHT als
    Platzhaltertext in der persistierten Attachment-Zeile landen."""
    event = _make_event(facility)
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    staged = StagedUpload(
        storage_name="phantom-mime.enc",
        output_path=tmp_path / "phantom-mime.enc",
        detected_mime="",
        file_size=3,
        original_name="a.pdf",
        content_type="application/pdf",
    )

    attachment = _commit_staged_upload(event, ft, staged, staff_user, audit=False)

    assert attachment.detected_mime == ""


def test_commit_staged_upload_verschluckt_die_urspruengliche_fehlermeldung_nicht(tmp_path, facility, staff_user):
    """Pins ``xǁ_commit_staged_uploadǁ__mutmut_70``/``_71``: dieselbe
    Aufraeum-Falle wie bei ``prepare_encrypted_upload`` (s.o.), hier im
    ``except``-Block nach einem DB-Fehler. Ein unzulaessiger Owner (kein
    Event/Client/Case) loest den fail-loud-``ValueError`` von
    ``owner_field_name`` aus, BEVOR die gestagte Datei je existiert hat —
    ``missing_ok=None``/``False`` ersetzt diese aussagekraeftige Meldung durch
    ein nichtssagendes ``FileNotFoundError``."""
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    staged = StagedUpload(
        storage_name="phantom-exc.enc",
        output_path=tmp_path / "phantom-exc.enc",
        detected_mime="application/pdf",
        file_size=3,
        original_name="a.pdf",
        content_type="application/pdf",
    )

    with pytest.raises(ValueError, match="Kein zulaessiger Attachment-Owner"):
        _commit_staged_upload(facility, ft, staged, staff_user)  # facility ist kein gueltiger Owner


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — store_encrypted_file
# ---------------------------------------------------------------------------


def test_store_encrypted_file_meldet_den_exakten_facility_mismatch_text(facility, second_facility, staff_user):
    """Pins ``xǁstore_encrypted_fileǁ__mutmut_13`` (XX-Marker auf dem Meldungs-
    Praefix), ``_16``/``_21``/``_22`` (die Meldung zeigt ``None`` statt der
    echten ``owner.facility_id`` — ``getattr(None, ...)`` bzw. ein falscher
    Attributname): die volle deutsche Fehlermeldung inkl. der echten
    Facility-ID muss WORTGENAU erscheinen, sonst verschwindet die einzige
    Diagnoseinformation fuer einen Facility-Scope-Verstoss."""
    dt = DocumentType.objects.create(facility=second_facility, name="Fremddoku")
    from django.utils import timezone

    owner = Event.objects.create(facility=second_facility, document_type=dt, occurred_at=timezone.now())
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    uploaded = SimpleUploadedFile("x.pdf", b"irrelevant", content_type="application/pdf")

    with pytest.raises(ValueError) as excinfo:
        store_encrypted_file(facility, uploaded, ft, owner, staff_user)

    assert str(excinfo.value) == (
        "Facility des Owners weicht von der uebergebenen Facility ab "
        f"(owner.facility_id={owner.facility_id!r}, facility={facility.pk!r})"
    )


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — soft_delete_attachment_chain
# ---------------------------------------------------------------------------


def test_soft_delete_attachment_chain_audit_feld_bleibt_leer_ohne_platzhalter(
    monkeypatch, settings, tmp_path, facility, staff_user
):
    """Pins ``xǁsoft_delete_attachment_chainǁ__mutmut_32`` (``field or ""`` ->
    ``or "XXXX"``): ein leeres ``field`` (explizit uebergeben, nicht
    hergeleitet) darf NICHT als Platzhaltertext in der Loesch-Audit-Zeile
    landen."""
    settings.MEDIA_ROOT = str(tmp_path)
    event = _make_event(facility)
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    uploaded = SimpleUploadedFile("a.pdf", PDF_HEADER, content_type="application/pdf")
    attachment = store_encrypted_file(facility, uploaded, ft, event, staff_user, audit=False)

    captured = {}

    def fake_audit_attachment_delete(owner, user, *, entry_id, field, versions):
        captured["field"] = field

    monkeypatch.setattr("core.services.file_vault.audit.audit_attachment_delete", fake_audit_attachment_delete)

    updated = soft_delete_attachment_chain(event, attachment.entry_id, staff_user, field="")

    assert updated == 1
    assert captured["field"] == ""


# ---------------------------------------------------------------------------
# core.services.file_vault.storage — get_original_filename
# ---------------------------------------------------------------------------


def test_get_original_filename_faellt_bei_kaputtem_chiffrat_auf_download_zurueck():
    """Pins ``xǁget_original_filenameǁ__mutmut_2`` (``default="download"`` ->
    ``default=None``), ``_4`` (Default-Argument komplett entfernt -> nutzt
    ``safe_decrypt``s eigenen Default ``"[verschlüsselt]"``), ``_5``/``_6``
    (XX-Marker/Grossschreibung): bei einem nicht entschluesselbaren Marker
    muss GENAU ``"download"`` zurueckkommen — der dokumentierte
    Download-Dateiname-Fallback, kein anderer Platzhalter."""
    attachment = _AttachmentLike({"__encrypted__": True, "value": "invalid-token"})

    assert get_original_filename(attachment) == "download"


class _AttachmentLike:
    """Minimaler Stellvertreter: ``get_original_filename`` liest nur dieses
    eine Attribut, ein echtes DB-Attachment ist dafuer nicht noetig."""

    def __init__(self, original_filename_encrypted):
        self.original_filename_encrypted = original_filename_encrypted


class _AttachmentPathLike:
    """Minimaler Stellvertreter: ``get_attachment_path``/``delete_attachment_file``
    lesen nur ``facility`` und ``storage_filename``."""

    def __init__(self, facility, storage_filename):
        self.facility = facility
        self.storage_filename = storage_filename


_STORAGE_LOGGER = "core.services.file_vault.storage"


def test_delete_attachment_file_meldet_die_exakte_warnung_bei_fehlgeschlagenem_unlink(
    caplog, settings, tmp_path, facility
):
    """Pins ``xǁdelete_attachment_fileǁ__mutmut_5`` (``logger.warning("...: %s",
    path)`` -> ``logger.warning(None, path)``, kompletter Meldungsverlust),
    ``_6`` (``%s``-Argument -> ``None``), ``_7`` (Formatstring entfernt, nur
    der Pfad bleibt), ``_8`` (Pfad-Argument entfernt), ``_9``/``_10``/``_11``
    (XX-Marker/Klein-/Grossschreibung inkl. ungueltigem ``%S``): ALLE
    veraendern die tatsaechlich geloggte Warnung — die exakte Meldung inkl.
    Pfad ist der einzige Hinweis im Betrieb, dass eine Attachment-Datei nicht
    entfernt werden konnte."""
    settings.MEDIA_ROOT = str(tmp_path)
    attachment = _AttachmentPathLike(facility, "ist-ein-verzeichnis.enc")
    path = get_attachment_path(attachment)
    path.mkdir(parents=True)  # ein Verzeichnis statt einer Datei -> unlink() scheitert mit OSError

    with caplog.at_level(logging.WARNING, logger=_STORAGE_LOGGER):
        delete_attachment_file(attachment)

    records = [r for r in caplog.records if r.name == _STORAGE_LOGGER]
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == f"Failed to delete attachment file: {path}"


def test_delete_attachment_file_loggt_keine_warnung_wenn_die_datei_schon_fehlt(caplog, settings, tmp_path, facility):
    """Pins ``xǁdelete_attachment_fileǁ__mutmut_3``/``_4`` (``missing_ok=True``
    -> ``None``/``False``): fehlt die Datei bereits (haeufiger Normalfall bei
    doppeltem Cleanup), bleibt das Original lautlos — die Mutanten werfen
    ``FileNotFoundError``, fangen ihn als ``OSError`` und loggen eine
    (unnoetige) Warnung."""
    settings.MEDIA_ROOT = str(tmp_path)
    attachment = _AttachmentPathLike(facility, "existiert-nie.enc")

    with caplog.at_level(logging.WARNING, logger=_STORAGE_LOGGER):
        delete_attachment_file(attachment)

    assert [r for r in caplog.records if r.name == _STORAGE_LOGGER] == []


# ---------------------------------------------------------------------------
# core.services.file_vault.cleanup — cleanup_orphan_storage_files
# ---------------------------------------------------------------------------


def test_cleanup_orphan_default_schwelle_bleibt_bei_3600_sekunden(monkeypatch, settings, tmp_path):
    """Pins ``xǁcleanup_orphan_storage_filesǁ__mutmut_1`` (Default
    ``min_age_seconds: int = 3600`` -> ``3601``): eine ``.enc``-Waise, die
    genau 3600,5 Sekunden alt ist, muss bei der DOKUMENTIERTEN
    Default-Schwelle geloescht werden — bei 3601 Sekunden Schwelle (noch)
    nicht."""
    settings.MEDIA_ROOT = str(tmp_path)
    fixed_now = 2_000_000_000.0
    monkeypatch.setattr(cleanup_module, "time", _FrozenTime(fixed_now))
    path = tmp_path / "knapp-ueber-der-stunde.enc"
    path.write_bytes(b"x")
    mtime = fixed_now - 3600.5
    os.utime(path, (mtime, mtime))

    deleted = cleanup_orphan_storage_files()  # kein min_age_seconds -> Default

    assert deleted == 1
    assert not path.exists()


def test_cleanup_orphan_datei_exakt_auf_der_schwelle_bleibt_verschont(monkeypatch, settings, tmp_path):
    """Pins ``xǁcleanup_orphan_storage_filesǁ__mutmut_36`` (``st_mtime >=
    cutoff`` -> ``> cutoff``): eine Datei, deren Alter EXAKT der Schwelle
    entspricht, muss verschont bleiben (``>=`` schuetzt Race-Condition-Faelle
    an der Grenze) — mit ``>`` faellt sie der Loeschung zum Opfer."""
    settings.MEDIA_ROOT = str(tmp_path)
    fixed_now = 2_000_000_000.0
    monkeypatch.setattr(cleanup_module, "time", _FrozenTime(fixed_now))
    path = tmp_path / "exakt-auf-der-schwelle.enc"
    path.write_bytes(b"x")
    min_age = 1000
    mtime = fixed_now - min_age  # exakt auf der Schwelle
    os.utime(path, (mtime, mtime))

    deleted = cleanup_orphan_storage_files(min_age_seconds=min_age)

    assert deleted == 0
    assert path.exists()


def test_cleanup_orphan_loescht_niemals_eine_bekannte_attachment_datei(settings, tmp_path, facility, staff_user):
    """Pins ``xǁcleanup_orphan_storage_filesǁ__mutmut_11``/``_16``
    (``values_list("storage_filename", flat=True)`` -> ``flat=None``/``False``
    liefert 1-Tupel statt Strings), ``_12`` (Feldname ``"storage_filename"``
    entfernt -> defaultet auf die Attachment-PK-Spalte) und ``_17``
    (``known |= {...}`` -> ``known = {...}`` verliert die Attachment-Namen
    komplett): in jedem Fall landet der Dateiname einer ECHTEN, referenzierten
    Attachment-Datei nicht mehr in ``known`` und wird als Waise geloescht —
    Datenverlust an einer aktiven Datei."""
    settings.MEDIA_ROOT = str(tmp_path)
    event = _make_event(facility)
    ft = FieldTemplate.objects.create(facility=facility, name="Scan", field_type=FieldTemplate.FieldType.FILE)
    uploaded = SimpleUploadedFile("real.pdf", PDF_HEADER, content_type="application/pdf")
    attachment = store_encrypted_file(facility, uploaded, ft, event, staff_user, audit=False)

    matches = list(tmp_path.rglob(attachment.storage_filename))
    assert len(matches) == 1, "die frisch gespeicherte Attachment-Datei muss unter MEDIA_ROOT liegen"
    path = matches[0]
    old_mtime = time.time() - 7200
    os.utime(path, (old_mtime, old_mtime))

    deleted = cleanup_orphan_storage_files()

    assert deleted == 0
    assert path.exists()


_CLEANUP_LOGGER = "core.services.file_vault.cleanup"


def test_cleanup_orphan_meldet_den_exakten_erfolg_beim_loeschen_einer_waise(caplog, settings, tmp_path):
    """Pins ``xǁcleanup_orphan_storage_filesǁ__mutmut_42`` (``%s``-Argument ->
    ``None``), ``_43`` (Formatstring entfernt, nur der Pfad bleibt), ``_44``
    (Pfad-Argument entfernt) und ``_45`` (XX-Marker): die geloggte
    Erfolgsmeldung ist der einzige Betriebs-Nachweis, WELCHE Datei der
    periodische Cron-Sweep entfernt hat."""
    settings.MEDIA_ROOT = str(tmp_path)
    path = _write_enc(tmp_path, "alte-waise.enc", age_seconds=7200)

    with caplog.at_level(logging.INFO, logger=_CLEANUP_LOGGER):
        deleted = cleanup_orphan_storage_files()

    assert deleted == 1
    records = [r for r in caplog.records if r.name == _CLEANUP_LOGGER and r.levelno == logging.INFO]
    assert len(records) == 1
    assert records[0].getMessage() == f"cleanup_orphan_storage_files removed orphan: {path}"


def test_cleanup_orphan_meldet_die_exakte_warnung_bei_fehlgeschlagenem_unlink(caplog, monkeypatch, settings, tmp_path):
    """Pins ``xǁcleanup_orphan_storage_filesǁ__mutmut_47`` (Meldung komplett
    -> ``None``), ``_48``/``_49`` (je ein ``%s``-Argument -> ``None``),
    ``_50`` (Formatstring entfernt), ``_51``/``_52`` (je ein Argument
    entfernt) und ``_53``/``_54`` (XX-Marker/Grossschreibung inkl.
    ungueltigem ``%S``): jede Variante veraendert die geloggte
    Fehler-Diagnose (welche Datei, welcher OS-Fehler) — der einzige Hinweis
    im Betrieb auf eine liegen gebliebene Waise."""
    settings.MEDIA_ROOT = str(tmp_path)
    path = tmp_path / "kaputte-waise.enc"
    path.write_bytes(b"ciphertext")
    old_mtime = time.time() - 7200
    os.utime(path, (old_mtime, old_mtime))
    original_unlink = Path.unlink

    def fail_target_unlink(candidate, *args, **kwargs):
        if candidate == path:
            raise OSError("simulierter unlink-Fehler")
        return original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with caplog.at_level(logging.WARNING, logger=_CLEANUP_LOGGER):
        deleted = cleanup_orphan_storage_files()

    assert deleted == 0
    records = [r for r in caplog.records if r.name == _CLEANUP_LOGGER and r.levelno == logging.WARNING]
    assert len(records) == 1
    prefix = f"cleanup_orphan_storage_files: {path} -> "
    message = records[0].getMessage()
    assert message.startswith(prefix)
    suffix = message[len(prefix) :]
    assert suffix not in ("", "None")


# ---------------------------------------------------------------------------
# core.services.file_vault.rotation — _reencrypt_data_json
# ---------------------------------------------------------------------------


def test_reencrypt_data_json_gibt_nicht_dict_werte_unveraendert_zurueck_statt_zu_crashen():
    """Pins ``xǁ_reencrypt_data_jsonǁ__mutmut_1`` (``if not payload or not
    isinstance(payload, dict)`` -> ``and``): ein wahrer, aber NICHT-dict-
    Payload (z. B. eine Liste aus Altbestand) muss unveraendert
    zurueckgegeben werden — das UND-verknuepfte Original crasht stattdessen
    mit einem ``ValueError`` aus ``dict(payload)``, was jeden Rotationslauf
    an dieser Zeile abbrechen liesse."""
    payload = ["kein-dict-aber-wahr"]

    result = _reencrypt_data_json(payload)

    assert result == (payload, False)


# ---------------------------------------------------------------------------
# core.services.file_vault.rotation — _reencrypt_events / _rotate_dynamic_json_model
# ---------------------------------------------------------------------------


def test_reencrypt_events_selects_related_document_type_ohne_extra_query(
    facility, staff_user, django_assert_num_queries
):
    """Pins ``xǁ_reencrypt_eventsǁ__mutmut_14`` (``select_related
    ("document_type")`` -> ``select_related(None)``): ``event.document_type``
    wird bei JEDER Zeile gelesen (Pflichtfeld); ohne den Join kostet das eine
    zusaetzliche Abfrage je Zeile — bei einem grossen Rotationslauf ein
    handfestes N+1-Problem."""
    from django.utils import timezone

    dt = DocumentType.objects.create(facility=facility, name="Doku")
    Event.objects.create(facility=facility, document_type=dt, occurred_at=timezone.now(), data_json={})

    with django_assert_num_queries(2):
        updated = _reencrypt_events(dry_run=True)

    assert updated == 0


def test_rotate_dynamic_json_model_selects_related_document_type_ohne_extra_query(
    facility, staff_user, doc_type_client_profile, django_assert_num_queries
):
    """Pins ``xǁ_rotate_dynamic_json_modelǁ__mutmut_14`` (``select_related
    ("document_type")`` -> ``select_related(None)``): analog zu
    ``_reencrypt_events``, hier fuer den Client/Case-Pfad."""
    Client.objects.create(
        facility=facility,
        pseudonym="QueryCount-1",
        created_by=staff_user,
        document_type=doc_type_client_profile,
        data_json={},
    )

    with django_assert_num_queries(2):
        updated = _rotate_dynamic_json_model(Client, dry_run=True)

    assert updated == 0


# ---------------------------------------------------------------------------
# core.services.file_vault.rotation — _reencrypt_shift_handover_notes
# ---------------------------------------------------------------------------


def test_reencrypt_shift_handover_notes_ueberspringt_nur_die_eine_unverschluesselte_notiz(facility):
    """Pins ``xǁ_reencrypt_shift_handover_notesǁ__mutmut_19`` (``if not
    is_encrypted_value(value): continue`` -> ``break``): eine Notiz ohne
    Verschluesselungs-Marker darf NUR sich selbst uebersprungen werden —
    ``break`` wuerde jede nachfolgende, tatsaechlich rotierbare Notiz
    stillschweigend liegen lassen."""
    _make_note(facility, date(2026, 1, 10), "Klartext-ohne-Marker")
    _make_note(facility, date(2026, 1, 9), encrypt_field("Wichtige Notiz"))

    updated = _reencrypt_shift_handover_notes(dry_run=True)

    assert updated == 1


def test_reencrypt_shift_handover_notes_ueberspringt_nur_die_eine_geloeschte_zeile(facility, monkeypatch):
    """Pins ``xǁ_reencrypt_shift_handover_notesǁ__mutmut_15`` (``if note is
    None: continue`` -> ``break``): eine zwischen pk-Sammlung und Re-Read
    verschwundene Zeile (Race) darf NUR sich selbst uebersprungen werden —
    ``break`` liesse jede nachfolgende Notiz unrotiert unter dem Alt-
    Schluessel liegen."""
    _make_note(facility, date(2026, 1, 10), encrypt_field("Erste"))
    note_middle = _make_note(facility, date(2026, 1, 9), encrypt_field("Wird waehrend des Laufs geloescht"))
    _make_note(facility, date(2026, 1, 8), encrypt_field("Letzte"))

    original_is_encrypted = rotation_module.is_encrypted_value
    state = {"fired": False}

    def delete_middle_then_check(value):
        result = original_is_encrypted(value)
        if not state["fired"] and result:
            state["fired"] = True
            ShiftHandoverNote.objects.filter(pk=note_middle.pk).delete()
        return result

    monkeypatch.setattr(rotation_module, "is_encrypted_value", delete_middle_then_check)

    updated = _reencrypt_shift_handover_notes(dry_run=True)

    assert state["fired"], "die Loeschung waehrend des Laufs muss ausgeloest worden sein"
    assert updated == 2


def test_reencrypt_shift_handover_notes_ueberschreibt_niemals_ein_kaputtes_chiffrat(facility):
    """Pins ``xǁ_reencrypt_shift_handover_notesǁ__mutmut_23`` (XX-Marker auf
    dem Platzhalter-Vergleich ``"[verschlüsselt]"``), ``_24``
    (Grossschreibung) und ``_25`` (``continue`` -> ``break`` am selben
    Vergleich): ein nicht entschluesselbares Chiffrat darf NIE erneut
    verschluesselt werden (der Platzhaltertext selbst wuerde dann als
    scheinbar gueltiger Inhalt persistiert — irreversibler Datenverlust) UND
    die naechste, echte Notiz muss trotzdem rotiert werden."""
    note_corrupted = _make_note(facility, date(2026, 1, 10), {"__encrypted__": True, "value": "invalid-token"})
    note_valid = _make_note(facility, date(2026, 1, 9), encrypt_field("Gueltiger Text"))
    original_corrupted_note = dict(note_corrupted.note)
    original_valid_token = dict(note_valid.note)

    updated = _reencrypt_shift_handover_notes(dry_run=False)

    note_corrupted.refresh_from_db()
    note_valid.refresh_from_db()
    assert updated == 1
    assert note_corrupted.note == original_corrupted_note
    assert note_valid.note != original_valid_token
    assert safe_decrypt(note_valid.note) == "Gueltiger Text"


def test_reencrypt_shift_handover_notes_dry_run_zaehlt_jede_faellige_zeile(facility):
    """Pins ``xǁ_reencrypt_shift_handover_notesǁ__mutmut_26`` (``updated +=
    1`` -> ``= 1`` im Dry-Run-Zweig) und ``_29`` (``continue`` -> ``break``
    direkt danach): bei ZWEI rotierbaren Notizen muss die Dry-Run-Bilanz 2
    sein, nicht bei 1 festkleben."""
    _make_note(facility, date(2026, 1, 10), encrypt_field("Erste"))
    _make_note(facility, date(2026, 1, 9), encrypt_field("Zweite"))

    updated = _reencrypt_shift_handover_notes(dry_run=True)

    assert updated == 2


def test_reencrypt_shift_handover_notes_real_run_zaehlt_jede_geschriebene_zeile(facility):
    """Pins ``xǁ_reencrypt_shift_handover_notesǁ__mutmut_43`` (``updated +=
    1`` -> ``= 1`` im echten Schreibpfad): bei ZWEI tatsaechlich rotierten
    Notizen muss die Rueckgabe 2 sein."""
    notes = [_make_note(facility, date(2026, 1, 10 - i), encrypt_field(f"Text-{i}")) for i in range(2)]

    updated = _reencrypt_shift_handover_notes(dry_run=False)

    assert updated == 2
    for i, note in enumerate(notes):
        note.refresh_from_db()
        assert safe_decrypt(note.note) == f"Text-{i}"


# ---------------------------------------------------------------------------
# core.services.file_vault.rotation — _rewrap_one_totp_device
# ---------------------------------------------------------------------------


def test_rewrap_one_totp_device_gibt_nach_dem_dokumentierten_default_von_drei_versuchen_auf(facility, staff_user):
    """Pins ``xǁ_rewrap_one_totp_deviceǁ__mutmut_1`` (Default ``attempts: int
    = 3`` -> ``4``): bei staendiger Kontention (jeder CAS-Versuch scheitert)
    muss die Funktion nach GENAU drei Versuchen ``"contended"`` melden — die
    dokumentierte Obergrenze fuer den Operator-Melde-Anker."""
    device = EncryptedTOTPDevice.objects.create(user=staff_user, name="dauer-kontendiert", confirmed=True)
    call_count = {"n": 0}

    def decrypt_totp_key(value):
        return value

    def encrypt_totp_key(value):
        call_count["n"] += 1
        # Simuliert einen konkurrierenden Schreibvorgang zwischen Lesen und
        # CAS-Update: der wertgebundene Filter greift danach nie.
        EncryptedTOTPDevice.objects.filter(pk=device.pk).update(key=f"changed-{call_count['n']}")
        return f"new-{call_count['n']}"

    outcome = _rewrap_one_totp_device(device.pk, decrypt_totp_key, encrypt_totp_key)

    assert outcome == "contended"
    assert call_count["n"] == 3
