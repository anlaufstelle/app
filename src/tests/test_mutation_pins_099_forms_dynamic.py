"""Pin-Tests der Welle L (0.99) — Vollmutation #2174/#2193, Batch ``dynamic-a``.

Refs https://github.com/anlaufstelle/app/issues/2174,
Refs https://github.com/anlaufstelle/app/issues/2193.

Modul: ``core.forms.dynamic`` (Familie ``forms``). Batch ``dynamic-a`` deckt
67 Survivor-Mutanten aus fünf Funktionen ab: ``DynamicDataForm.grouped_fields``,
``build_dynamic_form_field``, ``DynamicDataForm.clean``,
``MultipleFileField.clean`` und ``_file_upload_errors``. Die vollständige
Triage (Klasse ``test``/``skip``/``equivalent``/``bug`` je Mutante,
Begründung, Prio) steht in
``reports/m-scratch/pins-dynamic-a/triage-099-dynamic-a.json``; hier stehen
nur die als ``test`` klassifizierten Survivor mit Pin.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture wären Pins, deren Formulare
verschlüsselte Felder anfassen, aus dem FALSCHEN Grund rot.
"""

from __future__ import annotations

import uuid

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from core.forms.dynamic import (
    DynamicDataForm,
    MultipleFileField,
    _file_upload_errors,
    build_dynamic_form_field,
)
from core.forms.widgets import INPUT_CSS
from core.models import DocumentType, DocumentTypeField, FieldGroup, FieldTemplate

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]

FieldType = FieldTemplate.FieldType


def _upload(name: str, *, size_bytes: int = 16) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"x" * size_bytes, content_type="application/octet-stream")


def _dtf(document_type, field_template, *, sort_order=0, field_group=None, pk_int=None):
    """``DocumentTypeField`` anlegen; ``pk_int`` erzwingt einen deterministischen
    PK (``str(pk)``-Vergleich in ``grouped_fields`` ist sonst von der
    zufälligen UUID-Erzeugung abhängig und macht Tie-Break-Tests flakig)."""
    kwargs = {}
    if pk_int is not None:
        kwargs["id"] = uuid.UUID(int=pk_int)
    return DocumentTypeField.objects.create(
        document_type=document_type,
        field_template=field_template,
        field_group=field_group,
        sort_order=sort_order,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# DynamicDataForm.grouped_fields — Tie-Break-Ketten der Block-Sequenz (#1713)
# ---------------------------------------------------------------------------


class TestGroupedFieldsTieBreak:
    """Tie-Break bei Positionsgleichheit: Name vor Slug/PK unter freien
    Feldern, Gruppe vor Feld, Positions-Primat vor PK innerhalb einer Gruppe."""

    def test_free_blocks_tie_break_by_field_name_not_slug_or_pk(self, facility):
        """Zwei freie Felder mit GLEICHER Blockposition (0): der Tie-Break
        greift auf den Feld-NAMEN zurück (``ft.name``), nicht auf Slug oder
        PK. Tötet xǁDynamicDataFormǁgrouped_fields__mutmut_13 (``ft`` wird
        über einen genullten Lookup IMMER ``None``), _14 (Lookup-Schlüssel
        auf ``None`` verbogen — gleicher Effekt), _15 (``sort_name`` wird
        hart auf ``None`` gesetzt, Tie-Break fällt auf den PK durch) und _16
        (die Bedingung ``ft is not None`` wird zu ``ft is None`` gedreht — im
        Normalfall greift ebenfalls der Slug-Zweig). Slug UND PK sind
        bewusst GEGENLÄUFIG zur erwarteten Namensreihenfolge gewählt, damit
        jede der vier Mutationen die Reihenfolge sichtbar verändert."""
        dt_obj = DocumentType.objects.create(facility=facility, name="Tie-Break frei")
        ft_first = FieldTemplate.objects.create(
            facility=facility, name="Aaa Erstfeld", slug="zzz-slug-erst", field_type=FieldType.TEXT
        )
        ft_second = FieldTemplate.objects.create(
            facility=facility, name="Bbb Zweitfeld", slug="aaa-slug-zweit", field_type=FieldType.TEXT
        )
        _dtf(dt_obj, ft_first, sort_order=0, pk_int=9)
        _dtf(dt_obj, ft_second, sort_order=0, pk_int=1)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        blocks = form.grouped_fields()

        assert len(blocks) == 1
        group, bounds = blocks[0]
        assert group is None
        assert [bf.name for bf in bounds] == [ft_first.slug, ft_second.slug]

    def test_group_wins_tie_against_free_field_at_equal_position(self, facility):
        """Eine Gruppe und ein freies Feld mit GLEICHER Blockposition (0): die
        Gruppe kommt laut Docstring ("Tie-Break: Gruppe vor Feld, dann Name")
        IMMER zuerst — unabhängig vom Namensvergleich. Der Gruppenname liegt
        alphabetisch bewusst HINTER dem freien Feldnamen, damit ein
        Namens-Fallback die Reihenfolge sichtbar kippen würde. Tötet
        xǁDynamicDataFormǁgrouped_fields__mutmut_45 (Diskriminator der
        Gruppe wird von 0 auf 1 geändert — gleich dem der freien Felder —,
        der Tie fällt dann auf den Namensvergleich durch)."""
        dt_obj = DocumentType.objects.create(facility=facility, name="Tie-Break Gruppe-vs-Feld")
        group_obj = FieldGroup.objects.create(document_type=dt_obj, name="Zzz Gruppe", sort_order=0)
        ft_grouped = FieldTemplate.objects.create(facility=facility, name="In Gruppe", field_type=FieldType.TEXT)
        ft_free = FieldTemplate.objects.create(facility=facility, name="Aaa Freifeld", field_type=FieldType.TEXT)
        _dtf(dt_obj, ft_grouped, sort_order=0, field_group=group_obj)
        _dtf(dt_obj, ft_free, sort_order=0)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        blocks = form.grouped_fields()

        assert blocks[0][0] == group_obj

    def test_group_members_sort_by_position_then_pk_not_pure_pk_or_crash(self, facility):
        """Drei Felder EINER Gruppe: zwei teilen sich die Innen-Position 0
        (Tie-Break über den DocumentTypeField-PK), das dritte steht auf
        Position 1 und trägt bewusst den NIEDRIGSTEN PK der drei. Tötet
        xǁDynamicDataFormǁgrouped_fields__mutmut_42 (Sortierschlüssel wird zu
        ``(member[1], member[1])`` — die Position zählt gar nicht mehr, das
        dritte Feld rutscht trotz Position 1 an die erste Stelle) und _43
        (Sortierschlüssel wird zu ``(member[0], member[2])`` — bei
        Positionsgleichheit versucht ``sort()`` zwei ``BoundField``-Objekte
        zu vergleichen und wirft ``TypeError``, weil ``BoundField`` keine
        Ordnung definiert; unter dem Original crasht das nicht)."""
        dt_obj = DocumentType.objects.create(facility=facility, name="Gruppen-PK-Tie-Break")
        group_obj = FieldGroup.objects.create(document_type=dt_obj, name="Gruppe", sort_order=0)
        ft_a = FieldTemplate.objects.create(facility=facility, name="Feld A", field_type=FieldType.TEXT)
        ft_b = FieldTemplate.objects.create(facility=facility, name="Feld B", field_type=FieldType.TEXT)
        ft_c = FieldTemplate.objects.create(facility=facility, name="Feld C", field_type=FieldType.TEXT)
        _dtf(dt_obj, ft_a, sort_order=0, field_group=group_obj, pk_int=5)
        _dtf(dt_obj, ft_b, sort_order=0, field_group=group_obj, pk_int=1)
        _dtf(dt_obj, ft_c, sort_order=1, field_group=group_obj, pk_int=3)

        form = DynamicDataForm(document_type=dt_obj, facility=facility)
        blocks = form.grouped_fields()

        assert len(blocks) == 1
        group, bounds = blocks[0]
        assert group == group_obj
        assert [bf.name for bf in bounds] == [ft_b.slug, ft_a.slug, ft_c.slug]


# ---------------------------------------------------------------------------
# build_dynamic_form_field — CSS-Klassen-Zuweisung je Widget-Typ
# ---------------------------------------------------------------------------


class TestBuildDynamicFormFieldCssClasses:
    """Nicht-Checkbox-Widgets bekommen ``INPUT_CSS``, Checkbox-Widgets die
    Akzent-Klasse — beide Zweige exakt geprüft (Wortlaut UND welcher Zweig
    für welchen Widget-Typ greift)."""

    def test_text_widget_gets_input_css_exactly(self, facility):
        """Tötet x_build_dynamic_form_field__mutmut_7 (``css=None``), _8 (die
        ``isinstance``-Bedingung gedreht — ein Text-Widget liefe dann in den
        Checkbox-Zweig), _9/_10/_11/_12 (Schlüssel/Wert/Aufrufform von
        ``widget.attrs.setdefault`` verstümmelt) und _13/_14 (der
        Schlüssel-String ``"class"`` verunstaltet)."""
        ft = FieldTemplate.objects.create(facility=facility, name="Text", field_type=FieldType.TEXT)
        field = build_dynamic_form_field(ft, None)
        assert field.widget.attrs["class"] == INPUT_CSS

    def test_checkbox_widget_gets_accent_class_exactly(self, facility):
        """Tötet x_build_dynamic_form_field__mutmut_15/_16/_17/_18
        (Schlüssel/Wert/Aufrufform von ``widget.attrs.setdefault`` im
        Checkbox-Zweig verstümmelt), _19/_20 (der Schlüssel-String
        verunstaltet) und _21/_22 (der Wert-String
        ``"rounded border-subtle text-accent"`` verunstaltet)."""
        ft = FieldTemplate.objects.create(facility=facility, name="Ja Nein", field_type=FieldType.BOOLEAN)
        field = build_dynamic_form_field(ft, None)
        assert field.widget.attrs["class"] == "rounded border-subtle text-accent"
        assert field.widget.attrs["class"] != INPUT_CSS


# ---------------------------------------------------------------------------
# DynamicDataForm.clean — Facility-Guard-Wortlaut + Validierungs-Schleife
# ---------------------------------------------------------------------------


class TestCleanFacilityGuardMessage:
    UPLOAD_WITHOUT_FACILITY_MESSAGE = (
        "DynamicDataForm.clean(): Datei-Upload gebunden, aber kein facility uebergeben — "
        "Upload-Validierung (_resolve_upload_constraints) kann ohne facility nicht laufen. "
        "Programmierfehler des Aufrufers: facility muss beim Konstruieren der Form gesetzt sein."
    )

    def test_value_error_message_exact_wording(self, facility):
        """Tötet xǁDynamicDataFormǁclean__mutmut_8 (Botschaft auf ``None``
        gesetzt) sowie _9/_10/_11/_12/_13/_14/_15/_16/_17 (Wortteile per
        mutmut-Marker/Klein-/Großschreibung verunstaltet). Der Bestandstest
        ``TestCleanWithoutFacilityFailsLoud.test_bound_file_upload_without_facility_raises``
        (test_forms_dynamic.py) prüft nur den Exception-TYP, nicht den
        Wortlaut. Exakter String-Vergleich statt ``pytest.raises(match=...)``,
        weil ``match`` per ``re.search`` nur eine TEILSTRING-Suche ist und
        einen mutmut-Marker-Rahmen (``"XX…XX"``) um EINEN der drei
        aneinandergehängten Textbausteine nicht zuverlässig erkennen würde."""
        dt_obj = DocumentType.objects.create(facility=facility, name="Nachrichtentext")
        ft = FieldTemplate.objects.create(facility=facility, name="Anhang", field_type=FieldType.FILE)
        _dtf(dt_obj, ft, sort_order=0)
        upload = _upload("scan.pdf")
        form = DynamicDataForm({}, {ft.slug: upload}, document_type=dt_obj, facility=None)

        with pytest.raises(ValueError) as exc_info:
            form.is_valid()
        assert str(exc_info.value) == self.UPLOAD_WITHOUT_FACILITY_MESSAGE


class TestCleanFileFieldLoopBypass:
    def test_second_file_field_still_validated_when_first_is_empty(self, facility):
        """Tötet xǁDynamicDataFormǁclean__mutmut_23 (die äußere ``continue``
        wird zu ``break``): ist ein FRÜHERES FILE-Feld leer, darf die
        Validierungsschleife NICHT für alle nachfolgenden Felder abbrechen —
        sonst rutscht eine verbotene Dateiendung am zweiten Feld unbemerkt
        durch (Refs #1268/T3, Datei-DoS-Schutz)."""
        dt_obj = DocumentType.objects.create(facility=facility, name="Zwei Anhaenge")
        ft_a = FieldTemplate.objects.create(facility=facility, name="Anhang A", field_type=FieldType.FILE)
        ft_b = FieldTemplate.objects.create(facility=facility, name="Anhang B", field_type=FieldType.FILE)
        _dtf(dt_obj, ft_a, sort_order=0)
        _dtf(dt_obj, ft_b, sort_order=1)
        bad_upload = _upload("script.exe")

        form = DynamicDataForm({}, {ft_b.slug: bad_upload}, document_type=dt_obj, facility=facility)

        assert not form.is_valid()
        assert ft_b.slug in form.errors


# ---------------------------------------------------------------------------
# MultipleFileField.clean — Zu-viele-Dateien-Wortlaut
# ---------------------------------------------------------------------------


class TestMultipleFileFieldTooManyMessage:
    def test_too_many_files_message_exact_wording(self, settings):
        """Tötet xǁMultipleFileFieldǁclean__mutmut_24 (Botschaft auf ``None``
        gesetzt) sowie _27/_28 (Wortteile per mutmut-Marker/Kleinschreibung
        verunstaltet). Der Bestandstest
        ``TestMultipleFileCountCap.test_too_many_files_rejected``
        (test_file_upload_errors.py) prüft nur den Exception-TYP. Exakter
        Vergleich der ``messages``-Liste statt ``pytest.raises(match=...)``,
        weil ``match`` per ``re.search`` nur eine TEILSTRING-Suche ist und den
        mutmut-Marker-Rahmen (``"XX…XX"`` aus _27) nicht erkennen würde — der
        Original-Wortlaut bliebe darin als Teilstring enthalten."""
        settings.FILE_VAULT_MAX_UPLOAD_FILES = 2
        field = MultipleFileField(required=False)
        files = [_upload(f"f{i}.pdf") for i in range(3)]

        with pytest.raises(forms.ValidationError) as exc_info:
            field.clean(files)
        assert exc_info.value.messages == ["Zu viele Dateien (3). Maximal 2 pro Feld."]


# ---------------------------------------------------------------------------
# _file_upload_errors — Endungs-Ermittlung + Wortlaut bei fehlender Endung
# ---------------------------------------------------------------------------


class TestFileUploadErrorsExtension:
    def test_extension_taken_from_last_dot_not_first(self):
        """Tötet x__file_upload_errors__mutmut_8 (``rsplit`` wird zu
        ``split``): bei einem Dateinamen mit MEHREREN Punkten muss die
        Endung nach dem LETZTEN Punkt entnommen werden. Mit ``split`` wäre
        die vermeintliche 'Endung' stattdessen ``"v2.pdf"`` (nicht in der
        Whitelist) und die legitime PDF-Datei würde fälschlich abgelehnt."""
        up = _upload("bericht.v2.pdf")
        assert _file_upload_errors(up, {"pdf"}, 1, 1024 * 1024) == []

    def test_no_extension_error_message_exact_wording(self):
        """Tötet x__file_upload_errors__mutmut_15 (Fallback ``""`` wird zu
        ``"XXXX"`` für Dateien OHNE Punkt): die Fehlermeldung muss die LEERE
        Endung (``"."``) nennen, nicht den mutmut-Marker. Der Bestandstest
        ``TestFileUploadErrorsHelper.test_no_dot_yields_empty_extension_error``
        (test_mutation_followup_event_form.py) prüft nur die Anzahl."""
        up = _upload("readme")
        errors = _file_upload_errors(up, {"pdf"}, 1, 1024 * 1024)
        assert errors == ["Dateityp . nicht erlaubt. Erlaubt: pdf"]
