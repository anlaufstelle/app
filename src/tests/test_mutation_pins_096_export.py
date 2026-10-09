"""Pin-Tests zum Meilenstein release-0.96 — Prio-1-Survivors in ``core.services.system.export``.

Quelle: Mutations-Delta v0.96.0, Survivor-Triage des Moduls
``src/core/services/system/export.py``. Gepinnt wird ausschliesslich die
Prio-1-Menge (Export-INHALT, Sichtbarkeitsfilter, k-Anon-Schwelle,
Datenintegritaet des Streams):

* **Grundgesamtheit des CSV** (``_get_events_queryset``): der
  ``is_deleted=False``-Filter und **beide** Datumsgrenzen. Ohne sie verlaesst
  der Papierkorb die Anwendung ueber den Export bzw. die Datei enthaelt mehr
  Zeitraum, als am Bildschirm angefordert wurde.
* **Spaltenmenge** (``_collect_field_templates``): das ``continue`` am
  Datei-Feld (ein ``break`` kappte alle alphabetisch spaeteren Feldvorlagen)
  und die rollenbasierte Vorfilterung der Spaltenkoepfe — inklusive des
  dritten Arguments ``ft.sensitivity``, ohne das der **Feldname** einer
  hochsensiblen Vorlage im Header jeder Rolle steht.
* **Zeilenaufbau** (``_build_event_row``): der Sensitivity-Default fuer
  verwaiste Slugs (``""`` = „erbt vom Dokumentationstyp", nicht ein
  unbekannter Wert, der fail-closed jede Rolle aussperrt) und das
  ``continue`` im Restricted-Zweig (ein ``break`` schnitte die Zeile hinter
  dem ersten eingeschraenkten Feld ab — Zeile kuerzer als Header).
* **Stream-Integritaet** (``_stream_row``): ``output.seek(0)`` vor dem
  ``truncate(0)``. Mit ``seek(1)`` beginnt jede Zeile ausser der ersten mit
  einem Null-Byte.
* **Durchreichen des Nutzers** (``export_events_csv``) an Spalten- und
  Zeilenbau.
* **PDF data protection** (sample report PDF through the generic export): the
  facility from which the k-anonymity threshold is read.

Konventionen wie ``test_mutation_pins_094_export_1.py``: Zusicherungen
vergleichen EXAKT (``==``/``is``), nie per Teilstring, wo mutmut
``"XX<text>XX"``-Varianten erzeugen kann; Sprachkontext wird per
``translation.override("de")`` festgenagelt (die de-Katalogeintraege der hier
geprueften Texte sind identisch zur msgid); Fixtures kommen aus
``src/tests/conftest.py``, alles andere sind echte Model-Objekte. Jeder Test
nennt in der Docstring die Mutante(n) samt dem Wert, der beim Mutanten
abweicht.

**Warum zwei Pins die Funktion direkt aufrufen statt ueber
``export_events_csv`` zu gehen.** Der Restricted-Zweig in
``_build_event_row`` ist aus ``export_events_csv`` heraus nicht erreichbar:
``_get_events_queryset`` schneidet den QuerySet mit
``Event.objects.visible_to(user)``, und die Vorfilterung in
``_collect_field_templates`` prueft dieselbe Feld-Sensitivity gegen dieselbe
Rolle. Damit gilt fuer jede Kombination aus Rolle, Typ- und Feld-Sensitivity:
ist die Zeile ueberhaupt im Export und die Spalte im Header, ist das Feld
auch sichtbar. Der Zweig ist gewollte Defense-in-Depth fuer Direktaufrufer —
und nur als solcher pinnbar (dieselbe Aufrufform nutzt bereits
``test_mutation_followup_export.py``).
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta

import pytest
from django.utils import timezone, translation

from core.models import DocumentType, Event, FieldTemplate, Settings
from core.services.system import export as sys_export

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

#: Erste dynamische Spalte einer CSV-Zeile — davor stehen Datum, Uhrzeit,
#: Dokumentationstyp, Person, Kontaktstufe, Altersgruppe (``_build_header``).
ERSTE_DYNAMISCHE_SPALTE = 6


def _aware(target_date: date, hour: int = 12, minute: int = 0) -> datetime:
    """Aware Zeitstempel zur Mittagszeit — haelt ``occurred_at__date`` stabil."""
    return timezone.make_aware(datetime.combine(target_date, time(hour, minute)))


def _csv_zeilen(chunks) -> list[list[str]]:
    """Gestreamte Chunks zu geparsten CSV-Zeilen (Zeile 0 = Metadaten, 1 = Header)."""
    return list(csv.reader(io.StringIO("".join(chunks))))


def _doc_type(facility, name, sensitivity=DocumentType.Sensitivity.NORMAL) -> DocumentType:
    return DocumentType.objects.create(facility=facility, name=name, sensitivity=sensitivity)


def _event(facility, doc_type, user, when, data=None, *, is_deleted=False) -> Event:
    event = Event.objects.create(
        facility=facility,
        client=None,
        document_type=doc_type,
        occurred_at=when,
        data_json=data or {},
        is_anonymous=True,
        created_by=user,
    )
    if is_deleted:
        Event.objects.filter(pk=event.pk).update(is_deleted=True)
        event.refresh_from_db()
    return event


def _field_template(facility, name, *, field_type=FieldTemplate.FieldType.TEXT, sensitivity="") -> FieldTemplate:
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=field_type,
        sensitivity=sensitivity,
    )


def _weasyprint_stub(erfasst: dict):
    """``weasyprint.HTML``-Ersatz, der die kwargs erfasst und Fake-Bytes liefert."""

    class _StubHTML:
        def __init__(self, **kwargs):
            erfasst.update(kwargs)

        def write_pdf(self, *args, **kwargs):
            return b"%PDF-1.7 fake"

    return _StubHTML


# ---------------------------------------------------------------------------
# _get_events_queryset — Grundgesamtheit des Exports
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportGrundgesamtheit:
    """Was ueberhaupt in die Datei kommt: Papierkorb und Zeitraumgrenzen."""

    def test_soft_geloeschtes_ereignis_verlaesst_den_export_nicht(self, facility, admin_user):
        """Mutante ``x__get_events_queryset__mutmut_5`` (``is_deleted=False`` entfernt).

        Ohne den Filter nimmt die CSV das soft-geloeschte Ereignis wieder auf:
        Der Mutant liefert die Dokumentationstypen ``["Aktiv", "Papierkorb"]``
        statt nur ``["Aktiv"]``.
        """
        tag = timezone.localdate()
        _event(facility, _doc_type(facility, "Aktiv"), admin_user, _aware(tag))
        _event(facility, _doc_type(facility, "Papierkorb"), admin_user, _aware(tag), is_deleted=True)

        zeilen = _csv_zeilen(sys_export.export_events_csv(facility, tag, tag, user=admin_user))

        assert [z[2] for z in zeilen[2:]] == ["Aktiv"]

    def test_ereignis_vor_dem_zeitraum_bleibt_aussen_vor(self, facility, admin_user):
        """Mutante ``x__get_events_queryset__mutmut_6`` (untere Datumsgrenze entfernt).

        Der Mutant exportiert die gesamte Historie bis ``date_to`` und liefert
        ``["Innen", "Davor"]`` statt ``["Innen"]``.
        """
        bis = timezone.localdate() - timedelta(days=5)
        von = bis - timedelta(days=5)
        _event(facility, _doc_type(facility, "Innen"), admin_user, _aware(von + timedelta(days=2)))
        _event(facility, _doc_type(facility, "Davor"), admin_user, _aware(von - timedelta(days=10)))

        zeilen = _csv_zeilen(sys_export.export_events_csv(facility, von, bis, user=admin_user))

        assert [z[2] for z in zeilen[2:]] == ["Innen"]

    def test_ereignis_nach_dem_zeitraum_bleibt_aussen_vor(self, facility, admin_user):
        """Mutante ``x__get_events_queryset__mutmut_7`` (obere Datumsgrenze entfernt).

        Der Mutant exportiert ueber ``date_to`` hinaus und liefert
        ``["Danach", "Innen"]`` (absteigend nach ``occurred_at``) statt
        ``["Innen"]``.
        """
        bis = timezone.localdate() - timedelta(days=5)
        von = bis - timedelta(days=5)
        _event(facility, _doc_type(facility, "Innen"), admin_user, _aware(von + timedelta(days=2)))
        _event(facility, _doc_type(facility, "Danach"), admin_user, _aware(bis + timedelta(days=2)))

        zeilen = _csv_zeilen(sys_export.export_events_csv(facility, von, bis, user=admin_user))

        assert [z[2] for z in zeilen[2:]] == ["Innen"]


# ---------------------------------------------------------------------------
# _collect_field_templates — Spaltenmenge und Spaltenkoepfe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportSpaltenmenge:
    """Welche dynamischen Spalten im Header stehen — Datei-Felder und Rolle."""

    def test_dateifeld_kappt_die_nachfolgenden_spalten_nicht(self, facility, admin_user):
        """Mutante ``x__collect_field_templates__mutmut_7`` (``continue`` → ``break``).

        Die Schleife laeuft ueber ``order_by("name")``. Beim Mutanten bricht
        sie am alphabetisch ersten Datei-Feld ab; die Textspalte dahinter
        fehlt dann komplett (``[]`` statt ``["B Text"]``).
        """
        _field_template(facility, "A Datei", field_type=FieldTemplate.FieldType.FILE)
        _field_template(facility, "B Text")
        tag = timezone.localdate()
        _event(facility, _doc_type(facility, "Kontakt"), admin_user, _aware(tag))

        zeilen = _csv_zeilen(sys_export.export_events_csv(facility, tag, tag, user=admin_user))

        assert zeilen[1][ERSTE_DYNAMISCHE_SPALTE:] == ["B Text"]

    def test_hochsensible_spalte_fehlt_im_header_der_assistenz(self, facility, admin_user, assistant_user):
        """Mutanten ``x__collect_field_templates__mutmut_11`` (``user is not None``
        → ``user is None``), ``…__mutmut_16`` (``ft.sensitivity`` → ``None``),
        ``…__mutmut_19`` (drittes Argument entfernt) und
        ``x_export_events_csv__mutmut_14`` (``_collect_field_templates(facility,
        None)``).

        Alle vier heben die rollenbasierte Vorfilterung der Spaltenkoepfe auf:
        Der Header enthaelt beim Mutanten zusaetzlich ``"Geheimfeld"`` — der
        **Name** der hochsensiblen Vorlage, den die Assistenz nie sehen darf.
        """
        _field_template(facility, "Geheimfeld", sensitivity=DocumentType.Sensitivity.HIGH)
        _field_template(facility, "Normalfeld")
        tag = timezone.localdate()
        _event(facility, _doc_type(facility, "Kontakt"), admin_user, _aware(tag))

        zeilen = _csv_zeilen(sys_export.export_events_csv(facility, tag, tag, user=assistant_user))

        assert zeilen[1][ERSTE_DYNAMISCHE_SPALTE:] == ["Normalfeld"]


# ---------------------------------------------------------------------------
# _build_event_row — Sensitivity-Default und Restricted-Zweig
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportZeilenaufbau:
    """Sichtbarkeitsentscheid je Zelle — Direktaufruf (siehe Modul-Docstring)."""

    def test_verwaister_slug_erbt_die_typ_sensitivitaet(self, facility, admin_user):
        """Mutante ``x__build_event_row__mutmut_31`` (``""`` → ``"XXXX"``).

        ``ft is None`` heisst „kein Feld-Override, es gilt der
        Dokumentationstyp". ``"XXXX"`` ist dagegen ein unbekannter
        Sensitivity-Wert und faellt fail-closed oberhalb jedes Rollenmaximums:
        Der Mutant liefert ``"[Eingeschränkt]"`` statt ``"Rohwert"`` — auch
        fuer die Anwendungsbetreuung und bei einem Typ der Stufe ``normal``.
        """
        event = _event(
            facility,
            _doc_type(facility, "Kontakt"),
            admin_user,
            _aware(timezone.localdate()),
            data={"verwaist": "Rohwert"},
        )

        with translation.override("de"):
            zeile = sys_export._build_event_row(event, {}, ["verwaist"], admin_user)

        assert zeile[ERSTE_DYNAMISCHE_SPALTE] == "Rohwert"

    def test_zeile_behaelt_die_spalten_nach_einem_eingeschraenkten_feld(self, facility, admin_user, assistant_user):
        """Mutante ``x__build_event_row__mutmut_46`` (``continue`` → ``break``).

        Der Mutant bricht die Schleife am ersten eingeschraenkten Feld ab: Die
        Zeile ist mit 7 Zellen kuerzer als der Header und der sichtbare Wert
        ``"Sichtbar"`` der Folgespalte fehlt (Original: 8 Zellen).
        """
        geheim = _field_template(facility, "A Geheim", sensitivity=DocumentType.Sensitivity.HIGH)
        offen = _field_template(facility, "B Offen")
        event = _event(
            facility,
            _doc_type(facility, "Kontakt"),
            admin_user,
            _aware(timezone.localdate()),
            data={offen.slug: "Sichtbar"},
        )

        with translation.override("de"):
            zeile = sys_export._build_event_row(
                event,
                {geheim.slug: geheim, offen.slug: offen},
                [geheim.slug, offen.slug],
                assistant_user,
            )
            zeile = [str(zelle) for zelle in zeile]

        assert len(zeile) == ERSTE_DYNAMISCHE_SPALTE + 2
        assert zeile[ERSTE_DYNAMISCHE_SPALTE] == "[Eingeschränkt]"
        assert zeile[ERSTE_DYNAMISCHE_SPALTE + 1] == "Sichtbar"


# ---------------------------------------------------------------------------
# _stream_row / export_events_csv — Stream-Integritaet und Durchreichen
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExportStream:
    """Der gestreamte Bytestrom und die Argumente der Aufrufstellen."""

    def test_gestreamte_zeilen_enthalten_keine_nullbytes(self, facility, admin_user):
        """Mutante ``x__stream_row__mutmut_4`` (``output.seek(0)`` → ``seek(1)``).

        ``seek(1)`` vor ``truncate(0)`` laesst die Schreibposition auf 1
        stehen: ``StringIO`` fuellt die Luecke mit ``\\x00``, jede Zeile ausser
        der ersten beginnt beim Mutanten mit einem Null-Byte.
        """
        tag = timezone.localdate()
        _event(facility, _doc_type(facility, "Erst"), admin_user, _aware(tag, 9))
        _event(facility, _doc_type(facility, "Zweit"), admin_user, _aware(tag, 10))

        chunks = list(sys_export.export_events_csv(facility, tag, tag, user=admin_user))

        assert "\x00" not in "".join(chunks)
        assert [chunk.startswith("\x00") for chunk in chunks] == [False] * len(chunks)

    def test_zeilenbau_bekommt_den_aufrufenden_nutzer(self, facility, admin_user, monkeypatch):
        """Mutante ``x_export_events_csv__mutmut_52`` (``…, user)`` → ``…, None)``).

        Der Mutant reicht ``None`` an ``_build_event_row`` durch und schaltet
        damit die Sichtbarkeitspruefung je Zelle fuer JEDE Zeile ab. Am
        Ergebnis der CSV ist das nicht sichtbar (siehe Modul-Docstring: der
        Zweig ist ueber ``export_events_csv`` nicht erreichbar), an der
        Aufrufstelle schon — dieselbe Pin-Form wie in
        ``test_export_ssrf_url_fetcher.py`` fuer den ``url_fetcher``.
        """
        tag = timezone.localdate()
        _event(facility, _doc_type(facility, "Kontakt"), admin_user, _aware(tag))

        gesehen = []
        original = sys_export._build_event_row

        def spion(event, all_field_templates, field_slugs, user):
            gesehen.append(user)
            return original(event, all_field_templates, field_slugs, user)

        monkeypatch.setattr(sys_export, "_build_event_row", spion)

        list(sys_export.export_events_csv(facility, tag, tag, user=admin_user))

        assert gesehen == [admin_user]


# ---------------------------------------------------------------------------
# Sample report PDF (generic export, hybrid reader) — facility threshold
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPdfDatenschutzDefaults:
    """Der Default-Modus des Berichts-PDF und die Herkunft der k-Anon-Schwelle."""

    def _events(self, facility, user, name, anzahl, *, report_key=None, mit_person=None):
        dt = DocumentType.objects.create(facility=facility, name=name, report_key=report_key)
        for _ in range(anzahl):
            Event.objects.create(
                facility=facility,
                client=mit_person,
                document_type=dt,
                occurred_at=timezone.now(),
                data_json={},
                is_anonymous=mit_person is None,
                created_by=user,
            )

    def test_sachbericht_pdf_nutzt_die_schwelle_der_einrichtung(self, facility, admin_user, monkeypatch):
        """Mutante ``x_generate_sample_report_1_pdf__mutmut_9``
        (``suppress_sample_report_1_stats(facility, …)`` → ``(None, …)``).

        Gleiche Ursache wie oben, im am ehesten extern zirkulierenden
        Artefakt: Beim Mutanten gilt der Default 5 statt der konfigurierten
        10, und die Kategorie ``"Beratung"`` steht mit der rohen 7 im PDF.
        """
        Settings.objects.create(facility=facility, k_anonymity_threshold=10)
        self._events(facility, admin_user, "Kontakt", 12, report_key="contact")
        self._events(facility, admin_user, "Krise", 7, report_key="crisis")

        bis = timezone.localdate()
        von = bis - timedelta(days=2)

        erfasst: dict = {}
        gerendert: dict = {}

        def fake_render(template_name, context):
            gerendert["stats"] = context["stats"]
            return "<html></html>"

        monkeypatch.setattr(sys_export, "render_to_string", fake_render)
        monkeypatch.setattr(sys_export.weasyprint, "HTML", _weasyprint_stub(erfasst))

        from core.reporting.export import export_dataset
        from core.reporting.registry import get_backend

        export_dataset(get_backend("sample-report-1"), facility, von, bis, format="pdf")

        kategorien = {r["name"]: r for r in gerendert["stats"]["by_category"]}
        assert kategorien["Beratung"]["count"] is None
        assert kategorien["Beratung"]["suppressed"] is True
