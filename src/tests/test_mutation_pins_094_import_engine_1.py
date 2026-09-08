"""Pin-Tests zum Meilenstein release-0.94 — Modul-Batch ``import_engine_1``.

Quelle: ``src/core/services/import_engine.py``; Mutantenbaum
``mutants/src/core/services/import_engine.py``. Gepinnt wird
das triagierte, killbare Set aus der
Survivor-Triage release-0.94 (Ledger des Meilensteins) (60 Mutanten).

Schwerpunkte dieses Batches:

* **Spaltenbezug im Fehlerbericht.** Fast die Haelfte der Survivors verwirft den
  Spaltennamen (``columns.get(...)`` -> ``None`` / falscher Schluessel) oder den
  Fehlercode eines ``_CellError``. ``result_json`` traegt je Zeile genau drei
  Angaben — Zeile, Spalte, Code —, und die Spalte ist die einzige, die der
  importierenden Person sagt, WO sie korrigieren muss. Die bestehenden Tests
  pruefen durchgehend nur ``code``.
* **Feste Meldungstexte** (``_decode``, ``_parse_csv``, ``_error_entry``,
  ``_classify_import_error``): Zweige, deren Wortlaut nirgends exakt verglichen
  wird.
* **Reihenfolge der dynamischen Zuordnungsziele** (``_dynamic_templates``):
  ``ordered=True`` ist die Block-Reihenfolge (#1713); alle Bestandstests bauen
  aus dem Ergebnis nur Mengen.
* **Ablagepfad** (``_import_dir``) und die **Lock-Synchronisierung**
  (``_lock_job``: ``result_json`` aus der gesperrten Zeile).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` /
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``), nie
per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext wird
bei Texten per ``translation.override("de")`` festgenagelt; ``django_db`` nur wo
noetig; KEINE Imports aus ``test_rls_functional``; Fixtures kommen aus
``src/tests/conftest.py`` bzw. sind echte Model-Objekte (keine
``SimpleNamespace``-Fakes wie in ``test_mutation_followup_import_engine.py``).
Jeder Test nennt seine Mutanten in der Docstring samt dem Wert, der beim
Mutanten abweicht.
"""

import csv
import logging
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import translation

from core.models import (
    Client,
    DocumentType,
    DocumentTypeField,
    FieldGroup,
    FieldTemplate,
    ImportJob,
)
from core.services import import_engine

DECODE_FAILURE_MESSAGE = "Die Datei konnte weder als UTF-8 noch als Windows-1252 gelesen werden."
CSV_ERROR_MESSAGE = (
    "Die Datei konnte nicht als CSV gelesen werden (Fehler in Zeile 3). "
    "Bitte Anführungszeichen und Zeilenumbrüche innerhalb von Feldern prüfen."
)
GENERIC_ROW_MESSAGE = "Die Zeile konnte nicht übernommen werden."


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def client_job(facility, admin_user):
    """Personen-Lauf ohne Datei — die Zeilen-Helfer brauchen nur Facility+Typ."""
    return ImportJob.objects.create(
        facility=facility,
        created_by=admin_user,
        target_type=ImportJob.TargetType.CLIENT,
    )


@pytest.fixture
def event_job(facility, admin_user, doc_type_contact):
    """Ereignis-Lauf ohne Datei — Gegenstueck zu ``client_job``."""
    return ImportJob.objects.create(
        facility=facility,
        created_by=admin_user,
        target_type=ImportJob.TargetType.EVENT,
        document_type=doc_type_contact,
    )


# ============================================================================
# _import_dir — der Ablage-Unterordner (2 Mutanten)
# ============================================================================


class TestImportDir:
    """Der Unterordner heisst ``imports`` — buchstabengetreu.

    Der Pfad ist ein Datenvertrag mit der Disk: ``import_file_path`` schreibt
    beim Upload dorthin und ``delete_import_file`` loescht dort. Ein anderer
    Ordnername liesse Bestands-Uploads unauffindbar zurueck (PII, die nie
    geloescht wird). Kein Bestandstest vergleicht den Ordnernamen.
    """

    @pytest.mark.django_db
    def test_import_dir_is_the_imports_subfolder_of_the_facility(self, facility, media_root):
        """Pin: ``x__import_dir__mutmut_3``/``_4``.

        * ``_3``: ``"imports"`` -> ``"XXimportsXX"``;
        * ``_4``: ``"imports"`` -> ``"IMPORTS"``.

        Original: ``<MEDIA_ROOT>/<facility-pk>/imports``. Mutanten: derselbe
        Pfad mit ``XXimportsXX`` bzw. ``IMPORTS`` als letztem Segment — der
        exakte Vergleich (und ``.name``) scheitert bei beiden.
        """
        path = import_engine._import_dir(facility)

        assert path.name == "imports"
        assert path == Path(media_root) / str(facility.pk) / "imports"


# ============================================================================
# _decode — Meldung, wenn kein Encoding traegt (5 Mutanten)
# ============================================================================


class TestDecodeAllCandidatesFail:
    """Undekodierbare Bytes ergeben genau EINE, feste Nutzermeldung.

    ``0x81`` ist weder gueltiges UTF-8 noch in Windows-1252 definiert — beide
    Kandidaten aus ``SUPPORTED_ENCODINGS`` scheitern nachweislich, der Zweig
    ist also erreichbar. Die Meldung landet im Upload-Formular; ihr Wortlaut
    ist zugleich die ``msgid`` der Uebersetzung.
    """

    def test_undecodable_bytes_raise_the_exact_catalog_message(self):
        """Pin: ``x__decode__mutmut_4``/``_5``/``_6``/``_7``/``_8``.

        * ``_4``: ``ValidationError(_( … ))`` -> ``ValidationError(None)``
          -> ``.messages == ['None']``;
        * ``_5``: ``_( … )`` -> ``_(None)`` -> beim Erzwingen des Lazy-Proxys
          ``AttributeError: 'NoneType' object has no attribute 'replace'``
          statt einer ``ValidationError``;
        * ``_6``: ``"XXDie Datei konnte weder … werden.XX"``;
        * ``_7``: durchgehend klein geschrieben;
        * ``_8``: durchgehend gross geschrieben.

        Original: ``.messages == [DECODE_FAILURE_MESSAGE]``. Jeder Mutant
        liefert einen anderen Text (bzw. ueberhaupt keine ``ValidationError``).
        """
        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine._decode(bytes([0x81, 0xFF]), "")

        assert excinfo.value.messages == [DECODE_FAILURE_MESSAGE]


# ============================================================================
# _parse_csv — Meldung bei csv.Error (3 Mutanten)
# ============================================================================


class TestParseCsvErrorMessage:
    """Ein ``csv.Error`` wird zur festen Meldung MIT Zeilennummer.

    Erreichbar ueber ein Feld jenseits von ``csv.field_size_limit()`` — der
    Reader bricht dann in Zeile 3 ab (Kopfzeile + eine gute Datenzeile davor),
    und ``reader.line_num`` fliesst in die Meldung.
    """

    def test_csv_error_reports_the_line_and_the_exact_message(self):
        """Pin: ``x__parse_csv__mutmut_12``/``_15``/``_16``.

        * ``_12``: erster Satz -> ``"XXDie Datei konnte nicht als CSV gelesen
          werden (Fehler in Zeile %(line)s). XX"``;
        * ``_15``: zweiter Satz -> ``"XXBitte Anführungszeichen … prüfen.XX"``;
        * ``_16``: zweiter Satz komplett klein geschrieben.

        Original: ``.messages == [CSV_ERROR_MESSAGE]`` (mit ``Zeile 3``). Jeder
        Mutant setzt ``XX``-Marker bzw. Kleinschreibung in denselben Text —
        der exakte Listenvergleich scheitert.
        """
        oversized = "y" * (csv.field_size_limit() + 10)
        text = f"Pseudonym;Notizen\nIMP-01;kurz\nIMP-02;{oversized}\n"

        with translation.override("de"), pytest.raises(ValidationError) as excinfo:
            import_engine._parse_csv(text, ";")

        assert excinfo.value.messages == [CSV_ERROR_MESSAGE]


# ============================================================================
# _dynamic_templates — Block-Reihenfolge der Zusatzfelder (3 Mutanten)
# ============================================================================


class TestDynamicTemplatesOrdering:
    """Dynamische Zuordnungsziele folgen der Block-Reihenfolge (#1713).

    ``build_field_template_lookup(..., ordered=True)`` liest ueber
    ``block_ordered_dtf_queryset`` (Blockposition =
    ``COALESCE(field_group.sort_order, sort_order)``); ohne ``ordered`` gilt nur
    die Meta-Sortierung ``sort_order`` des ``DocumentTypeField``. Die beiden
    Reihenfolgen unterscheiden sich, sobald eine Feldgruppe im Spiel ist — genau
    diese Konstellation baut der Test. Alle Bestandstests in
    ``TestAvailableTargets`` bauen aus dem Ergebnis Mengen/Dicts und sehen die
    Reihenfolge nie.
    """

    @pytest.mark.django_db
    def test_targets_follow_the_block_order_not_the_raw_sort_order(self, facility, admin_user):
        """Pin: ``x__dynamic_templates__mutmut_4``/``_6``/``_7``.

        * ``_4``: ``ordered=True`` -> ``ordered=None`` (falsy);
        * ``_6``: ``ordered``-Argument weggelassen (Default ``False``);
        * ``_7``: ``ordered=True`` -> ``ordered=False``.

        Aufbau: „In Gruppe" liegt in einem Block mit ``FieldGroup.sort_order=0``,
        traegt aber selbst ``sort_order=5``; „Frei" ist ungruppiert mit
        ``sort_order=1``.

        Original (Block-Reihenfolge): ``["in-gruppe", "frei"]``.
        Alle drei Mutanten (roher ``sort_order``): ``["frei", "in-gruppe"]``.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Reihenfolgen-Typ")
        template_in_group = FieldTemplate.objects.create(
            facility=facility,
            name="In Gruppe",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        template_free = FieldTemplate.objects.create(
            facility=facility,
            name="Frei",
            field_type=FieldTemplate.FieldType.TEXT,
        )
        group = FieldGroup.objects.create(document_type=doc_type, name="Erster Block", sort_order=0)
        DocumentTypeField.objects.create(
            document_type=doc_type,
            field_template=template_in_group,
            field_group=group,
            sort_order=5,
        )
        DocumentTypeField.objects.create(document_type=doc_type, field_template=template_free, sort_order=1)
        job = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.EVENT,
            document_type=doc_type,
        )

        assert list(import_engine._dynamic_templates(job)) == [template_in_group.slug, template_free.slug]


# ============================================================================
# _coerce_option — Label-Default und Spaltenbezug (5 Mutanten)
# ============================================================================


class TestCoerceOptionLabelDefault:
    """Eine fehlende Beschriftung darf keine leere Zelle akzeptieren.

    Ein Options-Dict OHNE ``"label"``-Schluessel ist ueber das Formular nicht
    erzeugbar, ``options_json`` ist aber ein rohes ``JSONField`` ohne
    DB-Constraint (Seed, Paket-Import, Bestandsdaten). Refs #2156: Eine leere
    Zelle ist kein Optionswert und muss auch bei solchen Altdaten fail-closed
    bleiben.
    """

    @pytest.mark.django_db
    def test_option_without_label_key_never_matches_the_empty_cell(self, facility):
        """Pin des #2156-Leerwert-Guards mit einem mutantenwirksamen Altdatensatz.

        Die Option ohne Label traegt absichtlich auch einen leeren Slug, was
        regulaere Formulare nicht erzeugen, das rohe JSONField aber enthalten
        kann. Entfernt ein Mutant ``if raw == ""``, matcht die leere Zelle den
        leeren Slug und der Test wird rot; mit Guard bleiben Error-Code und
        Spaltenbezug exakt gepinnt.
        """
        template = FieldTemplate.objects.create(
            facility=facility,
            name="Zugang",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "", "is_active": True}],
        )

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._coerce_option(template, "", "Zugang")

        assert excinfo.value.code == "invalid_option"
        assert excinfo.value.column == "Zugang"

    @pytest.mark.django_db
    def test_invalid_option_keeps_its_column(self, facility):
        """Pin: ``x__coerce_option__mutmut_29``/``_31``.

        * ``_29``: ``_CellError("invalid_option", column)`` -> ``… , None)``;
        * ``_31``: zweites Argument weggelassen (Default ``None``).

        Original: ``exc.column == "Zugang"``. Beide Mutanten: ``None`` — im
        Fehlerbericht stuende ``"column": ""`` statt des Spaltennamens. Der
        Bestandstest ``test_inactive_option_is_rejected_even_by_its_exact_slug``
        prueft nur ``.code``.
        """
        template = FieldTemplate.objects.create(
            facility=facility,
            name="Zugang",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "d", "label": "D-Label", "is_active": True}],
        )

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._coerce_option(template, "gibt-es-nicht", "Zugang")

        assert excinfo.value.code == "invalid_option"
        assert excinfo.value.column == "Zugang"


# ============================================================================
# _coerce_choice — Spaltenbezug (2 Mutanten)
# ============================================================================


class TestCoerceChoiceColumn:
    """Auch der Choice-Zweig meldet seine Spalte."""

    def test_unknown_choice_keeps_its_column(self):
        """Pin: ``x__coerce_choice__mutmut_10``/``_12``.

        * ``_10``: ``_CellError("invalid_option", column)`` -> ``… , None)``;
        * ``_12``: zweites Argument weggelassen (Default ``None``).

        Original: ``exc.column == "Kontaktstufe"``. Beide Mutanten: ``None``.
        Der Bestandstest ``test_unknown_contact_stage_is_a_row_error`` prueft
        nur ``result["errors"][0]["code"]``.
        """
        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._coerce_choice("voellig-neu", Client.ContactStage, "Kontaktstufe")

        assert excinfo.value.code == "invalid_option"
        assert excinfo.value.column == "Kontaktstufe"


# ============================================================================
# _error_entry — Rueckfallmeldung fuer unbekannte Codes (2 Mutanten)
# ============================================================================


class TestErrorEntryFallbackMessage:
    """Ein Code ausserhalb von ``ERROR_MESSAGES`` bekommt die generische Meldung.

    Der Rueckfall ist kein toter Code: ``_classify_*`` darf einen Code liefern,
    zu dem (nach einer Katalog-Aenderung) kein Text existiert — dann muss der
    Bericht lesbar bleiben statt ``"None"`` anzuzeigen.
    """

    def test_unknown_code_falls_back_to_the_generic_message(self):
        """Pin: ``x__error_entry__mutmut_5``/``_7``.

        * ``_5``: ``ERROR_MESSAGES.get(code, _GENERIC_MESSAGE)`` -> ``…, None)``;
        * ``_7``: Default weggelassen (implizit ``None``).

        Original: ``"message" == "Die Zeile konnte nicht übernommen werden."``.
        Beide Mutanten: ``str(None)`` -> ``"None"`` im Fehlerbericht.
        """
        with translation.override("de"):
            entry = import_engine._error_entry(7, "Notizen", "ein_unbekannter_code", [])

        assert entry == {
            "row": 7,
            "column": "Notizen",
            "code": "ein_unbekannter_code",
            "message": GENERIC_ROW_MESSAGE,
        }


# ============================================================================
# _classify_import_error — das Sicherheitsnetz (4 Mutanten)
# ============================================================================


class TestClassifyImportErrorFallback:
    """Ein nicht klassifizierbarer Fehler wird PII-frei protokolliert.

    ``_IMPORT_ERROR_CLASSIFIERS`` endet bei ``ValueError`` — ein ``RuntimeError``
    faellt also wirklich in den defensiven #2156-Fallback. Code, feste Meldung
    und Exception-Log sind gemeinsam der Vertrag dieses Sicherheitsnetzes.
    """

    def test_unclassifiable_exception_returns_exact_fallback_and_logs(self, caplog):
        """Pin des neuen #2156-Fallbacks statt des alten ``TypeError``.

        Ein Mutant an Code, Meldung, Spaltenwert oder Log-Aufruf veraendert die
        exakte Ergebnismenge. Die RuntimeError-Interna bleiben ausschliesslich
        im Server-Traceback und gelangen nicht in die Nutzermeldung.
        """
        logger_name = "core.services.import_engine"
        with caplog.at_level(logging.ERROR, logger=logger_name):
            try:
                raise RuntimeError("beliebig")
            except RuntimeError as exc:
                classified = import_engine._classify_import_error(exc)

        assert (classified.column, classified.code, classified.message) == (
            None,
            "unexpected_error",
            "Beim Import ist ein unerwarteter Fehler aufgetreten.",
        )
        records = [record for record in caplog.records if record.name == logger_name]
        assert [(record.getMessage(), record.levelno, record.exc_info[0]) for record in records] == [
            ("unexpected_import_error", logging.ERROR, RuntimeError)
        ]

    def test_a_listed_type_is_still_classified(self):
        """Gegenprobe (kein eigener Mutant): ``ValueError`` erreicht das Netz nie."""
        classified = import_engine._classify_import_error(ValueError("x"))

        assert classified.code == "invalid_value"


# ============================================================================
# _import_client_row — Pflichtfeld und Spaltenbezug (9 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestImportClientRowErrorReporting:
    """Der Personen-Zeilenimport meldet Code UND Spalte korrekt."""

    def test_missing_pseudonym_target_is_a_required_missing_error(self, client_job, admin_user):
        """Pin: ``x__import_client_row__mutmut_8``.

        ``values.get("pseudonym", "")`` -> ``values.get("pseudonym", "XXXX")``.

        Original: ohne zugeordnetes Pseudonym-Ziel ist der Wert ``""`` ->
        ``_CellError("required_missing")``. Mutant: der Default ``"XXXX"`` ist
        truthy — es entstuende eine Person mit dem erfundenen Pseudonym
        ``"XXXX"`` statt eines Zeilenfehlers (kein ``_CellError``, der Test
        scheitert an ``pytest.raises``).
        """
        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_client_row(client_job, admin_user, {}, {})

        assert excinfo.value.code == "required_missing"
        assert Client.objects.for_facility(client_job.facility).count() == 0

    def test_unknown_contact_stage_reports_its_column(self, client_job, admin_user):
        """Pin: ``x__import_client_row__mutmut_30``/``_38``/``_39``/``_40``.

        * ``_30``: ``columns.get("contact_stage")`` -> ``None``;
        * ``_38``: ``columns.get(None)``;
        * ``_39``: ``columns.get("XXcontact_stageXX")``;
        * ``_40``: ``columns.get("CONTACT_STAGE")``.

        Original: ``exc.column == "Kontaktstufe"``. Alle vier Mutanten liefern
        ``None`` (die falschen Schluessel existieren im ``columns``-Dict nicht),
        der Fehlerbericht verloere den Spaltenbezug.
        """
        values = {"pseudonym": "IMP-01", "contact_stage": "gibt-es-nicht"}
        columns = {"pseudonym": "Pseudonym", "contact_stage": "Kontaktstufe"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_client_row(client_job, admin_user, values, columns)

        assert excinfo.value.code == "invalid_option"
        assert excinfo.value.column == "Kontaktstufe"

    def test_unknown_age_cluster_reports_its_column(self, client_job, admin_user):
        """Pin: ``x__import_client_row__mutmut_49``/``_55``/``_56``/``_57``.

        * ``_49``: ``columns.get("age_cluster")`` -> ``None``;
        * ``_55``: ``columns.get(None)``;
        * ``_56``: ``columns.get("XXage_clusterXX")``;
        * ``_57``: ``columns.get("AGE_CLUSTER")``.

        Original: ``exc.column == "Altersgruppe"``. Alle vier Mutanten: ``None``.
        """
        values = {"pseudonym": "IMP-02", "age_cluster": "gibt-es-nicht"}
        columns = {"pseudonym": "Pseudonym", "age_cluster": "Altersgruppe"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_client_row(client_job, admin_user, values, columns)

        assert excinfo.value.code == "invalid_option"
        assert excinfo.value.column == "Altersgruppe"


# ============================================================================
# _import_event_row — Pflichtfelder und Spaltenbezug (24 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestImportEventRowErrorReporting:
    """Der Ereignis-Zeilenimport meldet Code UND Spalte korrekt.

    Beide Pflichtfelder (Zeitpunkt, Pseudonym der Person) haben je einen
    ``required_missing``-Zweig und einen Coercion-/Lookup-Zweig; die Survivors
    sitzen praktisch vollstaendig im Argumentpaar ``(code, column)``.
    """

    def test_missing_occurred_at_target_is_a_required_missing_error(self, event_job, admin_user):
        """Pin: ``x__import_event_row__mutmut_8``.

        ``values.get("occurred_at", "")`` -> ``values.get("occurred_at", "XXXX")``.

        Original: ``_CellError("required_missing")``. Mutant: ``"XXXX"`` ist
        truthy und laeuft in ``_coerce_datetime`` — der Code waere
        ``"invalid_datetime"`` statt ``"required_missing"``.
        """
        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_event_row(event_job, admin_user, {}, {})

        assert excinfo.value.code == "required_missing"

    def test_empty_occurred_at_cell_reports_code_and_column(self, event_job, admin_user):
        """Pin: ``x__import_event_row__mutmut_10``–``_18`` (ohne ``_8``).

        Zeile 688, ``raise _CellError("required_missing", columns.get("occurred_at"))``:

        * ``_10``: Code -> ``None``;
        * ``_11``: Spalte -> ``None``;
        * ``_12``: nur ein Positionsargument — der Spaltenname ``"Zeitpunkt"``
          wuerde zum Fehler-CODE, die Spalte zu ``None``;
        * ``_13``: zweites Argument weggelassen -> Spalte ``None``;
        * ``_14``: Code -> ``"XXrequired_missingXX"``;
        * ``_15``: Code -> ``"REQUIRED_MISSING"``;
        * ``_16``: ``columns.get(None)`` -> Spalte ``None``;
        * ``_17``: ``columns.get("XXoccurred_atXX")`` -> Spalte ``None``;
        * ``_18``: ``columns.get("OCCURRED_AT")`` -> Spalte ``None``.

        Original: ``code == "required_missing"`` UND ``column == "Zeitpunkt"``;
        jeder Mutant weicht in genau einem der beiden Werte ab.
        """
        values = {"occurred_at": "", "client_pseudonym": "IMP-01"}
        columns = {"occurred_at": "Zeitpunkt", "client_pseudonym": "Pseudonym der Person"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_event_row(event_job, admin_user, values, columns)

        assert excinfo.value.code == "required_missing"
        assert excinfo.value.column == "Zeitpunkt"

    def test_invalid_occurred_at_reports_its_column(self, event_job, admin_user):
        """Pin: ``x__import_event_row__mutmut_21``/``_24``/``_25``/``_26``.

        Zeile 689, ``_coerce_datetime(raw_occurred, columns.get("occurred_at"))``:

        * ``_21``: Spaltenargument -> ``None``;
        * ``_24``: ``columns.get(None)``;
        * ``_25``: ``columns.get("XXoccurred_atXX")``;
        * ``_26``: ``columns.get("OCCURRED_AT")``.

        Original: ``_CellError("invalid_datetime", "Zeitpunkt")``. Alle vier
        Mutanten: Spalte ``None``.
        """
        values = {"occurred_at": "kein-zeitpunkt", "client_pseudonym": "IMP-01"}
        columns = {"occurred_at": "Zeitpunkt", "client_pseudonym": "Pseudonym der Person"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_event_row(event_job, admin_user, values, columns)

        assert excinfo.value.code == "invalid_datetime"
        assert excinfo.value.column == "Zeitpunkt"

    def test_missing_client_pseudonym_target_is_a_required_missing_error(self, event_job, admin_user):
        """Pin: ``x__import_event_row__mutmut_34``.

        ``values.get("client_pseudonym", "")`` -> ``… , "XXXX")``.

        Original: ``_CellError("required_missing")``. Mutant: ``"XXXX"`` ist
        truthy, es folgt der Klienten-Lookup — der Code waere
        ``"unknown_client"`` statt ``"required_missing"``.
        """
        values = {"occurred_at": "01.02.2026 10:00"}
        columns = {"occurred_at": "Zeitpunkt"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_event_row(event_job, admin_user, values, columns)

        assert excinfo.value.code == "required_missing"

    def test_empty_client_pseudonym_cell_reports_code_and_column(self, event_job, admin_user):
        """Pin: ``x__import_event_row__mutmut_36``–``_44``.

        Zeile 693, ``raise _CellError("required_missing", columns.get("client_pseudonym"))``:

        * ``_36``: Code -> ``None``;
        * ``_37``: Spalte -> ``None``;
        * ``_38``: nur ein Positionsargument — ``"Pseudonym der Person"`` wuerde
          zum Fehler-CODE, die Spalte zu ``None``;
        * ``_39``: zweites Argument weggelassen -> Spalte ``None``;
        * ``_40``: Code -> ``"XXrequired_missingXX"``;
        * ``_41``: Code -> ``"REQUIRED_MISSING"``;
        * ``_42``: ``columns.get(None)`` -> Spalte ``None``;
        * ``_43``: ``columns.get("XXclient_pseudonymXX")`` -> Spalte ``None``;
        * ``_44``: ``columns.get("CLIENT_PSEUDONYM")`` -> Spalte ``None``.

        Original: ``code == "required_missing"`` UND
        ``column == "Pseudonym der Person"``.
        """
        values = {"occurred_at": "01.02.2026 10:00", "client_pseudonym": ""}
        columns = {"occurred_at": "Zeitpunkt", "client_pseudonym": "Pseudonym der Person"}

        with pytest.raises(import_engine._CellError) as excinfo:
            import_engine._import_event_row(event_job, admin_user, values, columns)

        assert excinfo.value.code == "required_missing"
        assert excinfo.value.column == "Pseudonym der Person"


# ============================================================================
# _lock_job — Synchronisierung aus der gesperrten Zeile (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestLockJobSynchronisation:
    """``_lock_job`` uebernimmt den DB-Stand in die In-Memory-Instanz.

    Der Wizard laeuft ueber mehrere Requests; jede View haelt einen eigenen,
    moeglicherweise veralteten Snapshot. Nach dem Lock MUSS ``result_json`` der
    gesperrten Zeile entsprechen — ``commit_import`` entscheidet daran (und die
    View rendert daraus die Fehlerliste des Probelaufs).
    """

    def test_lock_takes_result_json_from_the_locked_row(self, facility, admin_user):
        """Pin: ``x__lock_job__mutmut_7`` — ``job.result_json = locked.result_json``
        -> ``job.result_json = None``.

        Original: der veraltete Snapshot ``{"stale": True}`` wird durch den
        DB-Stand ``{"created": 2, "errors": []}`` ersetzt. Mutant: ``None`` —
        der Probelauf-Bericht waere nach jedem Lock verschwunden (und
        ``commit_import`` entschiede auf einem leeren Ergebnis).
        """
        job = ImportJob.objects.create(
            facility=facility,
            created_by=admin_user,
            target_type=ImportJob.TargetType.CLIENT,
            status=ImportJob.Status.VALIDATED,
            storage_filename="abc.enc",
            result_json={"created": 2, "errors": []},
        )
        stale = ImportJob.objects.get(pk=job.pk)
        stale.result_json = {"stale": True}

        with transaction.atomic():
            import_engine._lock_job(stale)

        assert stale.result_json == {"created": 2, "errors": []}
        assert stale.status == ImportJob.Status.VALIDATED
        assert stale.storage_filename == "abc.enc"
