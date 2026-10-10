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
import re
import threading
import time
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, ImportJob, Settings
from core.models.attachment import Attachment
from core.services import import_engine
from core.services.attachments.crud import quota_backstop_audit_boundary
from core.services.audit import log_audit_event
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
        self, quota_facility, admin_user, offener_import, monkeypatch, tmp_path
    ):
        """Bestand ``Limit − Dateigroesse``, zwei zeitgleiche Uploads.

        Beide passieren den lockfreien Vorab-Check (er rechnet mit der
        Klartextgroesse und sieht denselben alten Stand). Entschieden wird erst
        unter der Sperre der Einrichtung, unmittelbar vor ``job.save()`` — der
        zweite Upload findet die Datei des ersten vor und wird abgewiesen.

        **Die Barriere liegt IM Pfad, nicht davor**: Ein Treffpunkt vor
        ``store_import_upload`` erzwingt das kritische Interleaving nicht — der
        Scheduler darf den ersten Thread nach der Freigabe bis zum Ende
        durchlaufen lassen, und der zweite wird dann schon vom **Vorab-Check**
        abgewiesen. Genau eine Zeile, genau eine Rechenschaftszeile: die
        ungefixte Basis bestuende den Test. Deshalb treffen sich beide Uploads
        hier NACH ihrem Vorab-Check und VOR der Verschluesselung — ab da ist
        fuer beide entschieden, dass der billige Check sie durchlaesst, und nur
        noch der Backstop unter der Sperre kann das Rennen schliessen.
        """
        raw = _csv_bytes("RENNEN")
        enc = _verschluesselte_groesse(raw, tmp_path)
        offener_import(LIMIT - enc)

        barrier = threading.Barrier(2, timeout=JOIN_TIMEOUT)
        ergebnisse: dict[int, object] = {}
        schutz = threading.Lock()

        echter_vorab_check = import_engine.enforce_storage_quota

        def vorab_check_dann_treffen(*args, **kwargs):
            """Beobachtend: erst der echte Vorab-Check, dann der Treffpunkt."""
            ergebnis = echter_vorab_check(*args, **kwargs)
            barrier.wait()
            return ergebnis

        monkeypatch.setattr(import_engine, "enforce_storage_quota", vorab_check_dann_treffen)

        def worker(idx: int) -> None:
            try:
                with connections["default"].cursor() as cur:
                    cur.execute(f"SET lock_timeout = {LOCK_TIMEOUT_MS}")
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
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

    def test_unter_aufrufer_transaktion_auditiert_der_aeussere_rand(
        self, quota_facility, admin_user, anhang_zeile, monkeypatch
    ):
        """Mit eigener ``atomic()`` des Aufrufers traegt die Ausnahme die
        Nutzdaten nach oben — geschrieben wird am aeussersten Rand.

        Innerhalb der zurueckgerollten Transaktion geschrieben, verschwaende die
        Zeile mit ihr; deshalb ``quota_backstop_audit_boundary`` UM die
        Transaktion (Muster Refs #2178). Die Einrichtung laeuft erst NACH dem
        Vorab-Check voll — sonst weist bereits dieser ab, und der Rand haette
        nichts zu schreiben.
        """
        echtes_encrypt = import_engine.encrypt_file

        def spaeter_belegen(quelle, ziel, *args, **kwargs):
            ergebnis = echtes_encrypt(quelle, ziel, *args, **kwargs)
            anhang_zeile(LIMIT)
            return ergebnis

        monkeypatch.setattr(import_engine, "encrypt_file", spaeter_belegen)

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
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
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


# ------------------------------------------------------- Sperrfolge-Guards --
#
# Die AuditLog-Hashkette nimmt je Einrichtung einen transaktionsweiten
# Advisory-Lock; denselben Schluessel nimmt der Kontingent-Backstop
# (``quota.storage_lock_name``). Die einrichtungsweite Ordnung lautet deshalb:
# **erst Zeilensperren, dann der Ketten-Lock** (Refs #2260). Der Insert der
# Jobzeile bricht diese Ordnung, wenn er unter dem Ketten-Lock zum ERSTEN Mal
# eine Zeile sperrt: PostgreSQL prueft die Fremdschluessel der neuen Zeile
# ``DEFERRABLE INITIALLY DEFERRED``, also beim COMMIT — und nimmt dabei auf
# jede FK-Zielzeile eine ``FOR KEY SHARE``-Sperre, mitten im gehaltenen
# Ketten-Lock. Ein Pfad, der eine dieser Zeilen schluessselaendernd haelt und
# danach den Ketten-Lock will (der Dokumentationstyp-Fallback im Django-Admin:
# ``super().save_model()`` und danach ``log_audit_event``), schliesst damit
# einen Zyklus, den PostgreSQL mit ``40P01`` aufloest.
#
# Zwei Sorten Guards, wie in ``test_attachment_lock_order.py``:
# ein **Sequenz-Guard** am mitgeschnittenen SQL (ein Prozess, kein Timing) und
# ein **Rendezvous-Guard**, der die Gegenrichtung tatsaechlich danebenstellt.

#: Pseudo-Tabellenname fuer den Ketten-Advisory-Lock im Sperrmitschnitt.
CHAIN = "audit_chain"

#: Tabelle der Nutzerzeile — aus dem Modell, nicht getippt.
USER_TABELLE = get_user_model()._meta.db_table

#: Die FK-Ziele der ``ImportJob``-Zeile: Einrichtung, Dokumentationstyp,
#: anlegende Person. Auf genau diese Zeilen nimmt die aufgeschobene
#: FK-Pruefung ihre ``FOR KEY SHARE``-Sperren.
FK_TABELLEN = frozenset({"core_facility", "core_documenttype", USER_TABELLE})

#: Jede Sperrstaerke, die PostgreSQL auf Zeilenebene kennt.
_SPERR_KLAUSEL = r"FOR (?:KEY SHARE|NO KEY UPDATE|SHARE|UPDATE)"
_ZEILENSPERRE_RE = re.compile(rf'FROM "(?P<table>\w+)"[^;]*{_SPERR_KLAUSEL}', re.IGNORECASE | re.DOTALL)
_UPDATE_RE = re.compile(r'\s*UPDATE "(?P<table>\w+)"', re.IGNORECASE)
_DELETE_RE = re.compile(r'\s*DELETE FROM "(?P<table>\w+)"', re.IGNORECASE)
_WHERE_ENDE_RE = re.compile(rf"\s+(?:{_SPERR_KLAUSEL}|ORDER BY|LIMIT|OFFSET|RETURNING)\b", re.IGNORECASE)

#: Obergrenze fuer jedes Warten im Rendezvous-Guard (Sekunden).
RENDEZVOUS_DEADLINE = 8.0

#: PostgreSQL-SQLSTATE fuer „deadlock detected".
DEADLOCK = "40P01"

#: SQLSTATEs der DB-seitigen Zeitgrenzen (``lock_not_available``,
#: ``query_canceled``) — der Rettungsanker, falls der Detektor den Zyklus
#: nicht aufloest: kein Test haengt, er faellt.
TIMEOUT_SQLSTATES = frozenset({"55P03", "57014"})

#: Die Sperrgrenzen liegen ueber ``deadlock_timeout`` (1 s), damit ein echter
#: Zyklus als ``40P01`` sichtbar wird und nicht als Zeitgrenze.
GEGEN_LOCK_TIMEOUT = "4000ms"
WORKER_LOCK_TIMEOUT = "9000ms"
WORKER_STATEMENT_TIMEOUT = "14000ms"


def _kennung(wert) -> str:
    """Normalisierte Form eines PK-Werts (UUID ohne Bindestriche, klein)."""
    return str(wert).replace("-", "").lower()


def _sperr_tabelle(sql):
    """Die FK-Zieltabelle, deren Zeilen diese Anweisung sperrt (oder ``None``)."""
    if re.search(_SPERR_KLAUSEL, sql, re.IGNORECASE):
        match = _ZEILENSPERRE_RE.search(sql)
    elif _DELETE_RE.match(sql):
        match = _DELETE_RE.match(sql)
    else:
        match = _UPDATE_RE.match(sql)
    if match is None:
        return None
    table = match.group("table")
    return table if table in FK_TABELLEN else None


def _treffer_abfrage(sql):
    """``(tabelle, SELECT auf DIESELBE Bedingung, Platzhalterzahl)`` oder ``None``.

    Der Mitschnitt raet die gesperrten Zeilen NICHT aus den Literalen des
    SQL-Textes: dieselbe ``WHERE``-Bedingung wird unmittelbar vor der Anweisung
    als reines ``SELECT id`` gefahren, das Ergebnis ist die tatsaechliche
    Treffermenge. Gelesen wird ab dem ersten ``WHERE``; alles ab der
    Sperrklausel bzw. ``ORDER BY``/``LIMIT``/``OFFSET``/``RETURNING`` gehoert
    nicht mehr zur Bedingung. Die Platzhalter der Bedingung sind die LETZTEN
    ``n`` der Parameterliste (bei ``UPDATE`` stehen die ``SET``-Werte davor).
    """
    table = _sperr_tabelle(sql)
    if table is None:
        return None
    _kopf, trenner, rest = sql.partition(" WHERE ")
    if not trenner:
        bedingung = "TRUE"
    else:
        ende = _WHERE_ENDE_RE.search(rest)
        bedingung = rest[: ende.start()] if ende else rest
    return (
        table,
        f'SELECT "{table}"."id" FROM "{table}" WHERE {bedingung} ORDER BY 1',  # noqa: S608 — feste Tabellennamen
        bedingung.count("%s"),
    )


class _Sperrmitschnitt(CaptureQueriesContext):
    """Query-Mitschnitt PLUS die TATSAECHLICH gesperrten Zeilen, in Reihenfolge.

    Sperrt eine Anweisung Zeilen einer FK-Zieltabelle, wird dieselbe Bedingung
    vorher als ``SELECT id`` gefahren und jeder Treffer als
    ``(tabelle, kennung)`` protokolliert; der Ketten-Advisory-Lock kommt als
    ``(CHAIN, None)`` in dieselbe Folge. Die Probe laeuft unter einem
    Savepoint — ein Praedikat, das sich nicht isoliert ausfuehren laesst, wuerde
    die umgebende Transaktion sonst unbrauchbar machen. Solche Faelle landen in
    ``unaufloesbar`` und lassen die Zusagen hart scheitern; stillschweigend
    gruen wird hier nichts.
    """

    def __init__(self, dbconnection=None):
        super().__init__(dbconnection or connection)
        self.ereignisse: list = []
        self.unaufloesbar: list = []
        self._aktiv = False
        self._wrapper_cm = None

    def __enter__(self):
        self._wrapper_cm = self.connection.execute_wrapper(self._wrapper)
        self._wrapper_cm.__enter__()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, tb):
        try:
            return super().__exit__(exc_type, exc_value, tb)
        finally:
            self._wrapper_cm.__exit__(exc_type, exc_value, tb)

    def _probe(self, dbconnection, probe_sql, werte):
        with dbconnection.cursor() as cur:
            cur.execute(probe_sql, werte)
            return [zeile[0] for zeile in cur.fetchall()]

    def _wrapper(self, execute, sql, params, many, context):
        if self._aktiv or many:
            return execute(sql, params, many, context)
        if "pg_advisory_xact_lock(" in sql:
            self.ereignisse.append((CHAIN, None))
            return execute(sql, params, many, context)
        abfrage = _treffer_abfrage(sql)
        if abfrage is None:
            return execute(sql, params, many, context)
        table, probe_sql, anzahl = abfrage
        alle = list(params or ())
        werte = alle[len(alle) - anzahl :] if anzahl else []
        dbconnection = context["connection"]
        self._aktiv = True
        try:
            if dbconnection.in_atomic_block:
                with transaction.atomic():
                    treffer = self._probe(dbconnection, probe_sql, werte)
            else:
                treffer = self._probe(dbconnection, probe_sql, werte)
        except Exception as exc:  # noqa: BLE001 — der Test bewertet den Fehlschlag
            self.unaufloesbar.append((probe_sql, repr(exc)))
            treffer = []
        finally:
            self._aktiv = False
        self.ereignisse.extend((table, _kennung(pk)) for pk in treffer)
        return execute(sql, params, many, context)


def _sperrmengen(mitschnitt):
    """``(vor dem ersten Ketten-Lock gesperrt, danach ERSTMALS gesperrt)``.

    Beide sind Listen in Erst-Sperrreihenfolge. Ein erneutes Sperren einer
    bereits gehaltenen Zeile wartet nie und ist deshalb zulaessig — es steht in
    der ersten Liste und faellt aus der zweiten heraus.
    """
    vor: list = []
    nach: list = []
    gesehen: set = set()
    kette_gesehen = False
    for tabelle, kennung in mitschnitt.ereignisse:
        if tabelle == CHAIN:
            kette_gesehen = True
            continue
        zeile = (tabelle, kennung)
        if zeile in gesehen:
            continue
        gesehen.add(zeile)
        (nach if kette_gesehen else vor).append(zeile)
    return vor, nach


def _setze_zeitgrenzen(dbconnection, *, lock_timeout, statement_timeout=None) -> None:
    """Gibt der Verbindung DB-seitige Zeitgrenzen (muss im Autocommit laufen).

    ``set_config(..., is_local=False)`` gilt fuer die Sitzung und ueberlebt
    jede spaetere Transaktion. Eine Zeitgrenze im Python-Code kann ein
    blockierendes ``SELECT`` nicht beenden; diese hier kann es.
    """
    assert dbconnection.get_autocommit(), "Zeitgrenzen vor der ersten Transaktion setzen"
    with dbconnection.cursor() as cur:
        cur.execute("SELECT set_config('lock_timeout', %s, false)", [lock_timeout])
        if statement_timeout is not None:
            cur.execute("SELECT set_config('statement_timeout', %s, false)", [statement_timeout])


def _sqlstate(exc):
    """SQLSTATE der DB-Ausnahme hinter einem Django-``OperationalError``."""
    return getattr(getattr(exc, "__cause__", None), "sqlstate", None)


def _bewerte_db_fehler(fehler, *, kontext) -> None:
    """Jeder Fehler wird am SQLSTATE bewertet — nichts gilt stillschweigend als Erfolg."""
    if fehler is None:
        return
    zustand = _sqlstate(fehler)
    if zustand == DEADLOCK:
        pytest.fail(f"Sperr-Zyklus ({kontext}): SQLSTATE {DEADLOCK} — {fehler!r}")
    if zustand in TIMEOUT_SQLSTATES:
        pytest.fail(f"Warten lief in die Zeitgrenze ({kontext}): SQLSTATE {zustand} — Zyklusverdacht, {fehler!r}")
    pytest.fail(f"unerwarteter Fehler ({kontext}): SQLSTATE {zustand} — {fehler!r}")


def _warte_auf_gegenseite(sonde, facility_id, worker_pid, deadline_ends_at) -> str:
    """Beobachtet den Upload-Pfad von aussen — ohne unbegrenztes Warten.

    Die Sonde laeuft auf einer EIGENEN Verbindung und probt genau zwei
    Zustaende:

    * ``"kette"`` — der Ketten-Advisory-Lock GENAU DIESER Einrichtung ist
      vergeben. Der Upload haelt ihn also schon, bevor er die Typzeile
      angefasst hat: die verkehrte Folge, der Zyklus steht. Geprobt wird mit
      ``pg_try_advisory_xact_lock`` auf demselben Schluessel; die
      Probe-Transaktion wird sofort zurueckgerollt und haelt selbst nichts.
    * ``"blockiert"`` — GENAU das Backend des Uploads wartet auf die
      Transaktion eines anderen (``locktype='transactionid'``), haengt also an
      der gehaltenen Typzeile, BEVOR es den Ketten-Lock genommen hat: die
      geforderte Richtung. Die Probe ist an ``worker_pid`` gebunden; eine
      beliebige andere Sitzung darf den Test nicht gruen faerben.

    ``"deadline"`` heisst „nichts davon beobachtet" — unschluessig, und der
    Aufrufer laesst den Test dann nicht stillschweigend gruen werden.
    """
    from core.services.audit.chain import advisory_lock_name

    while time.monotonic() < deadline_ends_at:
        with sonde.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", [advisory_lock_name(facility_id)])
            frei = bool(cur.fetchone()[0])
            cur.execute(
                "SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype = 'transactionid' AND pid = %s",
                [worker_pid],
            )
            wartet = cur.fetchone()[0]
        sonde.rollback()
        if not frei:
            return "kette"
        if wartet:
            return "blockiert"
        time.sleep(0.05)
    return "deadline"


def _im_thread(fn):
    """Startet ``fn`` als Daemon-Thread und sammelt Ergebnis bzw. Ausnahme ein.

    Der Thread meldet zuerst seine Backend-PID (die Warteprobe braucht sie) und
    setzt seine DB-seitigen Zeitgrenzen; erst danach laeuft ``fn``. Bleibt er
    trotz aller Grenzen haengen, faellt der Test am ``is_alive``-Check, haelt
    aber den Testlauf nicht auf.
    """
    ergebnis: dict = {}
    bereit = threading.Event()

    def runner():
        try:
            _setze_zeitgrenzen(
                connection,
                lock_timeout=WORKER_LOCK_TIMEOUT,
                statement_timeout=WORKER_STATEMENT_TIMEOUT,
            )
            with connection.cursor() as cur:
                cur.execute("SELECT pg_backend_pid()")
                ergebnis["pid"] = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — der Test bewertet die Ausnahme
            ergebnis["fehler"] = exc
            connections.close_all()
            return
        finally:
            bereit.set()
        try:
            ergebnis["wert"] = fn()
        except Exception as exc:  # noqa: BLE001 — der Test bewertet die Ausnahme
            ergebnis["fehler"] = exc
        finally:
            connections.close_all()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert bereit.wait(RENDEZVOUS_DEADLINE), "Thread hat seine Backend-PID nicht gemeldet"
    assert "pid" in ergebnis, f"Thread kam nicht bis zur Backend-PID: {ergebnis.get('fehler')!r}"
    return thread, ergebnis


@pytest.fixture
def doku_typ(quota_facility):
    """Ein Dokumentationstyp der Einrichtung als FK-Ziel des Importlaufs."""
    return DocumentType.objects.create(
        facility=quota_facility,
        name="Import-Zuordnung",
        sensitivity=DocumentType.Sensitivity.NORMAL,
        entity_type=DocumentType.EntityType.CLIENT,
    )


@pytest.mark.django_db
@pytest.mark.postgres
class TestSperrfolgeDerJobzeile:
    """Refs #2259: unter dem Ketten-Lock wird keine Zeile ERSTMALS gesperrt."""

    def test_fk_zielzeilen_werden_vor_dem_ketten_lock_gesperrt(self, quota_facility, admin_user, doku_typ):
        """Sequenz-Guard am Mitschnitt: Einrichtung, Dokumentationstyp und
        anlegende Person sind VOR dem Ketten-Lock gesperrt.

        Die FK-Pruefung des Inserts laeuft ``DEFERRABLE INITIALLY DEFERRED``,
        also serverseitig beim COMMIT — clientseitig ist sie nicht
        mitzuschneiden. Mitschneidbar und deshalb die Zusage dieses Guards ist
        die Vorsperre: Wer die FK-Zielzeilen VOR dem Ketten-Lock haelt, bei dem
        findet die spaetere Pruefung nur noch bereits gehaltene Sperren vor und
        wartet nicht. Fehlt die Vorsperre, nimmt der COMMIT die erste Sperre
        unter dem Ketten-Lock — genau die verkehrte Folge.
        """
        with _Sperrmitschnitt() as mitschnitt:
            import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(_csv_bytes("SPERRFOLGE", rows=1)),
                target_type=ImportJob.TargetType.CLIENT,
                document_type=doku_typ,
            )

        assert not mitschnitt.unaufloesbar, f"Treffermenge nicht ermittelbar: {mitschnitt.unaufloesbar}"
        sql = [q["sql"] for q in mitschnitt.captured_queries]
        kette_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
        insert_at = [i for i, s in enumerate(sql) if "INSERT INTO" in s and "core_importjob" in s]
        assert kette_at, f"keine Einrichtungs-Sperre im Import-Speicherpfad: {sql!r}"
        assert insert_at, f"kein ImportJob-Insert im Speicherpfad: {sql!r}"
        assert kette_at[0] < insert_at[0], "die Sperre muss VOR dem Insert genommen werden"

        vor, nach = _sperrmengen(mitschnitt)
        assert not nach, f"unter dem Ketten-Lock erstmals gesperrte Zeilen: {nach}"
        erwartet = {
            ("core_facility", _kennung(quota_facility.pk)),
            ("core_documenttype", _kennung(doku_typ.pk)),
            (USER_TABELLE, _kennung(admin_user.pk)),
        }
        fehlend = erwartet - set(vor)
        assert not fehlend, (
            f"FK-Zielzeilen der Jobzeile ohne Vorsperre — der COMMIT sperrt sie erstmals unter dem "
            f"Ketten-Lock: {sorted(fehlend)}; vorgesperrt wurde: {vor!r}"
        )

    def test_vorsperre_ist_kein_exklusiver_griff(self, quota_facility, admin_user, doku_typ):
        """Die Vorsperre darf gleichzeitige Importe nicht serialisieren.

        Der Insert selbst braucht auf den FK-Zielen nur ``FOR KEY SHARE``. Eine
        staerkere Vorsperre (``FOR UPDATE``/``FOR NO KEY UPDATE``) waere mehr
        als die Ordnung verlangt: zwei Uploads desselben Dokumentationstyps
        wuerden sich an der Typzeile gegenseitig aufhalten, obwohl sie dort
        nichts aendern.
        """
        with CaptureQueriesContext(connection) as ctx:
            import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(_csv_bytes("KEYSHARE", rows=1)),
                target_type=ImportJob.TargetType.CLIENT,
                document_type=doku_typ,
            )

        sperren = [q["sql"] for q in ctx.captured_queries if re.search(_SPERR_KLAUSEL, q["sql"], re.IGNORECASE)]
        assert sperren, "keine Vorsperre auf den FK-Zielzeilen"
        zu_stark = [s for s in sperren if not re.search(r"FOR KEY SHARE", s, re.IGNORECASE)]
        assert not zu_stark, f"Vorsperre staerker als der Insert sie braucht: {zu_stark!r}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestRendezvousMitTypumbenennung:
    """Refs #2259: Upload × Umbenennung des Dokumentationstyps — kein Zyklus."""

    def test_upload_wartet_auf_die_typzeile_bevor_er_den_ketten_lock_nimmt(self, quota_facility, admin_user, doku_typ):
        """Gegenrichtung: eine fremde Transaktion hat den Dokumentationstyp
        umbenannt und will danach ihre Rechenschaftszeile schreiben.

        Die Gegenseite stellt die beiden Schritte des Django-Admin-Fallbacks
        nach, jeden mit dem echten Code: ``DocumentType.save()`` (das, was
        ``super().save_model()`` ausfuehrt) und danach ``log_audit_event``
        (die Rechenschaftszeile, die ``DocumentTypeAdmin.save_model``
        anschliessend schreibt) — beides in EINER Transaktion, wie der
        Admin-Change-View sie aufspannt. Dazwischen haelt eine Barriere, damit
        die Beobachtung nicht vom Scheduler abhaengt.

        Der Namenswechsel beruehrt den Unique-Key ``(facility, name)``;
        PostgreSQL nimmt dafuer einen Zeilenlock, der mit ``FOR KEY SHARE``
        unvertraeglich ist. Nimmt der Upload seinerseits zuerst den Ketten-Lock
        und laesst erst den COMMIT die Typzeile sperren, schliesst sich der
        Zyklus und PostgreSQL beendet einen der beiden mit ``40P01``.
        """
        anfrage = RequestFactory().post("/admin/core/documenttype/")
        anfrage.user = admin_user
        anfrage.current_facility = quota_facility
        umbenannt = threading.Event()
        weiter = threading.Event()

        def umbenennen():
            with transaction.atomic():
                typ = DocumentType.objects.get(pk=doku_typ.pk)
                typ.name = "Import-Zuordnung (umbenannt)"
                typ.save()
                umbenannt.set()
                assert weiter.wait(RENDEZVOUS_DEADLINE), "Freigabe fuer den Rechenschafts-Schritt blieb aus"
                log_audit_event(
                    anfrage,
                    AuditLog.Action.CONFIG_UPDATE,
                    target_obj=typ,
                    detail={"name": typ.name, "changed_fields": ["name"]},
                    facility=quota_facility,
                )
            return typ.name

        def hochladen():
            return import_engine.store_import_upload(
                quota_facility,
                admin_user,
                _upload(_csv_bytes("RENDEZVOUS", rows=1)),
                target_type=ImportJob.TargetType.CLIENT,
                document_type=doku_typ,
            )

        sonde = connections.create_connection("default")
        _setze_zeitgrenzen(sonde, lock_timeout=GEGEN_LOCK_TIMEOUT)
        sonde.set_autocommit(False)
        threads: list = []
        erg_umbenennung: dict = {}
        erg_upload: dict = {}
        try:
            thread_a, erg_umbenennung = _im_thread(umbenennen)
            threads.append(thread_a)
            assert umbenannt.wait(RENDEZVOUS_DEADLINE), (
                f"die Umbenennung hat die Typzeile nicht gesperrt: {erg_umbenennung.get('fehler')!r}"
            )
            thread_b, erg_upload = _im_thread(hochladen)
            threads.append(thread_b)
            beobachtung = _warte_auf_gegenseite(
                sonde,
                quota_facility.pk,
                erg_upload["pid"],
                time.monotonic() + RENDEZVOUS_DEADLINE,
            )
        finally:
            weiter.set()
            try:
                sonde.rollback()
            except Exception:  # noqa: BLE001 — die Verbindung wird ohnehin verworfen
                pass
            sonde.close()
            for t in threads:
                t.join(timeout=RENDEZVOUS_DEADLINE * 2)

        assert not any(t.is_alive() for t in threads), "ein Thread laeuft nach der Deadline weiter"
        _bewerte_db_fehler(erg_umbenennung.get("fehler"), kontext="Umbenennung")
        _bewerte_db_fehler(erg_upload.get("fehler"), kontext="Import-Upload")
        assert beobachtung == "blockiert", (
            f"der Upload muss an der Typzeile warten, BEVOR er den Ketten-Lock haelt — beobachtet wurde: {beobachtung}"
        )
        assert isinstance(erg_upload.get("wert"), ImportJob), f"Upload nicht durchgelaufen: {erg_upload!r}"
        assert erg_umbenennung.get("wert") == "Import-Zuordnung (umbenannt)"
        assert ImportJob.objects.for_facility(quota_facility).count() == 1
        assert AuditLog.objects.filter(
            facility=quota_facility, action=AuditLog.Action.CONFIG_UPDATE, target_type="DocumentType"
        ).exists(), "die Rechenschaftszeile der Umbenennung fehlt"


def test_vorsperre_entfaellt_ohne_postgresql():
    """Ohne PostgreSQL gibt es keine Zeilensperre — wie beim Advisory-Lock.

    ``FOR KEY SHARE`` ist SQLite-Syntax fremd; der Vendor-Zweig ist deshalb
    kein Schoenheitsfehler, sondern die Bedingung dafuer, dass der
    Speicherpfad auf einem Nicht-PostgreSQL-Backend ueberhaupt laeuft. Die
    Attrappe hat bewusst KEIN ``cursor`` — faellt der Zweig weg, scheitert
    dieser Test sofort.
    """

    class _FremdesBackend:
        vendor = "sqlite"

    echte_verbindung = import_engine.connection
    import_engine.connection = _FremdesBackend()
    try:
        import_engine._lock_job_fk_rows(
            ImportJob(
                facility_id=uuid.uuid4(),
                created_by_id=1,
                target_type=ImportJob.TargetType.CLIENT,
            )
        )
    finally:
        import_engine.connection = echte_verbindung
