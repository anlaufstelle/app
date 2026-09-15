"""Nebenlaeufige Uploads am Speicherkontingent (Refs #2178).

Die Kontingent-Pruefung sitzt in ``prepare_encrypted_upload`` — bewusst **vor**
Virenscan und Verschluesselung und damit ausserhalb jeder Transaktion
(``enforce_storage_quota``, Refs #2129). Ihr Ergebnis ist deshalb nur eine
Momentaufnahme: zwischen „Summe gelesen" und „Attachment-Zeile geschrieben"
liegt der gesamte teure Pipeline-Teil. Mehrere gleichzeitige Uploads einer
Einrichtung lesen dieselbe alte Summe, kommen alle durch und ueberziehen das
Kontingent gemeinsam um ``Nebenlaeufigkeit x max_file_size_mb``. Der
``pending_bytes``-Offset deckt nur die Schleife INNERHALB eines Requests ab.

Diese Suite nagelt den Backstop im Commit-Pfad fest:

* ``_commit_staged_upload`` liest die Summe unter der per-Einrichtung-Sperre
  ein zweites Mal und weist ueberzaehlige Uploads ab — Ueberzug 0 Byte,
  unabhaengig von der Nebenlaeufigkeit,
* die Sperre ist **dieselbe**, die die AuditLog-Hashkette ohnehin je
  Einrichtung nimmt (ein einziger Lock je Einrichtung ⇒ es gibt keine
  Lock-Reihenfolge, die sich verdrehen koennte),
* sie wird VOR dem Insert genommen (ein Nachrechnen ohne Sperre schliesst das
  Rennen unter READ COMMITTED nicht: jede Transaktion sieht ihre eigene,
  unbestaetigte Zeile, aber keine fremde),
* die Abweisung traegt dieselbe Fehlerklasse und denselben Text wie der
  Vorab-Check und hinterlaesst weder ``.enc``-Datei noch Attachment-Zeile,
* die ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile wird NACH dem Rollback geschrieben
  (innerhalb der zurueckgerollten Transaktion verschwaende sie mit ihr) —
  samt der dokumentierten Grenze fuer Aufrufer mit eigener ``atomic()``.

Eigene Datei, weil der Renn-Nachweis ``django_db(transaction=True)`` und echte
Nebenlaeufigkeit auf eigenen DB-Verbindungen braucht, waehrend die uebrigen
Kontingent-Tests in ``test_file_vault_quota.py`` im schnellen
Transaktions-Rollback laufen.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

# ``prepare_encrypted_upload`` prueft Magic-Bytes via libmagic (#610).
try:
    import magic

    magic.from_buffer(b"%PDF-1.4\n", mime=True)
except Exception as _libmagic_exc:  # noqa: BLE001 — libmagic-Shared-Library fehlt
    pytest.skip(
        f"libmagic nicht lauffaehig ({_libmagic_exc}) — Tests erfordern libmagic1.",
        allow_module_level=True,
    )

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate, Settings  # noqa: E402
from core.models.attachment import Attachment  # noqa: E402
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload  # noqa: E402
from core.services.file_vault.encryption import encrypt_field  # noqa: E402

MB = 1024 * 1024

#: Nutzlast je Upload — klein genug, dass sechs Verschluesselungen die Suite
#: nicht bremsen, gross genug fuer eine saubere MB-Rechnung.
CHUNK = 256 * 1024

#: Obergrenze fuer ``Thread.join`` und ``Barrier.wait``. Kein Sleep-Warten:
#: die Barriere gibt alle Threads gleichzeitig frei, der Rest ist DB-Arbeit.
JOIN_TIMEOUT = 30

_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _pdf_of(size: int) -> bytes:
    """Ein PDF von **exakt** ``size`` Bytes — Fuellung als PDF-Kommentarzeile.

    Die Groesse muss exakt sein: die Tests rechnen das Kontingent so aus, dass
    genau ``k`` Uploads hineinpassen und der naechste nicht mehr.
    """
    filler = size - len(_PDF) - 2
    if filler < 0:
        raise ValueError(f"size {size} ist kleiner als das Minimal-PDF ({len(_PDF) + 2} Bytes)")
    head, tail = _PDF[:9], _PDF[9:]
    return head + b"%" + b"A" * filler + b"\n" + tail


@pytest.fixture
def media_root(settings, tmp_path):
    """Verschluesselte Dateien in ``tmp_path`` statt im Repo-``media/``."""
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def quota_facility(facility, settings):
    """Einrichtung mit 3-MB-Kontingent (Betreiber-Backstop abgeschaltet)."""
    settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0
    Settings.objects.update_or_create(
        facility=facility,
        defaults={"allowed_file_types": "pdf", "max_file_size_mb": 10, "max_storage_mb": 3},
    )
    return facility


@pytest.fixture
def upload_target(quota_facility, staff_user):
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
        created_by=staff_user,
    )
    return event, ft_file


@pytest.fixture
def occupy(quota_facility, upload_target, staff_user):
    """Belege ``size`` Bytes ohne echte Datei — fuer die Summe zaehlt die Zeile."""
    event, ft_file = upload_target

    def _occupy(size: int):
        return Attachment.objects.create(
            facility=quota_facility,
            event=event,
            field_template=ft_file,
            storage_filename=f"{uuid.uuid4()}.enc",
            original_filename_encrypted=encrypt_field("bestand.pdf"),
            file_size=size,
            mime_type="application/pdf",
            detected_mime="application/pdf",
            created_by=staff_user,
            is_current=True,
            entry_id=uuid.uuid4(),
        )

    return _occupy


@pytest.fixture
def stage(quota_facility, staff_user, media_root):
    """Staged ``count`` Uploads a ``CHUNK`` Bytes — wie im echten Request VOR
    der Transaktion (Scan + Verschluesselung), Kontingent-Vorabcheck inklusive.
    """

    def _stage(count: int):
        staged = []
        for i in range(count):
            uploaded = SimpleUploadedFile(f"neu-{i}.pdf", _pdf_of(CHUNK), content_type="application/pdf")
            staged.append(prepare_encrypted_upload(quota_facility, uploaded, staff_user, owner=None))
        return staged

    return _stage


def _commit_in_parallel(staged_list, event, ft_file, user):
    """Alle ``staged``-Dateien barrier-synchron committen — je eigener Thread,
    je eigene DB-Verbindung. Liefert ``{index: Attachment | Exception}``.
    """
    barrier = threading.Barrier(len(staged_list), timeout=JOIN_TIMEOUT)
    results: dict[int, object] = {}
    guard = threading.Lock()

    def worker(idx, staged):
        try:
            barrier.wait()
            attachment = commit_staged_upload(event, ft_file, staged, user, sort_order=idx)
            with guard:
                results[idx] = attachment
        except Exception as exc:  # noqa: BLE001 — Sammeltest, Auswertung unten
            with guard:
                results[idx] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, s)) for i, s in enumerate(staged_list)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), "Worker-Thread haengt — Deadlock-Verdacht"
    return results


def _used_bytes(facility) -> int:
    return Attachment.objects.for_facility(facility).aggregate(s=Sum("file_size"))["s"] or 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestKontingentGegenNebenlaeufigeUploads:
    """Refs #2178 (Red-Team B-13): Ueberzug 0 Byte, egal wie viele parallel."""

    def test_zwei_parallele_uploads_am_limit_gewinnt_nur_einer(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Platz fuer genau EINE Datei, zwei gleichzeitige Commits."""
        limit = 3 * MB
        occupy(limit - CHUNK)
        event, ft_file = upload_target
        staged_list = stage(2)

        results = _commit_in_parallel(staged_list, event, ft_file, staff_user)

        gewinner = [i for i, r in results.items() if isinstance(r, Attachment)]
        verlierer = [i for i, r in results.items() if isinstance(r, Exception)]
        assert len(gewinner) == 1, (
            f"Am Limit darf genau ein gleichzeitiger Upload durchkommen — durchgekommen: {len(gewinner)}, "
            f"Ergebnisse: {results!r}"
        )
        assert all(isinstance(results[i], ValidationError) for i in verlierer), (
            f"Der abgewiesene Upload muss dieselbe Fehlerklasse liefern wie der Vorab-Check: {results!r}"
        )
        assert _used_bytes(quota_facility) <= limit

    def test_sechs_parallele_uploads_ueberziehen_das_kontingent_nicht(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Platz fuer genau ZWEI Dateien, sechs gleichzeitige Commits.

        Das ist der gemessene Befund aus Runde 6 im Kleinen: ohne Backstop
        kommen alle sechs durch, der Ueberzug skaliert mit der Nebenlaeufigkeit.
        """
        limit = 3 * MB
        occupy(limit - 2 * CHUNK)
        event, ft_file = upload_target
        staged_list = stage(6)

        results = _commit_in_parallel(staged_list, event, ft_file, staff_user)

        gewinner = [i for i, r in results.items() if isinstance(r, Attachment)]
        verlierer = [i for i, r in results.items() if isinstance(r, Exception)]
        assert len(gewinner) == 2, (
            f"Es ist Platz fuer genau zwei Dateien — durchgekommen: {len(gewinner)}, Ergebnisse: {results!r}"
        )
        used = _used_bytes(quota_facility)
        assert used <= limit, f"Ueberzug von {used - limit} Byte ueber dem Kontingent"
        # Grenzwert ist inklusiv: die zwei Gewinner fuellen es exakt auf.
        assert used == limit

        # Genau eine Attachment-Zeile je Gewinner, keine fuer die Verlierer.
        assert Attachment.objects.for_facility(quota_facility).count() == 1 + len(gewinner)

        # Je abgewiesenem Upload eine Rechenschaftszeile — und kein
        # SECURITY_VIOLATION (erschoepftes Kontingent ist Betrieb, kein Angriff).
        assert AuditLog.objects.filter(
            facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED
        ).count() == len(verlierer)
        assert not AuditLog.objects.filter(facility=quota_facility, action=AuditLog.Action.SECURITY_VIOLATION).exists()

        # Die gestagte ``.enc`` des Verlierers ist weg, die des Gewinners liegt.
        for i in verlierer:
            assert not staged_list[i].output_path.exists(), f"verwaiste .enc-Datei von Upload {i}"
        for i in gewinner:
            assert staged_list[i].output_path.exists()

    def test_quota_audit_zeile_ueberlebt_ohne_aufrufer_transaktion(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Ohne umgebende ``atomic()`` wird die Audit-Zeile committet.

        Sie entsteht NACH dem Rollback der Commit-Transaktion — innerhalb
        dieser Transaktion geschrieben, waere sie mit ihr verschwunden.
        """
        event, ft_file = upload_target
        # Stagen VOR dem Belegen: der Vorab-Check in ``prepare_encrypted_upload``
        # sieht das Kontingent noch frei — genau das Fenster, das der Backstop
        # schliesst.
        staged = stage(1)[0]
        occupy(3 * MB)

        with pytest.raises(ValidationError):
            commit_staged_upload(event, ft_file, staged, staff_user)

        entry = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert entry.target_type == "Attachment"
        assert entry.detail["reason"] == "quota_exceeded"
        assert entry.detail["limit_mb"] == 3
        assert Attachment.objects.for_facility(quota_facility).count() == 1
        assert not staged.output_path.exists()

    def test_quota_audit_zeile_teilt_das_schicksal_der_aufrufer_transaktion(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Dokumentierte Grenze: mit eigener ``atomic()`` des Aufrufers rollt der
        ``ValidationError`` die Audit-Zeile mit zurueck.

        Genau so verhalten sich die produktiven Pfade (``finalize_staged_files``
        / ``apply_attachment_changes`` unter der View-Transaktion): der ganze
        Vorgang — Ereignis, Anhaenge, Audit — wird verworfen. Die Rechenschaft
        traegt dort der Vorab-Check, der VOR jeder Transaktion auditiert.
        Sicherheitswirksam ist nur, dass die Zeile nicht entsteht; der
        Ueberzug bleibt 0.
        """
        event, ft_file = upload_target
        staged = stage(1)[0]
        occupy(3 * MB)

        with pytest.raises(ValidationError), transaction.atomic():
            commit_staged_upload(event, ft_file, staged, staff_user)

        assert not AuditLog.objects.filter(
            facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED
        ).exists()
        assert Attachment.objects.for_facility(quota_facility).count() == 1
        assert not staged.output_path.exists()


@pytest.mark.django_db
@pytest.mark.postgres
class TestKontingentSperre:
    """Die Mechanik der Sperre — Reihenfolge und Namensraum."""

    def test_sperre_wird_vor_dem_insert_genommen(self, quota_facility, upload_target, staff_user, stage):
        """Ein Nachrechnen NACH dem Insert reicht unter READ COMMITTED nicht:
        jede Transaktion sieht ihre eigene unbestaetigte Zeile, aber keine
        fremde. Die Summe muss deshalb unter der Sperre und VOR dem Insert
        gelesen werden.
        """
        event, ft_file = upload_target
        staged = stage(1)[0]

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock" in s]
        insert_at = [i for i, s in enumerate(sql) if "INSERT INTO" in s and "core_attachment" in s]
        assert lock_at, f"keine Einrichtungs-Sperre im Commit-Pfad: {sql!r}"
        assert insert_at, f"kein Attachment-Insert im Commit-Pfad: {sql!r}"
        assert lock_at[0] < insert_at[0], "die Sperre muss VOR dem Insert genommen werden"

    def test_sperre_nutzt_denselben_namen_wie_die_audit_hashkette(self, quota_facility):
        """Ein einziger Lock je Einrichtung — deshalb gibt es keine
        Lock-Reihenfolge, die sich zwischen Pfaden verdrehen koennte.

        Ein eigener Namensraum waere hier ein Deadlock-Risiko: der Commit-Pfad
        laeuft produktiv in einer Transaktion, die ueber ``create_event``/
        ``update_event`` den Ketten-Lock BEREITS haelt (Reihenfolge Kette →
        Kontingent), waehrend ein Aufrufer ohne vorherige Audit-Zeile die
        umgekehrte Reihenfolge naehme.
        """
        from core.services.audit.chain import advisory_lock_name
        from core.services.file_vault.quota import storage_lock_name

        assert storage_lock_name(quota_facility.pk) == advisory_lock_name(quota_facility.pk)

    def test_ohne_kontingent_weder_sperre_noch_summe(self, quota_facility, upload_target, staff_user, stage, settings):
        """Einrichtungen ohne wirksames Kontingent zahlen fuer den Backstop nur
        die Konfigurationsfrage — keine zweite Sperre, keine Summen-Query.

        Uebrig bleibt genau der Lock-Aufruf, den die AuditLog-Hashkette ohnehin
        nimmt; er faellt NACH dem Insert an.
        """
        event, ft_file = upload_target
        staged = stage(1)[0]
        Settings.objects.filter(facility=quota_facility).update(max_storage_mb=None)
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock" in s]
        insert_at = [i for i, s in enumerate(sql) if "INSERT INTO" in s and "core_attachment" in s]
        assert len(lock_at) == 1, f"ohne Kontingent bleibt nur die Ketten-Sperre uebrig: {sql!r}"
        assert lock_at[0] > insert_at[0], "die verbliebene Sperre ist die der Hashkette (nach dem Insert)"
        assert not [s for s in sql if "SUM(" in s.upper() and "core_attachment" in s]

    def test_mit_kontingent_genau_eine_zusaetzliche_sperre(self, quota_facility, upload_target, staff_user, stage):
        """Mit Kontingent kommt genau EIN Lock-Aufruf hinzu (vor dem Insert) —
        derselbe Schluessel, den die Hashkette danach ohnehin haelt."""
        event, ft_file = upload_target
        staged = stage(1)[0]

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        assert len([s for s in sql if "pg_advisory_xact_lock" in s]) == 2

    def test_ohne_postgres_entfaellt_die_sperre(self, quota_facility):
        """Auf SQLite gibt es keinen Advisory-Lock — wie in ``assign_chain_fields``.

        Der Backstop bleibt dort trotzdem wirksam (die Summe wird gelesen), nur
        ohne Serialisierung; echte Nebenlaeufigkeit gibt es dort ohnehin nicht.
        """
        from unittest import mock

        from core.services.file_vault.quota import lock_facility_storage

        fake_connection = mock.MagicMock()
        fake_connection.vendor = "sqlite"
        with mock.patch("core.services.file_vault.quota.connection", fake_connection):
            lock_facility_storage(quota_facility.pk)

        fake_connection.cursor.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestKontingentSperreFailLoud:
    """Ausserhalb einer Transaktion waere die Xact-Sperre ein lautloser No-op."""

    def test_sperre_ausserhalb_einer_transaktion_ist_fail_loud(self, quota_facility):
        """``pg_advisory_xact_lock`` im Autocommit-Modus liefe in seiner eigenen
        impliziten Transaktion und waere mit dem Ende des ``SELECT`` schon
        wieder frei — es wuerde also nichts serialisiert, ohne dass es auffiele.
        """
        from core.services.file_vault.quota import lock_facility_storage

        assert not connection.in_atomic_block
        with pytest.raises(RuntimeError, match="ausserhalb einer Transaktion"):
            lock_facility_storage(quota_facility.pk)
