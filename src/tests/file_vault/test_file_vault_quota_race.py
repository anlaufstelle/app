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

import ast
import contextlib
import pathlib
import threading
import time
import uuid
from unittest import mock

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
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
from core.services.attachments.crud import quota_backstop_audit_boundary  # noqa: E402
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload  # noqa: E402
from core.services.file_vault.encryption import encrypt_field  # noqa: E402
from core.services.file_vault.quota import lock_facility_storage  # noqa: E402

MB = 1024 * 1024

#: Nutzlast je Upload — klein genug, dass sechs Verschluesselungen die Suite
#: nicht bremsen, gross genug fuer eine saubere MB-Rechnung.
CHUNK = 256 * 1024

#: Obergrenze fuer ``Thread.join`` und ``Barrier.wait``. Kein Sleep-Warten:
#: die Barriere gibt alle Threads gleichzeitig frei, der Rest ist DB-Arbeit.
JOIN_TIMEOUT = 30

#: Begrenztes Fenster, in dem der Soft-Delete seine Zeilensperre haelt, bevor
#: er den Ketten-Lock nimmt. Bewusst eine reine Untergrenze: laeuft der
#: Gegenspieler langsamer, ist der Test trotzdem aussagekraeftig (er haette die
#: Sperren dann erst recht nicht in verdrehter Reihenfolge genommen).
GNADENFENSTER = 0.75

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


def _commit_in_parallel(staged_list, commit):
    """Alle ``staged``-Dateien barrier-synchron committen — je eigener Thread,
    je eigene DB-Verbindung. ``commit(idx, staged)`` ist die Aufruferform
    (direkt, unter Aufrufer-Transaktion, …). Liefert
    ``{index: Attachment | Exception}``.
    """
    barrier = threading.Barrier(len(staged_list), timeout=JOIN_TIMEOUT)
    results: dict[int, object] = {}
    guard = threading.Lock()

    def worker(idx, staged):
        try:
            barrier.wait()
            attachment = commit(idx, staged)
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


def _direkt(event, ft_file, user):
    """Aufruferform „direkter Service-Aufruf ohne eigene Transaktion"."""

    def _commit(idx, staged):
        return commit_staged_upload(event, ft_file, staged, user, sort_order=idx)

    return _commit


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

        results = _commit_in_parallel(staged_list, _direkt(event, ft_file, staff_user))

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

        results = _commit_in_parallel(staged_list, _direkt(event, ft_file, staff_user))

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

    def test_quota_audit_zeile_ueberlebt_die_aufrufer_transaktion(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Auch mit eigener ``atomic()`` des Aufrufers bleibt die Zeile bestehen.

        Der Regelfall (``finalize_staged_files``/``apply_attachment_changes``
        unter der View-Transaktion) verwirft mit dem ``ValidationError`` den
        ganzen Vorgang — Ereignis, Anhaenge, Audit. Der Vorab-Check ersetzt die
        Zeile NICHT: im abgefangenen Rennen hat er erfolgreich geprueft und ist
        ohne Audit zurueckgekehrt. Die Ablehnung wird deshalb am aeussersten
        Transaktionsrand auditiert (``quota_backstop_audit_boundary``).
        """
        event, ft_file = upload_target
        staged = stage(1)[0]
        occupy(3 * MB)

        with pytest.raises(ValidationError), quota_backstop_audit_boundary(), transaction.atomic():
            lock_facility_storage(quota_facility.pk)
            commit_staged_upload(event, ft_file, staged, staff_user)

        entry = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert entry.detail["reason"] == "quota_exceeded"
        assert Attachment.objects.for_facility(quota_facility).count() == 1
        assert not staged.output_path.exists()

    @pytest.mark.parametrize(("anzahl", "plaetze"), [(2, 1), (6, 2)])
    @pytest.mark.parametrize("aufrufer", ["direkt", "aeussere_transaktion", "aeussere_transaktion_mit_ketten_lock"])
    def test_jede_ablehnung_hinterlaesst_eine_dauerhafte_audit_zeile(
        self, anzahl, plaetze, aufrufer, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Refs #2178: die Auditspur haengt nicht an der Aufruferform.

        Drei Aufruferformen, die es im Baum wirklich gibt: der direkte
        Service-Aufruf (Seed/Tests), der Regelfall unter einer
        View-Transaktion und derselbe Fall, wenn die Transaktion den
        Ketten-Lock schon haelt (``create_event``/``update_event`` haben ihre
        Audit-Zeile bereits geschrieben).
        """
        occupy(3 * MB - plaetze * CHUNK)
        event, ft_file = upload_target
        staged_list = stage(anzahl)

        def commit(idx, staged):
            if aufrufer == "direkt":
                return commit_staged_upload(event, ft_file, staged, staff_user, sort_order=idx)
            with quota_backstop_audit_boundary(), transaction.atomic():
                if aufrufer == "aeussere_transaktion_mit_ketten_lock":
                    lock_facility_storage(quota_facility.pk)
                return commit_staged_upload(event, ft_file, staged, staff_user, sort_order=idx)

        results = _commit_in_parallel(staged_list, commit)

        gewinner = [i for i, r in results.items() if isinstance(r, Attachment)]
        verlierer = [i for i, r in results.items() if isinstance(r, Exception)]
        assert len(gewinner) == plaetze, results
        assert all(isinstance(results[i], ValidationError) for i in verlierer), results
        assert _used_bytes(quota_facility) == 3 * MB
        assert AuditLog.objects.filter(
            facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED
        ).count() == len(verlierer), (
            f"jede Ablehnung braucht eine dauerhafte Rechenschaftszeile ({aufrufer}, n={anzahl})"
        )


@pytest.mark.django_db
@pytest.mark.postgres
class TestKontingentSperre:
    """Die Mechanik der Sperre — Reihenfolge und Namensraum."""

    def test_sperre_wird_vor_dem_insert_genommen(self, quota_facility, upload_target, staff_user, stage):
        """Quota usage is checked under its exclusive lock before insertion.

        The shared dynamic-protection gate is a separate lock namespace."""
        event, ft_file = upload_target
        staged = stage(1)[0]

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
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
        """Without a quota, only the audit-chain exclusive lock and no sum remain.

        Dynamic protection additionally requires its shared gate before insertion."""
        event, ft_file = upload_target
        staged = stage(1)[0]
        Settings.objects.filter(facility=quota_facility).update(max_storage_mb=None)
        settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        lock_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
        insert_at = [i for i, s in enumerate(sql) if "INSERT INTO" in s and "core_attachment" in s]
        assert len(lock_at) == 1, f"ohne Kontingent bleibt nur die Ketten-Sperre uebrig: {sql!r}"
        assert lock_at[0] > insert_at[0], "die verbliebene Sperre ist die der Hashkette (nach dem Insert)"
        protection_at = [i for i, query in enumerate(sql) if "pg_advisory_xact_lock_shared(2525," in query]
        assert len(protection_at) == 1
        assert protection_at[0] < insert_at[0]
        assert not [s for s in sql if "SUM(" in s.upper() and "core_attachment" in s]

    def test_mit_kontingent_genau_eine_zusaetzliche_sperre(self, quota_facility, upload_target, staff_user, stage):
        """An enabled quota adds exactly one exclusive chain/quota lock before insertion."""
        event, ft_file = upload_target
        staged = stage(1)[0]

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(event, ft_file, staged, staff_user)

        sql = [q["sql"] for q in ctx.captured_queries]
        assert len([s for s in sql if "pg_advisory_xact_lock(" in s]) == 2

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


def _run_threads(targets):
    """Startet ``targets`` (parameterlose Callables) gleichzeitig und sammelt
    Rueckgabewert bzw. Ausnahme je Index."""
    results: dict[int, object] = {}
    guard = threading.Lock()

    def worker(idx, fn):
        try:
            value = fn()
            with guard:
                results[idx] = value
        except Exception as exc:  # noqa: BLE001 — Sammeltest, Auswertung beim Aufrufer
            with guard:
                results[idx] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, fn)) for i, fn in enumerate(targets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_TIMEOUT)
    assert not any(t.is_alive() for t in threads), f"Thread haengt — Deadlock-Verdacht: {results!r}"
    return results


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestSperrReihenfolge:
    """Refs #2178: (1) Attachment-Zeile, (2) Advisory-Lock — ueberall.

    Der Replace-Pfad schreibt die Vorgaengerzeile erst am Ende. Nahm er den
    Advisory-Lock der Einrichtung davor (Kontingent-Backstop), stand er in
    genau umgekehrter Reihenfolge zu ``soft_delete_attachment_chain``, das die
    Zeile zuerst aktualisiert und den Lock erst mit seiner Audit-Zeile nimmt.
    PostgreSQL loest den Zyklus mit SQLSTATE ``40P01`` auf — ein 500er in einem
    unterstuetzten Servicepfad.
    """

    def test_replace_und_soft_delete_verklemmen_sich_nicht(
        self, quota_facility, upload_target, staff_user, occupy, stage, monkeypatch
    ):
        from core.services.file_vault import audit as audit_module
        from core.services.file_vault import soft_delete_attachment_chain

        owner, ft_file = upload_target
        alt = occupy(CHUNK)
        staged = stage(1)[0]
        zeile_gesperrt = threading.Event()
        echtes_audit = audit_module.audit_attachment_delete

        def verzoegertes_audit(*args, **kwargs):
            """Haelt die Zeilensperre des Soft-Deletes ein begrenztes Fenster lang,
            bevor der Ketten-Lock genommen wird — genau das Fenster, in dem der
            Replace-Pfad seine Sperren erwirbt."""
            zeile_gesperrt.set()
            time.sleep(GNADENFENSTER)
            return echtes_audit(*args, **kwargs)

        monkeypatch.setattr(audit_module, "audit_attachment_delete", verzoegertes_audit)

        def soft_delete():
            return soft_delete_attachment_chain(owner, alt.entry_id, staff_user)

        def replace():
            assert zeile_gesperrt.wait(JOIN_TIMEOUT), "Soft-Delete hat die Zeile nie gesperrt"
            return commit_staged_upload(owner, ft_file, staged, staff_user, supersedes=alt)

        results = _run_threads([soft_delete, replace])

        fehler = {i: r for i, r in results.items() if isinstance(r, Exception)}
        sqlstates = [getattr(getattr(exc, "__cause__", None), "sqlstate", None) for exc in fehler.values()]
        assert "40P01" not in sqlstates, f"Deadlock im Replace-/Soft-Delete-Paar: {results!r}"
        assert not fehler, f"beide Pfade muessen definiert enden: {results!r}"
        assert results[0] == 1, "der Soft-Delete hat genau eine Zeile der Kette markiert"
        assert isinstance(results[1], Attachment)

    def test_replace_sperrt_die_vorgaengerzeile_vor_dem_advisory_lock(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """The replaced attachment row precedes the exclusive chain/quota advisory lock."""
        owner, ft_file = upload_target
        alt = occupy(CHUNK)
        staged = stage(1)[0]

        with CaptureQueriesContext(connection) as ctx:
            commit_staged_upload(owner, ft_file, staged, staff_user, supersedes=alt)

        sql = [q["sql"] for q in ctx.captured_queries]
        row_lock_at = [i for i, s in enumerate(sql) if "FOR UPDATE" in s.upper() and "core_attachment" in s]
        advisory_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
        assert row_lock_at, f"keine Zeilensperre auf der Vorgaengerzeile: {sql!r}"
        assert advisory_at, f"kein Advisory-Lock im Commit-Pfad: {sql!r}"
        assert row_lock_at[0] < advisory_at[0], "Reihenfolge verletzt: Zeile muss vor dem Advisory-Lock kommen"


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestKontingentAuditImViewPfad:
    """Refs #2178: die Rechenschaftszeile ueberlebt den echten
    View-Rollback — gepruft am Request, nicht nur am Service.

    Der Vorab-Check wird fuer diese Faelle stillgelegt. Er ist im echten Rennen
    genau das, was er hier simuliert: er hat erfolgreich geprueft und ist ohne
    Audit zurueckgekehrt, waehrend eine andere Transaktion das Kontingent
    ausschoepfte. Erst dadurch greift der Backstop deterministisch — ohne Threads.

    ``transaction=True`` ist Pflicht: nur dann ist die ``atomic()`` der View die
    aeusserste Transaktion, und nur dann ist der Rollback ein echter Rollback.
    """

    @pytest.fixture
    def ohne_vorab_check(self, monkeypatch):
        from core.services.file_vault import storage as storage_module

        monkeypatch.setattr(storage_module, "enforce_storage_quota", lambda *args, **kwargs: None)

    def _post_datei(self, client, url, daten, slug):
        return client.post(
            url,
            {**daten, slug: SimpleUploadedFile("neu.pdf", _pdf_of(CHUNK), content_type="application/pdf")},
        )

    def test_ereignis_anlegen_auditiert_die_abgewiesene_datei(
        self, client, quota_facility, upload_target, staff_user, occupy, media_root, ohne_vorab_check
    ):
        event, ft_file = upload_target
        occupy(3 * MB)
        client.force_login(staff_user)

        resp = self._post_datei(
            client,
            reverse("core:event_create"),
            {
                "document_type": str(event.document_type.pk),
                "occurred_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
            },
            ft_file.slug,
        )

        assert resp.status_code == 200, "das Formular wird mit Fehler neu gerendert, kein 500"
        assert Event.objects.for_facility(quota_facility).count() == 1, "kein neues Ereignis"
        assert Attachment.objects.for_facility(quota_facility).count() == 1, "keine neue Anhangszeile"
        entry = AuditLog.objects.get(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED)
        assert entry.detail["reason"] == "quota_exceeded"

    def test_ereignis_aendern_auditiert_die_abgewiesene_datei(
        self, client, quota_facility, upload_target, staff_user, occupy, media_root, ohne_vorab_check
    ):
        event, ft_file = upload_target
        occupy(3 * MB)
        client.force_login(staff_user)

        resp = self._post_datei(
            client,
            reverse("core:event_update", kwargs={"pk": event.pk}),
            {
                "document_type": str(event.document_type.pk),
                "occurred_at": event.occurred_at.strftime("%Y-%m-%dT%H:%M"),
            },
            ft_file.slug,
        )

        # Rejection retains the bound form and its durable quota audit entry.
        assert resp.status_code == 200
        assert resp.context["data_form"].is_bound
        assert "Datei bitte erneut auswählen:" in resp.content.decode()
        assert Attachment.objects.for_facility(quota_facility).count() == 1
        assert (
            AuditLog.objects.filter(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED).count()
            == 1
        )


@pytest.mark.django_db
@pytest.mark.postgres
class TestQueryBudget:
    """Pin exact quota overhead relative to a disabled quota backstop."""

    @staticmethod
    @contextlib.contextmanager
    def _ohne_backstop():
        from core.services.file_vault import storage as storage_module

        with (
            mock.patch.object(storage_module, "check_storage_quota_at_commit"),
            mock.patch.object(storage_module, "_lock_superseded_row"),
        ):
            yield

    def _messen(self, commit):
        with self._ohne_backstop(), CaptureQueriesContext(connection) as referenz:
            commit(0)
        with CaptureQueriesContext(connection) as gemessen:
            commit(1)
        return len(gemessen.captured_queries) - len(referenz.captured_queries)

    @pytest.mark.parametrize(("with_quota", "expected"), [(True, 5), (False, 1)])
    def test_query_growth_when_adding(
        self, with_quota, expected, quota_facility, upload_target, staff_user, stage, settings
    ):
        """With quota: protection gate, configuration, quota lock and both usage sums.

        Without quota, only the configuration query is added."""
        event, ft_file = upload_target
        staged_list = stage(2)
        if not with_quota:
            Settings.objects.filter(facility=quota_facility).update(max_storage_mb=None)
            settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0

        delta = self._messen(lambda i: commit_staged_upload(event, ft_file, staged_list[i], staff_user, sort_order=i))

        assert delta == expected, f"Query growth when adding: {delta} instead of {expected}"

    @pytest.mark.parametrize(("with_quota", "expected"), [(True, 6), (False, 2)])
    def test_query_growth_when_replacing(
        self, with_quota, expected, quota_facility, upload_target, staff_user, occupy, stage, settings
    ):
        """Replacement also locks the predecessor row before the quota/audit key."""
        event, ft_file = upload_target
        vorgaenger = [occupy(CHUNK), occupy(CHUNK)]
        staged_list = stage(2)
        if not with_quota:
            Settings.objects.filter(facility=quota_facility).update(max_storage_mb=None)
            settings.FILE_VAULT_DEFAULT_FACILITY_QUOTA_MB = 0

        delta = self._messen(
            lambda i: commit_staged_upload(event, ft_file, staged_list[i], staff_user, supersedes=vorgaenger[i])
        )

        assert delta == expected, f"Query growth when replacing: {delta} instead of {expected}"


VIEW_MODULE_NAMES = ("events.py", "clients.py", "cases.py")
ATTACHMENT_COMMIT_ENTRIES = ("finalize_staged_files", "apply_attachment_changes")


def _with_context_names(node: ast.With) -> list[str]:
    """Die Namen der Kontextmanager eines ``with`` — ``f()`` wie ``a.b()``."""
    namen = []
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Name):
                namen.append(func.id)
            elif isinstance(func, ast.Attribute):
                namen.append(func.attr)
    return namen


def _commit_call_sites(tree: ast.AST):
    """``(name, zeile, [with-Namen von aussen nach innen])`` je Aufrufstelle."""
    sites: list[tuple[str, int, list[list[str]]]] = []

    def walk(node, stack):
        if isinstance(node, ast.With):
            stack = [*stack, _with_context_names(node)]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name in ATTACHMENT_COMMIT_ENTRIES:
                    sites.append((name, child.lineno, stack))
            walk(child, stack)

    walk(tree, [])
    return sites


@pytest.mark.architecture
class TestAudRandAnJederAufrufstelle:
    """Refs #2178: jede View-Aufrufstelle traegt den Audit-Rand.

    Ein Aufrufer, der ``finalize_staged_files``/``apply_attachment_changes`` in
    einer eigenen ``atomic()`` haelt und den Rand vergisst, verliert die
    ``ATTACHMENT_QUOTA_EXCEEDED``-Zeile wieder (die Ablehnung selbst bleibt —
    die Ausnahme IST ein ``ValidationError``). Der Guard scannt die drei
    Anlage-/Aenderungs-Views statt nur den Vertrag zu beschreiben.
    """

    def test_jede_view_aufrufstelle_liegt_in_einem_audit_rand(self):
        views = pathlib.Path(__file__).resolve().parents[2] / "core" / "views"
        gefunden = 0
        for name in VIEW_MODULE_NAMES:
            quelle = views / name
            tree = ast.parse(quelle.read_text(encoding="utf-8"))
            for aufruf, zeile, stack in _commit_call_sites(tree):
                gefunden += 1
                atomic_at = [i for i, namen in enumerate(stack) if "atomic" in namen]
                rand_at = [i for i, namen in enumerate(stack) if "quota_backstop_audit_boundary" in namen]
                assert atomic_at, f"{name}:{zeile} — {aufruf} ohne umgebende transaction.atomic()"
                assert rand_at, (
                    f"{name}:{zeile} — {aufruf} ohne quota_backstop_audit_boundary(); "
                    "ohne den Rand geht die ATTACHMENT_QUOTA_EXCEEDED-Zeile im Rollback verloren"
                )
                assert min(rand_at) < min(atomic_at), (
                    f"{name}:{zeile} — der Audit-Rand muss AUSSERHALB der Transaktion liegen, "
                    "sonst rollt die Audit-Zeile mit zurueck"
                )
        assert gefunden == 6, f"erwartet werden sechs View-Aufrufstellen, gefunden: {gefunden}"


@pytest.mark.django_db
@pytest.mark.postgres
class TestAuditIdempotenz:
    """``write_audit`` schreibt genau einmal — auch bei verschachtelten Raendern."""

    def test_zweiter_rand_schreibt_die_zeile_nicht_doppelt(self, quota_facility, upload_target, staff_user):
        from core.services.file_vault.policy import StorageQuotaBackstopError
        from core.services.file_vault.quota import StorageUsage

        event, _ft = upload_target
        exc = StorageQuotaBackstopError(
            facility=quota_facility,
            user=staff_user,
            owner=event,
            usage=StorageUsage(used_bytes=3 * MB, trash_bytes=0, limit_bytes=3 * MB),
            requested_bytes=CHUNK,
        )

        # Reihenfolge: ``pytest.raises`` aussen, dann zwei geschachtelte Raender —
        # der innere schreibt, der aeussere darf nicht noch einmal schreiben.
        with pytest.raises(StorageQuotaBackstopError), quota_backstop_audit_boundary(), quota_backstop_audit_boundary():
            raise exc

        assert exc.audited
        assert (
            AuditLog.objects.filter(facility=quota_facility, action=AuditLog.Action.ATTACHMENT_QUOTA_EXCEEDED).count()
            == 1
        )
        # Fehlerklasse und Text sind die des Vorab-Checks (Views reagieren unveraendert).
        assert isinstance(exc, ValidationError)
        assert "Speicherkontingent der Einrichtung erschöpft" in exc.messages[0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestBatchSperrReihenfolge:
    """Refs #2178: der Batch-Einstieg sperrt VOR dem ersten Commit.

    Ein einzelner ``commit_staged_upload`` sperrt seine Vorgaengerzeile selbst.
    Setzt ein Aufrufer aber MEHRERE Commits in derselben Transaktion ab, genuegt
    das nicht: Der erste Commit nimmt den Advisory-Lock der Einrichtung und
    haelt ihn bis zum Ende der aeusseren Transaktion; die Zeilensperre des
    ZWEITEN Commits faellt danach. Gegen einen gleichzeitigen Soft-Delete der
    zweiten Kette ist das wieder die verdrehte Reihenfolge — PostgreSQL bricht
    mit SQLSTATE ``40P01`` ab. Der Batch-Einstieg sperrt deshalb alle Zeilen der
    Batch-Menge in EINER Abfrage, bevor der erste Commit laeuft.
    """

    def test_zwei_ersetzungen_im_batch_gegen_soft_delete(
        self, quota_facility, upload_target, staff_user, occupy, stage, monkeypatch
    ):
        from core.services.attachments.crud import apply_attachment_changes, replace_upload_key
        from core.services.file_vault import audit as audit_module
        from core.services.file_vault import soft_delete_attachment_chain

        event, ft_file = upload_target
        alt = [occupy(CHUNK), occupy(CHUNK)]
        event.data_json = {
            ft_file.slug: {
                "__files__": True,
                "entries": [{"id": str(alt[0].pk), "sort": 0}, {"id": str(alt[1].pk), "sort": 1}],
            }
        }
        event.save(update_fields=["data_json"])
        staged_list = stage(2)
        staged_by_key = {
            replace_upload_key(ft_file.slug, alt[0].entry_id): [staged_list[0]],
            replace_upload_key(ft_file.slug, alt[1].entry_id): [staged_list[1]],
        }

        zeile_gesperrt = threading.Event()
        echtes_audit = audit_module.audit_attachment_delete

        def verzoegertes_audit(*args, **kwargs):
            zeile_gesperrt.set()
            time.sleep(GNADENFENSTER)
            return echtes_audit(*args, **kwargs)

        monkeypatch.setattr(audit_module, "audit_attachment_delete", verzoegertes_audit)

        def soft_delete():
            """Loescht die ZWEITE Kette — die der Batch als letzte ersetzt."""
            return soft_delete_attachment_chain(event, alt[1].entry_id, staff_user)

        def batch_ersetzen():
            assert zeile_gesperrt.wait(JOIN_TIMEOUT), "Soft-Delete hat die Zeile nie gesperrt"
            with quota_backstop_audit_boundary(), transaction.atomic():
                apply_attachment_changes(
                    event,
                    staff_user,
                    {},
                    {},
                    {},
                    event.document_type,
                    staged_by_key=staged_by_key,
                )
            return "batch"

        results = _run_threads([soft_delete, batch_ersetzen])

        fehler = {i: r for i, r in results.items() if isinstance(r, Exception)}
        sqlstates = [getattr(getattr(exc, "__cause__", None), "sqlstate", None) for exc in fehler.values()]
        assert "40P01" not in sqlstates, f"Deadlock im Batch-/Soft-Delete-Paar: {results!r}"
        assert not fehler, f"beide Vorgaenge muessen definiert enden: {results!r}"
        assert results[0] == 1
        assert results[1] == "batch"

    def test_batch_sperrt_alle_zeilen_vor_dem_ersten_advisory_lock(
        self, quota_facility, upload_target, staff_user, occupy, stage
    ):
        """Die Reihenfolge im Einzellauf: EINE ``FOR UPDATE``-Abfrage ueber die
        ganze Batch-Menge, und zwar vor dem ersten ``pg_advisory_xact_lock``."""
        from core.services.attachments.crud import apply_attachment_changes, replace_upload_key

        event, ft_file = upload_target
        alt = [occupy(CHUNK), occupy(CHUNK)]
        event.data_json = {
            ft_file.slug: {
                "__files__": True,
                "entries": [{"id": str(alt[0].pk), "sort": 0}, {"id": str(alt[1].pk), "sort": 1}],
            }
        }
        event.save(update_fields=["data_json"])
        staged_list = stage(2)
        staged_by_key = {
            replace_upload_key(ft_file.slug, alt[0].entry_id): [staged_list[0]],
            replace_upload_key(ft_file.slug, alt[1].entry_id): [staged_list[1]],
        }

        with transaction.atomic(), CaptureQueriesContext(connection) as ctx:
            apply_attachment_changes(event, staff_user, {}, {}, {}, event.document_type, staged_by_key=staged_by_key)

        sql = [q["sql"] for q in ctx.captured_queries]
        batch_locks = [i for i, s in enumerate(sql) if "FOR UPDATE" in s.upper() and "core_attachment" in s]
        advisory_at = [i for i, s in enumerate(sql) if "pg_advisory_xact_lock(" in s]
        assert batch_locks, f"keine Batch-Zeilensperre: {sql!r}"
        assert advisory_at, f"kein Advisory-Lock im Batch: {sql!r}"
        assert batch_locks[0] < advisory_at[0], "die Batch-Sperre muss VOR dem ersten Advisory-Lock liegen"
        # Beide Ketten stehen in DERSELBEN Abfrage (feste Reihenfolge nach pk).
        assert "IN (" in sql[batch_locks[0]].upper() or "= ANY" in sql[batch_locks[0]].upper(), sql[batch_locks[0]]


@pytest.mark.django_db(transaction=True)
@pytest.mark.postgres
class TestAutocommitServiceVertrag:
    """Refs #2178: der transaktionslose Service-Einstieg bleibt nutzbar.

    ``apply_attachment_changes`` oeffnet keine eigene Transaktion. Fuer Aufrufer
    **ohne** aeussere Transaktion (dokumentierter Bequemlichkeitsmodus, direkte
    Service-Aufrufe) gibt es nichts vorzusperren: Jede Storage-Operation schuetzt
    ihre eigene Mutation in ihrer eigenen kurzen Transaktion, und kein Aufrufer
    haelt den Advisory-Lock ueber mehrere Operationen hinweg. Eine
    ``select_for_update``-Vorsperre waere dort nicht nur nutzlos, sondern ein
    Abbruch (``TransactionManagementError``).
    """

    @pytest.fixture
    def marker(self, upload_target):
        """Setzt den ``__files__``-Marker des Owners auf die uebergebenen Zeilen."""
        event, ft_file = upload_target

        def _marker(*attachments):
            event.data_json = {
                ft_file.slug: {
                    "__files__": True,
                    "entries": [{"id": str(att.pk), "sort": i} for i, att in enumerate(attachments)],
                }
            }
            event.save(update_fields=["data_json"])
            return event

        return _marker

    def test_entfernen_ohne_aufrufer_transaktion(self, quota_facility, upload_target, staff_user, occupy, marker):
        from core.services.attachments.crud import apply_attachment_changes

        event, ft_file = upload_target
        alt = occupy(CHUNK)
        marker(alt)
        assert not connection.in_atomic_block, "dieser Fall prueft gerade den Autocommit-Modus"

        apply_attachment_changes(
            event,
            staff_user,
            {f"{ft_file.slug}__remove": str(alt.entry_id)},
            {},
            {},
            event.document_type,
            staged_by_key={},
        )

        alt.refresh_from_db()
        assert alt.deleted_at is not None, "REMOVE muss auch ohne Aufrufer-Transaktion wirken"

    def test_ersetzen_ohne_aufrufer_transaktion(self, quota_facility, upload_target, staff_user, occupy, stage, marker):
        from core.services.attachments.crud import apply_attachment_changes, replace_upload_key

        event, ft_file = upload_target
        alt = occupy(CHUNK)
        marker(alt)
        staged = stage(1)[0]
        assert not connection.in_atomic_block

        apply_attachment_changes(
            event,
            staff_user,
            {},
            {},
            {},
            event.document_type,
            staged_by_key={replace_upload_key(ft_file.slug, alt.entry_id): [staged]},
        )

        alt.refresh_from_db()
        assert not alt.is_current and alt.superseded_by_id is not None, "REPLACE muss den Vorgaenger abloesen"
        assert Attachment.objects.for_facility(quota_facility).count() == 2

    def test_hinzufuegen_ohne_aufrufer_transaktion(self, quota_facility, upload_target, staff_user, stage, marker):
        from core.services.attachments.crud import apply_attachment_changes

        event, ft_file = upload_target
        staged = stage(1)[0]
        assert not connection.in_atomic_block

        apply_attachment_changes(
            event, staff_user, {}, {}, {}, event.document_type, staged_by_key={ft_file.slug: [staged]}
        )

        assert Attachment.objects.for_facility(quota_facility).count() == 1
        assert staged.output_path.exists()

    def test_bequemlichkeitsmodus_ohne_staged_by_key(self, quota_facility, upload_target, staff_user, occupy, marker):
        """Der ausdruecklich zugesicherte Modus ``staged_by_key=None``."""
        from core.services.attachments.crud import apply_attachment_changes

        event, ft_file = upload_target
        alt = occupy(CHUNK)
        marker(alt)
        assert not connection.in_atomic_block

        apply_attachment_changes(
            event,
            staff_user,
            {f"{ft_file.slug}__remove": str(alt.entry_id)},
            {},
            {},
            event.document_type,
        )

        alt.refresh_from_db()
        assert alt.deleted_at is not None
