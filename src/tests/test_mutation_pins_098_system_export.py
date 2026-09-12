"""Pin-Tests der Welle K (0.98) — Prio-2-Testluecken in ``core.services.system.export``.

Refs https://github.com/anlaufstelle/app/issues/2193.

Quelle: Batch ``export-settings`` der Survivor-Triage (``triage-096-export-
dashboard.json``). Gepinnt werden fuenf Mutanten-Familien, alle ohne Sicherheits-
/Scoping-Bezug (reines Feldmapping bzw. Datei-/PDF-Inhalt):

* **Statische Spaltenkoepfe** (``_build_header``): 16 Gross-/Kleinschreibungs-
  bzw. mutmut-XX-Marker-Varianten der sechs festen CSV-Ueberschriften. Eine
  Teilstring-Pruefung liesse jede XX-markierte Variante durch — hier steht der
  exakte Spaltensatz.
* **Options-Label im Zeilenaufbau** (``_build_event_row``): wird die
  Feldvorlage beim Zellenaufbau durch ``None`` ersetzt, verliert
  ``_resolve_field_value`` die ``options_json``-Zuordnung und gibt den
  internen Options-Slug statt des lesbaren Labels aus.
* **Metadaten-Zeile** (``export_events_csv``): drei Mutanten degradieren die
  Provenienz-Zeile (Einrichtung, Nutzer, Zeitstempel) — der eigentliche
  Audit-Trail (``AuditLog`` via ``log_export`` in der View) ist davon
  unberuehrt, gepinnt wird der Datei-INHALT.
* **PDF-Template-Kontext** (``generate_report_pdf``): acht Mutanten benennen
  je einen von vier Context-Schluesseln um (``date_from``/``date_to``/
  ``internal_mode``/``generated_at``). Django rendert eine undefinierte
  Template-Variable klaglos leer — abgefangen wird das Kontext-Dict VOR dem
  (hier gestubbten) Rendering.
* **Sachbericht-PDF-Kontext** (``generate_sample_report_1_pdf``): zwoelf
  Mutanten an fuenf Context-Schluesseln, darunter die K3-Populationshinweise
  (``population_notes.by_category``/``by_age_cluster``), ohne die der Bericht
  laut Code-Kommentar "nicht nur undicht, sondern irrefuehrend" ist.

Konventionen wie ``test_mutation_pins_096_export.py``: Zusicherungen
vergleichen EXAKT (``==``/``is``), nie per Teilstring, wo mutmut
``"XX<text>XX"``-Varianten erzeugen kann; Sprachkontext wird per
``translation.override("de")`` festgenagelt; Fixtures kommen aus
``src/tests/conftest.py``. Jeder Test nennt in der Docstring die
Mutante(n) samt der abweichenden Semantik. ``_encryption_key`` liegt
modulweit an (Lehre 112): der Mutantenbaum hat keine ``.env`` und damit
keinen ``ENCRYPTION_KEY``.

**Warum die PDF-Kontext-Tests ``render_to_string`` monkeypatchen statt das
gerenderte HTML zu inspizieren.** Alle 20 Mutanten dieser Datei fuer
``generate_report_pdf``/``generate_sample_report_1_pdf`` benennen einen
Dictionary-Schluessel des Template-Kontexts um. Das abgefangene Dict vor dem
Rendering zu pruefen, trifft die Mutation direkt und bleibt unabhaengig von
Template-Markup-Details; ein vollstaendiger WeasyPrint-Lauf traegt hier keine
zusaetzliche Aussagekraft und wird deshalb (wie in ``test_mutation_pins_096_
export.py``) gestubbt.
"""

from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone, translation

from core.models import DocumentType, Event, FieldTemplate
from core.reporting.data_source import EVENTS_WITH_PERSON, MAPPED_EVENTS, SNAPSHOT_HYBRID, table_note
from core.services.system import export as sys_export

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


#: Erste dynamische Spalte einer CSV-Zeile — davor stehen Datum, Uhrzeit,
#: Dokumentationstyp, Person, Kontaktstufe, Altersgruppe (``_build_header``).
ERSTE_DYNAMISCHE_SPALTE = 6


def _weasyprint_stub():
    """Minimaler ``weasyprint.HTML``-Ersatz — die Kontext-Tests brauchen nur
    Fake-Bytes, keine erfassten kwargs (anders als in ``test_mutation_pins_
    096_export.py``, das den ``string``-kwarg fuer HTML-Assertions braucht)."""

    class _StubHTML:
        def __init__(self, **kwargs):
            pass

        def write_pdf(self, *args, **kwargs):
            return b"%PDF-1.7 fake"

    return _StubHTML


# ---------------------------------------------------------------------------
# _build_header — die sechs statischen Spaltenkoepfe
# ---------------------------------------------------------------------------


class TestBuildHeaderStaticColumns:
    """Alle 16 Survivor-Mutanten dieser Funktion sind Gross-/Kleinschreibungs-
    bzw. XX-Marker-Varianten derselben sechs Literale — ein Test mit exaktem
    Listenvergleich toetet alle."""

    def test_static_columns_are_exact_with_correct_casing(self):
        """Mutanten ``x__build_header__mutmut_3/_4/_5`` (``"Datum"`` ->
        ``"XXDatumXX"``/``"datum"``/``"DATUM"``), ``_7/_8/_9`` (``"Uhrzeit"``),
        ``_11`` (``"Dokumentationstyp"`` -> ``"XXDokumentationstypXX"``),
        ``_15/_16/_17`` (``"Person"``), ``_19/_20/_21`` (``"Kontaktstufe"``),
        ``_23/_24/_25`` (``"Altersgruppe"``).

        Jede Mutation aendert eine exportierte CSV-Spaltenueberschrift — realer
        Dateiinhalt, keine Log-/Fehlermeldung.
        """
        with translation.override("de"):
            header = [str(cell) for cell in sys_export._build_header({}, [])]

        assert header == [
            "Datum",
            "Uhrzeit",
            "Dokumentationstyp",
            "Person",
            "Kontaktstufe",
            "Altersgruppe",
        ]


# ---------------------------------------------------------------------------
# _build_event_row — Options-Label ueber die Feldvorlage
# ---------------------------------------------------------------------------


class TestBuildEventRowOptionLabel:
    def test_select_field_resolves_to_the_option_label_not_the_raw_slug(self, facility, admin_user):
        """Mutante ``x__build_event_row__mutmut_56``
        (``_resolve_field_value(value, ft)`` -> ``_resolve_field_value(value,
        None)``).

        Ohne die Feldvorlage kennt ``_resolve_field_value`` die
        ``options_json``-Zuordnung nicht mehr und gibt den internen
        Options-Slug unveraendert zurueck: Die Zelle enthaelt beim Mutanten
        ``"opt-a"`` statt des lesbaren Labels ``"Option A"``. Dieselben Daten,
        aber falsche Darstellung — kein Sicherheitsbezug.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        field = FieldTemplate.objects.create(
            facility=facility,
            name="Anliegen",
            field_type=FieldTemplate.FieldType.SELECT,
            options_json=[{"slug": "opt-a", "label": "Option A"}],
        )
        event = Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={field.slug: "opt-a"},
            is_anonymous=True,
            created_by=admin_user,
        )

        with translation.override("de"):
            row = sys_export._build_event_row(event, {field.slug: field}, [field.slug], admin_user)

        assert row[ERSTE_DYNAMISCHE_SPALTE] == "Option A"


# ---------------------------------------------------------------------------
# export_events_csv — die Metadaten-Zeile
# ---------------------------------------------------------------------------


class TestExportEventsCsvMetadataRow:
    def test_metadata_row_names_the_exporting_user_and_is_never_empty(self, facility, admin_user):
        """Mutanten ``x_export_events_csv__mutmut_30`` (``user_name`` hart auf
        ``None``), ``_34`` (``metadata`` hart auf ``None``), ``_41``
        (``_sanitize_csv_cell(metadata)`` -> ``_sanitize_csv_cell(None)`` hart).

        ``_34``/``_41`` leeren die Metadaten-Zeile komplett
        (``_sanitize_csv_cell(None) == ""``), ``_30`` traegt den Literalstring
        ``"None"`` statt des Nutzernamens. Der Audit-Trail (``AuditLog`` via
        ``log_export`` in der View) ist von allen drei unberuehrt — gepinnt
        wird der Datei-INHALT.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt")
        Event.objects.create(
            facility=facility,
            client=None,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={},
            is_anonymous=True,
            created_by=admin_user,
        )
        tag = timezone.localdate()

        chunks = list(sys_export.export_events_csv(facility, tag, tag, user=admin_user))
        erste_zeile = next(csv.reader(io.StringIO(chunks[0])))

        erwartetes_praefix = f"# Export: {facility.name} | {admin_user.get_full_name()} | "
        assert erste_zeile[0].startswith(erwartetes_praefix)
        assert len(erste_zeile[0]) > len(erwartetes_praefix), "Zeitstempel muss dem Praefix folgen"


# ---------------------------------------------------------------------------
# generate_report_pdf — der Template-Kontext
# ---------------------------------------------------------------------------


class TestGenerateReportPdfContext:
    def test_context_carries_date_range_mode_and_generation_timestamp(self, facility, monkeypatch):
        """Mutanten ``x_generate_report_pdf__mutmut_20/_21`` (Schluessel
        ``date_from`` -> ``XXdate_fromXX``/``DATE_FROM``), ``_22/_23``
        (``date_to``), ``_26/_27`` (``internal_mode``), ``_28/_29``
        (``generated_at``).

        Acht Mutationen an vier Context-Schluesseln des Templates
        ``report_pdf.html``. Mit dem umbenannten Schluessel fehlt die Angabe im
        gerenderten PDF, Django zeigt keinen Fehler (undefinierte
        Template-Variable rendert leer) — abgefangen wird das Kontext-Dict VOR
        dem (hier gestubbten) Rendering.
        """
        erfasst_context: dict = {}

        def fake_render(template_name, context):
            erfasst_context.update(context)
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _weasyprint_stub())

        bis = timezone.localdate()
        von = bis - timedelta(days=2)
        vor_dem_aufruf = timezone.now()

        sys_export.generate_report_pdf(facility, von, bis, {}, internal_mode=True)

        nach_dem_aufruf = timezone.now()

        assert erfasst_context.get("date_from") == von
        assert erfasst_context.get("date_to") == bis
        assert erfasst_context.get("internal_mode") is True
        generated_at = erfasst_context.get("generated_at")
        assert generated_at is not None
        assert vor_dem_aufruf <= generated_at <= nach_dem_aufruf


# ---------------------------------------------------------------------------
# generate_sample_report_1_pdf — der Template-Kontext
# ---------------------------------------------------------------------------


class TestGenerateSampleReport1PdfContext:
    def test_context_carries_date_range_generated_at_and_population_notes(self, facility, admin_user, monkeypatch):
        """Mutanten ``x_generate_sample_report_1_pdf__mutmut_23/_24`` (Schluessel
        ``date_from``), ``_25/_26`` (``date_to``), ``_29/_30``
        (``generated_at``), ``_35/_36`` (``population_notes``, aeusserer
        Schluessel), ``_37/_38`` (``by_category`` INNERHALB von
        ``population_notes``), ``_43/_44`` (``by_age_cluster`` INNERHALB von
        ``population_notes``).

        Zwoelf Mutanten an fuenf Context-Schluesseln des Templates
        ``sample_report_1_pdf.html`` — laut Modul-Kommentar "das am ehesten
        externe Artefakt". Ohne den K3-Populationshinweis ist der Bericht laut
        Code-Kommentar "nicht nur undicht, sondern irrefuehrend"; abgefangen
        wird das Kontext-Dict VOR dem (hier gestubbten) Rendering.
        """
        doc_type = DocumentType.objects.create(facility=facility, name="Kontakt", report_key="contact")
        for _ in range(3):
            Event.objects.create(
                facility=facility,
                client=None,
                document_type=doc_type,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=True,
                created_by=admin_user,
            )

        erfasst_context: dict = {}

        def fake_render(template_name, context):
            erfasst_context.update(context)
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _weasyprint_stub())

        bis = timezone.localdate()
        von = bis - timedelta(days=2)
        vor_dem_aufruf = timezone.now()

        sys_export.generate_sample_report_1_pdf(facility, von, bis)

        nach_dem_aufruf = timezone.now()

        assert erfasst_context.get("date_from") == von
        assert erfasst_context.get("date_to") == bis
        generated_at = erfasst_context.get("generated_at")
        assert generated_at is not None
        assert vor_dem_aufruf <= generated_at <= nach_dem_aufruf

        population_notes = erfasst_context.get("population_notes")
        assert population_notes is not None
        assert population_notes.get("by_category") == table_note(MAPPED_EVENTS, SNAPSHOT_HYBRID)
        assert population_notes.get("by_age_cluster") == table_note(EVENTS_WITH_PERSON, SNAPSHOT_HYBRID)
