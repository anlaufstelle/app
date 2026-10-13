"""Pin-Tests der Welle F (0.93) — Prio-1-Survivors in ``core.services.import_engine``.

Quelle: Survivor-Triage des Moduls ``src/core/services/import_engine.py``
(Mutantenbaum ``mutants/src/core/services/import_engine.py``). Gepinnt wird
das bestaetigte Prio-1-Set aus ``welle-f-0.93/reports/pin-import_engine.json``
(22 Mutanten). Schwerpunkte:

* die beiden harten Obergrenzen (``MAX_BYTES``/``MAX_ROWS``) — jeweils die
  Grenze SELBST und der bislang nie unabhaengig erreichte zweite
  Laengencheck in :func:`~core.services.import_engine._read_upload`;
* die Zuordnungs-Wahrheit des Ereignis-Imports (Pseudonym-Filter) und der
  Leer-Fallback fuer zu kurze Datenzeilen;
* die Tenant-Klammer des Zeilen-Locks (``facility_id``-Filter);
* die Provenienz: der handelnde Nutzer wird an ``run_import`` durchgereicht
  (Probelauf UND Uebernahme), und jeder Import-Audit-Eintrag traegt Nutzer,
  Einrichtung und Ziel-Objekt;
* die ``update_fields``-Beschraenkung der drei Teil-Saves (Lost-Update-Schutz
  ausserhalb bzw. neben der Lock-Klammer).

Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen
EXAKT (``==``), nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten);
Sprachkontext wird bei Texten per ``translation.override("de")`` festgenagelt;
KEINE Imports aus ``test_rls_functional``; Fixtures kommen aus
``src/tests/conftest.py`` bzw. werden als echte Model-Objekte angelegt (keine
Fakes). Jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der
beim Mutanten abweicht.
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import translation

from core.models import AuditLog, Client, DocumentType, Event, ImportJob
from core.services import import_engine

PSEUDONYM_HEADER = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\n"


def _upload(content, name="import.csv"):
    """CSV als hochgeladene Datei — Bytes wie sie der Browser schickt."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


class _SizelessUpload:
    """Upload-Objekt OHNE ``.size`` — genau so erreicht man den ZWEITEN Check.

    ``_read_upload`` prueft zuerst das (vom Client gemeldete, optionale)
    ``.size``-Attribut und danach die tatsaechlich gelesene Laenge. Alle
    bestehenden Tests nutzen ``SimpleUploadedFile``/``BytesIO`` mit korrekt
    gesetztem ``.size``, sodass immer schon der erste Check ausloest — der
    zweite ist damit strukturell unerreichbar. Dieses Objekt hat kein
    ``.size`` (``getattr(..., "size", None)`` -> ``None``) und schaltet den
    ersten Check ab.
    """

    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def client_job(facility, admin_user, media_root):
    """Personen-Import mit drei Zeilen, bereits zugeordnet (Status ``MAPPED``)."""
    content = PSEUDONYM_HEADER + "IMP-01;identified;u18;Erste\nIMP-02;qualified;27_plus;Zweite\nIMP-03;;;\n"
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(content),
        target_type=ImportJob.TargetType.CLIENT,
    )
    import_engine.apply_mapping(
        job,
        {
            "Pseudonym": "pseudonym",
            "Kontaktstufe": "contact_stage",
            "Altersgruppe": "age_cluster",
            "Notizen": "notes",
        },
    )
    return job


# ============================================================================
# _read_upload — die 5-MiB-Grenze und ihr zweiter, echter Check (7 Mutanten)
# ============================================================================


class TestReadUploadByteLimitBoundary:
    """Die Memory-DoS-Grenze liegt BEI ``MAX_BYTES``, nicht darunter.

    Der Docstring von ``_read_upload`` erklaert 5 MiB ausdruecklich fuer
    vertretbar; abgelehnt wird erst, was *groesser* ist. Beide bestehenden
    Tests arbeiten mit ``MAX_BYTES + 1`` und treffen deshalb bei ``>`` wie bei
    ``>=`` gleichermassen — die Grenze selbst ist ungetestet.

    Der zweite Check (auf der tatsaechlich gelesenen Laenge) ist der
    eigentliche Schutz: ``.size`` ist optional und clientseitig gemeldet. Er
    wird hier erstmals ueberhaupt erreicht, weil das Testobjekt kein ``.size``
    hat.
    """

    def test_size_attribute_exactly_at_the_limit_is_accepted(self):
        """Pin: ``x__read_upload__mutmut_11`` — ``size > MAX_BYTES`` -> ``size >= MAX_BYTES``.

        Original: liefert die 5-MiB-Datei zurueck (``len == MAX_BYTES``).
        Mutant: wirft bereits hier ``ValidationError`` — der Aufruf schlaegt fehl.
        """
        upload = SimpleUploadedFile("gross.csv", b"a" * import_engine.MAX_BYTES, content_type="text/csv")
        assert upload.size == import_engine.MAX_BYTES

        raw = import_engine._read_upload(upload)

        assert len(raw) == import_engine.MAX_BYTES

    def test_read_length_exactly_at_the_limit_is_accepted(self):
        """Pin: ``x__read_upload__mutmut_25`` — ``len(raw) > MAX_BYTES`` -> ``len(raw) >= MAX_BYTES``.

        Ohne ``.size`` ist dies der einzige greifende Check. Original: gibt die
        5 MiB zurueck. Mutant: wirft ``ValidationError`` fuer eine Datei, die
        exakt auf der erlaubten Grenze liegt.
        """
        upload = _SizelessUpload(b"a" * import_engine.MAX_BYTES)
        assert getattr(upload, "size", None) is None

        raw = import_engine._read_upload(upload)

        assert len(raw) == import_engine.MAX_BYTES

    def test_read_length_above_the_limit_raises_the_exact_message(self):
        """Pin: ``x__read_upload__mutmut_27``/``_28``/``_31``/``_32``/``_33``.

        Alle fuenf sitzen in der Meldung des ZWEITEN Checks, den kein
        bestehender Test unabhaengig erreicht:

        * ``_27``: ``_("…") % {...}`` -> ``… / {...}`` -> ``TypeError``
          (``unsupported operand type(s) for /``) statt ``ValidationError``;
        * ``_28``: ``_(None) % {...}`` -> ``AttributeError``
          (``'NoneType' object has no attribute 'replace'``);
        * ``_31``: Platzhalter ``%(mb)s`` -> ``%(MB)s`` bei dict-key ``"mb"``
          -> ``KeyError: 'MB'``;
        * ``_32``: dict-key ``"mb"`` -> ``"XXmbXX"`` -> ``KeyError: 'mb'``;
        * ``_33``: dict-key ``"mb"`` -> ``"MB"`` -> ``KeyError: 'mb'``.

        Original: geordnete ``ValidationError`` mit exakt der Katalogmeldung.
        Jeder Mutant laesst stattdessen eine ungefangene Ausnahme hochgehen —
        ``pytest.raises(ValidationError)`` scheitert damit. Der exakte
        Meldungsvergleich haelt zusaetzlich den Wortlaut (und die eingesetzte
        MB-Zahl) fest.
        """
        upload = _SizelessUpload(b"a" * (import_engine.MAX_BYTES + 1))

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine._read_upload(upload)

        assert excinfo.value.messages == ["Die Datei ist zu groß (maximal 5 MB)."]


# ============================================================================
# _parse_csv_rows — das Zeilenlimit (1 Mutant)
# ============================================================================


class TestParseCsvRowsRowLimitBoundary:
    """``MAX_ROWS`` Datenzeilen sind erlaubt — erst *mehr* ist ein Fehler."""

    def test_exactly_max_rows_is_accepted(self):
        """Pin: ``x__parse_csv_rows__mutmut_9`` — ``len(rows) > MAX_ROWS`` -> ``>=``.

        Original: liefert genau ``MAX_ROWS`` Zeilen zurueck. Mutant: wirft bei
        der ``MAX_ROWS``-ten Zeile ``ValidationError``, obwohl der Vertrag
        (Docstring „Harte Obergrenzen") genau diese Zeilenzahl zulaesst. Der
        einzige bestehende Test nutzt ``MAX_ROWS + 1`` und trifft beide
        Varianten gleich.
        """
        rows = import_engine._parse_csv_rows(iter([[f"P-{index}"] for index in range(import_engine.MAX_ROWS)]), 1)

        assert len(rows) == import_engine.MAX_ROWS

    def test_one_row_above_the_limit_is_rejected(self):
        """Gegenprobe zum Grenzwert (kein eigener Mutant, haelt die Richtung fest)."""
        with pytest.raises(ValidationError):
            import_engine._parse_csv_rows(iter([[f"P-{index}"] for index in range(import_engine.MAX_ROWS + 1)]), 1)


# ============================================================================
# _row_values — Leer-Fallback fuer zu kurze Datenzeilen (1 Mutant)
# ============================================================================


class TestRowValuesShortRowFallback:
    """Eine Datenzeile, die kuerzer als die Kopfzeile ist, liefert LEERE Zellen.

    Der Fallback ist kein Kosmetikdetail: die Pflichtfeld-Guards in
    ``_import_client_row``/``_import_event_row`` pruefen ``if not <wert>``. Ein
    truthy Fuellwert wuerde aus einem sauberen ``required_missing`` einen
    angelegten Datensatz mit Phantom-Pseudonym machen.
    """

    def test_missing_cell_becomes_the_empty_string(self):
        """Pin: ``x__row_values__mutmut_10`` — Fallback ``""`` -> ``"XXXX"``.

        Original: ``values == {"pseudonym": "ABC", "notes": ""}``.
        Mutant: ``values["notes"] == "XXXX"``.
        """
        values, columns = import_engine._row_values(
            ["Pseudonym", "Notizen"],
            ["ABC"],
            {"Pseudonym": "pseudonym", "Notizen": "notes"},
        )

        assert values == {"pseudonym": "ABC", "notes": ""}
        assert columns == {"pseudonym": "Pseudonym", "notes": "Notizen"}

    @pytest.mark.django_db
    def test_empty_required_cell_is_a_row_error(self, facility, admin_user, media_root):
        """A valid-width record with an empty required cell creates no person.

        Structurally short records are rejected on upload (Refs #2575), so
        required-value validation uses an explicit empty cell.
        """
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload("Notizen;Pseudonym\nOnly a note;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Notizen": "notes", "Pseudonym": "pseudonym"})

        result = import_engine.run_import(job, admin_user, commit=False)

        assert result["summary"]["errors"] == 1
        assert result["summary"]["created"] == 0
        assert result["errors"][0]["code"] == "required_missing"
        assert result["errors"][0]["column"] == "Pseudonym"


# ============================================================================
# store_import_upload — Audit-Zuordnung des Uploads (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestStoreImportUploadAuditCarriesActorAndFacility:
    """Der Upload-Audit-Eintrag nennt, WER in WELCHER Einrichtung hochgeladen hat.

    Der bestehende Test fuer ``IMPORT_UPLOADED`` prueft nur ``target_id`` und
    ``detail`` — Nutzer und Einrichtung des Eintrags waren ungepinnt, obwohl
    genau sie den Nachweis tragen (eine Datei mit Klientendaten).
    """

    def test_audit_entry_names_the_uploading_user_and_facility(self, facility, admin_user, media_root):
        """Pin: ``x_store_import_upload__mutmut_63`` (``user=user`` -> ``user=None``)
        und ``x_store_import_upload__mutmut_64`` (``facility=facility`` -> ``facility=None``).

        ``audit_event`` reicht beide Werte direkt in ``AuditLog.objects.create``
        durch. Original: ``entry.user == admin_user`` und
        ``entry.facility == facility``. Mutant ``_63``: ``entry.user is None``;
        Mutant ``_64``: ``entry.facility is None``.
        """
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "AUD-01;;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_UPLOADED).latest("timestamp")

        assert entry.user == admin_user
        assert entry.facility == facility
        assert entry.target_id == str(job.pk)


# ============================================================================
# _import_event_row — Ereignis an die RICHTIGE Person (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestImportEventRowResolvesTheExactPseudonym:
    """Die Zeile bindet das Ereignis an das GESUCHTE Pseudonym.

    Ohne den ``pseudonym``-Filter liefert die Query irgendeine aktive Person
    der Einrichtung (die erste laut ``Client.Meta.ordering = ["pseudonym"]``).
    Alle bestehenden Ereignis-Import-Tests legen genau EINE passende Person an,
    sodass „erste aktive Person" zufaellig die richtige ist.
    """

    def _event_job(self, facility, admin_user, doc_type, pseudonym):
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Datum;Person\n01.03.2026 09:30;{pseudonym}\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type,
        )
        import_engine.apply_mapping(job, {"Datum": "occurred_at", "Person": "client_pseudonym"})
        return job

    def test_event_lands_on_the_named_person_not_the_first_active_one(
        self, facility, admin_user, doc_type_contact, media_root
    ):
        """Pin: ``x__import_event_row__mutmut_48`` — ``filter(pseudonym=pseudonym, is_active=True)``
        -> ``filter(is_active=True)``.

        Zwei aktive Personen in derselben Einrichtung; importiert wird die
        alphabetisch ZWEITE. Original: das Ereignis haengt an ``MMM-02``.
        Mutant: ``.first()`` liefert ``AAA-01`` (Default-Ordering nach
        ``pseudonym``) — der Klartext landet an der falschen Person.
        """
        other = Client.objects.create(
            facility=facility,
            pseudonym="AAA-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        target = Client.objects.create(
            facility=facility,
            pseudonym="MMM-02",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        job = self._event_job(facility, admin_user, doc_type_contact, "MMM-02")

        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["summary"]["errors"] == 0
        event = Event.objects.get(document_type=doc_type_contact)
        assert event.client == target
        assert event.client.pseudonym == "MMM-02"
        assert not Event.objects.filter(client=other).exists()

    def test_unknown_pseudonym_stays_a_row_error_although_other_people_exist(
        self, facility, admin_user, doc_type_contact, media_root
    ):
        """Pin: ``x__import_event_row__mutmut_48`` (zweite Richtung).

        Original: ein Pseudonym, das es nicht gibt, ergibt ``unknown_client``,
        auch wenn ANDERE aktive Personen da sind. Mutant: die Query findet
        ``AAA-01`` und legt ein Ereignis an — ``errors == 0``, ``created == 1``.
        """
        Client.objects.create(
            facility=facility,
            pseudonym="AAA-01",
            contact_stage=Client.ContactStage.IDENTIFIED,
        )
        job = self._event_job(facility, admin_user, doc_type_contact, "GIBTSNICHT")

        result = import_engine.run_import(job, admin_user, commit=True)

        assert result["summary"]["errors"] == 1
        assert result["summary"]["created"] == 0
        assert result["errors"][0]["code"] == "unknown_client"
        assert not Event.objects.exists()


# ============================================================================
# _lock_job — Tenant-Klammer des Zeilen-Locks (1 Mutant)
# ============================================================================


@pytest.mark.django_db(transaction=True)
class TestLockJobFiltersByFacility:
    """Der Lock liest die Zeile mit ``pk`` UND ``facility_id`` neu.

    Der Docstring nennt den expliziten ``facility_id``-Filter ausdruecklich als
    das, was „Tenant- und RLS-Isolation" greifen laesst — und laut
    ``core/services/scoping.py`` wird RLS in Dev-/Test-/Cron-/Superadmin-
    Kontexten bewusst umgangen, der App-Filter ist dort die einzige Schranke.
    Bislang war nur der ``pk``-Filter gepinnt.
    """

    def test_lock_syncs_the_row_for_the_matching_facility(self, client_job):
        """Positivprobe: mit passender Einrichtung synchronisiert der Lock den Status."""
        ImportJob.objects.filter(pk=client_job.pk).update(status=ImportJob.Status.VALIDATED)
        stale = ImportJob.objects.get(pk=client_job.pk)
        stale.status = ImportJob.Status.UPLOADED

        with transaction.atomic():
            import_engine._lock_job(stale)

        assert stale.status == ImportJob.Status.VALIDATED

    def test_lock_does_not_find_the_job_under_a_foreign_facility(self, client_job, other_facility):
        """Pin: ``x__lock_job__mutmut_5`` — ``get(pk=job.pk, facility_id=job.facility_id)``
        -> ``get(pk=job.pk)``.

        Die Instanz traegt in-memory eine fremde ``facility_id`` (derselbe
        Primaerschluessel). Original: ``ImportJob.DoesNotExist`` — die Zeile
        gehoert nicht zu dieser Einrichtung. Mutant: die Query findet sie ueber
        den ``pk`` allein und synchronisiert sie klaglos, der Aufruf laeuft
        durch.
        """
        job = ImportJob.objects.get(pk=client_job.pk)
        job.facility_id = other_facility.pk

        with transaction.atomic(), pytest.raises(ImportJob.DoesNotExist):
            import_engine._lock_job(job)


# ============================================================================
# run_import — Teil-Save statt Voll-Save (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestRunImportSavesOnlyItsOwnFields:
    """``run_import`` schreibt NUR ``result_json``/``status``/``updated_at`` zurueck.

    Die Funktion arbeitet auf der Instanz, die der Aufrufer mitbringt — im
    Wizard ueber mehrere Requests hinweg. Ein Voll-Save wuerde alle uebrigen
    Felder mit diesem (moeglicherweise veralteten) Snapshot ueberschreiben und
    eine zwischenzeitliche Aenderung still verlieren (Lost-Update). Kein Test
    pinnte bisher ein ``update_fields``-Argument dieses Moduls.
    """

    def test_a_concurrent_change_to_another_field_survives_the_run(self, client_job, admin_user):
        """Pin: ``x_run_import__mutmut_70`` — ``update_fields=["result_json", "status",
        "updated_at"]`` -> ``update_fields=None``.

        Zwischen dem Laden der Instanz und dem Lauf setzt ein anderer
        Schreibzugriff ``mapping_json`` in der DB neu. Original: das Feld traegt
        danach weiterhin den nebenlaeufig gesetzten Wert (der Teil-Save fasst
        es nicht an). Mutant: der Voll-Save schreibt den veralteten
        In-Memory-Stand zurueck, ``mapping_json`` verliert den Schluessel
        ``"parallel"``.
        """
        concurrent_mapping = {**client_job.mapping_json, "parallel": "run_import"}
        ImportJob.objects.filter(pk=client_job.pk).update(mapping_json=concurrent_mapping)

        import_engine.run_import(client_job, admin_user, commit=False)

        reloaded = ImportJob.objects.get(pk=client_job.pk)
        assert reloaded.mapping_json == concurrent_mapping
        # Der Teil-Save muss sein eigenes Werk trotzdem persistieren.
        assert reloaded.status == ImportJob.Status.VALIDATED
        assert reloaded.result_json["summary"]["created"] == 3


# ============================================================================
# probe_import — der echte Nutzer im Probelauf (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestProbeImportRunsAsTheRealUser:
    """Der Probelauf sieht dieselben Rollen-Grenzen wie die Uebernahme.

    ``create_event`` prueft ``user_can_see_document_type`` nur, wenn ein Nutzer
    uebergeben wird. Faellt der Probelauf auf ``None`` zurueck, meldet die
    Vorschau eine Zeile als in Ordnung, die die Uebernahme spaeter ablehnt —
    Vorschau und Commit driften auseinander. Beide bestehenden
    ``probe_import``-Tests greifen nur den fruehen ``is_final``-Guard ab und
    erreichen den Zeilen-Loop nie.
    """

    def test_probe_reports_the_role_restriction_of_the_document_type(
        self, facility, admin_user, assistant_user, client_identified, media_root
    ):
        """Pin: ``x_probe_import__mutmut_7`` — ``run_import(job, user, commit=False)``
        -> ``run_import(job, None, commit=False)``.

        Der Dokumentationstyp ist ``high``, die Rolle ``assistant`` darf nur
        ``normal`` (``ROLE_MAX_SENSITIVITY``). Original: die Zeile scheitert mit
        Code ``permission`` (``errors == 1``, ``created == 0``). Mutant: ohne
        Nutzer entfaellt die Pruefung, der Probelauf meldet ``errors == 0`` und
        ``created == 1``.
        """
        doc_type = DocumentType.objects.create(
            facility=facility,
            name="Hochsensibel",
            sensitivity=DocumentType.Sensitivity.HIGH,
        )
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(f"Datum;Person\n01.03.2026 09:30;{client_identified.pseudonym}\n"),
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type,
        )

        result = import_engine.probe_import(job, assistant_user, {"Datum": "occurred_at", "Person": "client_pseudonym"})

        assert result["summary"]["errors"] == 1
        assert result["summary"]["created"] == 0
        assert result["errors"][0]["code"] == "permission"


# ============================================================================
# commit_import — Provenienz, Teil-Save und Audit-Ziel (4 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestCommitImportWritesWithTheRealUser:
    """Die uebernommenen Datensaetze tragen die handelnde Person."""

    def test_created_clients_and_their_audit_entries_name_the_user(self, client_job, admin_user):
        """Pin: ``x_commit_import__mutmut_15`` — ``run_import(job, user, commit=True)``
        -> ``run_import(job, None, commit=True)``.

        ``user`` wird ueber ``_execute_import_row`` an ``create_client``
        weitergereicht und dort zu ``Client.created_by``, ``Activity.actor`` und
        dem ``user`` des ``CLIENT_CREATE``-Audits. Original: beides ist
        ``admin_user``. Mutant: ``created_by is None`` und der Audit-Eintrag
        hat keinen Nutzer — der Import verliert vollstaendig die Zuordnung zur
        ausfuehrenden Person. Der bestehende Test prueft nur den aeusseren
        ``IMPORT_COMMITTED``-Eintrag, der separat mit dem echten Nutzer
        geschrieben wird.
        """
        import_engine.run_import(client_job, admin_user, commit=False)

        import_engine.commit_import(client_job, admin_user)

        created = Client.objects.get(facility=client_job.facility, pseudonym="IMP-01")
        assert created.created_by == admin_user
        entry = AuditLog.objects.get(action=AuditLog.Action.CLIENT_CREATE, target_id=str(created.pk))
        assert entry.user == admin_user


@pytest.mark.django_db
class TestCommitImportSavesOnlyItsOwnFields:
    """Der Aufraeum-Save nach der Uebernahme fasst nur seine drei Felder an.

    Er laeuft bewusst AUSSERHALB der ``atomic()``/Lock-Klammer (Kommentar im
    Modul). Ein Voll-Save wuerde eine Aenderung, die in diesem Fenster von
    anderer Seite persistiert wurde, mit dem alten In-Memory-Stand
    ueberschreiben.
    """

    def test_a_concurrent_change_to_another_field_survives_the_commit(self, client_job, admin_user):
        """Pin: ``x_commit_import__mutmut_35`` — ``update_fields=["storage_filename",
        "original_filename_encrypted", "updated_at"]`` -> ``update_fields=None``.

        Original: ``mapping_json`` behaelt den nebenlaeufig gesetzten Wert, und
        der Lauf raeumt trotzdem auf (``storage_filename == ""``). Mutant: der
        Voll-Save schreibt den veralteten Snapshot zurueck, der Schluessel
        ``"parallel"`` in ``mapping_json`` ist weg.
        """
        import_engine.run_import(client_job, admin_user, commit=False)
        concurrent_mapping = {**client_job.mapping_json, "parallel": "commit_import"}
        ImportJob.objects.filter(pk=client_job.pk).update(mapping_json=concurrent_mapping)

        import_engine.commit_import(client_job, admin_user)

        reloaded = ImportJob.objects.get(pk=client_job.pk)
        assert reloaded.mapping_json == concurrent_mapping
        assert reloaded.storage_filename == ""
        assert reloaded.original_filename_encrypted == {}
        assert reloaded.status == ImportJob.Status.COMMITTED


@pytest.mark.django_db
class TestCommitImportAuditPointsAtTheJob:
    """Der ``IMPORT_COMMITTED``-Eintrag ist mit dem konkreten Lauf verknuepft."""

    def test_audit_entry_targets_the_import_job(self, client_job, admin_user):
        """Pin: ``x_commit_import__mutmut_45`` (``target_obj=job`` -> ``target_obj=None``)
        und ``x_commit_import__mutmut_50`` (Kwarg ``target_obj`` ganz entfernt,
        Default ``None``).

        ``audit_event`` leitet ``target_type``/``target_id`` ausschliesslich aus
        ``target_obj`` ab. Original: ``"ImportJob"`` und ``str(job.pk)``.
        Beide Mutanten: beide Felder bleiben ``""`` — der Audit-Trail verliert
        die Verknuepfung zum Importlauf. Der bestehende Test prueft nur
        ``detail``.
        """
        import_engine.run_import(client_job, admin_user, commit=False)

        import_engine.commit_import(client_job, admin_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_COMMITTED).latest("timestamp")
        assert entry.target_type == "ImportJob"
        assert entry.target_id == str(client_job.pk)
        assert entry.user == admin_user
        assert entry.facility == client_job.facility


# ============================================================================
# abort_import — Teil-Save und Audit-Ziel (3 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestAbortImportSavesOnlyItsOwnFields:
    """Der Abbruch schreibt nur Dateiname, Titel-Token, Status und Zeitstempel.

    ``_lock_job`` synchronisiert beim Neuladen unter Zeilen-Lock ausdruecklich
    nur ``status``, ``result_json`` und ``storage_filename`` — alle uebrigen
    Felder behalten den Stand, den der Aufrufer zufaellig im Speicher hatte.
    Genau deshalb darf der Save nicht voll sein.
    """

    def test_a_concurrent_change_to_another_field_survives_the_abort(self, client_job, admin_user):
        """Pin: ``x_abort_import__mutmut_14`` — ``update_fields=["storage_filename",
        "original_filename_encrypted", "status", "updated_at"]`` -> ``update_fields=None``.

        Original: ``mapping_json`` traegt danach den nebenlaeufig gesetzten
        Wert, der Lauf steht auf ``ABORTED``. Mutant: der Voll-Save
        ueberschreibt ``mapping_json`` mit dem veralteten In-Memory-Stand, der
        Schluessel ``"parallel"`` fehlt.
        """
        concurrent_mapping = {**client_job.mapping_json, "parallel": "abort_import"}
        ImportJob.objects.filter(pk=client_job.pk).update(mapping_json=concurrent_mapping)

        import_engine.abort_import(client_job, admin_user)

        reloaded = ImportJob.objects.get(pk=client_job.pk)
        assert reloaded.mapping_json == concurrent_mapping
        assert reloaded.status == ImportJob.Status.ABORTED
        assert reloaded.storage_filename == ""


@pytest.mark.django_db
class TestAbortImportAuditPointsAtTheJob:
    """Der ``IMPORT_ABORTED``-Eintrag ist mit dem konkreten Lauf verknuepft."""

    def test_audit_entry_targets_the_import_job(self, client_job, admin_user):
        """Pin: ``x_abort_import__mutmut_26`` (``target_obj=job`` -> ``target_obj=None``)
        und ``x_abort_import__mutmut_31`` (Kwarg ``target_obj`` ganz entfernt,
        Default ``None``).

        Original: ``entry.target_type == "ImportJob"`` und
        ``entry.target_id == str(job.pk)``. Beide Mutanten: beide Felder bleiben
        ``""``. Fuer ``IMPORT_UPLOADED`` gibt es genau diesen Test, fuer
        ``IMPORT_ABORTED`` fehlte das Pendant.
        """
        import_engine.abort_import(client_job, admin_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).latest("timestamp")
        assert entry.target_type == "ImportJob"
        assert entry.target_id == str(client_job.pk)
        assert entry.user == admin_user
        assert entry.facility == client_job.facility
