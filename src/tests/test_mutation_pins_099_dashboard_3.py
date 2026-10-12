"""Pin-Tests der Welle L (0.99, Refs #2174 / #2193) — ``core.services.dashboard``,
Batch ``dashboard-3``.

Quelle: Vollmutation #2174, Batch ``dashboard-3``
(``welle-l-0.99/reports/m-scratch/batch-dashboard-3.json``, Triage
``welle-l-0.99/reports/m-scratch/pins-dashboard-3/triage-099-dashboard-3.json``).
Der Batch hat 70 Survivor-Mutanten ueber fuenf Module: ``release_log.py``
(Freigabe-Uebersicht/K4a, 32), ``quick_templates.py`` (Event-Schnellvorlagen,
17), ``disclosure.py`` (k-Anonymitaets-Rueckrechnung, 11), ``main.py``
(Rollen-Arbeitszentralen, 8) und ``snapshot.py`` (2). 56 sind Testluecken
(``test``), 14 sind ``equivalent`` (Begruendung jeweils im Test-Docstring
bzw. in der Triage-Datei — kein Fix, kein Pin).

Gruppierung der gepinnten Tests (Funktion -> Mutanten):

* ``_as_release`` — Identitaets-/Feld-Aufloesung einer Freigabe aus dem
  Audit-Detail (Backend-Slug/Format/Ziel-Prioritaet, Randfelder). 24
  Mutanten, vier Szenarien (Backend gesetzt / Backend leer+Format+Ziel /
  Backend+Format leer+Ziel gesetzt / alle drei leer).
* ``_overlaps`` — Ueberlappungspruefung zweier Freigabe-Zeitraeume (K4a). 3
  Mutanten: Vollstaendigkeits-Gate (falsch-positiv statt Kurzschluss) und
  zwei Grenzwert-Faelle (beruehrende Zeitraeume muessen als Ueberlappung
  zaehlen).
* ``_resolved_margins``/``findings`` (disclosure.py) — ``continue``/``break``
  an vier Schleifen-Stellen; ein ``break`` verschluckt ALLE nachfolgenden
  Partitionen/Zellen einer Mehrfach-Iteration und damit potenziell echte
  Datenschutz-Befunde.
* ``_bounds`` (disclosure.py) — Intervallgrenzen-Arithmetik mit einer
  ``hi=0``-Randzelle.
* ``list_templates_for_user`` — Authentifizierungs-Kurzschluss (``and``
  statt ``or``, echter Bypass fuer nicht angemeldete Nutzer) und
  Fallback-Vertrag bei fehlendem ``is_authenticated``-Attribut.
* ``filter_prefilled_data`` — vier ``continue``->``break``-Stellen (jede
  verliert bei Reihenfolge „Ausschluss-Grund zuerst" alle nachfolgenden
  gueltigen Feldwerte) plus ein Operator-Flip (TEXT-Felder werden faelschlich
  wie MULTI_SELECT behandelt).
* ``apply_template`` — Default-Erkennung fuer leere Formularwerte
  (``""`` vs. ``"XXXX"``).
* ``get_template_for_user`` — Verlust des ``pk=template_id``-Filters
  (Objekt-Identitaet, nicht nur Facility-Scope).
* ``get_templates_for_document_type`` — Verlust des ``is_active``-Filters
  und der ``sort_order``-Sortierung.
* ``staff_dashboard_context`` — „Heute"-Fenstergrenze, Sortierung/Limit der
  offenen Aufgaben.
* ``facility_admin_dashboard_context`` — MFA-Pflicht-Warntext (ein
  Exact-Match-Pin toetet sowohl die XX-Marker- als auch die
  Grossschreibungs-Variante).
* ``super_admin_dashboard_context`` — 24h-Fenster fuer juengste
  Audit-Ereignisse.
* ``select_related``-Verluste mit echtem nachgelagertem Leser
  (``list_templates_for_user``, ``filter_prefilled_data``,
  ``get_template_for_user``) werden ueber die Abfragezahl (``Capture
  QueriesContext``) nachgewiesen — ein reiner Query-Hint OHNE Leser
  (``staff_dashboard_context``s ``select_related("client")``: das
  zurueckgegebene ``my_open_workitems`` wird von keinem heutigen Template
  oder Test dereferenziert) bleibt ``equivalent``.

``_encryption_key`` liegt modulweit an: der Mutantenbaum
(``/work/anlaufstelle/mutants``) hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``; ohne die Fixture waeren alle Pins dort aus dem FALSCHEN
Grund rot.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (
    AuditLog,
    DocumentType,
    DocumentTypeField,
    FieldTemplate,
    QuickTemplate,
    Settings,
    WorkItem,
)
from core.services.dashboard.disclosure import (
    Cell,
    Partition,
    _bounds,
    _resolved_margins,
    findings,
)
from core.services.dashboard.main import (
    facility_admin_dashboard_context,
    staff_dashboard_context,
    super_admin_dashboard_context,
)
from core.services.dashboard.quick_templates import (
    apply_template,
    filter_prefilled_data,
    get_template_for_user,
    get_templates_for_document_type,
    list_templates_for_user,
)
from core.services.dashboard.release_log import Release, _as_release, _overlaps

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


# ---------------------------------------------------------------------------
# release_log._as_release (24 Mutanten)
# ---------------------------------------------------------------------------


class _Entry:
    """Minimaler Stand-in fuer ``AuditLog`` — ``_as_release`` liest nur
    ``detail``, ``timestamp`` und ``target_type``."""

    def __init__(self, detail, timestamp, target_type=""):
        self.detail = detail
        self.timestamp = timestamp
        self.target_type = target_type


class TestAsRelease:
    """``xǁ__as_releaseǁ`` — Feldaufloesung aus dem Audit-``detail``.

    Toetet core.services.dashboard.release_log.x__as_release__mutmut_9
    (``timestamp=None``), _11/_33..._37 (``format``-Feld: direkter
    ``None``-Ersatz, ``or``/``and``-Verwechslung, Schluessel auf ``None``/
    XX-Marker/Grossschreibung geaendert), _16/_53..._57 (dieselben vier
    Varianten fuer ``purpose``) ueber ein Szenario mit gesetztem, wahrem
    ``backend`` (Kurzschluss der ``artifact``-Kette).
    """

    def test_backend_present_pins_direct_fields(self):
        entry = _Entry(
            detail={
                "backend": "clamav_export",
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "recipient": "sozialamt",
                "purpose": "vollstreckung",
            },
            timestamp="2026-01-05T10:00:00+00:00",
            target_type="AuditLog",
        )
        release = _as_release(entry)
        assert release.timestamp == "2026-01-05T10:00:00+00:00"
        assert release.artifact == "clamav_export"
        assert release.backend == "clamav_export"
        assert release.format == "csv"
        assert release.recipient == "sozialamt"
        assert release.purpose == "vollstreckung"

    def test_backend_absent_format_and_target_present_pins_artifact_chain(self):
        """Toetet mutmut_8 (Backend-Default), _26 (ganze Kette -> ``str(None)``),
        _28 (``or``->``and`` bei format/target_type) und _29..._31
        (``format``-Schluessel im Kettenausdruck auf ``None``/XX-Marker/
        Grossschreibung geaendert) — Backend fehlt, ``format`` UND
        ``target_type`` sind beide wahr, sodass die Kette tatsaechlich
        ausgewertet wird."""
        entry = _Entry(detail={"format": "pdf"}, timestamp=None, target_type="AuditLog")
        release = _as_release(entry)
        assert release.artifact == "pdf"
        assert release.backend == ""

    def test_backend_and_format_absent_target_present_pins_or_chain_order(self):
        """Toetet mutmut_27 (``entry.target_type or ""`` -> ``and ""``) — nur
        beobachtbar, wenn ``format`` UND ``backend`` leer sind, ``target_type``
        aber wahr ist (sonst kuerzt ``format`` die Kette vorher ab)."""
        entry = _Entry(detail={}, timestamp=None, target_type="AuditLog")
        release = _as_release(entry)
        assert release.artifact == "AuditLog"

    def test_all_identity_fields_absent_pins_final_fallback_and_defaults(self):
        """Toetet mutmut_32 (letzter ``or ""``-Fallback der ``artifact``-Kette
        auf ``"XXXX"``), _38 (``format``-Feld-Fallback), _52 (``recipient``-
        Fallback) und _58 (``purpose``-Fallback) — nur beobachtbar, wenn
        Backend, Format UND Ziel-Typ zugleich leer sind."""
        entry = _Entry(detail={}, timestamp=None, target_type="")
        release = _as_release(entry)
        assert release.artifact == ""
        assert release.backend == ""
        assert release.format == ""
        assert release.recipient == ""
        assert release.purpose == ""


# ---------------------------------------------------------------------------
# release_log._overlaps (3 Testluecken; 5 equivalent, siehe Triage)
# ---------------------------------------------------------------------------


def _release(artifact: str, date_from: date | None, date_to: date | None) -> Release:
    return Release(
        timestamp=None,
        artifact=artifact,
        format="csv",
        backend=artifact,
        date_from=date_from,
        date_to=date_to,
        recipient="",
        purpose="",
    )


class TestOverlaps:
    """Toetet core.services.dashboard.release_log.x__overlaps__mutmut_5
    (Vollstaendigkeits-Gate: fehlender Zeitraum auf einer Seite darf NICHT
    als Ueberlappung zaehlen)."""

    def test_missing_period_on_one_side_is_not_an_overlap(self):
        first = _release("x", date(2026, 1, 1), date(2026, 1, 31))
        second = _release("x", None, None)
        assert _overlaps(first, second) is False

    def test_touching_periods_count_as_overlap_lower_boundary(self):
        """Toetet mutmut_18 (``first_from <= second_to`` -> ``<``): zwei
        Zeitraeume, die sich exakt am Tag beruehren, teilen sich einen Tag und
        sind damit eine echte Ueberlappung im Sinne von K4a."""
        first = _release("x", date(2026, 1, 20), date(2026, 1, 31))
        second = _release("x", date(2026, 1, 1), date(2026, 1, 20))
        assert _overlaps(first, second) is True

    def test_touching_periods_count_as_overlap_upper_boundary(self):
        """Toetet mutmut_19 (``second_from <= first_to`` -> ``<``), spiegelbildlich
        zur unteren Grenze."""
        first = _release("x", date(2026, 1, 1), date(2026, 1, 20))
        second = _release("x", date(2026, 1, 20), date(2026, 1, 31))
        assert _overlaps(first, second) is True


# ---------------------------------------------------------------------------
# disclosure.py — _resolved_margins, _bounds, findings (7 Testluecken)
# ---------------------------------------------------------------------------


class TestResolvedMargins:
    """Toetet core.services.dashboard.disclosure.x__resolved_margins__mutmut_3
    (``continue`` -> ``break``): eine Partition ohne ``margin_key`` VOR einer
    Partition MIT bekanntem Margin darf die spaetere Partition nicht aus der
    Aufloesung werfen."""

    def test_partition_without_margin_key_does_not_abort_later_partitions(self):
        no_key = Partition(name="p0", margin_key=None, margin=None, cells=())
        resolvable = Partition(
            name="p1",
            margin_key="total",
            margin=None,
            cells=(Cell(key="a", published=5), Cell(key="b", published=3)),
        )
        resolved = _resolved_margins([no_key, resolvable])
        assert resolved == {"total": 8}

    def test_unresolvable_margin_is_recorded_under_its_own_key(self):
        """Toetet mutmut_14 (``resolved.setdefault(partition.margin_key,
        None)`` -> ``resolved.setdefault(None, None)``) UND mutmut_15
        (-> ``resolved.setdefault(None)`` — ebenfalls der Schluessel ``None``,
        nur mit implizitem statt explizitem zweiten Argument; ``dict.
        setdefault`` hat ohnehin ``None`` als Default, das aendert an der
        Schluessel-Verwechslung nichts). Der Rueckgabewert der Funktion ist
        das VOLLSTAENDIGE Mapping, nicht nur das, was ein heutiger Aufrufer
        per ``.get(margin_key)`` daraus liest. Eine Partition mit bekanntem
        ``margin_key``, aber unbekanntem Margin UND nicht vollstaendig
        publizierten Zellen (die Rekonstruktion ueber die Zellensumme greift
        also nicht) muss den Platzhalter unter dem ECHTEN Schluessel ablegen:
        ``{"total": None}``. Beide Mutanten legen ihn stattdessen unter dem
        Literal ``None`` ab: ``{None: None}`` — ein beobachtbar anderer
        Rueckgabewert, unabhaengig davon, wie heutige Aufrufer ihn lesen.
        Der dritte Mutant dieser Gruppe, mutmut_16 (``resolved.setdefault
        (partition.margin_key, )`` — RICHTIGER Schluessel, nur das
        implizite ``None``-Default statt des expliziten), bleibt
        ``equivalent``: ``dict.setdefault(key)`` hat laut Python-Doku
        ohnehin ``None`` als Default, identisch zu ``setdefault(key,
        None)``."""
        partition = Partition(
            name="p",
            margin_key="total",
            margin=None,
            cells=(Cell(key="a", published=None, lo=1, hi=None),),
        )
        resolved = _resolved_margins([partition])
        assert resolved == {"total": None}
        assert set(resolved) == {"total"}


class TestBounds:
    """Toetet core.services.dashboard.disclosure.x__bounds__mutmut_18
    (``cell.hi or 0`` -> ``cell.hi or 1`` in der ``lower``-Berechnung): nur
    beobachtbar, wenn eine ANDERE Zelle ``hi=0`` traegt und alle anderen
    Zellen beschraenkt sind (sonst greift der ``unbounded``-Kurzschluss)."""

    def test_other_cell_with_zero_upper_bound_pins_lower_arithmetic(self):
        cells = (
            Cell(key="target", published=None, lo=1, hi=None),
            Cell(key="zero-hi", published=None, lo=0, hi=0),
            Cell(key="bounded", published=None, lo=0, hi=5),
        )
        lower, _upper = _bounds(cells, residual=10, index=0)
        assert lower == 5


class TestFindings:
    """Toetet core.services.dashboard.disclosure.x_findings__mutmut_8
    (Partitions-Schleife: ``continue`` -> ``break`` bei unbekanntem Margin)
    sowie _23/_26 (Zellen-Schleife: ``continue`` -> ``break`` bei „Spanne noch
    nicht minimal" bzw. „Pin auf 0")."""

    def test_partition_with_unknown_margin_does_not_abort_later_partitions(self):
        unresolvable = Partition(name="p0", margin_key=None, margin=None, cells=())
        finding_partition = Partition(
            name="p1",
            margin_key="total",
            margin=10,
            cells=(Cell(key="a", published=9), Cell(key="hidden", published=None, lo=1, hi=None)),
        )
        result = findings([unresolvable, finding_partition], threshold=5)
        assert result == [("p1", "hidden")]

    def test_wide_span_cell_before_exact_pin_does_not_abort_partition(self):
        """Drei verdeckte Zellen derselben Partition: die ersten beiden haben
        noch eine Spanne >= MIN_FEASIBLE_SPAN (kein Befund, eigene lockere
        Schranken [0, 8]), die dritte ist ueber ihre EIGENEN engen Schranken
        [2, 2] exakt bestimmt (Befund), unabhaengig von den beiden anderen —
        drei Zellen, damit das Fixieren der dritten die ersten beiden nicht
        automatisch mitfixiert. Ein ``break`` nach der ersten wuerde den
        echten Befund verschlucken."""
        partition = Partition(
            name="p",
            margin_key="total",
            margin=12,
            cells=(
                Cell(key="wide", published=None, lo=0, hi=8),
                Cell(key="buffer", published=None, lo=0, hi=8),
                Cell(key="exact", published=None, lo=2, hi=2),
            ),
        )
        result = findings([partition], threshold=10)
        assert result == [("p", "exact")]

    def test_zero_pin_cell_before_exact_pin_does_not_abort_partition(self):
        """Erste verdeckte Zelle ist auf 0 gepinnt (kein Befund), zweite ist
        ein echter Kleinstfallzahl-Befund. Ein ``break`` nach der Null-Zelle
        wuerde den echten Befund verschlucken."""
        partition = Partition(
            name="p",
            margin_key="total",
            margin=6,
            cells=(
                Cell(key="zero", published=None, lo=0, hi=0),
                Cell(key="known", published=4),
                Cell(key="exact", published=None, lo=1, hi=4),
            ),
        )
        result = findings([partition], threshold=10)
        assert result == [("p", "exact")]


# ---------------------------------------------------------------------------
# quick_templates.py — Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ft_bemerkung(facility):
    return FieldTemplate.objects.create(facility=facility, name="Bemerkung", field_type=FieldTemplate.FieldType.TEXT)


@pytest.fixture
def ft_priority_select(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Priorität",
        field_type=FieldTemplate.FieldType.SELECT,
        options_json=[
            {"slug": "hoch", "label": "Hoch", "is_active": True},
            {"slug": "alt", "label": "Alt (deaktiviert)", "is_active": False},
        ],
    )


@pytest.fixture
def ft_multi_select(facility):
    return FieldTemplate.objects.create(
        facility=facility,
        name="Themen",
        field_type=FieldTemplate.FieldType.MULTI_SELECT,
        options_json=[{"slug": "sucht", "label": "Sucht", "is_active": True}],
    )


@pytest.fixture
def ft_sensitive(facility):
    """ELEVATED, nicht HIGH: ``FieldTemplate.save()`` erzwingt bei HIGH
    automatisch ``is_encrypted=True`` (Modellregel „Hoch wird immer
    verschluesselt gespeichert") — ein HIGH-Feld wuerde damit bereits am
    ``is_encrypted``-Guard haengen bleiben, nicht erst am Sensitivitaets-Rang,
    und die beiden Testluecken (mutmut_13/_21) liessen sich nicht trennen."""
    return FieldTemplate.objects.create(
        facility=facility,
        name="Zusatzfeld",
        field_type=FieldTemplate.FieldType.TEXTAREA,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
    )


@pytest.fixture
def ft_encrypted(facility):
    return FieldTemplate.objects.create(
        facility=facility, name="Geheim", field_type=FieldTemplate.FieldType.TEXT, is_encrypted=True
    )


@pytest.fixture
def doc_type_all(facility, ft_bemerkung, ft_priority_select, ft_multi_select, ft_sensitive, ft_encrypted):
    dt = DocumentType.objects.create(facility=facility, name="Mit-Mix-Felder")
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_bemerkung, sort_order=0)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_priority_select, sort_order=1)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_multi_select, sort_order=2)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_sensitive, sort_order=3)
    DocumentTypeField.objects.create(document_type=dt, field_template=ft_encrypted, sort_order=4)
    return dt


@pytest.fixture
def quick_template_event(facility, doc_type_all):
    return QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Standard")


# ---------------------------------------------------------------------------
# quick_templates.list_templates_for_user (4 Testluecken; 1 equivalent)
# ---------------------------------------------------------------------------


class TestListTemplatesForUser:
    def test_anonymous_user_is_blocked_not_bypassed(self, facility, quick_template_event):
        """Toetet mutmut_1 (``user is None or not is_authenticated`` ->
        ``and``): ein nicht angemeldeter ``AnonymousUser`` (nicht ``None``,
        aber ``is_authenticated=False``) muss weiterhin [] liefern — mit
        ``and`` wuerde die Pruefung uebersprungen und der nachfolgende
        Sensitivitaets-Check faellt auf ein Objekt ohne ``.role`` (Absturz
        statt sauberem Leer-Ergebnis)."""
        assert list_templates_for_user(AnonymousUser(), facility) == []

    def test_missing_is_authenticated_attribute_fails_closed(self, facility, quick_template_event):
        """Toetet mutmut_9 (``getattr(user, "is_authenticated", False)`` ->
        ohne Default-Wert, wirft bei fehlendem Attribut) und mutmut_12
        (Default ``False`` -> ``True``, hebt die Sperre faktisch auf). Ein
        Default von ``None`` (mutmut_6) aendert am Ergebnis nichts, da
        ``not None`` ebenso wahr ist wie ``not False`` — das ist der
        dokumentierte Aequivalenzfall in der Triage."""

        class _UserWithoutAuthFlag:
            pass

        assert list_templates_for_user(_UserWithoutAuthFlag(), facility) == []

    def test_document_type_is_loaded_via_join_not_per_row(self, facility, doc_type_all, staff_user):
        """Toetet mutmut_18 (``select_related("document_type")`` ->
        ``select_related(None)``): die Funktion selbst liest
        ``t.document_type.entity_type`` fuer jedes Ergebnis — ohne den Join
        entsteht eine Zusatzquery je Treffer (echter Leser, Abfragezahl ist
        laut Aequivalenzregel kein Aequivalenzbeweis)."""
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Erste")
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Zweite")
        with CaptureQueriesContext(connection) as ctx:
            result = list_templates_for_user(staff_user, facility)
        assert len(result) == 2
        assert len(ctx.captured_queries) == 1


# ---------------------------------------------------------------------------
# quick_templates.filter_prefilled_data (7 Testluecken)
# ---------------------------------------------------------------------------


class TestFilterPrefilledData:
    def test_document_type_fields_are_loaded_via_join_not_per_field(self, doc_type_all):
        """Toetet mutmut_3 (``select_related("field_template")`` ->
        ``select_related(None)``): der Aufbau der ``field_templates``-Lookup-
        Tabelle liest ``dtf.field_template`` fuer JEDES Feld des DocumentType
        (5 in ``doc_type_all``) — ohne Join eine Zusatzquery je Feld."""
        with CaptureQueriesContext(connection) as ctx:
            cleaned = filter_prefilled_data(doc_type_all, {"bemerkung": "Hallo"})
        assert cleaned == {"bemerkung": "Hallo"}
        assert len(ctx.captured_queries) == 1

    def test_unknown_slug_before_known_slug_does_not_drop_the_known_value(self, doc_type_all):
        """Toetet mutmut_10 (``if ft is None: continue`` -> ``break``): ein
        unbekannter Slug VOR einem gueltigen Feld darf die weitere
        Verarbeitung nicht abbrechen (sonst gehen echte Formulardaten
        stillschweigend verloren)."""
        cleaned = filter_prefilled_data(doc_type_all, {"unbekannt": "x", "bemerkung": "Echter Wert"})
        assert cleaned == {"bemerkung": "Echter Wert"}

    def test_encrypted_field_before_normal_field_does_not_drop_the_normal_value(self, doc_type_all):
        """Toetet mutmut_13 (``if ft.is_encrypted or ...FILE: continue`` ->
        ``break``)."""
        cleaned = filter_prefilled_data(doc_type_all, {"geheim": "PIN-123", "bemerkung": "Echter Wert"})
        assert cleaned == {"bemerkung": "Echter Wert"}

    def test_elevated_sensitivity_field_before_normal_field_does_not_drop_the_normal_value(self, doc_type_all):
        """Toetet mutmut_21 (``if rank != 0: continue`` -> ``break``) — ELEVATED
        statt HIGH, weil HIGH ueber ``FieldTemplate.save()`` automatisch
        ``is_encrypted=True`` erzwingt und damit schon den ``is_encrypted``-
        Guard (mutmut_13) ausloesen wuerde, bevor der Sensitivitaets-Rang
        ueberhaupt geprueft wird."""
        cleaned = filter_prefilled_data(doc_type_all, {"zusatzfeld": "Darf nicht durch", "bemerkung": "Echt"})
        assert cleaned == {"bemerkung": "Echt"}

    def test_inactive_choice_before_normal_field_does_not_drop_the_normal_value(self, doc_type_all):
        """Toetet mutmut_28 (``if filtered_value is None: continue`` ->
        ``break``) — ``alt`` ist eine deaktivierte SELECT-Option, also liefert
        ``_filter_active_choices`` ``None``."""
        cleaned = filter_prefilled_data(doc_type_all, {"prioritaet": "alt", "bemerkung": "Echt"})
        assert cleaned == {"bemerkung": "Echt"}

    def test_empty_text_value_is_kept_not_treated_like_multi_select(self, doc_type_all):
        """Toetet mutmut_30 (``ft.field_type == MULTI_SELECT`` -> ``!=``): ein
        leerer TEXT-Wert ist NICHT vom „leere Mehrfachauswahl verwerfen"-Zweig
        betroffen und muss erhalten bleiben."""
        cleaned = filter_prefilled_data(doc_type_all, {"bemerkung": ""})
        assert cleaned == {"bemerkung": ""}

    def test_empty_multi_select_before_normal_field_does_not_drop_the_normal_value(self, doc_type_all):
        """Toetet mutmut_32 (``if MULTI_SELECT and not filtered_value:
        continue`` -> ``break``) — eine leere Mehrfachauswahl VOR einem
        gueltigen Feld."""
        cleaned = filter_prefilled_data(doc_type_all, {"themen": [], "bemerkung": "Echt"})
        assert cleaned == {"bemerkung": "Echt"}


# ---------------------------------------------------------------------------
# quick_templates.apply_template (1 Testluecke)
# ---------------------------------------------------------------------------


class TestApplyTemplate:
    def test_empty_string_form_value_is_overwritten_by_template_default(self, facility, doc_type_all):
        """Toetet mutmut_13 (``merged[slug] in (None, "", [])`` ->
        ``(None, "XXXX", [])``): ein leer gelassenes Formularfeld muss vom
        Template-Default gefuellt werden."""
        template = QuickTemplate.objects.create(
            facility=facility, document_type=doc_type_all, name="Std", prefilled_data={"bemerkung": "Default-Wert"}
        )
        merged = apply_template(template, form_data={"bemerkung": ""})
        assert merged["bemerkung"] == "Default-Wert"


# ---------------------------------------------------------------------------
# quick_templates.get_template_for_user (2 Testluecken)
# ---------------------------------------------------------------------------


class TestGetTemplateForUser:
    def test_wrong_template_id_is_not_silently_resolved_to_another_template(self, facility, staff_user, doc_type_all):
        """Toetet mutmut_4 (``.get(pk=template_id, is_active=True)`` ->
        ``.get(is_active=True)``): der Verlust des ``pk``-Filters ist eine
        Objekt-Identitaetsluecke — ein Aufruf mit einer NICHT existierenden
        ``template_id`` darf niemals ein anderes, tatsaechlich existierendes
        Template liefern."""
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Existiert")
        bogus_id = "00000000-0000-0000-0000-000000000000"
        assert get_template_for_user(staff_user, facility, bogus_id) is None

    def test_document_type_is_loaded_via_join_not_per_call(self, facility, staff_user, doc_type_all):
        """Toetet mutmut_6 (``select_related("document_type")`` ->
        ``select_related(None)``): ``template.document_type`` wird danach
        zweimal gelesen (Sichtbarkeits- und Entity-Type-Pruefung) — ohne Join
        eine Zusatzquery."""
        template = QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Sichtbar")
        with CaptureQueriesContext(connection) as ctx:
            result = get_template_for_user(staff_user, facility, template.pk)
        assert result == template
        assert len(ctx.captured_queries) == 1


# ---------------------------------------------------------------------------
# quick_templates.get_templates_for_document_type (2 Testluecken)
# ---------------------------------------------------------------------------


class TestGetTemplatesForDocumentType:
    def test_inactive_template_is_excluded(self, facility, staff_user, doc_type_all):
        """Toetet mutmut_15 (``.filter(is_active=True, document_type=...)`` ->
        ``.filter(document_type=...)``, verliert den ``is_active``-Filter)."""
        QuickTemplate.objects.create(facility=facility, document_type=doc_type_all, name="Aus", is_active=False)
        result = get_templates_for_document_type(staff_user, facility, doc_type_all)
        assert result == []

    def test_sort_order_wins_over_name_ordering(self, facility, staff_user, doc_type_all):
        """Toetet mutmut_11 (``.order_by("sort_order", "name")`` ->
        ``.order_by("name")``): ``sort_order`` muss vor dem alphabetisch
        entgegengesetzten Namen entscheiden."""
        first = QuickTemplate.objects.create(
            facility=facility, document_type=doc_type_all, name="Z-Vorlage", sort_order=0
        )
        second = QuickTemplate.objects.create(
            facility=facility, document_type=doc_type_all, name="A-Vorlage", sort_order=1
        )
        result = get_templates_for_document_type(staff_user, facility, doc_type_all)
        assert [t.pk for t in result] == [first.pk, second.pk]


# ---------------------------------------------------------------------------
# main.staff_dashboard_context (3 Testluecken; 2 equivalent)
# ---------------------------------------------------------------------------


class TestStaffDashboardContext:
    def test_today_window_excludes_tomorrow(self, facility, staff_user, doc_type_contact, client_identified):
        """Toetet mutmut_11 (``today_end = today_start + timedelta(days=1)``
        -> ``days=2``): ein Ereignis von morgen darf nicht als „heute"
        gezaehlt werden."""
        from core.models import Event

        today = timezone.localdate()
        tomorrow_start = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
        Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=tomorrow_start + timedelta(hours=6),
            data_json={},
            created_by=staff_user,
        )
        ctx = staff_dashboard_context(staff_user, facility)
        assert ctx["today_events_count"] == 0

    def test_open_workitems_order_by_includes_created_at_tiebreak(self, facility, staff_user, client_identified):
        """Toetet mutmut_34 (zweiter Sortierschluessel ``"-created_at"``
        entfernt). Eine BEOBACHTETE Zeilenreihenfolge bei gleichem due_date
        ist KEIN Aequivalenzbeweis (Postgres liefert ohne ORDER-BY-Schluessel
        keine garantierte Reihenfolge — empirisch verifiziert: eine
        Zwei-Zeilen-Stichprobe ohne den Tiebreak lieferte zufaellig dieselbe
        Reihenfolge wie mit ihm). Der Pin prueft deshalb direkt die erzeugte
        SQL-ORDER-BY-Klausel statt der Zeilenreihenfolge."""
        WorkItem.objects.create(
            facility=facility,
            title="A",
            assigned_to=staff_user,
            client=client_identified,
            created_by=staff_user,
            due_date=timezone.localdate(),
        )
        with CaptureQueriesContext(connection) as ctx:
            staff_dashboard_context(staff_user, facility)
        order_by_sql = next(
            q["sql"] for q in ctx.captured_queries if "core_workitem" in q["sql"] and "ORDER BY" in q["sql"]
        )
        assert '"core_workitem"."created_at" DESC' in order_by_sql

    def test_open_workitems_limited_to_five(self, facility, staff_user, client_identified):
        """Toetet mutmut_42 (``[:5]`` -> ``[:6]``): bei sechs offenen Aufgaben
        mit verschiedenen Faelligkeiten duerfen nur die fuenf frühesten
        erscheinen."""
        today = timezone.localdate()
        for offset in range(6):
            WorkItem.objects.create(
                facility=facility,
                title=f"Aufgabe {offset}",
                assigned_to=staff_user,
                client=client_identified,
                created_by=staff_user,
                due_date=today + timedelta(days=offset),
            )

        ctx = staff_dashboard_context(staff_user, facility)
        items = ctx["my_open_workitems"]
        assert len(items) == 5
        assert ctx["my_open_workitems_count"] == 6


# ---------------------------------------------------------------------------
# main.facility_admin_dashboard_context (2 Testluecken, EIN Pin)
# ---------------------------------------------------------------------------


class TestFacilityAdminDashboardContext:
    def test_mfa_warning_exact_wording(self, facility, admin_user):
        """Toetet mutmut_32 (Warntext mit XX-Markern umschlossen) UND
        mutmut_34 (Warntext komplett grossgeschrieben) — derselbe
        Exact-Match-Pin toetet beide Wortlaut-Varianten."""
        Settings.objects.create(facility=facility, mfa_enforced_facility_wide=False)
        ctx = facility_admin_dashboard_context(admin_user, facility)
        assert (
            "MFA-Pflicht (2FA) ist einrichtungsweit nicht aktiv — Login bleibt ohne zweiten Faktor möglich."
            in ctx["settings_warnings"]
        )


# ---------------------------------------------------------------------------
# main.super_admin_dashboard_context (1 Testluecke)
# ---------------------------------------------------------------------------


class TestSuperAdminDashboardContext:
    def test_24h_cutoff_excludes_older_events(self, facility, super_admin_user, staff_user):
        """Toetet mutmut_4 (``timedelta(hours=24)`` -> ``hours=25``): ein
        Audit-Ereignis von vor 24,5 Stunden darf NICHT mehr als „juengst"
        gezaehlt werden."""
        AuditLog.objects.create(
            facility=facility,
            user=staff_user,
            action=AuditLog.Action.LOGIN,
            target_type="User",
            target_id=str(staff_user.pk),
            timestamp=timezone.now() - timedelta(hours=24, minutes=30),
        )
        ctx = super_admin_dashboard_context(super_admin_user)
        assert ctx["recent_audit_events_count"] == 0
