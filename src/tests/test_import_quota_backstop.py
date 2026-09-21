"""Speicherkontingent im CSV-Importpfad (Refs #2259).

Die Zusage „0 Byte Ueberzug" galt seit Refs #2178 nur fuer **Dateianhaenge**:
``storage._commit_staged_upload`` liest die Summe unter der Sperre der
Einrichtung ein zweites Mal. ``import_engine.store_import_upload`` schrieb
seinen ``ImportJob`` dagegen ohne Transaktionsrahmen, ohne diese Sperre und
ohne zweite Pruefung — der Vorab-Check (``enforce_storage_quota``) aenderte dort
nur die Audit-Klassifikation. Zwei Luecken folgten daraus:

* **Rennen:** Zwei gleichzeitige CSV-Uploads am Limit passierten beide den
  lockfreien Vorab-Check und legten beide ihre Datei ab — Ueberzug eine ganze
  Importdatei.
* **Mischfall:** Der Anhangs-Rechner summierte offene Importdateien nicht mit.
  Ein Anhang wurde angenommen, waehrend eine offene Importdatei zusaetzlich
  Platz belegte — und umgekehrt.

Diese Suite nagelt beide Richtungen fest: EIN Einrichtungs-Lock (derselbe wie
fuer Anhaenge, kein zweiter Namensraum), EIN Verbrauchsrechner (Anhaenge +
offene Importdateien) und die Rechenschaftszeile
``ATTACHMENT_QUOTA_EXCEEDED``/``ImportJob`` NACH dem Rollback.

Eigene Datei, weil der Renn-Nachweis ``django_db(transaction=True)`` und echte
Nebenlaeufigkeit auf eigenen DB-Verbindungen braucht (Muster
``file_vault/test_file_vault_quota_race.py``).
"""

from __future__ import annotations

import io
import threading
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, ImportJob, Settings
from core.models.attachment import Attachment
from core.services import import_engine
from core.services.attachments.crud import quota_backstop_audit_boundary
from core.services.file_vault import commit_staged_upload, encrypt_field, encrypt_file, prepare_encrypted_upload

MB = 1024 * 1024

#: Kontingent der Testeinrichtung — klein genug fuer schnelle Laeufe, gross
#: genug fuer eine saubere Byte-Rechnung.
LIMIT = 3 * MB

#: Obergrenze fuer ``Thread.join`` und ``Barrier.wait``. Kein Sleep-Warten: die
#: Barriere gibt beide Threads gleichzeitig frei, der Rest ist DB-Arbeit.
JOIN_TIMEOUT = 45

#: DB-Zeitgrenzen je Renn-Verbindung — eine haengende Sperre soll den Lauf
#: abbrechen statt die Suite zu blockieren.
LOCK_TIMEOUT_MS = 20_000
STATEMENT_TIMEOUT_MS = 40_000

# ``prepare_encrypted_upload`` prueft Magic-Bytes via libmagic (#610); nur die
# Mischfall-Tests mit echten Anhaengen brauchen sie.
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
    _LIBMAGIC_FEHLER = ""
except Exception as _exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    _LIBMAGIC_FEHLER = str(_exc)

braucht_libmagic = pytest.mark.skipif(
    bool(_LIBMAGIC_FEHLER),
    reason=f"libmagic nicht lauffaehig ({_LIBMAGIC_FEHLER}) — Anhangs-Pfad erfordert libmagic1.",
)

_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _pdf_of(size: int) -> bytes:
    """Ein PDF von **exakt** ``size`` Bytes — Fuellung als PDF-Kommentarzeile."""
    filler = size - len(_PDF) - 2
    if filler < 0:
        raise ValueError(f"size {size} ist kleiner als das Minimal-PDF ({len(_PDF) + 2} Bytes)")
    head, tail = _PDF[:9], _PDF[9:]
    return head + b"%" + b"A" * filler + b"\n" + tail


def _csv_bytes(marker: str, rows: int = 6, row_bytes: int = 100_000) -> bytes:
    """Eine CSV mit gut komprimierungsfreier Nutzlast fester Groesse."""
    zeilen = [f"{marker}-{index};" + "x" * row_bytes for index in range(rows)]
    return ("Pseudonym;Notizen\n" + "\n".join(zeilen) + "\n").encode()


def _upload(raw: bytes, name: str = "import.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, raw, content_type="text/csv")


def _verschluesselte_groesse(raw: bytes, tmp_path) -> int:
    """Die Groesse, die ``raw`` als ``.enc`` auf der Platte belegt.

    Der Backstop rechnet mit der TATSAECHLICHEN Dateigroesse nach der
    Verschluesselung — dieselbe Groesse, die
    ``import_engine._pending_import_upload_bytes`` spaeter per ``stat()``
    wiederfindet. Die Probe nutzt eine ``file_id`` derselben Laenge wie im
    Produktivpfad (``<uuid>.enc``), damit die Groesse exakt uebereinstimmt.
    """
    probe = tmp_path / f"{uuid.uuid4()}.probe"
    encrypt_file(io.BytesIO(raw), probe, file_id=f"{uuid.uuid4()}.enc")
    groesse = probe.stat().st_size
    probe.unlink()
    return groesse


def _belegte_bytes(facility) -> int:
    """Gesamtbelegung der Einrichtung: Anhangszeilen + offene Importdateien."""
    anhaenge = Attachment.objects.for_facility(facility).aggregate(s=Sum("file_size"))["s"] or 0
    return anhaenge + import_engine._pending_import_upload_bytes(facility)


@pytest.fixture
def media_root(settings, tmp_path):
    """Verschluesselte Dateien in ``tmp_path`` statt im Repo-``media/``."""
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def quota_facility(facility, settings, media_root):
    """Einrichtung mit 3-MB-Kontingent (Betreiber-Backstop abgeschaltet)."""
    settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
    Settings.objects.update_or_create(
        facility=facility,
        defaults={"allowed_file_types": "pdf", "max_file_size_mb": 10, "max_storage_mb": 3},
    )
    return facility


@pytest.fixture
def offener_import(quota_facility, admin_user):
    """Belege ``size`` Bytes als offenen Importlauf — Zeile plus echte Datei.

    Der Inhalt ist fuer die Rechnung ohne Belang: der Verbrauchsrechner liest
    ausschliesslich die Dateigroesse auf der Platte.
    """

    def _belegen(size: int) -> ImportJob:
        storage_filename = f"{uuid.uuid4()}.enc"
        verzeichnis = import_engine._import_dir(quota_facility)
        verzeichnis.mkdir(parents=True, exist_ok=True)
        (verzeichnis / storage_filename).write_bytes(b"\x02" + b"B" * (size - 1))
        return ImportJob.objects.create(
            facility=quota_facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            status=ImportJob.Status.UPLOADED,
            storage_filename=storage_filename,
            original_filename_encrypted=encrypt_field("bestand.csv"),
            mapping_json={"columns": {}, "delimiter": ";", "encoding": "utf-8"},
        )

    return _belegen


@pytest.fixture
def anhang_ziel(quota_facility, admin_user):
    """``(event, field_template)`` als Owner der Anhaenge."""
    dt = DocumentType.objects.create(
        facility=quota_facility,
        name="DocWithFile",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_file = FieldTemplate.objects.create(
        facility=quota_facility,
        name="Scan",
        field_type=FieldTemplate.FieldType.FILE,
    )
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    event = Event.objects.create(
        facility=quota_facility,
        document_type=dt,
        occurred_at=timezone.now(),
        data_json={},
        created_by=admin_user,
    )
    return event, ft_file


@pytest.fixture
def anhang_zeile(quota_facility, anhang_ziel, admin_user):
    """Belege ``size`` Bytes als Anhangszeile — fuer die Summe zaehlt die Zeile."""
    event, ft_file = anhang_ziel

    def _belegen(size: int) -> Attachment:
        return Attachment.objects.create(
            facility=quota_facility,
            event=event,
            field_template=ft_file,
            storage_filename=f"{uuid.uuid4()}.enc",
            original_filename_encrypted=encrypt_field("bestand.pdf"),
            file_size=size,
            mime_type="application/pdf",
            detected_mime="application/pdf",
            created_by=admin_user,
            is_current=True,
            entry_id=uuid.uuid4(),
        )

    return _belegen


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestGleichzeitigeImporte:
    """Refs #2259: zwei gleichzeitige CSV-Uploads am Limit — genau einer gewinnt."""

    def test_zwei_gleichzeitige_uploads_am_limit_gewinnt_nur_einer(
        self, quota_facility, admin_user, offener_import, tmp_path
    ):
        """Bestand ``Limit − Dateigroesse``, zwei zeitgleiche Uploads.

        Beide passieren den lockfreien Vorab-Check (er rechnet mit der
        Klartextgroesse und sieht denselben alten Stand). Entschieden wird erst
        unter der Sperre der Einrichtung, unmittelbar vor ``job.save()`` — der
        zweite Upload findet die Datei des ersten vor und wird abgewiesen.
        """
        raw = _csv_bytes("RENNEN")
        enc = _verschluesselte_groesse(raw, tmp_path)
        offener_import(LIMIT - enc)

        barrier = threading.Barrier(2, timeout=JOIN_TIMEOUT)
        ergebnisse: dict[int, object] = {}
        schutz = threading.Lock()

        def worker(idx: int) -> None:
            try:
                with connections["default"].cursor() as cur:
                    cur.execute(f"SET lock_timeout = {LOCK_TIMEOUT_MS}")
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                barrier.wait()
                job = import_engine.store_import_upload(
                    quota_facility,
                    admin_user,
                    _upload(raw, name=f"rennen-{idx}.csv"),
                    target_type=ImportJob.TargetType.CLIENT,
                )
                with schutz:
                    ergebnisse[idx] = job
            except Exception as exc:  # noqa: BLE001 — Sammeltest, Auswertung unten
                with schutz:
                    ergebnisse[idx] = exc
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)
        assert not any(t.is_alive() for t in threads), "Worker-Thread haengt — Deadlock-Verdacht"

        gewinner = [i for i, r in ergebnisse.items() if isinstance(r, ImportJob)]
        verlierer = [i for i, r in ergebnisse.items() if isinstance(r, Exception)]
        assert len(gewinner) == 1, (
            f"Am Limit darf genau ein gleichzeitiger CSV-Upload durchkommen — durchgekommen: "
            f"{len(gewinner)}, Ergebnisse: {ergebnisse!r}"
        )
        assert all(isinstance(ergebnisse[i], ValidationError) for i in verlierer), (
            f"Der abgewiesene Upload muss dieselbe Fehlerklasse liefern wie der Vorab-Check: {ergebnisse!r}"
        )
        assert _belegte_bytes(quota_facility) <= LIMIT, "Ueberzug: die Zusage sind 0 Byte"
        assert ImportJob.objects.for_facility(quota_facility).count() == 2, (
            "Bestandslauf + Gewinner — der Verlierer darf keine Zeile hinterlassen"
        )
        assert len(list(import_engine._import_dir(quota_facility).glob("*.enc"))) == 2, (
            "der abgewiesene Upload darf keine verwaiste .enc-Datei hinterlassen"
        )
        eintraege = AuditLog.objects.filter(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert eintraege.count() == 1, "die Ablehnung braucht genau eine Rechenschaftszeile"
        assert eintraege.get().target_type == "ImportJob"


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestMischfallOhneGleichzeitigkeit:
    """Refs #2259: Anhaenge und offene Importdateien zaehlen gemeinsam."""

    @braucht_libmagic
    def test_anhang_committet_nach_import_wird_abgewiesen(self, quota_facility, admin_user, anhang_ziel, tmp_path):
        """Anhang stagen (Platz frei) → CSV-Import speichern → Anhang committen.

        Der Anhangs-Backstop muss die inzwischen abgelegte Importdatei
        mitrechnen; sonst liegen beide Dateien zusammen ueber dem Kontingent.
        """
        event, ft_file = anhang_ziel
        raw = _csv_bytes("MISCH")
        enc = _verschluesselte_groesse(raw, tmp_path)
        anhang_groesse = LIMIT - enc + 1

        staged = prepare_encrypted_upload(
            quota_facility,
            SimpleUploadedFile("scan.pdf", _pdf_of(anhang_groesse), content_type="application/pdf"),
            admin_user,
            owner=None,
        )
        import_engine.store_import_upload(
            quota_facility,
            admin_user,
            _upload(raw),
            target_type=ImportJob.TargetType.CLIENT,
        )

        with pytest.raises(ValidationError, match="Speicherkontingent der Einrichtung erschöpft"):
            commit_staged_upload(event, ft_file, staged, admin_user)

        assert Attachment.objects.for_facility(quota_facility).count() == 0
        assert not staged.output_path.exists(), "verwaiste .enc-Datei des abgewiesenen Anhangs"
        assert _belegte_bytes(quota_facility) <= LIMIT, "Ueberzug: die Zusage sind 0 Byte"
        eintrag = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert eintrag.target_type == "Attachment"
        assert eintrag.detail["reason"] == "quota_exceeded"

    def test_import_nach_anhaengen_wird_abgewiesen_und_auditiert(
        self, quota_facility, admin_user, anhang_zeile, monkeypatch, tmp_path
    ):
        """Umgekehrter Mischfall: die Einrichtung laeuft zwischen Vorab-Check
        und ``job.save()`` voll.

        Beobachtend statt per Sleep: die Anhangszeile entsteht im Moment der
        Verschluesselung — also genau in dem Fenster, das der Vorab-Check offen
        laesst. Die Rechenschaftszeile muss den Rollback ueberleben.
        """
        raw = _csv_bytes("UMGEKEHRT")
        echtes_encrypt = import_engine.encrypt_file

        def spaeter_belegen(quelle, ziel, *args, **kwargs):
            ergebnis = echtes_encrypt(quelle, ziel, *args, **kwargs)
            anhang_zeile(LIMIT)
            return ergebnis

        monkeypatch.setattr(import_engine, "encrypt_file", spaeter_belegen)

        with pytest.raises(ValidationError, match="Speicherkontingent der Einrichtung erschöpft"):
            import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(raw),
                target_type=ImportJob.TargetType.CLIENT,
            )

        assert ImportJob.objects.for_facility(quota_facility).count() == 0, (
            "der abgewiesene Upload darf keine Jobzeile hinterlassen"
        )
        assert list(import_engine._import_dir(quota_facility).glob("*.enc")) == [], (
            "der abgewiesene Upload darf keine verwaiste .enc-Datei hinterlassen"
        )
        assert _belegte_bytes(quota_facility) <= LIMIT
        eintrag = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert eintrag.target_type == "ImportJob", "die Zeile klassifiziert den Importlauf"
        assert eintrag.detail["reason"] == "quota_exceeded"
        assert eintrag.detail["limit_mb"] == 3
        assert "UMGEKEHRT" not in str(eintrag.detail), "kein Dateiinhalt in der Rechenschaftszeile"


@pytest.mark.django_db
@pytest.mark.postgres
class TestAuditRandUndSperrfolge:
    """Refs #2259: Rechenschaftsrand und Sperr-Reihenfolge des Importpfads."""

    def test_unter_aufrufer_transaktion_auditiert_der_aeussere_rand(self, quota_facility, admin_user, anhang_zeile):
        """Mit eigener ``atomic()`` des Aufrufers traegt die Ausnahme die
        Nutzdaten nach oben — geschrieben wird am aeussersten Rand.

        Innerhalb der zurueckgerollten Transaktion geschrieben, verschwaende die
        Zeile mit ihr; deshalb ``quota_backstop_audit_boundary`` UM die
        Transaktion (Muster Refs #2178).
        """
        anhang_zeile(LIMIT)

        with pytest.raises(ValidationError), quota_backstop_audit_boundary(), transaction.atomic():
            import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(_csv_bytes("RAND", rows=1)),
                target_type=ImportJob.TargetType.CLIENT,
            )

        eintrag = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert eintrag.target_type == "ImportJob"

    def test_einrichtungs_sperre_liegt_vor_dem_job_insert(self, quota_facility, admin_user):
        """Sequenz-Guard: Sperre vor ``job.save()``, kein erstmaliges Sperren
        unter dem Ketten-Lock.

        Ein Nachrechnen NACH dem Insert schliesst das Rennen unter READ
        COMMITTED nicht — jede Transaktion sieht ihre eigene unbestaetigte
        Zeile, aber keine fremde. Und eine Zeilensperre, die erst unter dem
        Advisory-Lock genommen wird, dreht die einrichtungsweite Sperrfolge um
        (Refs #2260).
        """
        with CaptureQueriesContext(connection) as ctx:
            import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(_csv_bytes("SEQUENZ", rows=1)),
                target_type=ImportJob.TargetType.CLIENT,
            )

        sql = [q["sql"] for q in ctx.captured_queries]
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock" in s]
        insert_at = [i for i, s in enumerate(sql) if "INSERT INTO" in s and "core_importjob" in s]
        assert lock_at, f"keine Einrichtungs-Sperre im Import-Speicherpfad: {sql!r}"
        assert insert_at, f"kein ImportJob-Insert im Speicherpfad: {sql!r}"
        assert lock_at[0] < insert_at[0], "die Sperre muss VOR dem Insert genommen werden"
        zeilensperren = [i for i, s in enumerate(sql) if "FOR UPDATE" in s]
        assert all(i < lock_at[0] for i in zeilensperren), f"erstmaliges Sperren unter dem Ketten-Lock: {sql!r}"

    def test_sperre_nutzt_denselben_namen_wie_der_anhangs_backstop(self, quota_facility):
        """Kein zweiter Lock-Namensraum: derselbe Key wie Anhaenge und
        AuditLog-Hashkette — eine einzige Sperre je Einrichtung kennt keine
        Reihenfolge, die sich verdrehen koennte."""
        from core.services.audit.chain import advisory_lock_name
        from core.services.file_vault.quota import storage_lock_name

        assert storage_lock_name(quota_facility.pk) == advisory_lock_name(quota_facility.pk)
