"""Follow-Up-Tests fuer Mutation-Survivors in ``core.forms.events.DynamicEventDataForm``.

Refs #930. Ziel: die 32 Survivors an ``DynamicEventDataForm.clean``
(src/core/forms/events.py:197) sowie an der Field-Generierung in
``__init__`` toeten.

Boundary-Schwerpunkte (Mutmut mutiert typischerweise ``<=``/``<``/``>``/``>=``,
``True``/``False``, Konstanten und Operatoren):

- Settings-Fallback: keine ``Settings``-Row → DEFAULT_ALLOWED_FILE_TYPES /
  DEFAULT_MAX_FILE_SIZE_MB greifen (fail-closed, Refs #771).
- Whitespace-/Leere-Whitelist: leerer ``allowed_file_types`` faellt auf
  Defaults zurueck, eine echte Whitelist toetet die Mutation ``not allowed``
  → ``allowed``.
- Extension-Boundary: erlaubte Endung valid, fremde Endung invalid; Datei
  ohne ``.`` im Namen → leere Extension.
- Groessen-Boundary: ``uploaded.size == max_bytes`` ist OK,
  ``> max_bytes`` triggert Fehler.
- Cleaned-Data-Early-Exit: ``self.facility is None`` → keine Validierung,
  auch wenn FileField vorhanden ist.
- Sensitivity/Initial-Data: deaktivierte SELECT-Option, die im
  ``initial_data`` vorkommt, wird als ``(... (deaktiviert))`` an die
  Choices angehaengt; ohne ``initial_data`` ist sie weg.
- Required-Branch: ``ft.is_required=True`` produziert Fehler bei leerem
  Wert; ``False`` erlaubt leere Submissionen.

Die Tests verzichten auf libmagic — wir testen ausschliesslich die
Form-Layer-Validierung, nicht den Storage-Layer.
"""

from __future__ import annotations

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from core.constants import DEFAULT_ALLOWED_FILE_TYPES, DEFAULT_MAX_FILE_SIZE_MB
from core.forms.events import DynamicEventDataForm, MultipleFileField
from core.models import (
    DocumentType,
    DocumentTypeField,
    FieldTemplate,
    Settings,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_doc_type_with_fields(facility, fields):
    """Erzeuge ein ``DocumentType`` mit ``FieldTemplate``-/``DocumentTypeField``-
    Verkettung.

    ``fields`` ist eine Liste von ``dict``s mit Keys, die direkt an
    ``FieldTemplate.objects.create`` durchgereicht werden (mind. ``name`` und
    ``field_type``). ``sort_order`` ergibt sich aus der Reihenfolge.
    """
    dt = DocumentType.objects.create(
        facility=facility,
        name=f"DT-{id(fields)}",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    created = []
    for i, spec in enumerate(fields):
        ft = FieldTemplate.objects.create(facility=facility, **spec)
        DocumentTypeField.objects.create(document_type=dt, field_template=ft, sort_order=i)
        created.append(ft)
    return dt, created


def _set_file_settings(facility, *, allowed: str, max_mb: int) -> None:
    Settings.objects.update_or_create(
        facility=facility,
        defaults={"allowed_file_types": allowed, "max_file_size_mb": max_mb},
    )


def _upload(name: str, *, size_bytes: int = 16, content: bytes | None = None) -> SimpleUploadedFile:
    payload = content if content is not None else (b"x" * size_bytes)
    return SimpleUploadedFile(name, payload, content_type="application/octet-stream")


# ===========================================================================
# DynamicEventDataForm.__init__ — Field-Generierung
# ===========================================================================


@pytest.mark.django_db
class TestDynamicEventDataFormInit:
    """Field-Generation-Branches in ``__init__``.

    Boundary-Mutationen: ``ft.is_required True ↔ False``, FILE-Sonderpfad
    (``MultipleFileField``), SELECT/MULTI_SELECT-Choices,
    ``initial_data``-Branch fuer deaktivierte Options.
    """

    def test_no_document_type_yields_empty_fields(self, facility):
        """Ohne ``document_type`` baut ``__init__`` keine Felder."""
        form = DynamicEventDataForm(document_type=None, facility=facility)
        assert form.fields == {}

    def test_required_field_flagged_required(self, facility):
        """``is_required=True`` → Django-Field ist ``required=True``."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Pflicht", "field_type": FieldTemplate.FieldType.TEXT, "is_required": True}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields[ft.slug].required is True

    def test_optional_field_flagged_optional(self, facility):
        """``is_required=False`` → Django-Field ist ``required=False``.

        Toetet Mutation ``required=True`` ↔ ``required=False``.
        """
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Optional", "field_type": FieldTemplate.FieldType.TEXT, "is_required": False}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields[ft.slug].required is False

    def test_required_text_field_blank_submit_invalid(self, facility):
        """Pflicht-TEXT-Feld + leerer Submit → Form invalid mit Field-Error."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Pflicht", "field_type": FieldTemplate.FieldType.TEXT, "is_required": True}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: ""})
        assert not form.is_valid()
        assert ft.slug in form.errors

    def test_optional_text_field_blank_submit_valid(self, facility):
        """Optional-TEXT-Feld + leerer Submit → Form valid."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Frei", "field_type": FieldTemplate.FieldType.TEXT, "is_required": False}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: ""})
        assert form.is_valid(), form.errors

    def test_number_field_uses_integer_field(self, facility):
        """NUMBER → ``IntegerField`` (Spec-Registry-Mapping)."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Anzahl", "field_type": FieldTemplate.FieldType.NUMBER}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert isinstance(form.fields[ft.slug], forms.IntegerField)

    def test_number_field_rejects_non_numeric(self, facility):
        """NUMBER + ``"abc"`` → Field-Error (IntegerField-Validator)."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Anzahl", "field_type": FieldTemplate.FieldType.NUMBER, "is_required": True}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: "abc"})
        assert not form.is_valid()
        assert ft.slug in form.errors

    def test_file_field_uses_multiple_file_field(self, facility):
        """FILE → ``MultipleFileField`` (Refs #622)."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Anhang", "field_type": FieldTemplate.FieldType.FILE}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert isinstance(form.fields[ft.slug], MultipleFileField)

    def test_select_field_prepends_empty_choice(self, facility):
        """SELECT bekommt ``("", "---------")`` als erste Choice."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Kategorie",
                    "field_type": FieldTemplate.FieldType.SELECT,
                    "options_json": [
                        {"slug": "a", "label": "Alpha", "is_active": True},
                        {"slug": "b", "label": "Beta", "is_active": True},
                    ],
                }
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        choices = form.fields[ft.slug].choices
        assert choices[0] == ("", "---------")
        assert ("a", "Alpha") in choices
        assert ("b", "Beta") in choices

    def test_multi_select_field_has_no_empty_choice(self, facility):
        """MULTI_SELECT haengt ``("", ...)`` **nicht** vorne dran."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Tags",
                    "field_type": FieldTemplate.FieldType.MULTI_SELECT,
                    "options_json": [
                        {"slug": "x", "label": "X", "is_active": True},
                    ],
                }
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        choices = list(form.fields[ft.slug].choices)
        assert ("", "---------") not in choices
        assert ("x", "X") in choices

    def test_inactive_option_re_added_when_in_initial(self, facility):
        """Deaktivierte Option, die im ``initial_data`` erscheint, wird mit
        ``(deaktiviert)``-Suffix als waehlbare Choice zurueckgebracht.

        Toetet Mutation ``is_active True ↔ False`` und das ``in current_values``-
        Check.
        """
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Kategorie",
                    "field_type": FieldTemplate.FieldType.SELECT,
                    "options_json": [
                        {"slug": "a", "label": "Alpha", "is_active": True},
                        {"slug": "b", "label": "Beta", "is_active": False},
                    ],
                }
            ],
        )
        form = DynamicEventDataForm(
            document_type=dt,
            facility=facility,
            initial_data={ft.slug: "b"},
        )
        labels = {slug: label for slug, label in form.fields[ft.slug].choices}
        assert "b" in labels
        assert "deaktiviert" in labels["b"]

    def test_inactive_option_omitted_without_initial(self, facility):
        """Deaktivierte Option ohne ``initial_data``-Match bleibt unsichtbar."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Kategorie",
                    "field_type": FieldTemplate.FieldType.SELECT,
                    "options_json": [
                        {"slug": "a", "label": "Alpha", "is_active": True},
                        {"slug": "b", "label": "Beta", "is_active": False},
                    ],
                }
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        slugs = [slug for slug, _ in form.fields[ft.slug].choices]
        assert "b" not in slugs

    def test_initial_data_sets_field_initial(self, facility):
        """``initial_data[slug]`` → ``field.initial``."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Notiz", "field_type": FieldTemplate.FieldType.TEXT}],
        )
        form = DynamicEventDataForm(
            document_type=dt,
            facility=facility,
            initial_data={ft.slug: "vorher"},
        )
        assert form.fields[ft.slug].initial == "vorher"

    def test_default_value_used_when_no_initial(self, facility):
        """``default_value`` greift, wenn ``initial_data`` fehlt und Spec
        ``allows_default`` ist."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Standard",
                    "field_type": FieldTemplate.FieldType.TEXT,
                    "default_value": "voreingestellt",
                }
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields[ft.slug].initial == "voreingestellt"

    def test_field_label_matches_template_name(self, facility):
        """``field.label`` == ``ft.name``."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Hauptanliegen", "field_type": FieldTemplate.FieldType.TEXT}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields[ft.slug].label == "Hauptanliegen"

    def test_help_text_propagated_when_set(self, facility):
        """``ft.help_text`` → ``field.help_text``."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [
                {
                    "name": "Mit Hilfe",
                    "field_type": FieldTemplate.FieldType.TEXT,
                    "help_text": "Bitte praezise.",
                }
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        assert form.fields[ft.slug].help_text == "Bitte praezise."

    def test_field_order_follows_sort_order(self, facility):
        """``DocumentTypeField.sort_order`` bestimmt die Reihenfolge."""
        dt, [ft1, ft2, ft3] = _make_doc_type_with_fields(
            facility,
            [
                {"name": "Erstens", "field_type": FieldTemplate.FieldType.TEXT},
                {"name": "Zweitens", "field_type": FieldTemplate.FieldType.TEXT},
                {"name": "Drittens", "field_type": FieldTemplate.FieldType.TEXT},
            ],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility)
        order = list(form.fields.keys())
        assert order == [ft1.slug, ft2.slug, ft3.slug]


# ===========================================================================
# DynamicEventDataForm.clean — Settings-Fallback + File-Validation
# ===========================================================================


@pytest.mark.django_db
class TestDynamicEventDataFormClean:
    """``DynamicEventDataForm.clean`` (src/core/forms/events.py:197).

    Boundaries:
    - ``self.facility`` None vs. gesetzt
    - ``Settings.DoesNotExist`` → Defaults
    - ``allowed_file_types`` leer vs. gefuellt
    - Extension in/nicht in Whitelist
    - ``uploaded.size`` <=/> max_bytes
    - Non-FileField wird uebersprungen
    - ``MultipleFileField``-Liste vs. Einzeldatei
    """

    # -- Cleaned-Data-Early-Exit ------------------------------------------

    def test_no_facility_skips_validation_entirely(self):
        """``self.facility=None`` → ``clean`` short-circuited, FileField wird
        nicht gegen Whitelist/Max gecheckt."""
        form = DynamicEventDataForm(document_type=None, facility=None, data={})
        assert form.is_valid()
        assert form.cleaned_data == {}

    def test_form_without_file_fields_passes(self, facility):
        """Form ohne FileField → kein Whitelist-/Size-Check noetig."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Notiz", "field_type": FieldTemplate.FieldType.TEXT, "is_required": False}],
        )
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: "egal"})
        assert form.is_valid(), form.errors

    # -- Settings-Fallback ------------------------------------------------

    def test_missing_settings_falls_back_to_defaults(self, facility):
        """Ohne ``Settings``-Row → ``DEFAULT_ALLOWED_FILE_TYPES`` + DEFAULT_MAX.

        Wir wählen eine Extension aus dem Default-Set (z.B. ``pdf``) und
        pruefen, dass der Upload akzeptiert wird. Mutation
        ``DEFAULT_ALLOWED_FILE_TYPES`` → leer würde failen.
        """
        assert not Settings.objects.filter(facility=facility).exists()
        # Sicherstellen, dass der Test-Asset eine Default-Extension hat.
        ext = next(iter(DEFAULT_ALLOWED_FILE_TYPES))
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload(f"anhang.{ext}", size_bytes=128)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_empty_allowed_file_types_falls_back_to_defaults(self, facility):
        """``allowed_file_types=""`` → Default-Set greift (fail-closed).

        Mutmut-Boundary: ``if not allowed`` ↔ ``if allowed`` würde die
        leere Whitelist nutzen und jeden Upload ablehnen.
        """
        _set_file_settings(facility, allowed="", max_mb=DEFAULT_MAX_FILE_SIZE_MB)
        ext = next(iter(DEFAULT_ALLOWED_FILE_TYPES))
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload(f"a.{ext}", size_bytes=32)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_whitespace_only_allowed_file_types_falls_back(self, facility):
        """``allowed_file_types="  ,   ,  "`` → leer nach Split → Defaults.

        Mutmut killt hier den ``ext.strip()``-/``if ext.strip()``-Branch.
        """
        _set_file_settings(facility, allowed="  ,   ,  ", max_mb=DEFAULT_MAX_FILE_SIZE_MB)
        ext = next(iter(DEFAULT_ALLOWED_FILE_TYPES))
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload(f"a.{ext}", size_bytes=32)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_allowed_extension_passes(self, facility):
        """Explizit erlaubte Extension → Form valid."""
        _set_file_settings(facility, allowed="pdf,jpg", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload("scan.pdf", size_bytes=128)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_disallowed_extension_rejected(self, facility):
        """Nicht-erlaubte Extension → Field-Error."""
        _set_file_settings(facility, allowed="pdf", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload("script.exe", size_bytes=64)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert not form.is_valid()
        assert ft.slug in form.errors
        msg = " ".join(str(e) for e in form.errors[ft.slug])
        assert "exe" in msg.lower()

    def test_extension_compared_case_insensitively(self, facility):
        """``PDF`` → ``pdf`` Vergleich. Toetet Mutation ``ext.lower()`` weg."""
        _set_file_settings(facility, allowed="pdf", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload("SCAN.PDF", size_bytes=64)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_whitelist_entry_with_leading_dot_normalized(self, facility):
        """``allowed_file_types=".pdf"`` → ``pdf`` nach ``lstrip('.')``.

        Tötet die Mutation ``.lstrip(".")`` → ``.lstrip("")`` / weg.
        """
        _set_file_settings(facility, allowed=".pdf,.jpg", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload("a.pdf", size_bytes=32)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_file_without_dot_yields_empty_extension(self, facility):
        """Filename ohne ``.`` → ``ext=""`` → niemals in Whitelist → Error.

        Toetet Mutation ``"." in uploaded.name`` ↔ ``"." not in uploaded.name``.
        """
        _set_file_settings(facility, allowed="pdf", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        upload = _upload("ohne_punkt", size_bytes=32)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert not form.is_valid()
        assert ft.slug in form.errors

    # -- Groessen-Boundary -----------------------------------------------

    def test_size_at_limit_is_accepted(self, facility):
        """``uploaded.size == max_bytes`` → kein Fehler (Boundary ``>`` vs. ``>=``).

        Toetet Mutation ``> max_bytes`` → ``>= max_bytes``.
        """
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        exact = 1 * 1024 * 1024
        upload = _upload("a.pdf", size_bytes=exact)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert form.is_valid(), form.errors

    def test_size_one_byte_over_limit_is_rejected(self, facility):
        """``uploaded.size == max_bytes + 1`` → Fehler."""
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        over = 1 * 1024 * 1024 + 1
        upload = _upload("a.pdf", size_bytes=over)
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={ft.slug: upload})
        assert not form.is_valid()
        msg = " ".join(str(e) for e in form.errors[ft.slug])
        assert "max" in msg.lower() or "groß" in msg.lower() or "gross" in msg.lower()

    # -- Multiple-File-Branch --------------------------------------------

    def test_multiple_files_all_validated(self, facility):
        """Bei ``MultipleFileField``: jede Datei in der Liste wird geprueft.

        Erste Datei OK, zweite Datei zu gross → Form invalid.
        """
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        ok = _upload("a.pdf", size_bytes=128)
        too_big = _upload("b.pdf", size_bytes=2 * 1024 * 1024)
        form = DynamicEventDataForm(
            document_type=dt,
            facility=facility,
            data={},
            files={ft.slug: [ok, too_big]},
        )
        assert not form.is_valid()
        assert ft.slug in form.errors

    def test_multiple_files_disallowed_ext_in_second_rejected(self, facility):
        """Erste Datei erlaubt, zweite mit verbotener Extension → invalid."""
        _set_file_settings(facility, allowed="pdf", max_mb=10)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE}],
        )
        ok = _upload("a.pdf", size_bytes=32)
        bad = _upload("b.exe", size_bytes=32)
        form = DynamicEventDataForm(
            document_type=dt,
            facility=facility,
            data={},
            files={ft.slug: [ok, bad]},
        )
        assert not form.is_valid()
        assert ft.slug in form.errors

    def test_empty_file_value_skipped(self, facility):
        """``cleaned[field]`` ist ``None``/leer → kein Crash, kein Fehler.

        Optionales FILE-Feld ohne Upload → Form valid (kein Schleifen-
        Aufenthalt; Mutation ``if not value`` ↔ ``if value`` wuerde crashen).
        """
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Datei", "field_type": FieldTemplate.FieldType.FILE, "is_required": False}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={}, files={})
        assert form.is_valid(), form.errors

    # -- Non-FileField-Skip ----------------------------------------------

    def test_non_file_field_not_checked_against_whitelist(self, facility):
        """TEXT-Felder bleiben vom File-Check unangetastet.

        Ein TEXT-Wert, der wie ein Filename mit verbotener Extension aussieht
        (``"hack.exe"``), darf das Form nicht invalidieren.
        """
        _set_file_settings(facility, allowed="pdf", max_mb=1)
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Notiz", "field_type": FieldTemplate.FieldType.TEXT}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: "hack.exe"})
        assert form.is_valid(), form.errors

    def test_returned_cleaned_data_contains_field_keys(self, facility):
        """``clean()`` gibt das ``cleaned_data``-Dict zurueck (nicht ``None``)."""
        dt, [ft] = _make_doc_type_with_fields(
            facility,
            [{"name": "Notiz", "field_type": FieldTemplate.FieldType.TEXT}],
        )
        form = DynamicEventDataForm(document_type=dt, facility=facility, data={ft.slug: "hi"})
        assert form.is_valid(), form.errors
        assert form.cleaned_data.get(ft.slug) == "hi"


# ===========================================================================
# Refs #1160 R1a: direkte Tests der extrahierten Form-Helfer
# ===========================================================================


@pytest.mark.django_db
class TestSelectChoicesHelper:
    """``_select_choices`` — Leer-Option-Prefix + Reaktivierung inaktiver Werte."""

    def _ft(self, facility, *, field_type, options_json):
        return FieldTemplate.objects.create(
            facility=facility, name="Cat", field_type=field_type, options_json=options_json
        )

    def test_select_prepends_empty_choice(self, facility):
        from core.forms.events import _select_choices
        from core.services.system import SELECT

        ft = self._ft(
            facility,
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "a", "label": "Alpha", "is_active": True}],
        )
        choices = _select_choices(ft, None, SELECT)
        assert choices[0] == ("", "---------")
        assert ("a", "Alpha") in choices

    def test_multi_select_no_empty_choice(self, facility):
        from core.forms.events import _select_choices
        from core.services.system import SELECT

        ft = self._ft(
            facility,
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "x", "label": "X", "is_active": True}],
        )
        choices = _select_choices(ft, None, SELECT)
        assert ("", "---------") not in choices
        assert ("x", "X") in choices

    def test_inactive_option_readded_when_in_initial(self, facility):
        from core.forms.events import _select_choices
        from core.services.system import SELECT

        ft = self._ft(
            facility,
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "a", "label": "Alpha", "is_active": True},
                {"slug": "b", "label": "Beta", "is_active": False},
            ],
        )
        choices = _select_choices(ft, {ft.slug: "b"}, SELECT)
        labels = dict(choices)
        assert "b" in labels
        assert "deaktiviert" in labels["b"]

    def test_inactive_option_omitted_without_initial(self, facility):
        from core.forms.events import _select_choices
        from core.services.system import SELECT

        ft = self._ft(
            facility,
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[
                {"slug": "a", "label": "Alpha", "is_active": True},
                {"slug": "b", "label": "Beta", "is_active": False},
            ],
        )
        slugs = [s for s, _ in _select_choices(ft, None, SELECT)]
        assert "b" not in slugs

    def test_non_list_initial_value_normalized(self, facility):
        """``initial_data[slug]`` als Skalar (nicht-Liste) wird auf eine Liste
        normalisiert, bevor der ``in current_values``-Check laeuft."""
        from core.forms.events import _select_choices
        from core.services.system import SELECT

        ft = self._ft(
            facility,
            field_type=FieldTemplate.FieldType.MULTI_SELECT,
            options_json=[{"slug": "b", "label": "Beta", "is_active": False}],
        )
        choices = dict(_select_choices(ft, {ft.slug: "b"}, SELECT))
        assert "deaktiviert" in choices["b"]


@pytest.mark.django_db
class TestBuildDynamicFieldHelper:
    """``_build_dynamic_field`` — Field-Klasse/Widget/Initial pro Feldtyp."""

    def _ft(self, facility, **kwargs):
        kwargs.setdefault("name", "F")
        return FieldTemplate.objects.create(facility=facility, **kwargs)

    def test_file_field_is_multiple_file_field(self, facility):
        from core.forms.events import _build_dynamic_field

        ft = self._ft(facility, field_type=FieldTemplate.FieldType.FILE)
        field = _build_dynamic_field(ft, None)
        assert isinstance(field, MultipleFileField)

    def test_required_flag_propagated(self, facility):
        from core.forms.events import _build_dynamic_field

        ft = self._ft(facility, field_type=FieldTemplate.FieldType.TEXT, is_required=True)
        assert _build_dynamic_field(ft, None).required is True

    def test_label_and_help_text(self, facility):
        from core.forms.events import _build_dynamic_field

        ft = self._ft(facility, name="Anliegen", field_type=FieldTemplate.FieldType.TEXT, help_text="Hinweis")
        field = _build_dynamic_field(ft, None)
        assert field.label == "Anliegen"
        assert field.help_text == "Hinweis"

    def test_initial_data_wins_over_default(self, facility):
        from core.forms.events import _build_dynamic_field

        ft = self._ft(facility, field_type=FieldTemplate.FieldType.TEXT, default_value="DEFAULT")
        field = _build_dynamic_field(ft, {ft.slug: "INITIAL"})
        assert field.initial == "INITIAL"

    def test_default_used_when_no_initial(self, facility):
        from core.forms.events import _build_dynamic_field

        ft = self._ft(facility, field_type=FieldTemplate.FieldType.TEXT, default_value="DEFAULT")
        field = _build_dynamic_field(ft, None)
        assert field.initial == "DEFAULT"


@pytest.mark.django_db
class TestResolveUploadConstraintsHelper:
    """``_resolve_upload_constraints`` — fail-closed Fallback auf Defaults."""

    def test_missing_settings_uses_defaults(self, facility):
        from core.forms.events import _resolve_upload_constraints

        allowed, max_mb, max_bytes = _resolve_upload_constraints(facility)
        assert allowed == set(DEFAULT_ALLOWED_FILE_TYPES)
        assert max_mb == DEFAULT_MAX_FILE_SIZE_MB
        assert max_bytes == max_mb * 1024 * 1024

    def test_empty_whitelist_falls_back_to_defaults(self, facility):
        from core.forms.events import _resolve_upload_constraints

        _set_file_settings(facility, allowed="", max_mb=7)
        allowed, max_mb, _ = _resolve_upload_constraints(facility)
        assert allowed == set(DEFAULT_ALLOWED_FILE_TYPES)
        assert max_mb == 7

    def test_explicit_whitelist_normalized(self, facility):
        from core.forms.events import _resolve_upload_constraints

        _set_file_settings(facility, allowed=".PDF, .Jpg ", max_mb=3)
        allowed, max_mb, max_bytes = _resolve_upload_constraints(facility)
        assert allowed == {"pdf", "jpg"}
        assert max_mb == 3
        assert max_bytes == 3 * 1024 * 1024


class TestFileUploadErrorsHelper:
    """``_file_upload_errors`` — reine Funktion ohne DB."""

    def test_allowed_within_size_no_errors(self):
        from core.forms.events import _file_upload_errors

        up = _upload("a.pdf", size_bytes=10)
        assert _file_upload_errors(up, {"pdf"}, 1, 1024 * 1024) == []

    def test_disallowed_extension_error(self):
        from core.forms.events import _file_upload_errors

        up = _upload("a.exe", size_bytes=10)
        errors = _file_upload_errors(up, {"pdf"}, 1, 1024 * 1024)
        assert len(errors) == 1
        assert "exe" in " ".join(str(e) for e in errors).lower()

    def test_size_at_limit_ok_one_over_errors(self):
        from core.forms.events import _file_upload_errors

        limit = 1024 * 1024
        assert _file_upload_errors(_upload("a.pdf", size_bytes=limit), {"pdf"}, 1, limit) == []
        over = _file_upload_errors(_upload("a.pdf", size_bytes=limit + 1), {"pdf"}, 1, limit)
        assert len(over) == 1

    def test_no_dot_yields_empty_extension_error(self):
        from core.forms.events import _file_upload_errors

        up = _upload("noext", size_bytes=10)
        errors = _file_upload_errors(up, {"pdf"}, 1, 1024 * 1024)
        assert len(errors) == 1

    def test_both_errors_when_disallowed_and_too_big(self):
        from core.forms.events import _file_upload_errors

        limit = 1024
        up = _upload("a.exe", size_bytes=limit + 1)
        errors = _file_upload_errors(up, {"pdf"}, 1, limit)
        assert len(errors) == 2
