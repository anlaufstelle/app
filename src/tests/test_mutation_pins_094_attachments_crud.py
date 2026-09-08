"""Pin-Tests der Prio-1-Survivor-Triage zu ``core.services.attachments.crud``.

Quelle: ``src/core/services/attachments/crud.py`` (Schreibpfade der
Anhang-Domaene, M24/AP2, Refs #1738/#1956/#1874/#2123). Gepinnt wird das
Prio-1-Set der Survivor-Triage des Checkpoint-Deltas: 28 bestaetigte
Testluecken.

Die Luecken gruppieren sich in fuenf Familien:

* **Akteurs-Weitergabe** — ``user`` faellt auf dem Weg zu
  ``commit_staged_upload``/``soft_delete_attachment_chain``/
  ``prepare_encrypted_upload`` auf ``None``. Weder ``Attachment.created_by``
  noch der ``AuditLog``-Akteur (ATTACHMENT_UPLOAD/ATTACHMENT_DELETE) war
  bislang irgendwo zugesichert — ein Nachvollziehbarkeits-Verlust, den kein
  Verhaltenstest bemerkt haette (die FK sind nullable, es kracht nichts).
* **Owner-Weitergabe** — ``owner=owner`` faellt beim Staging auf den
  Signatur-Default ``None`` und nimmt der SECURITY_VIOLATION-Attribution
  ihren Bezug (ADR-039 §5).
* **``continue`` → ``break``** — drei Schleifen-Guards, die genau EINEN
  Eintrag ueberspringen sollen. Als ``break`` verlieren sie alle
  nachfolgenden, gueltigen Felder/Entries: stiller Datenverlust bzw. eine
  Verfuegbarkeits-Regression im R11-Sensitivity-Gate.
* **``update_fields``** — feld-gescopte Saves (``["data_json"]``) gegen
  ``None`` (Voll-Save). Der Unterschied ist nur an der ausgefuehrten
  UPDATE-Spaltenliste bzw. an einem verlorenen Konkurrenz-Update sichtbar.
* **Sperr-Praedikat** — ``_lock_owner_row`` sperrt mit ``filter(pk=...,
  facility=...)``. Faellt ein Teil des Praedikats weg, wird gar keine oder
  die FALSCHE Zeile gesperrt; das ``FOR UPDATE`` steht trotzdem im SQL, die
  Bestandstests (Regex auf Tabellenname + ``FOR UPDATE``) koennen das nicht
  unterscheiden. Gepinnt wird deshalb die Parameterliste der Sperr-Query.

Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: eine Testklasse
je Funktion; jeder Test nennt seine Mutanten in der Docstring samt dem Wert,
der beim Mutanten abweicht; Zusicherungen vergleichen EXAKT (``==`` bzw.
``is``), nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); keine
Fixture-Faken — es laufen echte Model-Objekte durch den echten File-Vault;
KEIN Import aus ``test_rls_functional`` (dessen modulweiter Skip unter
``MUTANT_UNDER_TEST`` wuerde jeden Pin entwerten). Sprachabhaengige Texte
kommen hier nicht vor (die Datei pinnt Datenfluss, keine Wortlaute), deshalb
ist kein ``translation.override`` noetig.
"""

import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection

from core.models import AuditLog, DocumentType, DocumentTypeField, Event, FieldTemplate
from core.services.attachments.crud import (
    _lock_owner_row,
    apply_attachment_changes,
    finalize_staged_files,
    purge_attachment_chain,
    replace_upload_key,
    stage_attachment_uploads,
)
from core.services.file_vault import commit_staged_upload, prepare_encrypted_upload

#: Minimales, gueltiges PDF — ``prepare_encrypted_upload`` prueft Magic-Bytes.
PDF_HEADER = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
    b"xref\n0 3\n0000000000 65535 f\n"
    b"trailer<</Size 3/Root 1 0 R>>\n"
    b"startxref\n9\n%%EOF\n"
)


def _upload(name="beleg.pdf"):
    return SimpleUploadedFile(name, PDF_HEADER, content_type="application/pdf")


def _stage(owner, name="beleg.pdf"):
    """Echte ``StagedUpload``-Instanz (Scan + Fernet), ohne DB-Zeile."""
    return prepare_encrypted_upload(owner.facility, _upload(name), None, owner=owner)


def _attach(owner, ft, user, *, name="beleg.pdf", sort_order=None):
    staged = prepare_encrypted_upload(owner.facility, _upload(name), user, owner=owner)
    return commit_staged_upload(owner, ft, staged, user, sort_order=sort_order)


def _marker(*attachments):
    return {
        "__files__": True,
        "entries": [{"id": str(a.pk), "sort": i} for i, a in enumerate(attachments)],
    }


def _spy_save(owner):
    """Ersetzt ``owner.save`` durch einen Spion, der das Original ausfuehrt.

    Instanz-Attribut, kein Klassen-Patch: die Zusicherung gilt genau dem
    Aufruf, den der Service an SEINEM Owner absetzt.
    """
    spy = Mock(side_effect=owner.save)
    owner.save = spy
    return spy


# ---------------------------------------------------------------------------
# Fixtures — Dokumentationstypen mit FILE-Feldern
# ---------------------------------------------------------------------------


@pytest.fixture
def ft_file(facility):
    """Sichtbares FILE-Feld (erbt ``normal`` vom Dokumentationstyp)."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Beleg (Pins 0.94)",
        field_type=FieldTemplate.FieldType.FILE,
    )


@pytest.fixture
def ft_file_hoch(facility):
    """FILE-Feld mit Sensitivity ``high`` — fuer STAFF unsichtbar.

    ``ROLE_MAX_SENSITIVITY[STAFF] == 1``, ``high`` hat Rang 2; das
    R11-Gate in ``_iter_file_fields`` ueberspringt das Feld also.
    """
    return FieldTemplate.objects.create(
        facility=facility,
        name="Verschlusssache (Pins 0.94)",
        field_type=FieldTemplate.FieldType.FILE,
        sensitivity=DocumentType.Sensitivity.HIGH,
    )


@pytest.fixture
def dt_file(facility, ft_file):
    dt = DocumentType.objects.create(facility=facility, name="Pins 0.94 — ein Dateifeld")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=0)
    return dt


@pytest.fixture
def dt_nur_hoch(facility, ft_file_hoch):
    dt = DocumentType.objects.create(facility=facility, name="Pins 0.94 — nur Verschlusssache")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file_hoch, sort_order=0)
    return dt


@pytest.fixture
def dt_hoch_dann_offen(facility, ft_file_hoch, ft_file):
    """Zwei FILE-Felder: das gesperrte kommt in der Iteration ZUERST.

    ``build_field_template_lookup`` iteriert ueber ``document_type.fields``
    mit ``Meta.ordering = ["sort_order"]`` — die Reihenfolge ist damit
    festgenagelt und der ``continue``/``break``-Unterschied beobachtbar.
    """
    dt = DocumentType.objects.create(facility=facility, name="Pins 0.94 — zwei Dateifelder")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file_hoch, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_file, sort_order=1)
    return dt


@pytest.fixture
def medienordner(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# finalize_staged_files — Create-Pfad (3 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestFinalizeStagedFiles:
    """Der Create-Pfad persistiert JEDEN gueltigen Slug, mit Akteur, feld-gescopt.

    ``finalize_staged_files`` laeuft im ``transaction.atomic`` des Owner-
    Creates (Refs #1345/#584) — dort haelt der AuditLog-Hashchain-Advisory-Lock
    der Einrichtung. Genau deshalb ist der Save hier bewusst auf
    ``["data_json"]`` beschraenkt, und genau deshalb darf ein unbrauchbarer
    Slug die Schleife nicht abbrechen.
    """

    def test_ungueltiger_slug_stoppt_die_schleife_nicht(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_finalize_staged_files__mutmut_9`` — ``continue`` -> ``break``.

        ``staged_by_slug`` traegt zuerst einen Slug ohne Feldzuordnung
        (``field_templates.get(...) is None`` -> Guard) und danach den echten
        Slug mit einer gestagten Datei. Das Original ueberspringt nur den
        ersten; der Mutant verlaesst die Schleife und persistiert die zweite
        Datei nie.

        Abweichung beim Mutanten: ``sample_event.data_json`` enthaelt den
        Slug gar nicht (``KeyError``) und ``attachments.count()`` ist ``0``
        statt ``1`` — hochgeladene Anhaenge verschwinden lautlos.
        """
        staged = _stage(sample_event)

        finalize_staged_files(
            sample_event,
            staff_user,
            {"pins-094-slug-ohne-feld": [], ft_file.slug: [staged]},
            dt_file,
        )

        sample_event.refresh_from_db()
        attachment = sample_event.attachments.get()
        assert sample_event.data_json[ft_file.slug] == {
            "__files__": True,
            "entries": [{"id": str(attachment.pk), "sort": 0}],
        }

    def test_anhang_traegt_den_handelnden_user(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_finalize_staged_files__mutmut_16`` — ``commit_staged_upload(..., user, ...)``
        -> ``commit_staged_upload(..., None, ...)``.

        ``user`` fliesst in ``Attachment.created_by`` UND in den
        ATTACHMENT_UPLOAD-Audit-Akteur. Beide FK sind nullable, der Mutant
        laeuft also fehlerfrei durch.

        Abweichung beim Mutanten: ``created_by`` ist ``None`` statt
        ``staff_user``, die Audit-Zeile hat ``user is None`` — der Upload
        waere niemandem mehr zuzuordnen.
        """
        staged = _stage(sample_event)

        finalize_staged_files(sample_event, staff_user, {ft_file.slug: [staged]}, dt_file)

        attachment = sample_event.attachments.get()
        assert attachment.created_by == staff_user
        eintrag = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert eintrag.user == staff_user
        assert eintrag.detail["mode"] == "add"

    def test_speichert_ausschliesslich_data_json(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_finalize_staged_files__mutmut_35`` — ``save(update_fields=["data_json"])``
        -> ``save(update_fields=None)``.

        Der Voll-Save schriebe alle Spalten aus dem In-Memory-Stand zurueck
        und ueberschriebe damit jede zwischenzeitliche Fremdaenderung an der
        Owner-Zeile (Lost Update). Beobachtbar ist das nur an der
        Aufrufsignatur — der Datenstand des Happy Path ist identisch.

        Abweichung beim Mutanten: der Spion sieht
        ``call(update_fields=None)`` statt ``call(update_fields=["data_json"])``.
        """
        staged = _stage(sample_event)
        spy = _spy_save(sample_event)

        finalize_staged_files(sample_event, staff_user, {ft_file.slug: [staged]}, dt_file)

        spy.assert_called_once_with(update_fields=["data_json"])


# ---------------------------------------------------------------------------
# _iter_file_fields — R11-Sensitivity-Gate (1 Mutant)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestIterFileFieldsGate:
    """Das R11-Gate (Refs #1476/#1666) sperrt EIN Feld, nicht den Rest.

    Alle Bestandstests des Gates (``test_attachment_mutation_authz.py``)
    arbeiten mit Dokumentationstypen, die genau ein FILE-Feld haben — dort
    sind ``continue`` und ``break`` ununterscheidbar.
    """

    def test_gesperrtes_feld_blockiert_das_folgende_nicht(
        self, staff_user, sample_event, ft_file, ft_file_hoch, dt_hoch_dann_offen, medienordner
    ):
        """Pin: ``x__iter_file_fields__mutmut_17`` — ``continue`` -> ``break`` im
        Sensitivity-Gate.

        Der Typ hat zwei FILE-Felder in fester Reihenfolge: das
        ``high``-Feld (fuer STAFF unsichtbar) zuerst, danach das sichtbare.
        STAFF haengt an das ZWEITE Feld eine Datei.

        Abweichung beim Mutanten: die Iteration endet am ersten Feld,
        ``sample_event.data_json`` bekommt gar keinen Marker
        (``KeyError``) und es entsteht kein ``Attachment`` — eine
        Verfuegbarkeits-Regression mitten im Sicherheits-Gate.
        """
        apply_attachment_changes(
            sample_event,
            staff_user,
            {},
            {},
            {ft_file.slug: [_upload("sichtbar.pdf")]},
            dt_hoch_dann_offen,
        )

        sample_event.refresh_from_db()
        attachment = sample_event.attachments.get()
        assert attachment.field_template_id == ft_file.pk
        assert sample_event.data_json[ft_file.slug] == {
            "__files__": True,
            "entries": [{"id": str(attachment.pk), "sort": 0}],
        }


# ---------------------------------------------------------------------------
# stage_attachment_uploads — Vor-Transaktions-Staging (11 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestStageAttachmentUploads:
    """Scan + Verschluesselung VOR der Transaktion (Refs #1956).

    Diese Stufe entscheidet, WAS ueberhaupt gescannt und als ``.enc`` auf die
    Disk geschrieben wird. Jeder Fehler hier ist entweder eine
    Policy-Umgehung (Gate uebersprungen), stiller Datenverlust (Schleife
    abgebrochen) oder eine kaputte Cleanup-Buchhaltung (#662).
    """

    def test_gate_greift_auch_beim_staging(self, staff_user, sample_event, ft_file_hoch, dt_nur_hoch, medienordner):
        """Pin: ``x_stage_attachment_uploads__mutmut_9`` —
        ``_iter_file_fields(owner, user, ...)`` -> ``_iter_file_fields(owner, None, ...)``.

        ``_iter_file_fields`` gated nur ``if user is not None`` (``None`` =
        System-/Service-Kontext ohne Rolle). Mit ``user=None`` faellt das
        R11-Gate beim Staging komplett aus — genau die Policy-Umgehung, vor
        der der Docstring von ``_iter_file_fields`` warnt: Dateien fuer
        gesperrte Felder werden gescannt und verschluesselt.

        Abweichung beim Mutanten: ``staged_by_key`` enthaelt den Slug des
        gesperrten Feldes, ``staged_all`` hat ein Element, und im
        ``MEDIA_ROOT`` liegt eine ``.enc``-Waise.
        """
        staged_by_key, staged_all = stage_attachment_uploads(
            sample_event,
            staff_user,
            {},
            {ft_file_hoch.slug: [_upload("verschluss.pdf")]},
            dt_nur_hoch,
        )

        assert staged_by_key == {}
        assert staged_all == []
        assert list(Path(medienordner).rglob("*.enc")) == []

    def test_kuenftige_markerlage_des_aufrufers_gewinnt(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_stage_attachment_uploads__mutmut_11`` (``data_json`` -> ``None``)
        und ``x_stage_attachment_uploads__mutmut_15`` (viertes Argument entfernt,
        faellt auf den Default ``data_json=None``).

        Der Update-Pfad der Views (``events.py``/``cases.py``/``clients.py``)
        uebergibt hier die KUENFTIGE, gemergte Markerlage. Ein Entry, den
        diese Lage nicht mehr kennt (Feldzuordnung entfernt, Typwechsel),
        darf keinen REPLACE-Scan mehr ausloesen.

        Aufbau: der Owner traegt einen Bestandsmarker mit ``att``, der
        Aufrufer uebergibt aber ``data_json={}``; im Request liegt der
        REPLACE-Schluessel fuer ``att``.

        Abweichung bei beiden Mutanten: die Markerlage des Owners wird
        gelesen, der Entry existiert dort, also enthaelt ``staged_by_key``
        den REPLACE-Schluessel und es liegt eine ``.enc`` auf der Disk —
        ein Scan fuer einen Entry, den es kuenftig nicht mehr gibt.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])
        vorher = set(Path(medienordner).rglob("*.enc"))

        staged_by_key, staged_all = stage_attachment_uploads(
            sample_event,
            staff_user,
            {replace_upload_key(ft_file.slug, att.entry_id): _upload("ersatz.pdf")},
            {},
            dt_file,
            data_json={},
        )

        assert staged_by_key == {}
        assert staged_all == []
        assert set(Path(medienordner).rglob("*.enc")) == vorher

    def test_unaufloesbarer_entry_stoppt_die_schleife_nicht(
        self, staff_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x_stage_attachment_uploads__mutmut_22`` — ``if att is None: continue``
        -> ``if att is None: break`` in der ``current_entries``-Schleife.

        (Die Triage-Liste beschreibt den Mutanten als Bruch am Guard
        ``if replace_file is None``; der Rumpf im Mutantenbaum mutiert
        tatsaechlich den Guard eine Zeile darueber — ``if att is None``.
        Fachlich derselbe Befund, und dieselbe Testluecke: keine Bestandsprobe
        laesst in dieser Schleife mehr als einen Entry laufen.)

        Aufbau: drei Marker-Entries in einem Slug — ein verwaister (keine
        Attachment-Zeile), danach ein Bestandsanhang OHNE Replace-Upload und
        zuletzt der tatsaechlich zu ersetzende. Das Original ueberspringt die
        ersten beiden ueber die zwei ``continue``-Guards und staged den
        dritten; der Mutant verlaesst die Schleife schon am verwaisten Entry.

        Abweichung beim Mutanten: ``staged_by_key`` ist ``{}`` und
        ``staged_all`` leer statt des REPLACE-Schluessels von ``att2`` mit
        genau einer gestagten Datei — der Upload geht ohne Fehlermeldung
        verloren.
        """
        att1 = _attach(sample_event, ft_file, staff_user, name="erste.pdf", sort_order=0)
        att2 = _attach(sample_event, ft_file, staff_user, name="zweite.pdf", sort_order=1)
        sample_event.data_json[ft_file.slug] = {
            "__files__": True,
            "entries": [
                {"id": str(uuid.uuid4()), "sort": 0},
                {"id": str(att1.pk), "sort": 1},
                {"id": str(att2.pk), "sort": 2},
            ],
        }
        sample_event.save(update_fields=["data_json"])
        schluessel = replace_upload_key(ft_file.slug, att2.entry_id)

        staged_by_key, staged_all = stage_attachment_uploads(
            sample_event,
            staff_user,
            {schluessel: _upload("ersatz.pdf")},
            {},
            dt_file,
        )

        assert list(staged_by_key) == [schluessel]
        assert len(staged_by_key[schluessel]) == 1
        assert len(staged_all) == 1

    def test_replace_zweig_reicht_akteur_und_owner_durch(
        self, staff_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x_stage_attachment_uploads__mutmut_35`` (``user`` -> ``None``),
        ``…__mutmut_36`` (``owner=owner`` -> ``owner=None``) und ``…__mutmut_40``
        (``owner``-Kwarg entfernt, faellt auf den Signatur-Default ``owner=None``).

        ``user`` und ``owner`` fliessen in ``prepare_encrypted_upload``
        ausschliesslich in die SECURITY_VIOLATION-Attribution
        (``log_attachment_violation`` -> ``audit_security_violation``,
        ``_owner_reference``). Scheitert ein REPLACE-Upload an Virus,
        Magic-Bytes oder Groesse, haengt die forensische Zuordnung des
        Hashketten-Audits genau an diesen beiden Argumenten — im Happy Path
        ist der Unterschied unsichtbar, deshalb die Aufruf-Zusicherung.

        Abweichung bei den Mutanten: der aufgezeichnete Aufruf lautet
        ``(facility, datei, None, owner=<event>)`` (35),
        ``(facility, datei, staff_user, owner=None)`` (36) bzw.
        ``(facility, datei, staff_user)`` ohne ``owner``-Kwarg (40).
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])
        datei = _upload("ersatz.pdf")

        with patch("core.services.file_vault.prepare_encrypted_upload") as vorbereiten:
            stage_attachment_uploads(
                sample_event,
                staff_user,
                {replace_upload_key(ft_file.slug, att.entry_id): datei},
                {},
                dt_file,
            )

        # Refs #2129: seit dem Speicherkontingent reicht der Ersatz-Zweig die laufende
        # Staging-Summe (``pending_bytes``) mit — beim einzigen Ersatz ist sie 0.
        vorbereiten.assert_called_once_with(
            sample_event.facility, datei, staff_user, owner=sample_event, pending_bytes=0
        )

    def test_cleanup_liste_traegt_die_gestagte_datei_selbst(
        self, staff_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x_stage_attachment_uploads__mutmut_41`` — ``staged_all.append(staged)``
        -> ``staged_all.append(None)`` im REPLACE-Zweig.

        ``staged_all`` ist die Cleanup-Buchhaltung des Aufrufers (#662): sein
        ``finally`` ruft ``staged.output_path.unlink(...)`` fuer jeden
        Eintrag. Ein ``None`` darin heisst entweder ``AttributeError`` im
        Aufraeumpfad oder — mit defensivem Aufrufer — eine ``.enc``-Waise,
        die nie abgerechnet wird. Dasselbe gilt fuer den internen
        ``except``-Block der Funktion.

        Abweichung beim Mutanten: ``staged_all == [None]``, die
        Identitaets-Zusicherung gegen das Objekt aus ``staged_by_key``
        schlaegt fehl.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])
        schluessel = replace_upload_key(ft_file.slug, att.entry_id)

        staged_by_key, staged_all = stage_attachment_uploads(
            sample_event,
            staff_user,
            {schluessel: _upload("ersatz.pdf")},
            {},
            dt_file,
        )

        assert len(staged_all) == 1
        assert staged_all[0] is staged_by_key[schluessel][0]
        assert staged_all[0].output_path.exists()

    def test_add_zweig_reicht_akteur_und_owner_durch(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_stage_attachment_uploads__mutmut_49`` (``user`` -> ``None``),
        ``…__mutmut_50`` (``owner=owner`` -> ``owner=None``) und ``…__mutmut_54``
        (``owner``-Kwarg entfernt, Default ``owner=None``).

        Gleiche Attributionskette wie im REPLACE-Zweig, nur fuer neu
        hinzugefuegte Dateien: ohne ``user`` verliert ein Policy-/Virus-Reject
        seinen Taeter, ohne ``owner`` seinen Bezug auf Ereignis/Person/Fall
        (``owner_type``/``owner_id``/``target_id``, ADR-039 §5).

        Abweichung bei den Mutanten: der aufgezeichnete Aufruf lautet
        ``(facility, datei, None, owner=<event>)`` (49),
        ``(facility, datei, staff_user, owner=None)`` (50) bzw.
        ``(facility, datei, staff_user)`` ohne ``owner``-Kwarg (54).
        """
        datei = _upload("neu.pdf")

        with patch("core.services.file_vault.prepare_encrypted_upload") as vorbereiten:
            stage_attachment_uploads(sample_event, staff_user, {}, {ft_file.slug: [datei]}, dt_file)

        # Refs #2129: seit dem Speicherkontingent reicht der Ersatz-Zweig die laufende
        # Staging-Summe (``pending_bytes``) mit — beim einzigen Ersatz ist sie 0.
        vorbereiten.assert_called_once_with(
            sample_event.facility, datei, staff_user, owner=sample_event, pending_bytes=0
        )


# ---------------------------------------------------------------------------
# _apply_remove — Soft-Delete-Zweig (1 Mutant)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestApplyRemove:
    """Der Soft-Delete schreibt eine ATTACHMENT_DELETE-Zeile — mit Akteur."""

    def test_soft_delete_audit_nennt_den_handelnden_user(
        self, staff_user, lead_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x__apply_remove__mutmut_22`` —
        ``soft_delete_attachment_chain(owner, att.entry_id, user, field=slug)``
        -> ``… , None, field=slug)``.

        ``user`` wird in ``soft_delete_attachment_chain`` ausschliesslich als
        AuditLog-Akteur verwendet (``audit_attachment_delete``). Der
        Loeschvorgang selbst bleibt identisch — nur das Protokoll verliert
        den Handelnden.

        Der Anhang wird von ``staff_user`` angelegt und von ``lead_user``
        entfernt; die Zusicherung unterscheidet damit ``None`` UND einen
        zufaellig gleichen Akteur.

        Abweichung beim Mutanten: ``AuditLog(action=ATTACHMENT_DELETE).user``
        ist ``None`` statt ``lead_user``.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])

        apply_attachment_changes(
            sample_event,
            lead_user,
            {f"{ft_file.slug}__remove": str(att.entry_id)},
            {},
            {},
            dt_file,
            staged_by_key={},
        )

        att.refresh_from_db()
        assert att.deleted_at is not None
        eintrag = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_DELETE)
        assert eintrag.user == lead_user


# ---------------------------------------------------------------------------
# _apply_replace — Ersetzen bestehender Entries (2 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestApplyReplace:
    """Ein unaufloesbarer Entry darf die uebrigen nicht mitreissen."""

    def test_verwaister_entry_kostet_die_folgenden_nicht(
        self, staff_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x__apply_replace__mutmut_8`` — ``if not att: continue``
        -> ``if not att: break``.

        Der Marker traegt zwei Entries: der erste zeigt auf eine
        Attachment-PK, die es nicht (mehr) gibt, der zweite auf ein echtes
        Attachment. ``_iter_file_fields`` laedt nur den zweiten in
        ``attachments_by_pk``.

        Das Original ueberspringt den verwaisten Entry; der Mutant bricht ab
        und liefert eine LEERE ``updated_entries``-Liste. ``_write_file_marker``
        loescht daraufhin den ganzen Slug aus ``data_json`` — die zugehoerigen
        Attachments existieren weiter in der DB, sind fuer die Oberflaeche
        aber unerreichbar.

        Abweichung beim Mutanten: ``ft_file.slug`` fehlt in
        ``sample_event.data_json`` (``KeyError``) statt den zweiten Entry
        unveraendert zu tragen.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = {
            "__files__": True,
            "entries": [{"id": str(uuid.uuid4()), "sort": 0}, {"id": str(att.pk), "sort": 1}],
        }
        sample_event.save(update_fields=["data_json"])

        apply_attachment_changes(sample_event, staff_user, {}, {}, {}, dt_file, staged_by_key={})

        sample_event.refresh_from_db()
        assert sample_event.data_json[ft_file.slug] == {
            "__files__": True,
            "entries": [{"id": str(att.pk), "sort": 1}],
        }

    def test_ersatzversion_traegt_den_handelnden_user(
        self, staff_user, lead_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x__apply_replace__mutmut_21`` —
        ``commit_staged_upload(owner, ft, staged_list[0], user, supersedes=att)``
        -> ``… , None, supersedes=att)``.

        ``user`` setzt ``created_by`` der neuen Version und den Akteur der
        ATTACHMENT_UPLOAD-Zeile mit ``mode="replace"``. Die Bestandstests
        pruefen an dieser Stelle nur ``entry_id``/``sort_order``/``is_current``.

        Der Erstupload kommt von ``staff_user``, das Ersetzen von
        ``lead_user`` — die Zusicherung faellt damit auch bei einer
        versehentlichen Wiederverwendung des Erst-Akteurs.

        Abweichung beim Mutanten: ``created_by`` der neuen Version ist
        ``None`` statt ``lead_user``, ebenso der Audit-Akteur.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])

        apply_attachment_changes(
            sample_event,
            lead_user,
            {},
            {replace_upload_key(ft_file.slug, att.entry_id): _upload("ersatz.pdf")},
            {},
            dt_file,
        )

        neu = sample_event.attachments.get(entry_id=att.entry_id, is_current=True)
        assert neu.pk != att.pk
        assert neu.created_by == lead_user
        eintrag = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD, detail__mode="replace")
        assert eintrag.user == lead_user


# ---------------------------------------------------------------------------
# _apply_add — Anhaengen neuer Dateien (1 Mutant)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestApplyAdd:
    """Auch der ADD-Zweig protokolliert seinen Akteur."""

    def test_neue_datei_traegt_den_handelnden_user(self, lead_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x__apply_add__mutmut_26`` —
        ``commit_staged_upload(owner, ft, staged, user, sort_order=...)``
        -> ``… , None, sort_order=...)``.

        Wie im REPLACE-Zweig: ``user`` ist ``created_by`` UND
        AuditLog-Akteur (``mode="add"``). ``test_add_nach_remove_persistiert_
        das_stufe_b_schema`` prueft an dieser Stelle nur das Marker-Schema.

        Abweichung beim Mutanten: ``created_by is None`` statt
        ``lead_user`` und die Audit-Zeile hat keinen Akteur.
        """
        apply_attachment_changes(
            sample_event,
            lead_user,
            {},
            {},
            {ft_file.slug: [_upload("neu.pdf")]},
            dt_file,
        )

        attachment = sample_event.attachments.get()
        assert attachment.created_by == lead_user
        eintrag = AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_UPLOAD)
        assert eintrag.user == lead_user
        assert eintrag.detail["mode"] == "add"


# ---------------------------------------------------------------------------
# apply_attachment_changes — Orchestrierung (5 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestApplyAttachmentChanges:
    """Der Orchestrator reicht ``user`` an alle vier Schritte durch — und speichert feld-gescopt."""

    def test_eigenes_staging_laeuft_unter_dem_echten_user(
        self, staff_user, sample_event, ft_file_hoch, dt_nur_hoch, medienordner
    ):
        """Pin: ``x_apply_attachment_changes__mutmut_6`` —
        ``stage_attachment_uploads(owner, user, ...)``
        -> ``stage_attachment_uploads(owner, None, ...)``.

        Im Bequemlichkeits-Modus (``staged_by_key=None``) staged die Funktion
        selbst. Mit ``user=None`` faellt das R11-Gate im Staging aus: eine
        Datei fuer ein gesperrtes Feld wird gescannt und verschluesselt,
        obwohl der spaetere Apply-Lauf (mit echtem ``user``) sie ohnehin
        verwirft. Der Endzustand von DB und Marker ist deshalb in beiden
        Faellen gleich — sichtbar wird der Unterschied nur am Scan-Aufruf.
        Fachlich zaehlt genau der: ein Policy-Reject (Virus, Groesse) im
        gesperrten Feld liesse die GESAMTE Anfrage inklusive erlaubter
        Aenderungen mit ``ValidationError`` scheitern.

        Abweichung beim Mutanten: ``prepare_encrypted_upload`` wird einmal
        aufgerufen statt gar nicht.
        """
        with patch("core.services.file_vault.prepare_encrypted_upload") as vorbereiten:
            apply_attachment_changes(
                sample_event,
                staff_user,
                {},
                {},
                {ft_file_hoch.slug: [_upload("verschluss.pdf")]},
                dt_nur_hoch,
            )

        vorbereiten.assert_not_called()
        assert sample_event.attachments.count() == 0

    def test_remove_schritt_bekommt_den_user(self, staff_user, lead_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_apply_attachment_changes__mutmut_29`` —
        ``_apply_remove(..., owner, user)`` -> ``_apply_remove(..., owner, None)``.

        Derselbe Verlust wie bei ``x__apply_remove__mutmut_22``, nur eine
        Ebene hoeher eingespeist: der Akteur der ATTACHMENT_DELETE-Zeile
        faellt weg. Die Zusicherung sitzt deshalb bewusst an einem zweiten,
        eigenstaendigen Aufruf — ein Pin je Einspeisungspunkt.

        Abweichung beim Mutanten: ``AuditLog(action=ATTACHMENT_DELETE).user``
        ist ``None`` statt ``lead_user``.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])

        apply_attachment_changes(
            sample_event,
            lead_user,
            {f"{ft_file.slug}__remove": str(att.entry_id)},
            {},
            {},
            dt_file,
        )

        assert AuditLog.objects.get(action=AuditLog.Action.ATTACHMENT_DELETE).user == lead_user

    def test_replace_schritt_bekommt_den_user(
        self, staff_user, lead_user, sample_event, ft_file, dt_file, medienordner
    ):
        """Pin: ``x_apply_attachment_changes__mutmut_43`` —
        ``_apply_replace(..., owner, user)`` -> ``_apply_replace(..., owner, None)``.

        Einspeisungspunkt-Pendant zu ``x__apply_replace__mutmut_21``: die per
        REPLACE erzeugte Version verliert ``created_by`` und Audit-Akteur.

        Abweichung beim Mutanten: ``created_by`` der neuen Version ist
        ``None`` statt ``lead_user``.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])

        apply_attachment_changes(
            sample_event,
            lead_user,
            {},
            {replace_upload_key(ft_file.slug, att.entry_id): _upload("ersatz.pdf")},
            {},
            dt_file,
        )

        neu = sample_event.attachments.get(entry_id=att.entry_id, is_current=True)
        assert neu.created_by == lead_user

    def test_add_schritt_bekommt_den_user(self, lead_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x_apply_attachment_changes__mutmut_57`` —
        ``_apply_add(..., owner, user)`` -> ``_apply_add(..., owner, None)``.

        Einspeisungspunkt-Pendant zu ``x__apply_add__mutmut_26``.

        Abweichung beim Mutanten: ``created_by`` des neuen Anhangs ist
        ``None`` statt ``lead_user``.
        """
        apply_attachment_changes(sample_event, lead_user, {}, {}, {ft_file.slug: [_upload("neu.pdf")]}, dt_file)

        assert sample_event.attachments.get().created_by == lead_user

    def test_speichert_ausschliesslich_data_json(self, staff_user, sample_event, dt_file, medienordner):
        """Pin: ``x_apply_attachment_changes__mutmut_70`` —
        ``save(update_fields=["data_json"])`` -> ``save(update_fields=None)``.

        Der feld-gescopte Save haelt das Lost-Update-Fenster zu (Refs #584):
        alles ausser ``data_json`` bleibt an der Owner-Zeile unberuehrt. Der
        Voll-Save des Mutanten schriebe den kompletten In-Memory-Stand
        zurueck. Der reine Text-Edit (weder Uploads noch REMOVE) ist der
        haeufigste Pfad und erreicht den Save unveraendert.

        Abweichung beim Mutanten: der Spion sieht
        ``call(update_fields=None)`` statt ``call(update_fields=["data_json"])``.
        """
        spy = _spy_save(sample_event)

        apply_attachment_changes(sample_event, staff_user, {}, {}, {}, dt_file)

        spy.assert_called_once_with(update_fields=["data_json"])


# ---------------------------------------------------------------------------
# _drop_purged_marker_entries — Marker-Bereinigung im Purge (1 Mutant)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.usefixtures("_encryption_key")
class TestDropPurgedMarkerEntries:
    """Invariante I-M1 (ADR-039 §7) raeumt Marker auf — und NUR Marker."""

    def test_purge_ueberschreibt_keine_fremdaenderung(self, staff_user, sample_event, ft_file, dt_file, medienordner):
        """Pin: ``x__drop_purged_marker_entries__mutmut_28`` —
        ``owner.save(update_fields=["data_json"])`` -> ``owner.save(update_fields=None)``.

        Der Purge arbeitet auf einem In-Memory-Owner, der vor der
        Transaktion geladen wurde. Aendert ein anderer Vorgang
        zwischenzeitlich eine ANDERE Spalte derselben Zeile, darf der
        Marker-Save sie nicht mitschreiben — sonst ist es ein Lost Update
        ausgerechnet in dem Pfad, der laut Docstring auf Zeilen- und
        Transaktionssicherheit ausgelegt ist (Refs #2123).

        Aufbau: ``search_text`` wird per ``queryset.update()`` (also ohne die
        In-Memory-Instanz) auf einen Markerwert gesetzt; der Purge laeuft
        danach auf der jetzt veralteten Instanz.

        Abweichung beim Mutanten: ``search_text`` steht nach dem Purge
        wieder auf dem veralteten Wert des In-Memory-Objekts
        (``sample_event.search_text``) statt auf
        ``"pins094-konkurrierende-aenderung"``.
        """
        att = _attach(sample_event, ft_file, staff_user)
        sample_event.data_json[ft_file.slug] = _marker(att)
        sample_event.save(update_fields=["data_json"])
        assert sample_event.search_text != "pins094-konkurrierende-aenderung"

        # Konkurrierender Schreiber: umgeht die In-Memory-Instanz vollstaendig.
        Event.objects.filter(pk=sample_event.pk).update(search_text="pins094-konkurrierende-aenderung")

        entfernt = purge_attachment_chain(sample_event, att.entry_id)

        assert entfernt == 1
        sample_event.refresh_from_db()
        assert sample_event.search_text == "pins094-konkurrierende-aenderung"
        assert ft_file.slug not in sample_event.data_json


# ---------------------------------------------------------------------------
# _lock_owner_row — zweite Stufe der kanonischen Sperrordnung (3 Mutanten)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLockOwnerRow:
    """Refs #2123 (kimi-k3 B-1): die Owner-Zeile selbst muss gesperrt werden.

    Die Bestandstests (``test_attachment_purge.py``) suchen im mitgeschnittenen
    SQL nach ``core_event`` + ``FOR UPDATE``. Beides steht auch dann im
    Statement, wenn das WHERE-Praedikat nie eine Zeile trifft oder die falsche
    trifft — die Sperr-Eigenschaft selbst ist einzelthreadig nicht beobachtbar
    (eine Transaktion blockiert sich nicht selbst). Gepinnt wird deshalb die
    Parameterliste der Sperr-Query: sie muss GENAU aus Owner-PK und
    Facility-ID bestehen.
    """

    def test_sperrquery_traegt_pk_und_facility(self, sample_event, doc_type_contact, client_identified, staff_user):
        """Pin: ``x__lock_owner_row__mutmut_6`` (``pk=owner.pk`` -> ``pk=None``),
        ``…__mutmut_7`` (``facility=owner.facility_id`` -> ``facility=None``) und
        ``…__mutmut_8`` (``pk``-Filter ersatzlos entfernt).

        Ein zweites, juengeres Ereignis derselben Einrichtung macht den
        dritten Mutanten fachlich sichtbar: ohne ``pk``-Filter liefert
        ``.first()`` nach ``Meta.ordering = ["-occurred_at"]`` die JUENGERE
        Zeile — gesperrt wuerde also ein fremder Datensatz.

        Abweichung bei den Mutanten (Parameterliste der ``FOR UPDATE``-Query):
        ``[facility_id]`` bei 6 (``"id" IS NULL``), ``[event_pk]`` bei 7
        (``facility_id IS NULL``) und ``[facility_id]`` bei 8 — statt der
        zwei Werte ``[event_pk, facility_id]`` des Originals.
        """
        from datetime import timedelta

        from django.utils import timezone

        juenger = Event.objects.create(
            facility=sample_event.facility,
            client=client_identified,
            document_type=doc_type_contact,
            created_by=staff_user,
            occurred_at=timezone.now() + timedelta(days=1),
        )
        assert juenger.pk != sample_event.pk

        mitschnitt = []

        def _aufzeichnen(execute, sql, params, many, context):
            mitschnitt.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(_aufzeichnen):
            _lock_owner_row(sample_event)

        sperrend = [(sql, params) for sql, params in mitschnitt if "FOR UPDATE" in sql.upper()]
        assert len(sperrend) == 1
        _sql, params = sperrend[0]
        assert sorted(str(wert) for wert in params) == sorted([str(sample_event.pk), str(sample_event.facility_id)])
