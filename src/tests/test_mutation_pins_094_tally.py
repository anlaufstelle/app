"""Pin-Tests zum Meilenstein release-0.94 — triagierte Survivors in ``core.services.events.tally``.

Quelle: Survivor-Triage des Moduls ``src/core/services/events/tally.py``
(Mutantenbaum ``mutants/src/core/services/events/tally.py``, Rumpf-Vergleich
``x_<fn>__mutmut_orig`` vs. ``x_<fn>__mutmut_N``). Vorgabe ist die Survivor-Triage release-0.94
(Ledger des Meilensteins) (36 Mutanten, Modul-Batch ``tally``).
Schwerpunkte:

* die **kanonische Block-Reihenfolge** der Feldzuordnungen
  (:func:`~core.services.events.tally._tally_assignment_queryset`) — bis heute
  gab es KEINE Testdaten mit ``FieldGroup`` im Tally-Pfad, weshalb die vier
  Sortierschluessel ``block_pos``/``block_is_free``/``sort_order``/``pk``
  ebenso ungeprueft waren wie die beiden Annotationswerte selbst;
* der ``select_related``-Vertrag derselben Query (Query-Zahl statt SQL-Text);
* der ODER-Zweig ``min_contact_stage=""`` des statischen Vorfilters — die
  Config-UI speichert ein leer gelassenes Feld als ``""``, kein bisheriger
  Tally-Testtyp verwendete den leeren String ``""``;
* die Tie-Breaker der Schicht-Auswahl (``sort_order`` vor ``pk``) und der
  Typ-Liste (``name`` vor ``pk``) — beide vorher nur zufaellig gruen;
* die Facility-Bindung und die Kontext-Wiederverwendung von
  :func:`~core.services.events.tally.tally_request_matches_live_context`
  sowie die UTC-Normalisierung beider Fenster-Seiten;
* der Fehlertext des Server-Idempotenzschluessels (``pragma: no cover``
  schliesst nur die Coverage-Messung aus, nicht die Erreichbarkeit);
* die Zaehlgrenzen der Strichlisten-Aggregation (soft-geloescht, Zeitfenster)
  und die ``requires_number_input``-Annotation (nur Pflichtfelder).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``),
nie per Teilstring (mutmut erzeugt ``"XX<text>XX"``-Varianten); Sprachkontext
wird bei Texten per ``translation.override("de")`` festgenagelt; KEINE Imports
aus ``test_rls_functional``; Fixtures kommen aus ``src/tests/conftest.py``
bzw. werden als echte Model-Objekte angelegt (keine Fakes). Jeder Test nennt
seine Mutanten in der Docstring samt dem Wert, der beim Mutanten abweicht.

NICHT gepinnt: ``x_build_tally_summary__mutmut_17`` (Wegfall von
``facility=facility``). Der zusammengesetzte Fremdschluessel
``core_event_document_type_facility_fk`` erzwingt
``Event.facility == Event.document_type.facility`` (verifiziert: ein Insert
mit fremder Facility endet in ``IntegrityError``); ``counts`` ist nach
``document_type_id`` gekeyt und wird ausschliesslich mit PKs aus
``DocumentType.objects.for_facility(facility)`` abgefragt. Fremde Zeilen
koennen den Dict-Lookup daher nicht erreichen — der Mutant ist unter dieser
DB-Invariante aequivalent (nur ein Query-Plan-Nachteil).
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone, translation

from core.models import DocumentType, DocumentTypeField, Event, FieldGroup, FieldTemplate, TimeFilter
from core.services.events.tally import (
    TallyLiveContext,
    _tally_assignment_queryset,
    _tally_static_candidate_queryset,
    build_tally_summary,
    current_tally_context,
    new_tally_idempotency_key,
    tally_document_types,
    tally_eligibilities,
    tally_request_matches_live_context,
)

# Feste UUIDs: die Sortier-Pins brauchen eine BEKANNTE pk-Reihenfolge, sonst
# testen sie gegen den Zufall von ``uuid4`` (genau der Grund, warum die
# betroffenen Mutanten ueberlebt haben).
PK_LOW = uuid.UUID("00000000-0000-4000-8000-000000000001")
PK_MID = uuid.UUID("00000000-0000-4000-8000-000000000002")
PK_HIGH = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

NIGHT_INSTANT = datetime(2026, 6, 21, 23, 30, tzinfo=UTC)


def _number_template(facility, name):
    return FieldTemplate.objects.create(
        facility=facility,
        name=name,
        field_type=FieldTemplate.FieldType.NUMBER,
        is_required=True,
    )


def _assignment(document_type, *, name, sort_order=0, field_group=None, pk=None):
    """Echte Zuordnung — optional mit expliziter pk fuer die Tie-Break-Pins."""
    values = {
        "document_type": document_type,
        "field_template": _number_template(document_type.facility, name),
        "field_group": field_group,
        "sort_order": sort_order,
    }
    if pk is not None:
        values["id"] = pk
    return DocumentTypeField.objects.create(**values)


def _names_in_block_order(*document_types):
    rows = _tally_assignment_queryset().filter(document_type__in=document_types)
    return [row.field_template.name for row in rows]


# ============================================================================
# _tally_assignment_queryset — kanonische Block-Reihenfolge (16 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestTallyAssignmentQuerysetBlockOrder:
    def test_block_annotations_and_group_position_beat_field_sort_order(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_30 — ``Coalesce(None, "sort_order")``;
        mutmut_31 — ``Coalesce("field_group__sort_order", None)``;
        mutmut_39 — ``Case(default=None)``; mutmut_42 — ``default=0`` entfaellt (== 39);
        mutmut_41 — ``When(...)`` entfaellt; mutmut_45 — ``then=None``;
        mutmut_47 — ``then`` entfaellt (== 45); mutmut_48 — ``field_group__isnull=False``;
        mutmut_49 — ``then=2``; mutmut_50 — ``default=1``;
        mutmut_12 — ``block_pos`` faellt aus ``order_by`` heraus.

        Abweichende Werte beim Mutanten: block_pos des GRUPPIERTEN Felds ist 0
        statt 5 (30), block_pos des FREIEN Felds ist ``None`` statt 3 (31);
        block_is_free des gruppierten Felds ist ``None`` statt 0 (39/42) bzw. 1
        statt 0 (50), das des freien Felds 0 statt 1 (41/48), ``None`` statt 1
        (45/47) oder 2 statt 1 (49). Bei mutmut_12 kippt die Reihenfolge auf
        ``[Gruppenfeld, freies Feld]``, weil dann ``block_is_free`` (0 vor 1)
        primaer sortiert statt der Blockposition (3 vor 5).
        """
        document_type = DocumentType.objects.create(facility=facility, name="Blockordnung", tally_enabled=True)
        group = FieldGroup.objects.create(document_type=document_type, name="Spaeter Block", sort_order=5)
        grouped = _assignment(document_type, name="Im Block", sort_order=0, field_group=group)
        free = _assignment(document_type, name="Freies Feld", sort_order=3)

        rows = {row.pk: row for row in _tally_assignment_queryset().filter(document_type=document_type)}

        assert rows[grouped.pk].block_pos == 5
        assert rows[grouped.pk].block_is_free == 0
        assert rows[free.pk].block_pos == 3
        assert rows[free.pk].block_is_free == 1
        assert _names_in_block_order(document_type) == ["Freies Feld", "Im Block"]

    def test_free_field_sorts_behind_the_group_block_at_the_same_position(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_13 — ``block_is_free`` faellt aus ``order_by``.

        Gruppe und freies Feld teilen sich hier die Blockposition 4. Original:
        ``block_is_free`` bricht den Gleichstand (gruppiert 0 vor frei 1) →
        ``["Im Block", "Freies Feld"]``. Ohne diesen Schluessel entscheidet
        ``sort_order`` (4 vor 9) und der Mutant liefert
        ``["Freies Feld", "Im Block"]``.
        """
        document_type = DocumentType.objects.create(facility=facility, name="Gleichstand", tally_enabled=True)
        group = FieldGroup.objects.create(document_type=document_type, name="Block vier", sort_order=4)
        _assignment(document_type, name="Im Block", sort_order=9, field_group=group)
        _assignment(document_type, name="Freies Feld", sort_order=4)

        assert _names_in_block_order(document_type) == ["Im Block", "Freies Feld"]

    def test_group_internal_order_follows_sort_order_not_creation_pk(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_14 — ``sort_order`` faellt aus ``order_by``.

        Beide Felder liegen in DERSELBEN Gruppe, haben also identisches
        ``block_pos``/``block_is_free``; ihre pks laufen der konfigurierten
        Reihenfolge bewusst entgegen. Original sortiert nach ``sort_order``
        (1 vor 2) → ``["Erstes", "Zweites"]``; ohne den Schluessel entscheidet
        die pk und der Mutant liefert ``["Zweites", "Erstes"]``.
        """
        document_type = DocumentType.objects.create(facility=facility, name="Gruppenintern", tally_enabled=True)
        group = FieldGroup.objects.create(document_type=document_type, name="Block", sort_order=0)
        _assignment(document_type, name="Erstes", sort_order=1, field_group=group, pk=PK_HIGH)
        _assignment(document_type, name="Zweites", sort_order=2, field_group=group, pk=PK_LOW)

        assert _names_in_block_order(document_type) == ["Erstes", "Zweites"]

    def test_pk_breaks_complete_ties_deterministically(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_15 — abschliessendes ``"pk"`` entfaellt.

        Fix-Runde 1 Review): der urspruengliche Pin verliess sich auf
        die REIHENFOLGE dreier Zeilen mit identischem
        ``block_pos``/``block_is_free``/``sort_order`` und tötete den Mutanten
        damit nicht zuverlaessig — ohne einen ORDER-BY-Schluessel ist die
        Zeilenreihenfolge, die PostgreSQL fuer einen Sequential/Bitmap-Heap-Scan
        liefert, vom physischen Tabellenlayout abhaengig (Einfuege-/
        Vacuum-/Statistik-Historie), NICHT vom Fehlen des ``pk``-Schluessels an
        sich; reproduzierbar zeigten unabhaengige Simulationslaeufe dieses
        Mutanten sowohl ``["Eins", "Zwei", "Drei"]`` (identisch zum Original)
        als auch ``["Drei", "Zwei", "Eins"]``, je nach Zustand der Test-DB.
        Der Pin prueft deshalb den Query-VERTRAG statt eines konkreten
        Ergebniswerts: ``query.order_by`` ist exakt das an ``.order_by(...)``
        uebergebene Tupel, unabhaengig von Zeilendaten oder DB-Ausfuehrung.
        Original endet auf ``"pk"`` (5-Tupel); der Mutant liefert ein 4-Tupel
        ohne ``"pk"``. Die drei angelegten Zeilen bleiben als Beleg fuer den
        Admin-Fehlerfall (identische Sortierung) und pinnen zusaetzlich die
        tatsaechliche PRODUKTIONS-Garantie: die echte (unmutierte) Funktion
        liefert bei komplettem Gleichstand deterministisch die pk-Reihenfolge
        ``["Eins", "Zwei", "Drei"]`` — das toetet mutmut_15 nicht (s.o.), haelt
        aber den Vertrag der echten Funktion fest.
        """
        document_type = DocumentType.objects.create(facility=facility, name="Vollgleichstand", tally_enabled=True)
        _assignment(document_type, name="Eins", sort_order=0, pk=PK_LOW)
        _assignment(document_type, name="Zwei", sort_order=0, pk=PK_MID)
        _assignment(document_type, name="Drei", sort_order=0, pk=PK_HIGH)

        assert _tally_assignment_queryset().query.order_by == (
            "document_type_id",
            "block_pos",
            "block_is_free",
            "sort_order",
            "pk",
        )
        assert _names_in_block_order(document_type) == ["Eins", "Zwei", "Drei"]

    def test_rows_stay_grouped_per_document_type(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_11 — ``document_type_id`` faellt aus ``order_by``.

        Die Blockpositionen zweier Typen sind bewusst verschraenkt (1/3 gegen
        2/4). Original haelt die Zeilen je Typ zusammen (pk-Reihenfolge der
        Typen: ``PK_LOW`` vor ``PK_HIGH``) → ``["A1", "A3", "B2", "B4"]``.
        Ohne den fuehrenden Schluessel sortiert der Mutant rein nach
        ``block_pos`` und liefert ``["A1", "B2", "A3", "B4"]``.
        """
        type_a = DocumentType.objects.create(id=PK_LOW, facility=facility, name="Typ A", tally_enabled=True)
        type_b = DocumentType.objects.create(id=PK_HIGH, facility=facility, name="Typ B", tally_enabled=True)
        _assignment(type_a, name="A1", sort_order=1)
        _assignment(type_a, name="A3", sort_order=3)
        _assignment(type_b, name="B2", sort_order=2)
        _assignment(type_b, name="B4", sort_order=4)

        assert _names_in_block_order(type_a, type_b) == ["A1", "A3", "B2", "B4"]

    def test_field_group_is_preloaded_without_an_extra_query(self, facility):
        """Pin: x__tally_assignment_queryset__mutmut_25 — ``select_related("field_group")`` entfaellt.

        Original laedt die Gruppe mit; der Zugriff auf ``row.field_group.name``
        kostet 0 zusaetzliche Queries. Beim Mutanten ist es genau 1 (N+1-Query
        je Zeile) — die Zusicherung ``len(ctx.captured_queries) == 0`` faellt.
        """
        document_type = DocumentType.objects.create(facility=facility, name="Vorladen", tally_enabled=True)
        group = FieldGroup.objects.create(document_type=document_type, name="Block", sort_order=1)
        _assignment(document_type, name="Im Block", sort_order=0, field_group=group)

        rows = list(_tally_assignment_queryset().filter(document_type=document_type))
        with CaptureQueriesContext(connection) as captured:
            group_name = rows[0].field_group.name

        assert group_name == "Block"
        assert len(captured.captured_queries) == 0


# ============================================================================
# _tally_static_candidate_queryset / tally_eligibilities (3 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestTallyStaticCandidateQueryset:
    def test_empty_min_contact_stage_string_stays_a_candidate(self, facility, staff_user):
        """Pin: x__tally_static_candidate_queryset__mutmut_17 — ``Q(min_contact_stage=None)``;
        mutmut_18 — ``Q(min_contact_stage="XXXX")``.

        Die Config-UI speichert ein leer gelassenes Auswahlfeld als ``""``,
        nicht als ``None`` — genau dafuer existiert der zweite ODER-Zweig.
        Original nimmt den Typ auf; beide Mutanten pruefen einen Wert, den die
        Zeile nicht traegt (``None`` bzw. ``"XXXX"``), und liefern eine LEERE
        Kandidatenliste statt ``["Leere Kontaktstufe"]``.
        """
        document_type = DocumentType.objects.create(
            facility=facility,
            name="Leere Kontaktstufe",
            tally_enabled=True,
            min_contact_stage="",
        )
        document_type.refresh_from_db()

        assert document_type.min_contact_stage == ""
        assert list(_tally_static_candidate_queryset(facility, staff_user).values_list("name", flat=True)) == [
            "Leere Kontaktstufe"
        ]
        assert [result.document_type.name for result in tally_eligibilities(facility, staff_user)] == [
            "Leere Kontaktstufe"
        ]

    def test_equal_sort_order_is_broken_by_name_before_pk(self, facility, staff_user):
        """Pin: x_tally_eligibilities__mutmut_6 — ``.order_by("sort_order", "pk")`` ohne ``"name"``.

        Beide Typen haben ``sort_order=5``; die pks laufen der alphabetischen
        Reihenfolge bewusst entgegen (``"Alpha zwei"`` traegt die HOEHERE pk).
        Original ordnet nach Name → ``["Alpha zwei", "Beta eins"]``; ohne den
        Namensschluessel entscheidet die pk und der Mutant liefert
        ``["Beta eins", "Alpha zwei"]``. (Der Mutant ist in der Triage als
        ``flaky`` markiert, weil der Bestandstest zufaellige uuid4-pks nutzt —
        die expliziten pks hier machen ihn deterministisch.)
        """
        DocumentType.objects.create(id=PK_HIGH, facility=facility, name="Alpha zwei", tally_enabled=True, sort_order=5)
        DocumentType.objects.create(id=PK_LOW, facility=facility, name="Beta eins", tally_enabled=True, sort_order=5)

        assert [result.document_type.name for result in tally_eligibilities(facility, staff_user)] == [
            "Alpha zwei",
            "Beta eins",
        ]


# ============================================================================
# current_tally_context — Auswahl der laufenden Schicht (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestCurrentTallyContextShiftSelection:
    def test_sort_order_decides_between_two_covering_shifts(self, facility):
        """Pin: x_current_tally_context__mutmut_7 — ``.order_by("pk")`` statt ``("sort_order", "pk")``.

        Beide Schichten decken den Zeitpunkt ab. ``sort_order=0`` traegt
        bewusst die HOEHERE pk. Original liefert ``"Kuratiert zuerst"``; ohne
        ``sort_order`` gewinnt die kleinere pk und der Mutant liefert
        ``"Kuratiert zuletzt"``.
        """
        TimeFilter.objects.create(
            id=PK_LOW,
            facility=facility,
            label="Kuratiert zuletzt",
            start_time=time(0, 0),
            end_time=time(23, 59),
            sort_order=1,
        )
        TimeFilter.objects.create(
            id=PK_HIGH,
            facility=facility,
            label="Kuratiert zuerst",
            start_time=time(0, 0),
            end_time=time(23, 59),
            sort_order=0,
        )

        context = current_tally_context(facility)

        assert context.time_filter is not None
        assert context.time_filter.label == "Kuratiert zuerst"

    def test_pk_breaks_ties_between_shifts_with_the_same_sort_order(self, facility):
        """Pin: x_current_tally_context__mutmut_8 — abschliessendes ``"pk"`` entfaellt.

        Beide Schichten haben ``sort_order=0`` und decken den Zeitpunkt ab; die
        Zeile mit der HOEHEREN pk wird zuerst eingefuegt. Original entscheidet
        per pk → ``"Kleine pk"``. Ohne den Tie-Break bleibt nur die
        DB-Rueckgabereihenfolge (hier die Einfuegereihenfolge) und der Mutant
        liefert ``"Grosse pk"``.
        """
        TimeFilter.objects.create(
            id=PK_HIGH,
            facility=facility,
            label="Grosse pk",
            start_time=time(0, 0),
            end_time=time(23, 59),
            sort_order=0,
        )
        TimeFilter.objects.create(
            id=PK_LOW,
            facility=facility,
            label="Kleine pk",
            start_time=time(0, 0),
            end_time=time(23, 59),
            sort_order=0,
        )

        context = current_tally_context(facility)

        assert context.time_filter is not None
        assert context.time_filter.label == "Kleine pk"


# ============================================================================
# tally_request_matches_live_context — Kontextbindung (7 Mutanten)
# ============================================================================


def _night_shift(facility):
    return TimeFilter.objects.create(
        facility=facility,
        label="Nachtdienst",
        start_time=time(22, 0),
        end_time=time(8, 0),
        sort_order=0,
    )


@pytest.mark.django_db
class TestTallyRequestMatchesLiveContext:
    def test_window_marker_is_compared_against_the_facilitys_own_context(self, facility):
        """Pin: x_tally_request_matches_live_context__mutmut_30 — ``current_tally_context(None)``
        im Marker-Zweig.

        Ohne ``context=``-Argument muss der Live-Kontext DER FACILITY berechnet
        werden. Original vergleicht die Marker mit dem Nachtdienst-Fenster
        (22:00–08:00) und liefert ``True``. ``for_facility(None)`` trifft keine
        Schicht (verifiziert: leeres QuerySet), der Mutant faellt auf das
        Ganztagesfenster des Folgetags zurueck, der Fenstervergleich schlaegt
        fehl und er liefert ``False``.
        """
        _night_shift(facility)

        with patch("django.utils.timezone.now", return_value=NIGHT_INSTANT):
            context = current_tally_context(facility)
            params = {
                "tally_window_start": context.window_start.isoformat(),
                "tally_window_end": context.window_end.isoformat(),
            }
            matches = tally_request_matches_live_context(facility, params)

        assert context.window_start.isoformat() == "2026-06-21T22:00:00+02:00"
        assert matches is True

    def test_shift_filter_is_compared_against_the_facilitys_own_context(self, facility):
        """Pin: x_tally_request_matches_live_context__mutmut_51 — ``current_tally_context(None)``
        im zweiten Zweig (Datum/Schichtfilter).

        Original erkennt den Nachtdienst der Facility als laufende Schicht und
        akzeptiert ``time_filter``/``date`` → ``True``. Beim Mutanten ist
        ``context.time_filter`` ``None`` (facility=None findet keine Schicht),
        die Funktion nimmt den ``return False``-Zweig und liefert ``False``.
        """
        night_shift = _night_shift(facility)

        with patch("django.utils.timezone.now", return_value=NIGHT_INSTANT):
            params = {"time_filter": str(night_shift.pk), "date": "2026-06-21"}
            matches = tally_request_matches_live_context(facility, params)

        assert matches is True

    def test_passed_context_is_reused_instead_of_being_recomputed(self, facility):
        """Pin: x_tally_request_matches_live_context__mutmut_50 — ``context and
        current_tally_context(facility)``.

        Der uebergebene Kontext benennt eine (hier bewusst INAKTIVE) Schicht.
        Original nutzt ihn unveraendert: ``time_filter``-pk und ``date``
        stimmen → ``True``. Der Mutant verwirft ihn und rechnet neu; die
        Neuberechnung findet keine aktive Schicht (``time_filter is None``) und
        liefert ``False``.
        """
        inactive_shift = TimeFilter.objects.create(
            facility=facility,
            label="Nicht aktiv",
            start_time=time(6, 0),
            end_time=time(14, 0),
            sort_order=0,
            is_active=False,
        )
        today = timezone.localdate()
        now_local = timezone.localtime()
        context = TallyLiveContext(
            time_filter=inactive_shift,
            shift_date=today,
            window_start=now_local - timedelta(hours=1),
            window_end=now_local + timedelta(hours=1),
        )
        params = {"time_filter": str(inactive_shift.pk), "date": today.isoformat()}

        assert tally_request_matches_live_context(facility, params, context=context) is True
        assert current_tally_context(facility).time_filter is None

    def test_both_window_sides_are_normalised_to_utc(self, facility):
        """Pin: x_tally_request_matches_live_context__mutmut_32 — ``parsed_start.astimezone(None)``;
        mutmut_33 — ``parsed_end.astimezone(None)``;
        mutmut_35 — ``context.window_start.astimezone(None)``;
        mutmut_36 — ``context.window_end.astimezone(None)``.

        Der Kontext liegt am oberen Rand des Datumsbereichs. Original
        normalisiert beide Seiten nach UTC, erkennt denselben Instant und
        liefert ``True``. ``astimezone(None)`` rechnet dagegen in die
        Systemzeitzone (Django setzt ``TZ=Europe/Berlin``) und laeuft dabei
        ueber ``datetime.max`` hinaus: alle vier Mutanten werfen an dieser
        Zeile ``OverflowError: date value out of range`` — ausserhalb des
        ``except (TypeError, ValueError)``-Blocks, also ungefangen.
        """
        context = TallyLiveContext(
            time_filter=None,
            shift_date=date(9999, 12, 31),
            window_start=datetime(9999, 12, 31, 23, 0, tzinfo=UTC),
            window_end=datetime(9999, 12, 31, 23, 30, tzinfo=UTC),
        )
        params = {
            "tally_window_start": context.window_start.isoformat(),
            "tally_window_end": context.window_end.isoformat(),
        }

        assert tally_request_matches_live_context(facility, params, context=context) is True


# ============================================================================
# new_tally_idempotency_key — Fehlertext des Vertragsbruchs (4 Mutanten)
# ============================================================================


class TestNewTallyIdempotencyKeyContract:
    def test_invalid_normalisation_raises_the_exact_german_message(self, monkeypatch):
        """Pin: x_new_tally_idempotency_key__mutmut_4 — ``RuntimeError(None)``;
        mutmut_5 — ``"XXServergenerierter …ungueltigXX"``;
        mutmut_6 — komplett kleingeschrieben; mutmut_7 — komplett grossgeschrieben.

        ``# pragma: no cover`` nimmt den Zweig nur von der Coverage-MESSUNG
        aus, nicht von der Erreichbarkeit: ``normalize_idempotency_key`` liegt
        als Modulglobal vor und laesst sich deterministisch auf ``None``
        zwingen. Verglichen wird der VOLLE String (kein Teilstring, kein
        ``re.search``): der Mutant liefert ``"None"`` (4), die
        ``XX``-Klammerfassung (5), ``"servergenerierter …"`` (6) bzw.
        ``"SERVERGENERIERTER …"`` (7).
        """
        monkeypatch.setattr("core.services.events.tally.normalize_idempotency_key", lambda raw: None)

        with translation.override("de"), pytest.raises(RuntimeError) as excinfo:
            new_tally_idempotency_key()

        assert str(excinfo.value) == "Servergenerierter Tally-Idempotenzschlüssel ist ungültig"


# ============================================================================
# tally_document_types / build_tally_summary — Zaehl- und Annotationsgrenzen
# ============================================================================


@pytest.mark.django_db
class TestTallyDocumentTypesAnnotation:
    def test_requires_number_input_counts_only_required_fields(self, facility, staff_user):
        """Pin: x_tally_document_types__mutmut_10 — ``field_template__is_required=True``
        faellt aus dem ``Exists``-Subquery.

        Der Typ hat GENAU EIN Feld, und zwar ein optionales Textfeld. Original
        annotiert ``requires_number_input == False`` (kein Pflichtfeld
        vorhanden); ohne den Filter matcht jedes Feld und der Mutant liefert
        ``True``.
        """
        document_type = DocumentType.objects.create(facility=facility, name="Nur optional", tally_enabled=True)
        optional_template = FieldTemplate.objects.create(
            facility=facility,
            name="Freitext",
            field_type=FieldTemplate.FieldType.TEXT,
            is_required=False,
        )
        DocumentTypeField.objects.create(
            document_type=document_type,
            field_template=optional_template,
            sort_order=0,
        )

        annotated = tally_document_types(facility, staff_user).get(pk=document_type.pk)

        assert annotated.requires_number_input is False


@pytest.mark.django_db
class TestBuildTallySummaryCounting:
    def test_counts_only_undeleted_anonymous_events_inside_the_window(self, facility, staff_user):
        """Pin: x_build_tally_summary__mutmut_18 — ``is_deleted=False`` entfaellt;
        mutmut_20 — ``occurred_at__range=(…)`` entfaellt.

        Neben dem einen zaehlbaren Ereignis existieren ein soft-geloeschtes im
        Fenster und ein nicht geloeschtes DREI TAGE vor dem Fenster. Original
        zaehlt genau 1. Ohne den Loesch-Filter zaehlt der Mutant 2 (18), ohne
        den Fenster-Filter ebenfalls 2 (20).
        """
        document_type = DocumentType.objects.create(facility=facility, name="Anonyme Kontakte", tally_enabled=True)
        now = timezone.now()
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=now,
            is_anonymous=True,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=now,
            is_anonymous=True,
            is_deleted=True,
            created_by=staff_user,
        )
        Event.objects.create(
            facility=facility,
            document_type=document_type,
            occurred_at=now - timedelta(days=3),
            is_anonymous=True,
            created_by=staff_user,
        )

        summary = build_tally_summary(facility, staff_user)

        assert [(row["document_type"].name, row["count"]) for row in summary["rows"]] == [("Anonyme Kontakte", 1)]
