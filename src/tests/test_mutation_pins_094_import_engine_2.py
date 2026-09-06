"""Pin-Tests zum Meilenstein release-0.94 — Batch ``import_engine_2``.

Quelle: ``src/core/services/import_engine.py``; Mutantenbaum
``mutants/src/core/services/import_engine.py``. Herkunft der Triage:
die Survivor-Triage release-0.94 (Ledger des Meilensteins) (60 triagierte, killbare
Survivors; Restmenge nach dem 0.93-Batch ``test_mutation_pins_093_import_engine.py``).

Schwerpunkt dieses Batches ist der **Wortlaut-Vertrag** der Import-Engine —
genau der Teil, den die Bestandstests bislang nur per Teilstring (``match=``)
oder gar nicht geprueft haben:

* die Zuordnungsziele (:func:`~core.services.import_engine.available_targets`):
  Beschriftung UND ``required``-Flag jedes statischen wie dynamischen Ziels
  (27 Mutanten — Label ``None``, ``XX..XX``-Marker, Gross-/Kleinschreibung,
  ``required=None``);
* die vier Mapping-Fehlermeldungen (unbekanntes Ziel, Mehrfachzuordnung,
  fehlende Pflichtfelder inkl. Trennzeichen der Mehrfachliste, abgeschlossener
  Lauf);
* die Meldungen der harten Grenzen (``MAX_BYTES``-Anzeige in MB inkl. der
  Ganzzahl-Division, ``MAX_ROWS``) und der CSV-Parserfehler;
* die Statusmeldungen von :func:`commit_import`/:func:`abort_import` sowie der
  Vorgabewert ``reason="user"`` und die ``update_fields``-Beschraenkung von
  :func:`apply_mapping`;
* das Sniffer-Probenfenster (4096 Zeichen) und der Leerwert-Vorgabewert des
  PII-Filters.

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); der
Sprachkontext wird bei Texten per ``translation.override("de")``
festgenagelt; ``pytest.mark.django_db`` nur wo noetig (die reinen Helfer
laufen ohne DB); Fixtures kommen aus ``src/tests/conftest.py``, Model-Objekte
sind echt (keine Fakes); KEINE Imports aus ``test_rls_functional``. Jeder Test
nennt seine Mutanten in der Docstring samt dem Wert, der beim Mutanten
abweicht.
"""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import translation

from core.models import AuditLog, Client, ImportJob
from core.services import import_engine

PSEUDONYM_HEADER = "Pseudonym;Kontaktstufe;Altersgruppe;Notizen\n"

MSG_ALREADY_FINAL = "Der Importlauf ist bereits abgeschlossen."
MSG_TOO_LARGE = "Die Datei ist zu groß (maximal 5 MB)."


def _upload(content, name="import.csv"):
    """CSV als hochgeladene Datei — Bytes wie sie der Browser schickt."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, raw, content_type="text/csv")


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def client_job(facility, admin_user, media_root):
    """Personen-Import, hochgeladen und zugeordnet (Spiegel der Bestands-Fixture)."""
    content = PSEUDONYM_HEADER + "IMP-01;identified;u18;Erste\nIMP-02;qualified;27_plus;Zweite\n"
    job = import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(content),
        target_type=ImportJob.TargetType.CLIENT,
    )
    import_engine.apply_mapping(job, {"Pseudonym": "pseudonym", "Notizen": "notes"})
    return job


@pytest.fixture
def unmapped_client_job(facility, admin_user, media_root):
    """Frisch hochgeladener Personen-Import OHNE Zuordnung."""
    return import_engine.store_import_upload(
        facility,
        admin_user,
        _upload(PSEUDONYM_HEADER + "A;;;\n"),
        target_type=ImportJob.TargetType.CLIENT,
    )


@pytest.fixture
def event_job(facility, admin_user, doc_type_contact, media_root):
    """Ereignis-Import mit dynamischem Zusatzfeld ``Dauer``."""
    return import_engine.store_import_upload(
        facility,
        admin_user,
        _upload("Datum;Dauer\n01.01.2026;5\n"),
        target_type=ImportJob.TargetType.EVENT,
        document_type=doc_type_contact,
    )


class TestReadUploadMessage:
    """Der Wortlaut der Groessengrenze — inklusive der angezeigten MB-Zahl."""

    def test_size_check_names_the_limit_in_whole_megabytes(self):
        """Pin: x__read_upload__mutmut_12 — ``ValidationError(None)`` statt Meldung;
        x__read_upload__mutmut_15 — Text mit ``XX..XX``-Markern umschlossen;
        x__read_upload__mutmut_16 — Text komplett kleingeschrieben;
        x__read_upload__mutmut_20 — ``MAX_BYTES / (1024 * 1024)`` (Anzeige ``5.0``);
        x__read_upload__mutmut_21 — Nenner ``1024 / 1024`` (Anzeige ``5242880.0``);
        x__read_upload__mutmut_22 — Nenner ``1025 * 1024`` (Anzeige ``4``);
        x__read_upload__mutmut_23 — Nenner ``1024 * 1025`` (Anzeige ``4``).

        Die Bestandspruefung (``test_read_upload_enforces_the_byte_limit``)
        prueft nur, DASS ein ``ValidationError`` fliegt — jeder dieser sieben
        Mutanten ueberlebt sie. Original: ``Die Datei ist zu groß (maximal 5
        MB).``; Mutanten liefern ``None`` bzw. ``XX..XX``, Kleinschreibung,
        ``5.0``, ``5242880.0`` oder ``4``.
        """
        stream = io.BytesIO(b"x" * 16)
        stream.size = import_engine.MAX_BYTES + 1

        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine._read_upload(stream)

        assert exc_info.value.messages == [MSG_TOO_LARGE]


class TestSniffDelimiterSample:
    """Das Probenfenster des Sniffers ist genau 4096 Zeichen breit."""

    def test_sample_stops_after_4096_characters(self):
        """Pin: x__sniff_delimiter__mutmut_5 — ``text[:4096]`` -> ``text[:4097]``.

        Der Text besteht aus 4096 trennerlosen Zeichen, erst danach folgt ein
        Komma. Im Fenster ``[:4096]`` findet ``csv.Sniffer`` keinen Trenner
        (``csv.Error``) und der Rueckfallwert ``";"`` greift; der Mutant sieht
        das Komma mit und liefert ``","``.
        """
        text = "a" * 4096 + ","

        assert import_engine._sniff_delimiter(text) == ";"


class TestParseCsvMessages:
    """Wortlaut der beiden Parser-Abbrueche (Zeilenlimit, kaputtes CSV)."""

    def test_row_limit_message_names_the_limit(self):
        """Pin: x__parse_csv_rows__mutmut_10 — ``ValidationError(None)``
        (``.messages`` -> ``['None']``);
        x__parse_csv_rows__mutmut_13 — Text mit ``XX..XX``-Markern;
        x__parse_csv_rows__mutmut_14 — Text komplett kleingeschrieben.

        Original: ``Die Datei hat mehr als 1000 Datenzeilen.``
        """
        text = "Pseudonym\n" + "".join(f"P-{index}\n" for index in range(import_engine.MAX_ROWS + 1))

        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine._parse_csv(text, ";")

        assert exc_info.value.messages == [f"Die Datei hat mehr als {import_engine.MAX_ROWS} Datenzeilen."]

    def test_malformed_csv_message_is_the_full_two_sentence_hint(self):
        """Pin: x__parse_csv__mutmut_17 — zweiter Satz komplett grossgeschrieben.

        Original: ``... (Fehler in Zeile 2). Bitte Anführungszeichen und
        Zeilenumbrüche innerhalb von Feldern prüfen.``; der Mutant schreibt
        den zweiten Satz in Versalien. Die Bestandstests matchen nur auf
        ``"Fehler in Zeile 2"`` und ueberleben das.
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine._parse_csv("Pseudonym\r\na\rb\r\n", ";")

        assert exc_info.value.messages == [
            "Die Datei konnte nicht als CSV gelesen werden (Fehler in Zeile 2). "
            "Bitte Anführungszeichen und Zeilenumbrüche innerhalb von Feldern prüfen."
        ]


class TestPiiSafeMessage:
    """Leere Zellen duerfen den Fail-safe-Filter NIE ausloesen."""

    def test_empty_cells_never_trigger_the_filter(self):
        """Pin: x__pii_safe_message__mutmut_3 — ``(cell or "")`` -> ``(cell or "XXXX")``.

        Mit ausschliesslich leeren Zellen bleibt die Meldung im Original
        unveraendert; der Mutant macht aus jeder leeren Zelle den Wert
        ``"XXXX"``, findet ihn in der Meldung und ersetzt sie durch
        ``Die Zeile konnte nicht übernommen werden.``
        """
        with translation.override("de"):
            kept = import_engine._pii_safe_message("Text mit XXXX drin", ["", None, "   "])

        assert kept == "Text mit XXXX drin"


@pytest.mark.django_db
class TestAvailableTargets:
    """Beschriftung und ``required``-Flag jedes Zuordnungsziels."""

    def test_client_targets_carry_their_exact_labels_and_flags(self, unmapped_client_job):
        """Pin: x_available_targets__mutmut_12/13/14 — ``gettext("Pseudonym")``
        -> ``XXPseudonymXX`` / ``pseudonym`` / ``PSEUDONYM``;
        x_available_targets__mutmut_17 — Label von ``contact_stage`` -> ``None``;
        x_available_targets__mutmut_18 — ``required`` von ``contact_stage`` -> ``None``;
        x_available_targets__mutmut_25/26/27 — ``Kontaktstufe`` -> ``XX..XX`` /
        klein / gross;
        x_available_targets__mutmut_30 — Label von ``age_cluster`` -> ``None``;
        x_available_targets__mutmut_31 — ``required`` von ``age_cluster`` -> ``None``;
        x_available_targets__mutmut_38/39/40 — ``Altersgruppe`` -> ``XX..XX`` /
        klein / gross;
        x_available_targets__mutmut_43 — Label von ``notes`` -> ``None``;
        x_available_targets__mutmut_51/52/53 — ``Notizen`` -> ``XX..XX`` /
        klein / gross.

        Die Bestandspruefung vergleicht nur die Schluesselmenge und zwei
        ``required``-Flags — die Beschriftungen (die im Zuordnungs-Formular und
        in jeder Mapping-Fehlermeldung stehen) waren voellig ungepinnt. Beim
        Mutanten weicht der jeweilige Wert der ``labels``-Abbildung ab
        (``None``, ``"XXPseudonymXX"``, ``"pseudonym"`` …) bzw. ist
        ``required`` ``None`` statt ``False``.
        """
        with translation.override("de"):
            targets = import_engine.available_targets(unmapped_client_job)

        assert {target.key: target.label for target in targets} == {
            "pseudonym": "Pseudonym",
            "contact_stage": "Kontaktstufe",
            "age_cluster": "Altersgruppe",
            "notes": "Notizen",
        }
        flags = {target.key: target.required for target in targets}
        assert flags["pseudonym"] is True
        assert flags["contact_stage"] is False
        assert flags["age_cluster"] is False
        assert flags["notes"] is False

    def test_event_targets_carry_their_exact_labels_and_flags(self, event_job):
        """Pin: x_available_targets__mutmut_57 — Label von ``occurred_at`` -> ``None``;
        x_available_targets__mutmut_65/66/67 — ``Zeitpunkt`` -> ``XXZeitpunktXX``
        / ``zeitpunkt`` / ``ZEITPUNKT``;
        x_available_targets__mutmut_70 — Label von ``client_pseudonym`` -> ``None``;
        x_available_targets__mutmut_78/79/80 — ``Pseudonym der Person`` ->
        ``XX..XX`` / klein / gross;
        x_available_targets__mutmut_84 — Label des dynamischen Ziels
        (``template.name``) -> ``None``;
        x_available_targets__mutmut_85 — ``required`` der dynamischen Ziele ->
        ``None`` statt ``False``.

        Beim Mutanten weicht der jeweilige Label-Wert ab bzw. liefert
        ``keys["data_json.dauer"].required`` ``None`` statt ``False``
        (``None is False`` ist falsch).
        """
        with translation.override("de"):
            targets = import_engine.available_targets(event_job)

        labels = {target.key: target.label for target in targets}
        assert labels["occurred_at"] == "Zeitpunkt"
        assert labels["client_pseudonym"] == "Pseudonym der Person"
        assert labels["data_json.dauer"] == "Dauer"
        assert labels["data_json.notiz"] == "Notiz"
        flags = {target.key: target.required for target in targets}
        assert flags["occurred_at"] is True
        assert flags["client_pseudonym"] is True
        assert flags["data_json.dauer"] is False
        assert flags["data_json.notiz"] is False


@pytest.mark.django_db
class TestMappingErrorMessages:
    """Der Wortlaut der drei Einzel-Mapping-Fehler und der Pflichtfeldliste."""

    def test_unknown_target_message_is_exact(self, unmapped_client_job):
        """Pin: x__validate_mapping_entry__mutmut_14 — Meldungs-Lambda -> ``None``
        (``.messages`` -> ``['None']``);
        x__validate_mapping_entry__mutmut_17 — Text mit ``XX..XX``-Markern;
        x__validate_mapping_entry__mutmut_18 — ``Zielfeld.`` -> ``zielfeld.``.

        Original: ``„facility“ ist kein zulässiges Zielfeld.``
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(unmapped_client_job, {"Pseudonym": "pseudonym", "Notizen": "facility"})

        assert exc_info.value.messages == ["„facility“ ist kein zulässiges Zielfeld."]

    def test_duplicate_target_message_names_the_label(self, unmapped_client_job):
        """Pin: x__validate_mapping_entry__mutmut_24 — Meldungs-Lambda -> ``None``;
        x__validate_mapping_entry__mutmut_27 — Text mit ``XX..XX``-Markern;
        x__validate_mapping_entry__mutmut_28 — ``Das Zielfeld`` -> ``das zielfeld``.

        Original: ``Das Zielfeld „Pseudonym“ ist mehrfach zugeordnet.``
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(unmapped_client_job, {"Pseudonym": "pseudonym", "Notizen": "pseudonym"})

        assert exc_info.value.messages == ["Das Zielfeld „Pseudonym“ ist mehrfach zugeordnet."]

    def test_missing_required_message_lists_the_single_field(self, unmapped_client_job):
        """Pin: x__validate_required_mappings__mutmut_7 — Meldungs-Lambda -> ``None``;
        x__validate_required_mappings__mutmut_10 — Text mit ``XX..XX``-Markern;
        x__validate_required_mappings__mutmut_11 — Text komplett kleingeschrieben.

        Original: ``Diese Pflichtfelder brauchen eine Spalte: Pseudonym``
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(unmapped_client_job, {"Notizen": "notes"})

        assert exc_info.value.messages == ["Diese Pflichtfelder brauchen eine Spalte: Pseudonym"]

    def test_missing_required_message_joins_several_fields_with_comma_space(self, event_job):
        """Pin: x__validate_required_mappings__mutmut_16 — ``", ".join(missing)``
        -> ``"XX, XX".join(missing)``.

        Der Ereignis-Lauf hat ZWEI Pflichtziele; bleibt keines belegt, wird die
        Liste erstmals wirklich verbunden. Original: ``Diese Pflichtfelder
        brauchen eine Spalte: Zeitpunkt, Pseudonym der Person``; der Mutant
        liefert ``Zeitpunkt`` + ``XX, XX`` + ``Pseudonym der Person``.
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(event_job, {"Dauer": "data_json.dauer"})

        assert exc_info.value.messages == ["Diese Pflichtfelder brauchen eine Spalte: Zeitpunkt, Pseudonym der Person"]


@pytest.mark.django_db
class TestApplyMapping:
    """Abschluss-Guard und Schreibumfang der Zuordnung."""

    def test_final_job_message_is_exact(self, unmapped_client_job):
        """Pin: x_apply_mapping__mutmut_3 — Text mit ``XX..XX``-Markern;
        x_apply_mapping__mutmut_4 — Text komplett kleingeschrieben.

        Original: ``Der Importlauf ist bereits abgeschlossen.``
        """
        unmapped_client_job.status = ImportJob.Status.COMMITTED
        unmapped_client_job.save(update_fields=["status"])

        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.apply_mapping(unmapped_client_job, {"Pseudonym": "pseudonym"})

        assert exc_info.value.messages == [MSG_ALREADY_FINAL]

    def test_save_touches_only_the_mapping_fields(self, unmapped_client_job):
        """Pin: x_apply_mapping__mutmut_39 — ``update_fields=["mapping_json",
        "status", "updated_at"]`` -> ``update_fields=None``.

        Zwischen dem Laden der View-Instanz und dem Mapping-POST schreibt ein
        anderer Vorgang ``result_json`` direkt in die DB. Das Original laesst
        die Spalte in Ruhe; der Mutant schreibt die gesamte (veraltete)
        In-Memory-Instanz zurueck und macht aus ``{"marker": 1}`` wieder
        ``{}`` — ein Lost Update.
        """
        ImportJob.objects.filter(pk=unmapped_client_job.pk).update(result_json={"marker": 1})

        import_engine.apply_mapping(unmapped_client_job, {"Pseudonym": "pseudonym"})

        unmapped_client_job.refresh_from_db()
        assert unmapped_client_job.result_json == {"marker": 1}
        assert unmapped_client_job.status == ImportJob.Status.MAPPED


@pytest.mark.django_db
class TestCommitImportMessages:
    """Die beiden Vorbedingungen der Uebernahme melden sich woertlich."""

    def test_unvalidated_job_message_is_exact(self, client_job, admin_user):
        """Pin: x_commit_import__mutmut_5 — Text mit ``XX..XX``-Markern;
        x_commit_import__mutmut_6 — Text komplett kleingeschrieben.

        Der Lauf steht auf ``MAPPED``. Original: ``Der Importlauf muss zuerst
        fehlerfrei geprüft werden.``
        """
        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.commit_import(client_job, admin_user)

        assert exc_info.value.messages == ["Der Importlauf muss zuerst fehlerfrei geprüft werden."]

    def test_error_count_guard_message_is_exact(self, facility, admin_user, media_root):
        """Pin: x_commit_import__mutmut_8 — ``raise ValidationError(_("Der
        Importlauf enthält Fehler und kann nicht übernommen werden."))`` ->
        ``raise ValidationError(None)``.

        Der Probelauf endet mit einem Zeilenfehler (Pseudonym bereits
        vergeben), der Lauf steht damit auf ``VALIDATED`` MIT
        ``error_count > 0``. Beim Mutanten meldet die Oberflaeche den
        literalen Text ``None`` statt der Erklaerung.
        """
        Client.objects.create(facility=facility, pseudonym="DUP-A")
        job = import_engine.store_import_upload(
            facility,
            admin_user,
            _upload(PSEUDONYM_HEADER + "DUP-A;;;\n"),
            target_type=ImportJob.TargetType.CLIENT,
        )
        import_engine.apply_mapping(job, {"Pseudonym": "pseudonym"})
        import_engine.run_import(job, admin_user, commit=False)
        assert job.status == ImportJob.Status.VALIDATED
        assert job.error_count == 1

        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.commit_import(job, admin_user)

        assert exc_info.value.messages == ["Der Importlauf enthält Fehler und kann nicht übernommen werden."]


@pytest.mark.django_db
class TestAbortImport:
    """Vorgabegrund und Abschluss-Guard des Abbruchs."""

    def test_default_reason_is_recorded_as_user(self, client_job, admin_user):
        """Pin: x_abort_import__mutmut_1 — Vorgabewert ``reason="user"`` ->
        ``"XXuserXX"``; x_abort_import__mutmut_2 — ``reason="user"`` -> ``"USER"``.

        Der Aufruf laesst ``reason`` weg (so ruft die View ab). Der
        Audit-Eintrag traegt im Original ``"user"``; beim Mutanten steht dort
        ``"XXuserXX"`` bzw. ``"USER"`` — die Auswertung „Abbruch durch Person
        oder durch die Aufraeum-Routine" liefe ins Leere.
        """
        import_engine.abort_import(client_job, admin_user)

        entry = AuditLog.objects.filter(action=AuditLog.Action.IMPORT_ABORTED).latest("timestamp")
        assert entry.detail["reason"] == "user"

    def test_final_job_message_is_exact(self, client_job, admin_user):
        """Pin: x_abort_import__mutmut_6 — Text mit ``XX..XX``-Markern;
        x_abort_import__mutmut_7 — Text komplett kleingeschrieben.

        Original: ``Der Importlauf ist bereits abgeschlossen.``
        """
        import_engine.abort_import(client_job, admin_user)

        with translation.override("de"), pytest.raises(ValidationError) as exc_info:
            import_engine.abort_import(client_job, admin_user)

        assert exc_info.value.messages == [MSG_ALREADY_FINAL]
