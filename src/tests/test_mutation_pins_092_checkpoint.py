"""Pin-Tests aus der Survivor-Triage des 0.92-Checkpoint-Deltas (BASE v0.91.0).

Der ``mutation-delta``-Lauf zum Checkpoint ``release-0.92`` deckte 8
Scope-Dateien mit 1978 Delta-Mutanten ab: 230 Survivors plus 2 ohne
deckenden Test (232 Eintraege insgesamt). Die Volltriage klassifizierte
alle 232 Eintraege vollstaendig — 164 echte Testluecken, 52 Aequivalenz-
und 16 Kosmetik-Claims; jeder Aequivalenz-/Kosmetik-Claim wurde
adversarial gegengeprueft, mit dem Ergebnis 40 refutiert / 28 bestaetigt.
Diese Datei pinnt das Prio-1-Set der Testluecken — vier Module:

* ``core.services.dashboard.search`` — die Filterpraedikate ``is_active``
  (Client) und ``Event.is_deleted`` in der Suchfilter-Kette;
* ``core.services.dashboard.snapshot`` — der ``persons_by_contact_stage``-
  Merge in ``_merge_stats`` sowie der Standstempel ``SNAPSHOT_AS_OF_KEY``
  in ``get_sample_report_1_statistics_hybrid``;
* ``core.services.events.feed`` — die Feld-Sensitivity der Vorschau
  (``_build_event_preview_fields``) und die Facility-/Papierkorb-
  Praedikate in ``_feed_bans``/``_feed_events``;
* ``core.services.events.tally`` — Fenstermarker- und Datums-Validierung
  in ``tally_request_matches_live_context``.

Die uebrigen 146 Testluecken des Deltas sind bewusst NICHT hier:
Folge-Ticket #2120.

Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen
vergleichen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
``"XX<text>XX"``-Varianten); KEINE Imports aus ``test_rls_functional``
(its fixture ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin
requesting it never runs in a mutation run);
jeder Test nennt seine Mutanten in der Docstring. Der Kill-Nachweis ist
der Delta-RERUN des Checkpoints (Delta-Mutanten werden immer neu
getestet).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone, translation

from core.models import Client, DocumentType, DocumentTypeField, Event, FieldTemplate, StatisticsSnapshot
from core.services.dashboard.search import (
    client_search_queryset,
    event_search_queryset,
    search_clients_and_events,
    search_result_counts,
)
from core.services.dashboard.snapshot import (
    SNAPSHOT_AS_OF_KEY,
    _merge_stats,
    get_sample_report_1_statistics_hybrid,
)
from core.services.events import (
    TallyLiveContext,
    build_feed_items,
    enrich_events_with_preview,
    tally_request_matches_live_context,
)
from core.services.events.feed import _build_event_preview_fields

# ── core.services.dashboard.search ────────────────────────────────────────────
#
# Pin-Tests der Prio-1-Testluecken in ``core.services.dashboard.search``.
#
# Quelle: Survivor-Triage des ``mutation-delta``-Laufs zum Checkpoint 0.92.
# Modul unter Test: ``src/core/services/dashboard/search.py`` (Mutantenbaum
# ``mutants/src/core/services/dashboard/search.py``).
#
# Gepinnt werden die beiden bestaetigten Luecken der Suchfilter-Kette — beide
# sind Sichtbarkeits-, keine Kosmetikbefunde:
#
# * ``x_client_search_queryset__mutmut_5`` — ``is_active=True`` faellt aus dem
#   Client-Filter. ``is_active=False`` markiert laut ``core/models/client.py``
#   (Refs #2013) u.a. die von der K-Anon-Bridge stillgelegten Personen, und die
#   Bridge setzt es ausdruecklich OHNE Papierkorb: solche Zeilen sind
#   ``is_deleted=False`` und waeren nach der Mutation wieder exakt suchbar. Der
#   Bestand deckte nur den ``is_deleted``-Pfad (Refs #1803) ab.
# * ``x_event_search_queryset__mutmut_11`` — ``is_deleted=False`` faellt aus dem
#   Event-Filter. Soft-geloeschte Ereignisse tauchten damit wieder in Suche und
#   Gruppenzahlen auf. Der Bestand pruefte nur den Papierkorb der PERSON
#   (``exclude(client__is_deleted=True)``), nie den des Ereignisses selbst.
#
# Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: eine Testklasse
# je Funktion, jeder Test nennt seine Mutanten in der Docstring; Zusicherungen
# vergleichen EXAKT (``==``/``is``), nie per Teilstring (mutmut erzeugt
# ``"XX<text>XX"``-Varianten); KEINE Imports aus ``test_rls_functional`` (its fixture
# ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin requesting it never
# runs in a mutation run);
# Fixtures kommen aus ``src/tests/conftest.py`` und legen echte Model-Objekte an,
# keine Fakes. Jeder Negativtest fuehrt einen Positiv-Zwilling mit, damit ein
# still leer laufendes Queryset nicht als gruener Pin durchgeht.


# ============================================================================
# core.services.dashboard.search.client_search_queryset
# ============================================================================


@pytest.mark.django_db
class TestClientSearchQuerysetSkipsInactiveClients:
    """Stillgelegte Personen (``is_active=False``) sind nicht suchbar.

    ``soft_delete()`` und Stilllegung sind zwei unabhaengige Achsen: die
    K-Anon-Bridge setzt ``is_active=False`` OHNE ``is_deleted=True`` (Refs
    #2013). Ein Filter, der nur den Papierkorb prueft, holt genau diese
    Personen zurueck in Dropdown, Vollseite und Gruppenzahl.
    """

    @pytest.fixture
    def inactive_client(self, facility, staff_user):
        """Stillgelegt, aber NICHT im Papierkorb — die Bridge-Konstellation."""
        return Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Inaktiv-01",
            created_by=staff_user,
            is_active=False,
        )

    def test_inactive_client_is_not_in_the_queryset(self, facility, inactive_client):
        """Pin: ``x_client_search_queryset__mutmut_5`` — ``.filter(pseudonym__ilike=query,
        is_active=True, is_deleted=False)`` → ``.filter(pseudonym__ilike=query,
        is_deleted=False)``.

        Auf dem Original ist die Zaehlung ``0``; beim Mutanten faellt
        ``is_active=True`` weg und die Zeile ist ``is_deleted=False``, also
        ``1``.
        """
        assert inactive_client.is_deleted is False  # kein Papierkorb-Umweg
        assert client_search_queryset(facility, "Inaktiv-01").count() == 0

    def test_active_client_with_the_same_prefix_still_matches(self, facility, staff_user, inactive_client):
        """Positiv-Zwilling: die Query selbst trifft — sonst waere der Pin leer.

        Ohne diesen Test bliebe der Negativtest auch dann gruen, wenn
        ``pseudonym__ilike`` gar nichts mehr faende.
        """
        active = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.IDENTIFIED,
            pseudonym="Inaktiv-02",
            created_by=staff_user,
        )

        assert list(client_search_queryset(facility, "Inaktiv-0")) == [active]

    def test_service_returns_no_client_for_an_inactive_hit(self, facility, staff_user, inactive_client):
        """Pin: ``x_client_search_queryset__mutmut_5`` — Service-Flaeche.

        ``search_clients_and_events`` materialisiert dasselbe Queryset.
        Original: ``[]``; Mutant: ``[<Client: Inaktiv-01>]``.
        """
        clients, _ = search_clients_and_events(facility, staff_user, "Inaktiv-01")

        assert clients == []

    def test_group_count_does_not_advertise_an_inactive_hit(self, facility, staff_user, inactive_client):
        """Pin: ``x_client_search_queryset__mutmut_5`` — Gruppenzahl (Refs #1803, L7).

        Original: ``{"clients": 0, "events": 0}``; Mutant: ``{"clients": 1,
        "events": 0}`` — eine Ueberschrift, die einen Treffer beziffert, den
        die Liste nicht zeigt.
        """
        assert search_result_counts(facility, staff_user, "Inaktiv-01") == {"clients": 0, "events": 0}


# ============================================================================
# core.services.dashboard.search.event_search_queryset
# ============================================================================


@pytest.mark.django_db
class TestEventSearchQuerysetSkipsSoftDeletedEvents:
    """Ereignisse im Papierkorb sind nicht suchbar — unabhaengig von der Person.

    Der Bestand prueft nur ``exclude(client__is_deleted=True)``: den Papierkorb
    der PERSON. Faellt ``is_deleted=False`` aus dem Event-Filter, bleibt ein
    einzeln geloeschtes Ereignis einer weiterhin aktiven Person ueber Pseudonym
    UND Suchindex auffindbar.
    """

    @pytest.fixture
    def trashed_event(self, facility, staff_user, doc_type_contact, client_identified):
        """Nur das Ereignis liegt im Papierkorb, die Person nicht.

        ``is_deleted`` wird direkt gesetzt statt ueber den CRUD-Loeschpfad:
        ``soft_delete_event`` leert ``data_json``/``search_text`` mit, der
        Suchindex waere danach ohnehin leer und der Mutant bliebe unbemerkt.
        Das ``pre_save``-Signal ``_refresh_event_search_text`` befuellt
        ``search_text`` aus ``data_json``.
        """
        return Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Papierkorbnotiz-Geheim"},
            created_by=staff_user,
            is_deleted=True,
        )

    def test_trashed_event_is_not_in_the_queryset_on_the_search_text_branch(self, facility, staff_user, trashed_event):
        """Pin: ``x_event_search_queryset__mutmut_11`` — ``.filter(is_deleted=False,
        document_type__sensitivity__in=allowed_sens)`` →
        ``.filter(document_type__sensitivity__in=allowed_sens)``.

        Die Person ist aktiv, ``exclude(client__is_deleted=True)`` greift also
        nicht. Original: ``0``; Mutant: ``1``.
        """
        assert trashed_event.client.is_deleted is False  # nur das Ereignis ist weg
        assert event_search_queryset(facility, staff_user, "Papierkorbnotiz-Geheim").count() == 0

    def test_trashed_event_is_not_in_the_queryset_on_the_pseudonym_branch(self, facility, staff_user, trashed_event):
        """Pin: ``x_event_search_queryset__mutmut_11`` — zweiter ``Q``-Zweig.

        Das ``Q``-ODER erreicht dieselbe Zeile ueber ``client__pseudonym``.
        Original: ``0``; Mutant: ``1``.
        """
        assert event_search_queryset(facility, staff_user, "Test-ID-01").count() == 0

    def test_a_live_event_of_the_same_person_still_matches(
        self, facility, staff_user, doc_type_contact, client_identified, trashed_event
    ):
        """Positiv-Zwilling: der Suchindex-Pfad trifft — sonst waere der Pin leer."""
        live = Event.objects.create(
            facility=facility,
            client=client_identified,
            document_type=doc_type_contact,
            occurred_at=timezone.now(),
            data_json={"notiz": "Papierkorbnotiz-Sichtbar"},
            created_by=staff_user,
        )

        assert list(event_search_queryset(facility, staff_user, "Papierkorbnotiz-")) == [live]

    def test_service_returns_no_event_for_a_trashed_hit(self, facility, staff_user, trashed_event):
        """Pin: ``x_event_search_queryset__mutmut_11`` — Service-Flaeche.

        Original: ``[]``; Mutant: ``[<Event: Papierkorbnotiz-Geheim>]``.
        """
        _, events = search_clients_and_events(facility, staff_user, "Papierkorbnotiz-Geheim")

        assert events == []

    def test_group_count_does_not_advertise_a_trashed_event(self, facility, staff_user, trashed_event):
        """Pin: ``x_event_search_queryset__mutmut_11`` — Gruppenzahl (Refs #1803, L7).

        Original: ``{"clients": 0, "events": 0}``; Mutant: ``{"clients": 0,
        "events": 1}``.
        """
        assert search_result_counts(facility, staff_user, "Papierkorbnotiz-Geheim") == {"clients": 0, "events": 0}


# ── core.services.dashboard.snapshot ──────────────────────────────────────────
#
# Mutations-Pins fuer ``core.services.dashboard.snapshot`` (Staging Welle E/0.92).
#
# Quelle: ``src/core/services/dashboard/snapshot.py``; Mutantenbaum
# ``mutants/src/core/services/dashboard/snapshot.py``. Gepinnt wird das
# bestaetigte Prio-1-Set der Survivor-Triage — fuenf Mutanten in zwei
# Funktionen:
#
# * ``_merge_stats`` (mutmut_8/_48/_85/_86) — die K0-b-Mechanik
#   ``persons_by_contact_stage``. Alle vier Mutanten fuehren auf **denselben**
#   beobachtbaren Zustand: der Merge liefert fuer jede Stufe ``None``
#   ("unbekannt"), entweder weil der Akkumulator schon so startet (``_8``),
#   weil kein Segmentwert mehr gefunden wird (``_48``) oder weil das Ergebnis
#   unter einem falsch geschriebenen Schluessel abgelegt wird und der
#   All-``None``-Init aus ``_empty_stats()`` stehen bleibt (``_85``/``_86``).
#   Der bisherige Bestand prueft den Merge nur mit Segmenten ohne Personenzahl
#   und erwartet dadurch zufaellig ebenfalls All-``None`` — die Suppressions-
#   mechanik von ``external_report.py`` (``persons=raw.get(...)``) waere still
#   tot gewesen.
# * ``get_sample_report_1_statistics_hybrid`` (mutmut_30) — der K4c-Stand-
#   stempel ``SNAPSHOT_AS_OF_KEY``. Mit ``_newer(None, snapshot_as_of)`` wird
#   die Akkumulation ueber vorherige Segmente verworfen; es ueberlebt nur der
#   Stand des ZULETZT verarbeiteten Segments (und ``None``, sobald dieses live
#   ist). Der Bestand (``test_reporting_population_stamp.py``) fuhr nur
#   Einsegment-Zeitraeume und konnte den Unterschied nicht sehen.
#
# Konventionen wie ``src/tests/test_mutation_pins_091_cut.py``: Zusicherungen
# vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
# ``"XX<text>XX"``-Varianten); eine Testklasse je Funktion; jeder Test nennt
# seine Mutanten samt abweichendem Wert in der Docstring; keine Fixture-Faken
# (echte Model-Objekte); KEIN Import aus ``test_rls_functional``.


# ---------------------------------------------------------------------------
# Hilfsdaten
# ---------------------------------------------------------------------------

_STATS_KEYS = [
    "by_age_cluster",
    "by_contact_stage",
    "by_document_type",
    "persons_by_contact_stage",
    "total_contacts",
    "unique_clients",
]


def _segment_stats(*, total, anonym, identifiziert, qualifiziert, persons):
    """Ein Segment im ``get_statistics``-Format mit echten Personenzahlen."""
    return {
        "total_contacts": total,
        "by_contact_stage": {
            "anonym": anonym,
            "identifiziert": identifiziert,
            "qualifiziert": qualifiziert,
        },
        "persons_by_contact_stage": dict(persons),
        "by_document_type": [],
        "by_age_cluster": [],
    }


def _sample_report_payload(total):
    """Roh-Aggregat des ``sample-report-1``-Backends (``DatasetResult.summary``)."""
    return {
        "total": total,
        "by_category": [["Beratung", total]],
        "by_age_cluster": [{"cluster": "27_plus", "label": "27+", "count": total, "persons": total}],
        "unique_clients": total,
    }


def _snapshot_with_stand(facility, year, month, stand, *, total):
    """Snapshot mit ``sample-report-1``-Payload und festgenageltem ``updated_at``.

    ``updated_at`` ist ``auto_now`` und laesst sich per ``save()`` nicht setzen —
    deshalb der ``queryset.update()``-Nachzug.
    """
    snapshot = StatisticsSnapshot.objects.create(
        facility=facility,
        year=year,
        month=month,
        data={},
        backend_data={"sample-report-1": _sample_report_payload(total)},
    )
    StatisticsSnapshot.objects.filter(pk=snapshot.pk).update(updated_at=stand)
    snapshot.refresh_from_db()
    return snapshot


# ---------------------------------------------------------------------------
# core.services.dashboard.snapshot._merge_stats
# ---------------------------------------------------------------------------


class TestMergeStatsPersonsByContactStage:
    """K0-b: Personen je Kontaktstufe werden ueber Segmente **maximiert**.

    Kein DB-Zugriff: ``_merge_stats`` ist eine reine Dict-Funktion.
    """

    def test_maximum_statt_none_ueber_mehrere_segmente(self):
        """Pin: core.services.dashboard.snapshot.x__merge_stats__mutmut_8 —
        ``stage_persons = dict.fromkeys(_STAGE_KEYS, 0)`` →
        ``dict.fromkeys(_STAGE_KEYS, None)``.
        Pin: core.services.dashboard.snapshot.x__merge_stats__mutmut_48 —
        ``segment_stage_persons.get(key)`` → ``segment_stage_persons.get(None)``.

        Original: ``{"anonym": 5, "identifiziert": 4, "qualifiziert": 7}``
        (Maximum je Stufe, nie Summe — 3/5 waeren summiert 8).
        mutmut_8: ``_merge_person_count(None, value)`` liefert sofort ``None``,
        das Ergebnis ist ``{"anonym": None, "identifiziert": None,
        "qualifiziert": None}``.
        mutmut_48: ``.get(None)`` findet nie einen Segmentwert, also ebenfalls
        durchgehend ``None``.
        """
        segment_a = _segment_stats(
            total=9,
            anonym=4,
            identifiziert=3,
            qualifiziert=2,
            persons={"anonym": 3, "identifiziert": 4, "qualifiziert": 7},
        )
        segment_b = _segment_stats(
            total=6,
            anonym=2,
            identifiziert=3,
            qualifiziert=1,
            persons={"anonym": 5, "identifiziert": 2, "qualifiziert": 6},
        )

        merged = _merge_stats([segment_a, segment_b])

        assert merged["persons_by_contact_stage"] == {
            "anonym": 5,
            "identifiziert": 4,
            "qualifiziert": 7,
        }
        # Gegenprobe: die Ereigniszahlen werden sehr wohl summiert — der
        # Unterschied zwischen beiden Regeln ist der Kern von K4b/K0-b.
        assert merged["by_contact_stage"] == {"anonym": 6, "identifiziert": 6, "qualifiziert": 3}
        assert merged["total_contacts"] == 15

    def test_ergebnis_steht_unter_dem_exakten_schluessel(self):
        """Pin: core.services.dashboard.snapshot.x__merge_stats__mutmut_85 —
        ``merged["persons_by_contact_stage"]`` → ``merged["XXpersons_by_contact_stageXX"]``.
        Pin: core.services.dashboard.snapshot.x__merge_stats__mutmut_86 —
        ``merged["persons_by_contact_stage"]`` → ``merged["PERSONS_BY_CONTACT_STAGE"]``.

        Original: ``sorted(merged)`` enthaelt genau die sechs Felder aus
        ``_empty_stats()``, und ``merged["persons_by_contact_stage"]`` traegt
        ``{"anonym": 2, "identifiziert": 6, "qualifiziert": 1}``.
        mutmut_85/_86: das Ergebnis landet unter ``"XXpersons_by_contact_stageXX"``
        bzw. ``"PERSONS_BY_CONTACT_STAGE"`` — ``sorted(merged)`` hat sieben
        Eintraege, und unter dem echten Schluessel steht der All-``None``-Init
        aus ``_empty_stats()``. Konsumenten wie ``external_report.py``
        (``persons=raw.get("persons_by_contact_stage")``) laesen damit immer
        ``None``.
        """
        segment = _segment_stats(
            total=4,
            anonym=1,
            identifiziert=2,
            qualifiziert=1,
            persons={"anonym": 2, "identifiziert": 6, "qualifiziert": 1},
        )

        merged = _merge_stats([segment])

        assert sorted(merged) == _STATS_KEYS
        assert merged["persons_by_contact_stage"] == {
            "anonym": 2,
            "identifiziert": 6,
            "qualifiziert": 1,
        }


# ---------------------------------------------------------------------------
# core.services.dashboard.snapshot.get_sample_report_1_statistics_hybrid
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSampleReport1HybridStandStempel:
    """K4c: Der Stempel ist der **juengste** Stand ueber ALLE Snapshot-Segmente."""

    def test_juengster_stand_gewinnt_ueber_segmentreihenfolge(self, facility):
        """Pin: core.services.dashboard.snapshot.x_get_sample_report_1_statistics_hybrid__mutmut_30 —
        ``as_of = _newer(as_of, snapshot_as_of)`` → ``as_of = _newer(None, snapshot_as_of)``.

        Zwei Snapshot-Segmente, deren ``updated_at`` bewusst NICHT der
        Monatsreihenfolge folgt: der Januar-Snapshot wurde spaeter neu
        berechnet als der Februar-Snapshot.
        Original: ``merged[SNAPSHOT_AS_OF_KEY]`` == Stand des Januar-Snapshots
        (2026-03-05 09:00 UTC), das Maximum ueber beide Segmente.
        mutmut_30: die Akkumulation wird je Runde verworfen, uebrig bleibt der
        Stand des zuletzt verarbeiteten Segments — 2026-02-01 08:00 UTC.
        """
        stand_januar = datetime(2026, 3, 5, 9, 0, tzinfo=UTC)
        stand_februar = datetime(2026, 2, 1, 8, 0, tzinfo=UTC)
        _snapshot_with_stand(facility, 2026, 1, stand_januar, total=3)
        _snapshot_with_stand(facility, 2026, 2, stand_februar, total=5)

        merged = get_sample_report_1_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 2, 28))

        assert merged[SNAPSHOT_AS_OF_KEY] == stand_januar
        # Beide Segmente sind wirklich aus Aggregaten gekommen (sonst waere der
        # Stempel trivial): die Ereigniszahl ist die Summe beider Payloads.
        assert merged["total"] == 8

    def test_live_schlusssegment_loescht_den_stempel_nicht(self, facility):
        """Pin: core.services.dashboard.snapshot.x_get_sample_report_1_statistics_hybrid__mutmut_30 —
        zweiter Kill-Vektor desselben Mutanten (``_newer(None, ...)``).

        Nur der Januar hat einen Snapshot; der Februar faellt auf den
        Live-Zweig zurueck und traegt ``snapshot_as_of=None``.
        Original: ``merged[SNAPSHOT_AS_OF_KEY]`` == Januar-Stand
        (2026-03-05 09:00 UTC) — ``_newer`` haelt den akkumulierten Wert, wenn
        der Kandidat ``None`` ist.
        mutmut_30: ``_newer(None, None)`` fuer das letzte Segment ⇒ der Stempel
        ist ``None``, das Artefakt verliert seine Herkunft.
        """
        stand_januar = datetime(2026, 3, 5, 9, 0, tzinfo=UTC)
        _snapshot_with_stand(facility, 2026, 1, stand_januar, total=3)

        merged = get_sample_report_1_statistics_hybrid(facility, date(2026, 1, 1), date(2026, 2, 28))

        assert merged[SNAPSHOT_AS_OF_KEY] == stand_januar
        # Der Februar kam live aus einer leeren Datenbank — nur der Januar-
        # Payload traegt zur Summe bei.
        assert merged["total"] == 3


# ── core.services.events.feed ─────────────────────────────────────────────────
#
# Pin-Tests fuer die Prio-1-Testluecken in ``core.services.events.feed``.
#
# Quelle: Survivor-Triage des ``mutation-delta``-Laufs zum Checkpoint 0.92.
# Modul unter Test: :mod:`core.services.events.feed` (``src/core/services/
# events/feed.py``), Mutantenbaum ``mutants/src/core/services/events/feed.py``.
#
# Gepinnt werden fuenf Mutanten aus drei Funktionen — alle drei Luecken sind
# Sichtbarkeits-/Mandantenluecken, keine Kosmetik:
#
# * ``_feed_events`` — ``is_deleted=False`` im Zeitstrom-Eventzweig.
#   ``Event.objects`` hat KEINEN Default-Filter auf ``is_deleted``, und
#   ``visible_to()`` filtert ausschliesslich nach Sensitivity. Ohne das
#   Praedikat laufen Papierkorb-Ereignisse mit Titel, Personenlink und
#   Vorschau wieder durch den Zeitstrom (ADR-044 §7, Refs #1818).
# * ``_feed_bans`` — ``facility=facility`` und ``is_deleted=False`` im
#   Hausverbots-Zweig. Ohne das ``facility``-Praedikat liefert der Zweig
#   Hausverbote FREMDER Einrichtungen aus (Mandantenbruch auf App-Ebene;
#   RLS traegt hier nicht, siehe ``visible_to``-Docstring).
# * ``_build_event_preview_fields`` — das dritte Argument von
#   ``user_can_see_field``. ``effective_sensitivity`` wertet
#   ``field_sensitivity`` nur ueber ``if field_sensitivity:`` aus; sowohl
#   ``None`` (mutmut_9) als auch das weggelassene Argument mit Default ``""``
#   (mutmut_12) lassen den Feld-Level-Override lautlos fallen. Ein
#   ELEVATED/HIGH-Feld auf einem NORMAL-Dokumentationstyp wuerde damit in der
#   Feed-Vorschau fuer zu niedrige Rollen sichtbar.
#
# Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen
# vergleichen EXAKT (``==``), nie per Teilstring (mutmut erzeugt
# ``"XX<text>XX"``-Varianten); Sprachkontext wird bei Textzusicherungen per
# ``translation.override("de")`` festgenagelt; KEINE Imports aus
# ``test_rls_functional``; keine Fixture-Faken — alle Objekte sind echte
# Model-Instanzen. Jeder Test nennt seine Mutanten in der Docstring und sagt,
# welcher Wert unter dem Mutanten abweicht.


def _noon(target_date):
    """Aware Datetime mitten im Tagesfenster von ``get_time_range``."""
    return timezone.make_aware(datetime.combine(target_date, time(12, 0)))


def _make_event(facility, doc_type, user, when, data=None, *, client=None):
    return Event.objects.create(
        facility=facility,
        client=client,
        document_type=doc_type,
        occurred_at=when,
        data_json=data or {},
        created_by=user,
    )


def _trash(event):
    """Event in den Papierkorb legen, ohne ``save()``-Nebenwirkungen."""
    Event.objects.filter(pk=event.pk).update(is_deleted=True)


def _ban_doc_type(facility, name):
    return DocumentType.objects.create(
        facility=facility,
        name=name,
        sensitivity=DocumentType.Sensitivity.ELEVATED,
        system_type=DocumentType.SystemType.BAN,
    )


# ============================================================================
# _feed_events — Papierkorb-Ereignisse (1 Mutant)
# ============================================================================


@pytest.mark.django_db
class TestFeedEventsExcludesTrashedEvents:
    """Der Eventzweig des Zeitstroms zeigt keine Papierkorb-Ereignisse.

    Pin: core.services.events.feed.x__feed_events__mutmut_7 —
    ``filter(facility=…, is_deleted=False, occurred_at__gte=…)`` verliert das
    Praedikat ``is_deleted=False``.

    ``Event.objects`` filtert ``is_deleted`` NICHT von sich aus und
    ``visible_to()`` sieht nur die Sensitivity des Dokumentationstyps an —
    das Praedikat in ``_feed_events`` ist die einzige Stelle, die geloeschte
    Ereignisse aus dem Zeitstrom haelt.

    Abweichung unter dem Mutanten: die Trefferliste enthaelt zusaetzlich die
    PK des Papierkorb-Ereignisses, also zwei statt einer PK.
    """

    def test_trashed_event_is_absent_from_the_events_feed(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        today = timezone.localdate()
        when = _noon(today)
        aktiv = _make_event(facility, doc_type_contact, staff_user, when, {"dauer": 15}, client=client_identified)
        geloescht = _make_event(facility, doc_type_contact, staff_user, when, {"dauer": 30}, client=client_identified)
        _trash(geloescht)

        items = build_feed_items(facility, today, feed_type="events", user=staff_user)

        assert [item["object"].pk for item in items] == [aktiv.pk]

    def test_trashed_event_is_absent_from_the_mixed_feed(
        self, facility, staff_user, doc_type_contact, client_identified
    ):
        """Derselbe Mutant ueber den Sammel-Zweig (``feed_type='all'``)."""
        today = timezone.localdate()
        when = _noon(today)
        aktiv = _make_event(facility, doc_type_contact, staff_user, when, {"dauer": 15}, client=client_identified)
        geloescht = _make_event(facility, doc_type_contact, staff_user, when, {"dauer": 30}, client=client_identified)
        _trash(geloescht)

        items = build_feed_items(facility, today, feed_type="all", user=staff_user)

        assert [item["object"].pk for item in items if item["type"] == "event"] == [aktiv.pk]


# ============================================================================
# _feed_bans — Mandantengrenze und Papierkorb (2 Mutanten)
# ============================================================================


@pytest.mark.django_db
class TestFeedBansStayInsideTheOwnFacility:
    """Der Hausverbots-Zweig liefert nur Hausverbote der eigenen Einrichtung.

    Pin: core.services.events.feed.x__feed_bans__mutmut_14 —
    ``filter(facility=facility, …)`` verliert das ``facility``-Praedikat.

    ``Event.objects.visible_to(user)`` filtert ausschliesslich nach
    Sensitivity, nicht nach Einrichtung; beide Hausverbote hier sind
    ELEVATED und damit fuer die Fachkraft-Rolle sichtbar. Das
    ``facility``-Praedikat ist also die einzige Mandantengrenze des Zweigs.

    Abweichung unter dem Mutanten: die Trefferliste enthaelt zusaetzlich die
    PK des Hausverbots der zweiten Einrichtung.
    """

    def test_foreign_facility_ban_never_appears(
        self, facility, staff_user, client_identified, second_facility, second_facility_user
    ):
        today = timezone.localdate()
        when = _noon(today)
        eigener = _make_event(
            facility,
            _ban_doc_type(facility, "Hausverbot"),
            staff_user,
            when,
            {"grund": "eigen"},
            client=client_identified,
        )
        fremder = _make_event(
            second_facility,
            _ban_doc_type(second_facility, "Hausverbot (fremd)"),
            second_facility_user,
            when,
            {"grund": "fremd"},
        )

        items = build_feed_items(facility, today, feed_type="bans", user=staff_user)

        assert {item["object"].pk for item in items} == {eigener.pk}
        assert fremder.facility_id != facility.pk

    def test_foreign_facility_ban_never_appears_in_the_mixed_feed(
        self, facility, staff_user, client_identified, second_facility, second_facility_user
    ):
        """Derselbe Mutant ueber den Sammel-Zweig (``feed_type='all'``)."""
        today = timezone.localdate()
        when = _noon(today)
        eigener = _make_event(
            facility,
            _ban_doc_type(facility, "Hausverbot"),
            staff_user,
            when,
            {"grund": "eigen"},
            client=client_identified,
        )
        _make_event(
            second_facility,
            _ban_doc_type(second_facility, "Hausverbot (fremd)"),
            second_facility_user,
            when,
            {"grund": "fremd"},
        )

        items = build_feed_items(facility, today, feed_type="all", user=staff_user)

        assert {item["object"].pk for item in items if item["type"] == "ban"} == {eigener.pk}


@pytest.mark.django_db
class TestFeedBansExcludeTrashedBans:
    """Soft-geloeschte Hausverbote bleiben aus dem Zeitstrom draussen.

    Pin: core.services.events.feed.x__feed_bans__mutmut_15 —
    ``filter(facility=…, is_deleted=False, …)`` verliert das Praedikat
    ``is_deleted=False``.

    ADR-044 §7 / Refs #1818 nehmen Geloeschtes aus dem Zeitstrom; das
    Hausverbot ist die sensibelste Ereignisart darin. Weder ``Event.objects``
    noch ``visible_to()`` filtern ``is_deleted``.

    Abweichung unter dem Mutanten: die Trefferliste enthaelt zusaetzlich die
    PK des geloeschten Hausverbots, also zwei statt einer PK.
    """

    def test_trashed_ban_is_absent_from_the_bans_feed(self, facility, staff_user, client_identified):
        today = timezone.localdate()
        when = _noon(today)
        doc_type = _ban_doc_type(facility, "Hausverbot")
        aktiv = _make_event(facility, doc_type, staff_user, when, {"grund": "aktiv"}, client=client_identified)
        geloescht = _make_event(facility, doc_type, staff_user, when, {"grund": "geloescht"}, client=client_identified)
        _trash(geloescht)

        items = build_feed_items(facility, today, feed_type="bans", user=staff_user)

        assert [item["object"].pk for item in items] == [aktiv.pk]

    def test_trashed_ban_is_absent_from_the_mixed_feed(self, facility, staff_user, client_identified):
        """Derselbe Mutant ueber den Sammel-Zweig (``feed_type='all'``).

        Der Eventzweig kann hier nicht einspringen: ``_feed_events`` schliesst
        ``system_type='ban'`` im Sammel-Modus ausdruecklich aus.
        """
        today = timezone.localdate()
        when = _noon(today)
        doc_type = _ban_doc_type(facility, "Hausverbot")
        aktiv = _make_event(facility, doc_type, staff_user, when, {"grund": "aktiv"}, client=client_identified)
        geloescht = _make_event(facility, doc_type, staff_user, when, {"grund": "geloescht"}, client=client_identified)
        _trash(geloescht)

        items = build_feed_items(facility, today, feed_type="all", user=staff_user)

        assert [item["object"].pk for item in items] == [aktiv.pk]


# ============================================================================
# _build_event_preview_fields — Feld-Level-Sensibilitaet (2 Mutanten)
# ============================================================================


def _doc_type_with_field_level_override(facility, *, field_sensitivity):
    """NORMALer Dokumentationstyp mit einem normalen und einem strengeren Feld.

    Der Dokumentationstyp selbst ist NORMAL — nur ``FieldTemplate.sensitivity``
    hebt das zweite Feld an. Genau diese Achse faellt unter den Mutanten weg.
    """
    doc_type = DocumentType.objects.create(
        facility=facility,
        name="Kontaktbogen",
        sensitivity=DocumentType.Sensitivity.NORMAL,
    )
    ft_offen = FieldTemplate.objects.create(
        facility=facility,
        name="Anwesend",
        field_type=FieldTemplate.FieldType.TEXT,
    )
    ft_streng = FieldTemplate.objects.create(
        facility=facility,
        name="Schutzhinweis",
        field_type=FieldTemplate.FieldType.TEXT,
        sensitivity=field_sensitivity,
    )
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_offen, sort_order=0)
    DocumentTypeField.objects.create(document_type=doc_type, field_template=ft_streng, sort_order=1)
    return doc_type, ft_offen, ft_streng


@pytest.mark.django_db
class TestPreviewFieldsHonourFieldLevelSensitivity:
    """Die Feed-Vorschau respektiert den Feld-Level-Override der Sensibilitaet.

    Pins:

    * core.services.events.feed.x__build_event_preview_fields__mutmut_9 —
      ``user_can_see_field(user, doc_sensitivity, ft.sensitivity)`` wird zu
      ``user_can_see_field(user, doc_sensitivity, None)``.
    * core.services.events.feed.x__build_event_preview_fields__mutmut_12 —
      dasselbe ohne drittes Argument (Default ``field_sensitivity=""``).

    Beide Ruempfe sind wirkungsgleich: ``effective_sensitivity`` prueft
    ``if field_sensitivity:``; ``None`` wie ``""`` sind falsy, der Rang des
    Feldes wird nie gegen den des Dokumentationstyps gemaxt. Der
    Dokumentationstyp ist hier NORMAL (Rang 0), das Feld ERHOEHT (Rang 1),
    die Assistenz darf hoechstens Rang 0 sehen.

    Abweichung unter beiden Mutanten: ``preview_fields``/``expanded_fields``
    der Assistenz enthalten zusaetzlich das Label ``"Schutzhinweis"`` —
    ``["Anwesend", "Schutzhinweis"]`` statt ``["Anwesend"]``.
    """

    def test_elevated_field_is_hidden_from_the_assistant_preview(self, facility, assistant_user, client_identified):
        doc_type, _ft_offen, _ft_streng = _doc_type_with_field_level_override(
            facility, field_sensitivity=DocumentType.Sensitivity.ELEVATED
        )
        event = _make_event(
            facility,
            doc_type,
            assistant_user,
            timezone.now(),
            {"anwesend": "ja, mit Begleitung", "schutzhinweis": "Kontaktverbot Person X"},
            client=client_identified,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]

        with translation.override("de"):
            enrich_events_with_preview(feed_items, assistant_user)

            assert [f["label"] for f in event.preview_fields] == ["Anwesend"]
            assert [f["value"] for f in event.preview_fields] == ["ja, mit Begleitung"]
            assert [f["label"] for f in event.expanded_fields] == ["Anwesend"]

    def test_elevated_field_stays_visible_for_the_staff_counterprobe(self, facility, staff_user, client_identified):
        """Gegenprobe: der Test oben darf nicht bloss „nichts sichtbar" pinnen."""
        doc_type, _ft_offen, _ft_streng = _doc_type_with_field_level_override(
            facility, field_sensitivity=DocumentType.Sensitivity.ELEVATED
        )
        event = _make_event(
            facility,
            doc_type,
            staff_user,
            timezone.now(),
            {"anwesend": "ja, mit Begleitung", "schutzhinweis": "Kontaktverbot Person X"},
            client=client_identified,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]

        with translation.override("de"):
            enrich_events_with_preview(feed_items, staff_user)

            assert [f["label"] for f in event.preview_fields] == ["Anwesend", "Schutzhinweis"]
            assert [f["value"] for f in event.preview_fields] == [
                "ja, mit Begleitung",
                "Kontaktverbot Person X",
            ]

    def test_helper_called_directly_skips_the_elevated_field_for_the_assistant(
        self, facility, assistant_user, staff_user, client_identified
    ):
        """Direkter Unit-Test auf ``_build_event_preview_fields`` (mutmut_9/12).

        Ohne den Umweg ueber ``enrich_events_with_preview``: die Feldliste
        wird als Parameter uebergeben, die Rollen-Achse bleibt die einzige
        Variable.
        """
        doc_type, ft_offen, ft_streng = _doc_type_with_field_level_override(
            facility, field_sensitivity=DocumentType.Sensitivity.ELEVATED
        )
        event = _make_event(
            facility,
            doc_type,
            staff_user,
            timezone.now(),
            {"anwesend": "ja", "schutzhinweis": "Kontaktverbot Person X"},
            client=client_identified,
        )

        with translation.override("de"):
            preview, expanded = _build_event_preview_fields(event, [ft_offen, ft_streng], assistant_user)

            assert [f["label"] for f in preview] == ["Anwesend"]
            assert [f["label"] for f in expanded] == ["Anwesend"]

            preview_staff, expanded_staff = _build_event_preview_fields(event, [ft_offen, ft_streng], staff_user)

            assert [f["label"] for f in preview_staff] == ["Anwesend", "Schutzhinweis"]
            assert [f["label"] for f in expanded_staff] == ["Anwesend", "Schutzhinweis"]

    def test_high_field_is_hidden_from_staff_but_visible_for_the_lead(
        self, facility, staff_user, lead_user, client_identified, _encryption_key
    ):
        """Zweite Stufe desselben Overrides: HOCH auf NORMALem Dokumentationstyp.

        Pins dieselben beiden Mutanten mit der Rollen-Grenze eine Stufe hoeher
        (Fachkraft Rang 1 gegen Feld-Rang 2). ``FieldTemplate.save()`` erzwingt
        bei HOCH ``is_encrypted`` (Refs #1270 T5), deshalb der Fernet-Key und
        Zusicherungen nur auf den Labels — der Wert ist ein Token.

        Abweichung unter beiden Mutanten: die Fachkraft sieht zusaetzlich
        ``"Schutzhinweis"``.
        """
        doc_type, _ft_offen, _ft_streng = _doc_type_with_field_level_override(
            facility, field_sensitivity=DocumentType.Sensitivity.HIGH
        )
        event = _make_event(
            facility,
            doc_type,
            staff_user,
            timezone.now(),
            {"anwesend": "ja", "schutzhinweis": "Art.-9-Angabe"},
            client=client_identified,
        )
        feed_items = [{"type": "event", "occurred_at": event.occurred_at, "object": event}]

        with translation.override("de"):
            enrich_events_with_preview(feed_items, staff_user)
            assert [f["label"] for f in event.preview_fields] == ["Anwesend"]

            enrich_events_with_preview(feed_items, lead_user)
            assert [f["label"] for f in event.preview_fields] == ["Anwesend", "Schutzhinweis"]


# ── core.services.events.tally ────────────────────────────────────────────────
#
# Pin-Tests der Prio-1-Testluecken in ``core.services.events.tally`` (Welle E, 0.92).
#
# Quelle: Survivor-Triage des ``mutation-delta``-Laufs zum 0.92-Checkpoint.
# Modul unter Test: ``src/core/services/events/tally.py`` — genauer die Funktion
# ``tally_request_matches_live_context`` (Quellzeilen ~275–320), die den
# Schreibpfad der anonymen Strichlisten-Increments gated
# (``core/views/events.py`` ~Zeile 679). Ihr Vertrag: nur Parameter, die die
# LIVE-Erfassung meinen, werden akzeptiert — "Abweichende Parameter werden
# abgewiesen statt als Nacherfassung zu wirken".
#
# Gepinnt sind sechs bestaetigte Survivors, alle in dieser einen Funktion:
#
# * ``__mutmut_18`` — unvollstaendiges Fenster-Markerpaar wird akzeptiert;
# * ``__mutmut_23`` — unparsbarer Fenster-Marker wird akzeptiert;
# * ``__mutmut_24`` — ``or`` → ``and`` in der Naivitaetspruefung: ein einzelner
#   naiver Marker passiert die Pruefung und wird unter Server-Systemzeitzone
#   interpretiert;
# * ``__mutmut_27`` — zwei naive Marker beenden die Funktion sofort mit ``True``,
#   ganz OHNE Fenstervergleich (schwerster Fund des Sets);
# * ``__mutmut_56`` — ``parsed_date = None`` → ``""``: die live gemeinte Anfrage
#   ``{"time_filter": "all"}`` ohne ``date`` wird faelschlich abgelehnt;
# * ``__mutmut_59`` — unparsbares ``date`` wird akzeptiert.
#
# Konventionen wie ``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen
# EXAKT (nie per Teilstring — mutmut erzeugt ``"XX<text>XX"``-Varianten); es
# werden echte Model-Objekte (Fixture ``facility`` aus ``tests/conftest.py``)
# benutzt, keine Fakes; KEIN Import aus ``test_rls_functional`` (its fixture
# ``rls_test_role`` skips itself under ``MUTANT_UNDER_TEST``; a pin requesting it never
# runs in a mutation run). Ein
# ``translation.override`` ist hier nicht noetig: die Funktion liefert
# ausschliesslich ``bool``, keine uebersetzten Texte.
#
# Alle Tests reichen den ``TallyLiveContext`` explizit herein. Damit ist das
# Ergebnis unabhaengig von der Uhrzeit des Laufs, und die Kurzschluss-Auswertung
# ``context or current_tally_context(facility)`` haelt die Pins frei von
# Zusatz-Queries.


BERLIN = ZoneInfo("Europe/Berlin")

# Live-Nachtschicht 21.06.2026 22:00 → 22.06.2026 08:00 (Berliner Sommerzeit).
LIVE_WINDOW_START = datetime(2026, 6, 21, 22, 0, tzinfo=BERLIN)
LIVE_WINDOW_END = datetime(2026, 6, 22, 8, 0, tzinfo=BERLIN)


def _live_context(*, window_start=LIVE_WINDOW_START, window_end=LIVE_WINDOW_END):
    """Live-Kontext ohne Schichtfilter (``all``-Fall) mit gesetztem Fenster."""
    return TallyLiveContext(
        time_filter=None,
        shift_date=date(2026, 6, 21),
        window_start=window_start,
        window_end=window_end,
    )


@pytest.mark.django_db
class TestTallyRequestMatchesLiveContext:
    """Negativ- und Positivpfade des Live-Gates der Strichliste."""

    def test_half_window_marker_pair_is_rejected(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_18
        — ``if not marker_start or not marker_end: return False`` → ``return True``.

        Schickt der Client nur EINEN der beiden Fenster-Marker, fehlt dem
        Server die zweite Haelfte des Vergleichs. Das Original weist ab
        (``False``); der Mutant liefert fuer beide Haelften ``True`` und
        laesst die Anfrage ohne jeden Fenstervergleich als live durch.
        """
        context = _live_context()

        only_start = tally_request_matches_live_context(
            facility,
            {"tally_window_start": LIVE_WINDOW_START.isoformat()},
            context=context,
        )
        only_end = tally_request_matches_live_context(
            facility,
            {"tally_window_end": LIVE_WINDOW_END.isoformat()},
            context=context,
        )
        empty_start = tally_request_matches_live_context(
            facility,
            {"tally_window_start": "", "tally_window_end": LIVE_WINDOW_END.isoformat()},
            context=context,
        )

        assert only_start is False
        assert only_end is False
        assert empty_start is False

    def test_unparsable_window_marker_is_rejected(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_23
        — ``except (TypeError, ValueError): return False`` → ``return True``.

        ``datetime.fromisoformat("kaputt")`` wirft ``ValueError``, ebenso der
        syntaktisch wohlgeformte, aber unmoegliche 32. Juni. Das Original
        wertet das als Abweisung (``False``); der Mutant macht aus dem
        Parse-Fehler ein Live-Match (``True``) — ein Validierungs-Bypass, der
        den Fenstervergleich komplett ueberspringt.
        """
        context = _live_context()

        broken_start = tally_request_matches_live_context(
            facility,
            {"tally_window_start": "kaputt", "tally_window_end": LIVE_WINDOW_END.isoformat()},
            context=context,
        )
        broken_end = tally_request_matches_live_context(
            facility,
            {"tally_window_start": LIVE_WINDOW_START.isoformat(), "tally_window_end": "2026-06-32T08:00:00+02:00"},
            context=context,
        )

        assert broken_start is False
        assert broken_end is False

    def test_single_naive_window_marker_is_rejected(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_24
        — ``parsed_start.utcoffset() is None or parsed_end...`` → ``... and ...``.

        Ist nur EIN Marker naiv, greift beim Mutanten die Ablehnung nicht mehr:
        der naive Wert wird per ``astimezone(UTC)`` unter der Systemzeitzone des
        Servers gedeutet. Der Kontext ist hier bewusst so gesetzt, dass genau
        diese Deutung auf das Live-Fenster faellt — der Mutant laeuft damit
        durch bis ``return True``, das Original bleibt bei ``False``.
        """
        naive_start = datetime(2026, 6, 21, 22, 0)
        naive_end = datetime(2026, 6, 22, 8, 0)

        naive_start_only = tally_request_matches_live_context(
            facility,
            {
                "tally_window_start": naive_start.isoformat(),
                "tally_window_end": LIVE_WINDOW_END.isoformat(),
            },
            context=_live_context(window_start=naive_start.astimezone(UTC)),
        )
        naive_end_only = tally_request_matches_live_context(
            facility,
            {
                "tally_window_start": LIVE_WINDOW_START.isoformat(),
                "tally_window_end": naive_end.isoformat(),
            },
            context=_live_context(window_end=naive_end.astimezone(UTC)),
        )

        assert naive_start_only is False
        assert naive_end_only is False

    def test_two_naive_window_markers_do_not_short_circuit_to_live(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_27
        — ``if parsed_start.utcoffset() is None or ...: return False`` → ``return True``.

        Beim Mutanten genuegen zwei beliebige naive Zeitstempel, um das Gate
        SOFORT zu passieren — der Fenstervergleich wird nie erreicht. Die hier
        uebergebenen Marker (2020) liegen Jahre neben dem Live-Fenster; das
        Original antwortet ``False``, der Mutant ``True``.
        """
        result = tally_request_matches_live_context(
            facility,
            {
                "tally_window_start": "2020-01-01T00:00:00",
                "tally_window_end": "2020-01-01T01:00:00",
            },
            context=_live_context(),
        )

        assert result is False

    def test_time_filter_all_without_date_is_live(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_56
        — ``parsed_date = None`` → ``parsed_date = ""``.

        ``{"time_filter": "all"}`` ohne ``date`` ist der Normalfall der
        Strichlisten-Ansicht ohne Schichtfilter: kein Datum verlangt, also
        live. Das Original endet in ``return parsed_date is None or ...``
        mit ``True``; beim Mutanten ist ``parsed_date == ""`` weder ``None``
        noch das heutige Datum — er liefert ``False`` und wuerde eine gueltige
        Live-Erfassung abweisen.
        """
        result = tally_request_matches_live_context(
            facility,
            {"time_filter": "all"},
            context=_live_context(),
        )

        assert result is True

    def test_unparsable_date_parameter_is_rejected(self, facility):
        """Pin: core.services.events.tally.x_tally_request_matches_live_context__mutmut_59
        — ``except ValueError: return False`` → ``return True`` (Parsen von ``params["date"]``).

        ``date.fromisoformat("kaputt")`` wirft ``ValueError``. Das Original
        weist die Anfrage ab (``False``); der Mutant erklaert den Parse-Fehler
        zum Live-Match (``True``) und laesst so einen beliebigen, nie
        geprueften Datumsparameter in den Schreibpfad.
        """
        result = tally_request_matches_live_context(
            facility,
            {"date": "kaputt", "time_filter": "all"},
            context=_live_context(),
        )

        assert result is False
