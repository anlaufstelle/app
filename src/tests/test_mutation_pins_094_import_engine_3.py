"""Pin-Tests zum Meilenstein release-0.94 — Modul-Batch ``import_engine_3``.

Quelle: Survivor-Triage des Moduls ``src/core/services/import_engine.py``
(Mutantenbaum ``mutants/src/core/services/import_engine.py``). Gepinnt wird
das triagierte, killbare Set aus der Survivor-Triage release-0.94 (Ledger des Meilensteins)
(38 Mutanten). Schwerpunkte:

* die **idempotente Dateiloeschung** (``missing_ok=True``) und der bis hierher
  voellig ungetestete ``except OSError``-Zweig von
  :func:`~core.services.import_engine.delete_import_file` — der einzige
  forensische Anker fuer eine verwaiste ``.enc``-Datei mit Klientendaten
  (Format-String UND Argumentvertrag des ``logger.warning``);
* die vier nutzersichtbaren **Fehlermeldungen** von :func:`read_rows` und
  :func:`commit_import` im vollen Wortlaut (die Bestandstests pruefen nur die
  Exception-Klasse bzw. matchen per Teilstring, den die ``XX…XX``- und
  Case-Varianten unbeschadet ueberleben);
* das **chunkgenaue Zusammensetzen** der entschluesselten Quelldatei
  (``b"".join``) — sichtbar erst oberhalb von ``CHUNK_SIZE`` (64 KiB), was
  keine Bestands-Fixture erreicht;
* der **persistierte Delimiter**: er gewinnt gegen die Sniffer-Heuristik
  (Bestandstest waehlt zufaellig einen Delimiter, den der Sniffer ebenfalls
  erraet);
* der **Dateinamen-Fallback** des Uploads und die Startform von
  ``mapping_json`` sowie das Aufraeumen der ``.enc``-Datei, wenn der DB-Write
  scheitert;
* ``committed`` ist im Probelauf-Ergebnis ein echtes ``False`` (bool), nicht
  irgendein falsy Wert;
* ``_CellError`` traegt seinen Fehlercode auch als Exception-Meldung.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override("de")`` festgenagelt;
``pytest.mark.django_db`` nur, wo es DB braucht; KEINE Imports aus
``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py`` bzw.
werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt seine
Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.
"""

import logging
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import translation

from core.models import Client, ImportJob
from core.services import import_engine
from core.services.file_vault import CHUNK_SIZE, decrypt_field

PSEUDONYM_HEADER = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\n"
LOGGER_NAME = "core.services.import_engine"


def _upload(content, name="import.csv"):
    """CSV als hochgeladene Datei — Bytes wie sie der Browser schickt."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


class _NamelessUpload:
    """Upload-Objekt OHNE ``.name`` (und ohne ``.size``).

    ``store_import_upload`` ist eine oeffentliche Service-Funktion ohne
    Typ-Erzwingung; sie liest den Dateinamen bewusst defensiv per
    ``getattr(uploaded_file, "name", "")``. Nur mit einem Objekt ohne dieses
    Attribut wird der Default ueberhaupt erreicht.
    """

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _UnnamedUpload(_NamelessUpload):
    """Upload-Objekt MIT leerem ``.name`` — der zweite Weg in den Fallback.

    ``SimpleUploadedFile`` kann diesen Fall nicht abbilden: Django lehnt einen
    leeren Dateinamen bereits im Konstruktor mit ``SuspiciousFileOperation``
    ab. Ein leerer Name kommt in der Praxis aus fremden Clients/Skripten, die
    den Service direkt bedienen.
    """

    name = ""


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _stored_job(facility, admin_user, content=PSEUDONYM_HEADER + "IMP-01;;;\n", **kwargs):
    """Hochgeladener Lauf mit echter ``.enc``-Datei auf der Disk."""
    return import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(content),
        target_type=ImportJob.TargetType.CLIENT,
        **kwargs,
    )


# ============================================================================
# delete_import_file — Idempotenz und der Log des Fehlerfalls (9 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestDeleteImportFileIsSilentlyIdempotent:
    """Die Loeschung ist idempotent — eine bereits fehlende Datei ist KEIN Fehler.

    Der Docstring sagt „idempotent"; ``missing_ok=True`` ist der Traeger dieser
    Zusage. Der Doppel-Loesch-Fall ist real: ``commit_import`` und
    ``abort_import`` loeschen beide, und ein Aufraeum-Lauf kann die Datei
    zwischenzeitlich entfernt haben.
    """

    def test_a_second_deletion_logs_no_warning(self, facility, admin_user, media_root, caplog):
        """Pin: ``x_delete_import_file__mutmut_2`` (``missing_ok=True`` -> ``None``)
        und ``x_delete_import_file__mutmut_4`` (``missing_ok=True`` -> ``False``).

        Beide Werte sind falsy und wirken wie ``missing_ok=False``: die bereits
        fehlende Datei laesst ``unlink`` ``FileNotFoundError`` werfen, das
        ``except OSError`` faengt es und loggt eine WARNING.

        Original: ``caplog.records == []`` (kein Log, sauberer Zweitaufruf).
        Mutanten: genau ein WARNING-Record („Import-Datei konnte nicht
        geloescht werden") fuer einen voellig regulaeren Vorgang.
        """
        job = _stored_job(facility, admin_user)
        path = import_engine.import_file_path(job)
        assert path.exists()
        import_engine.delete_import_file(job)
        assert not path.exists()

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = import_engine.delete_import_file(job)

        assert result is None
        assert [record for record in caplog.records if record.name == LOGGER_NAME] == []


@pytest.mark.django_db
class TestDeleteImportFileLogsTheOrphanedRun:
    """Scheitert das Loeschen wirklich, benennt der Log den Lauf — buchstabengetreu.

    Der Zweig traegt ``# pragma: no cover``; das nimmt ihn aus der
    Coverage-Messung, macht ihn aber weder unerreichbar noch bedeutungslos: Er
    ist die einzige Spur, dass eine verschluesselte Datei mit Klientendaten auf
    der Disk zurueckblieb. Ohne Formatstring UND Argument ist die Meldung fuer
    Ops wertlos.
    """

    def test_oserror_is_swallowed_but_logged_with_the_exact_format_and_pk(
        self, facility, admin_user, media_root, caplog, monkeypatch
    ):
        """Pin: ``x_delete_import_file__mutmut_5``/``_6``/``_7``/``_8``/``_9``/``_10``/``_11``.

        * ``_5``: ``logger.warning(None, job.pk)`` -> ``record.msg is None``;
        * ``_6``: zweites Argument ``None`` -> ``record.args == (None,)``,
          gerendert ``"… job=None"`` statt der Lauf-UUID;
        * ``_7``: ``logger.warning(job.pk)`` -> ``record.msg`` ist die UUID,
          ``record.args == ()`` — die Meldung verschwindet komplett;
        * ``_8``: Argument entfaellt -> ``record.args == ()``, gerendert bleibt
          ``"%s"`` unsubstituiert stehen;
        * ``_9``: ``"XXImport-Datei konnte nicht geloescht werden: job=%sXX"``;
        * ``_10``: durchgehend kleingeschrieben;
        * ``_11``: durchgehend gross UND ``%S`` statt ``%s`` (kein gueltiger
          Konversionstyp — die Meldung waere nicht einmal renderbar).

        Original: genau ein WARNING-Record mit
        ``msg == "Import-Datei konnte nicht geloescht werden: job=%s"``,
        ``args == (job.pk,)`` und der gerenderten Zeile inklusive Lauf-UUID;
        die Funktion schluckt den ``OSError`` und liefert ``None``. (``_5`` und
        ``_11`` scheitern schon beim Rendern der Meldung, statt am Vergleich —
        auch das ist ein Kill, und in Produktion ein Log-Handler-Fehler.)
        """
        job = _stored_job(facility, admin_user)

        def _explode(self, *args, **kwargs):
            raise OSError("EIO")

        monkeypatch.setattr(Path, "unlink", _explode)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            result = import_engine.delete_import_file(job)

        assert result is None
        records = [record for record in caplog.records if record.name == LOGGER_NAME]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].msg == "Import-Datei konnte nicht geloescht werden: job=%s"
        assert records[0].args == (job.pk,)
        assert records[0].getMessage() == f"Import-Datei konnte nicht geloescht werden: job={job.pk}"


# ============================================================================
# read_rows — die beiden Fehlermeldungen im vollen Wortlaut (9 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestReadRowsWithoutASourceFile:
    """Ein Lauf ohne ``storage_filename`` sagt WARUM er nichts lesen kann.

    Die Meldung landet ueber den Fehler-Redirect des Import-Wizards in der
    ``messages``-Ausgabe. Die einzigen Bestandstests (View-Ebene) pruefen den
    302er, kein Test vergleicht den Text — jede Text-Mutation (bis hin zur
    leeren Meldung) blieb daher unsichtbar.
    """

    def test_missing_storage_filename_raises_the_exact_message(self, facility, admin_user):
        """Pin: ``x_read_rows__mutmut_2``/``_4``/``_5``/``_6``.

        * ``_2``: ``ValidationError(None)`` -> ``messages == ["None"]``;
        * ``_4``: ``"XXZu diesem Importlauf gibt es keine Quelldatei mehr.XX"``;
        * ``_5``: durchgehend kleingeschrieben;
        * ``_6``: durchgehend GROSSGESCHRIEBEN.

        Original: ``messages == ["Zu diesem Importlauf gibt es keine
        Quelldatei mehr."]``. Jeder Mutant liefert einen anderen String — der
        exakte Vergleich faellt bei allen vier durch.
        """
        job = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
        )
        assert job.storage_filename == ""

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine.read_rows(job)

        assert excinfo.value.messages == ["Zu diesem Importlauf gibt es keine Quelldatei mehr."]


@pytest.mark.django_db
class TestReadRowsWithADeletedUpload:
    """Ist die Datei weg, aber der Lauf kennt sie noch, sagt das die Meldung.

    Der Bestandstest (``test_reading_a_deleted_upload_fails_loudly``) prueft
    ausschliesslich die Exception-Klasse; eine LEERE Fehlermeldung an die
    Anwenderin waere ihm nicht aufgefallen.
    """

    def test_deleted_file_raises_the_exact_message(self, facility, admin_user, media_root):
        """Pin: ``x_read_rows__mutmut_10``/``_11``/``_12``/``_13``/``_14``.

        * ``_10``: ``ValidationError(None)`` -> ``messages == ["None"]``;
        * ``_11``: ``_(None)`` -> ``ValidationError(_(None)).messages`` wirft
          ``AttributeError`` (kein ``ValidationError`` mit Text mehr);
        * ``_12``: ``"XXDie hochgeladene Datei ist nicht mehr vorhanden.XX"``;
        * ``_13``: durchgehend kleingeschrieben;
        * ``_14``: durchgehend GROSSGESCHRIEBEN.

        Original: ``messages == ["Die hochgeladene Datei ist nicht mehr
        vorhanden."]`` — der exakte Vergleich toetet alle fuenf.
        """
        job = _stored_job(facility, admin_user)
        import_engine.import_file_path(job).unlink()

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine.read_rows(job)

        assert excinfo.value.messages == ["Die hochgeladene Datei ist nicht mehr vorhanden."]


# ============================================================================
# read_rows — Chunk-Zusammenbau und der persistierte Delimiter (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestReadRowsJoinsChunksWithoutSeparator:
    """Die entschluesselten Chunks werden LUECKENLOS aneinandergehaengt.

    ``decrypt_file_stream`` liefert Klartext in Bloecken von ``CHUNK_SIZE``
    (64 KiB). Jede bestehende Fixture liegt weit darunter und erzeugt genau
    EINEN Block — bei einem einzigen Element ignoriert ``join`` den Trenner
    vollstaendig, weshalb dort jede Trenner-Mutation unsichtbar bleibt. Erst
    oberhalb von 64 KiB (und weiterhin unter ``MAX_BYTES``) traegt die Zusage.
    """

    def test_a_file_larger_than_one_chunk_is_reassembled_byte_exact(self, facility, admin_user, media_root):
        """Pin: ``x_read_rows__mutmut_17`` — ``b"".join(...)`` -> ``b"XXXX".join(...)``.

        Original: 600 unveraenderte Datenzeilen. Mutant: an jeder
        64-KiB-Grenze stehen vier Fremdbytes ``XXXX`` mitten in einer Zelle
        (bei dieser Fixture landen die Bytes im Notizfeld von ``P-0314``,
        nicht im Pseudonym), der exakte Zeilenvergleich schlaegt fehl.
        """
        filler = "N" * 200
        content = "Pseudonym;Notizen\n" + "".join(f"P-{index:04d};{filler}\n" for index in range(600))
        assert len(content.encode("utf-8")) > CHUNK_SIZE

        job = _stored_job(facility, admin_user, content=content, delimiter=";")
        header, rows = import_engine.read_rows(job)

        assert header == ["Pseudonym", "Notizen"]
        assert rows == [[f"P-{index:04d}", filler] for index in range(600)]


@pytest.mark.django_db
class TestReadRowsPrefersTheStoredDelimiter:
    """Der beim Upload gewaehlte Delimiter GEWINNT gegen die Sniffer-Heuristik.

    Der manuelle Override existiert laut ``_sniff_delimiter``-Docstring genau
    deshalb, weil ``csv.Sniffer`` fehlbar ist. Der Bestandstest
    (``test_explicit_delimiter_overrides_detection``) waehlt einen Tab-Text,
    den der Sniffer ebenfalls als Tab erkennt — der gespeicherte Wert wird dort
    nie als tatsaechlich ANDERE Entscheidung geprueft.
    """

    def test_stored_delimiter_beats_the_sniffed_one(self, facility, admin_user, media_root):
        """Pin: ``x_read_rows__mutmut_36``/``_37``/``_38``.

        * ``_36``: ``mapping.get(None)``;
        * ``_37``: ``mapping.get("XXdelimiterXX")``;
        * ``_38``: ``mapping.get("DELIMITER")``.

        Alle drei treffen den Key nicht, liefern ``None`` und snieffen erneut.
        Der Text ist semikolongetrennt (Sniffer -> ``";"``), gespeichert ist
        aber ``"|"``. Original: ``header == ["A;B"]`` und ``rows == [["1;2"]]``
        (eine Spalte, wie vom Upload beschlossen). Mutanten: ``header ==
        ["A", "B"]`` und ``rows == [["1", "2"]]``.
        """
        job = _stored_job(facility, admin_user, content="A;B\n1;2\n", delimiter="|")
        assert job.mapping_json["delimiter"] == "|"

        header, rows = import_engine.read_rows(job)

        assert header == ["A;B"]
        assert rows == [["1;2"]]


# ============================================================================
# store_import_upload — Dateiname, Start-Mapping, Aufraeumen (8 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestStoreImportUploadFilenameFallback:
    """Ohne brauchbaren Dateinamen wird ``import.csv`` gespeichert — exakt so.

    Der Originaldateiname wird ausschliesslich verschluesselt persistiert; der
    Fallback ist der Wert, den die Detailseite spaeter anzeigt. Beide Wege in
    den Fallback (Attribut fehlt / Name leer) sind in der Bestandssuite
    ungetestet, weil dort ueberall ``SimpleUploadedFile`` mit gesetztem Namen
    steht.
    """

    def test_upload_object_without_a_name_attribute_falls_back(self, facility, admin_user, media_root):
        """Pin: ``x_store_import_upload__mutmut_47`` (getattr-Default entfernt)
        und ``x_store_import_upload__mutmut_50`` (Default ``""`` -> ``"XXXX"``).

        Original: ``decrypt_field(job.original_filename_encrypted) ==
        "import.csv"``. Mutant ``_47``: ``getattr`` ohne Default wirft
        ``AttributeError`` — der Upload scheitert komplett. Mutant ``_50``:
        der truthy Default gewinnt gegen das ``or`` und persistiert ``"XXXX"``
        als Dateinamen.
        """
        upload = _NamelessUpload((PSEUDONYM_HEADER + "IMP-01;;;\n").encode("utf-8"))
        assert not hasattr(upload, "name")

        job = import_engine.store_import_upload(
            facility,
            admin_user,
            upload,
            target_type=ImportJob.TargetType.CLIENT,
        )

        assert decrypt_field(job.original_filename_encrypted) == "import.csv"

    def test_empty_upload_name_falls_back_to_the_exact_literal(self, facility, admin_user, media_root):
        """Pin: ``x_store_import_upload__mutmut_51`` (``"XXimport.csvXX"``)
        und ``x_store_import_upload__mutmut_52`` (``"IMPORT.CSV"``).

        Original: ``"import.csv"``. Mutanten persistieren
        ``"XXimport.csvXX"`` bzw. ``"IMPORT.CSV"`` — der exakte Vergleich des
        entschluesselten Feldes faellt bei beiden durch.
        """
        upload = _UnnamedUpload((PSEUDONYM_HEADER + "IMP-01;;;\n").encode("utf-8"))
        assert upload.name == ""

        job = import_engine.store_import_upload(
            facility,
            admin_user,
            upload,
            target_type=ImportJob.TargetType.CLIENT,
        )

        assert decrypt_field(job.original_filename_encrypted) == "import.csv"


@pytest.mark.django_db
class TestStoreImportUploadStartsWithAnEmptyColumnMap:
    """``mapping_json`` startet als ``{"columns": {}, ...}`` — mit genau diesem Key.

    ``apply_mapping`` schreibt spaeter per Spread ``{**job.mapping_json,
    "columns": columns}`` den richtigen Key nach, und ``_mapped_columns()``
    liest ``.get("columns") or {}`` — ein falscher Startkey faellt deshalb
    nirgends auf, bleibt aber als Muell-Key dauerhaft in der Zeile stehen.
    Kein Bestandstest vergleicht ``mapping_json`` direkt nach dem Upload.
    """

    def test_mapping_json_has_the_exact_shape_after_upload(self, facility, admin_user, media_root):
        """Pin: ``x_store_import_upload__mutmut_54`` (``"XXcolumnsXX"``)
        und ``x_store_import_upload__mutmut_55`` (``"COLUMNS"``).

        Original: ``{"columns": {}, "delimiter": ";", "encoding":
        "utf-8-sig"}``. Mutanten: derselbe Inhalt unter ``"XXcolumnsXX"`` bzw.
        ``"COLUMNS"`` — die exakte Dict-Gleichheit (in-memory UND aus der DB)
        faellt bei beiden durch.
        """
        job = _stored_job(facility, admin_user, delimiter=";")

        expected = {"columns": {}, "delimiter": ";", "encoding": "utf-8-sig"}
        assert job.mapping_json == expected
        job.refresh_from_db()
        assert job.mapping_json == expected


@pytest.mark.django_db
class TestStoreImportUploadCleanupKeepsTheOriginalError:
    """Scheitert der DB-Write, raeumt der Cleanup auf — OHNE den Fehler zu maskieren.

    Der ``except``-Zweig existiert, damit keine verwaiste ``.enc``-Datei
    zurueckbleibt; der eigentliche Fehler muss danach unveraendert
    weiterfliegen (``raise``). ``missing_ok=True`` ist genau die Zusage, dass
    das Aufraeumen selbst nicht scheitern kann — auch nicht, wenn die Datei zu
    diesem Zeitpunkt bereits weg ist. Der ganze Zweig ist in der Bestandssuite
    ungetestet.
    """

    def test_an_already_removed_file_does_not_mask_the_db_error(self, facility, admin_user, media_root, monkeypatch):
        """Pin: ``x_store_import_upload__mutmut_60`` (``missing_ok=None``)
        und ``x_store_import_upload__mutmut_61`` (``missing_ok=False``).

        Der Testdouble von ``ImportJob.save`` entfernt die soeben geschriebene
        ``.enc``-Datei (Race/externe Loeschung) und wirft dann den DB-Fehler.

        Original: der ``RuntimeError("db-write kaputt")`` erreicht den
        Aufrufer unveraendert. Mutanten: ``unlink`` wirft im ``except``-Zweig
        ``FileNotFoundError`` — der Aufrufer sieht den falschen Fehler und
        erfaehrt nie, dass der DB-Write scheiterte.
        """

        def _exploding_save(self, *args, **kwargs):
            for leftover in Path(media_root).rglob("*.enc"):
                leftover.unlink()
            raise RuntimeError("db-write kaputt")

        monkeypatch.setattr(ImportJob, "save", _exploding_save)

        with pytest.raises(RuntimeError) as excinfo:
            _stored_job(facility, admin_user)

        assert str(excinfo.value) == "db-write kaputt"


# ============================================================================
# probe_import — ``committed`` ist ein echtes ``False`` (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestProbeImportReportsCommittedAsBool:
    """Der Probelauf meldet ``committed = False`` — bool, nicht bloss falsy.

    ``run_import`` bildet ``committed = commit and not errors``; ein falsy
    ``commit`` schlaegt per Kurzschluss direkt auf den Rueckgabewert durch. Der
    Wert landet unveraendert in ``result_json`` (und damit in der Detailseite
    und in jeder spaeteren Auswertung) — der ``summary``-Vertrag im
    ``run_import``-Docstring nennt ausdruecklich einen Wahrheitswert.
    """

    def test_dry_run_summary_carries_the_boolean_false(self, facility, admin_user, media_root):
        """Pin: ``x_probe_import__mutmut_8`` — ``run_import(job, user, commit=False)``
        -> ``commit=None``.

        Original: ``result["summary"]["committed"] is False`` (und in der DB
        ``false``). Mutant: ``committed`` ist ``None`` — die Identitaetspruefung
        schlaegt fehl, in ``result_json`` steht ``null`` statt ``false``.
        """
        job = _stored_job(facility, admin_user, content=PSEUDONYM_HEADER + "IMP-01;;;\n")

        result = import_engine.probe_import(job, admin_user, {"Pseudonym": "pseudonym"})

        assert result["summary"]["committed"] is False
        assert result["summary"]["errors"] == 0
        job.refresh_from_db()
        assert job.result_json["summary"]["committed"] is False
        assert job.status == ImportJob.Status.VALIDATED


# ============================================================================
# commit_import — die beiden Abbruch-Meldungen (6 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestCommitImportRejectsAnErroneousRun:
    """Ein Lauf mit Zeilenfehlern wird mit klarem Wortlaut abgelehnt.

    Die Meldung erreicht ueber den Fehler-Redirect des Wizards die
    ``messages``-Ausgabe. ``test_commit_requires_a_clean_dry_run`` prueft nur
    die Exception-Klasse.
    """

    def test_error_count_blocks_the_commit_with_the_exact_message(self, facility, admin_user, media_root):
        """Pin: ``x_commit_import__mutmut_9``/``_10``/``_11``/``_12``.

        * ``_9``: ``_(None)`` — der lazy Proxy fliegt erst beim Rendern
          auseinander: ``excinfo.value.messages`` wirft ``AttributeError``
          (``'NoneType' object has no attribute 'replace'``), im View waere es
          eine 500 statt des Fehler-Redirects;
        * ``_10``: ``"XXDer Importlauf enthält Fehler und kann nicht
          übernommen werden.XX"``;
        * ``_11``: durchgehend kleingeschrieben;
        * ``_12``: durchgehend GROSSGESCHRIEBEN.

        Original: ``messages == ["Der Importlauf enthält Fehler und kann nicht
        übernommen werden."]``.
        """
        Client.objects.create(facility=facility, pseudonym="DUP-A")
        job = _stored_job(facility, admin_user, content=PSEUDONYM_HEADER + "DUP-A;;;\n")
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, admin_user, commit=False)
        assert job.status == ImportJob.Status.VALIDATED
        assert job.error_count == 1

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine.commit_import(job, admin_user)

        assert excinfo.value.messages == ["Der Importlauf enthält Fehler und kann nicht übernommen werden."]


@pytest.mark.django_db
class TestCommitImportConflictAfterTheDryRun:
    """Der All-or-nothing-Rueckfall sagt ausdruecklich, dass NICHTS gespeichert wurde.

    ``test_a_conflict_after_the_dry_run_rolls_back_but_keeps_the_report``
    matcht per Teilstring ``"nichts gespeichert"`` — dieser Ausschnitt
    ueberlebt sowohl den ``XX…XX``-Wrapper als auch die Kleinschreibung des
    ganzen Satzes unbeschadet.
    """

    def test_conflicting_pseudonym_raises_the_exact_message(self, facility, admin_user, media_root):
        """Pin: ``x_commit_import__mutmut_28`` (``"XXBeim Übernehmen … gespeichert.XX"``)
        und ``x_commit_import__mutmut_29`` (durchgehend kleingeschrieben).

        Nach sauberem Probelauf vergibt jemand das Pseudonym; die Uebernahme
        faellt zurueck. Original: ``messages == ["Beim Übernehmen sind Fehler
        aufgetreten — es wurde nichts gespeichert."]``. Mutanten liefern den
        gewrappten bzw. kleingeschriebenen Satz — der exakte Vergleich faellt
        bei beiden durch, waehrend der Bestands-Teilstring gruen bliebe.
        """
        job = _stored_job(facility, admin_user, content=PSEUDONYM_HEADER + "IMP-02;;;\n")
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, admin_user, commit=False)
        assert job.error_count == 0
        Client.objects.create(facility=facility, pseudonym="IMP-02")

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine.commit_import(job, admin_user)

        assert excinfo.value.messages == ["Beim Übernehmen sind Fehler aufgetreten — es wurde nichts gespeichert."]


# ============================================================================
# _CellError — der Fehlercode ist auch die Exception-Meldung (1 Mutant)
# ============================================================================


class TestCellErrorCarriesItsCodeAsMessage:
    """``_CellError`` traegt den Code auch in ``args``/``str()``.

    ``self.code``/``self.column`` sind der Vertrag nach innen; die
    Exception-Meldung ist der Vertrag nach aussen — sie ist das Einzige, was
    im Traceback bzw. im Error-Tracking steht, sollte die Ausnahme je an einem
    Klassifikationszweig vorbeilaufen.
    """

    def test_str_and_args_repeat_the_code(self):
        """Pin: ``xǁ_CellErrorǁ__init____mutmut_1`` — ``super().__init__(code)``
        -> ``super().__init__(None)``.

        Original: ``str(error) == "invalid_boolean"`` und ``args ==
        ("invalid_boolean",)``. Mutant: ``str(error) == "None"`` und ``args ==
        (None,)`` — der Code steht nur noch im Attribut, nicht mehr im
        Traceback.
        """
        error = import_engine._CellError("invalid_boolean", column="Aktiv")

        assert str(error) == "invalid_boolean"
        assert error.args == ("invalid_boolean",)
        assert error.code == "invalid_boolean"
        assert error.column == "Aktiv"
