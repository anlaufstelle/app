"""Mutations-Pins zum Meilenstein release-0.94 — Modul-Batch ``snapshot``.

Quelle: ``src/core/services/dashboard/snapshot.py``; Mutantenbaum
``mutants/src/core/services/dashboard/snapshot.py``
(Ruempfe ``x_<fn>__mutmut_orig`` vs. ``x_<fn>__mutmut_N``). Herkunft der
Triage: die Survivor-Triage release-0.94 (Ledger des Meilensteins) — 27 triagierte, killbare
Testluecken aus dem 0.92er Survivor-Bestand, davon der groesste Teil
adversarial gegengeprueft (``refutiert: true``, d. h. der urspruengliche
Aequivalenz-/Kosmetik-Verdacht wurde widerlegt).

Gepinnte Schwerpunkte:

* die **Personen-Untergrenzen** des Merges (``_accumulate_age_cluster``,
  ``_merge_stats``): ein um 1 zu hoher Startwert hebt eine Kleinstzelle ueber
  die k-Anon-Schwelle und publiziert, was unterdrueckt gehoerte (K0-b/K4b,
  Refs #1770);
* die **Jahresklammer** der Snapshot-Auswahl in ``_iter_segments`` — ohne sie
  liefert ein Monatssegment Daten eines fremden Jahres;
* die **Pseudonym-Rangliste** ``top_clients``, die weder aus einem Trend-
  Segment noch aus einem persistierten Snapshot herausfallen darf, und die
  Toleranz gegen Payloads, die den Schluessel gar nicht fuehren;
* die **Alt-Snapshot-Toleranz** von ``_merge_sample_report_1_stats``
  (fehlende Top-Level-Schluessel duerfen kein ``TypeError`` und keine stille
  Falschzahl erzeugen);
* der **Metadaten-Filter** von ``aggregate_trend_segments`` (``label``/
  ``source`` gehen nie in den Merge-Input);
* die **Log-Vertraege** von ``create_or_update_snapshot`` (Refs #2070): der
  Backend-Ausfall wird laut und vollstaendig gemeldet, der Erfolgs-Eintrag
  traegt die Einrichtung — beides ist im Monats-Cron die einzige Spur je
  Einrichtung;
* der **Negativzweig** der ``document_type_id``-Anreicherung (kein passender
  ``DocumentType`` -> leerer String, nie ein Platzhalter).

Konventionen wie ``test_mutation_pins_093_import_engine.py`` und
``test_mutation_pins_091_cut.py``: Zusicherungen vergleichen EXAKT (``==``
bzw. Element-Gleichheit in einer Liste), nie per Teilstring — mutmut erzeugt
``"XX<text>XX"``-Varianten, die ein Teilstring-Assert ueberlebt; Sprachkontext
wird bei Textzusicherungen per ``translation.override("de")`` festgenagelt;
``pytest.mark.django_db`` nur, wo wirklich eine Datenbank noetig ist; Fixtures
kommen aus ``src/tests/conftest.py`` bzw. werden als echte Model-Objekte
angelegt (keine Model-Fakes); KEIN Import aus ``test_rls_functional``. Jeder
Test nennt seine Mutanten in der Docstring samt dem Wert, der beim Mutanten
abweicht.
"""

import copy
import logging
from datetime import date

import pytest
from django.utils import translation

from core.models import StatisticsSnapshot
from core.reporting.base import DatasetBackend, DatasetResult, FieldSpec
from core.reporting.registry import register_backend
from core.services.dashboard import snapshot as snapshot_mod
from core.services.dashboard.statistics import PERSONS_KEY
from tests.conftest import isolate_reporting_registry

MODUL_LOGGER = "core.services.dashboard.snapshot"


def _stats_payload(**overrides):
    """Ein vollstaendiger ``get_statistics``-Rueckgabewert als frisches Dict.

    ``create_or_update_snapshot`` mutiert das Dict in place (``pop``/Anreicherung),
    deshalb je Aufruf ein neues Objekt.
    """
    payload = {
        "total_contacts": 1,
        "by_contact_stage": {"anonym": 0, "identifiziert": 1, "qualifiziert": 0},
        "persons_by_contact_stage": {"anonym": 0, "identifiziert": 1, "qualifiziert": 0},
        "by_document_type": [],
        "by_age_cluster": [],
        "top_clients": [],
        "unique_clients": 1,
    }
    payload.update(overrides)
    return payload


class _PinBasisBackend(DatasetBackend):
    """Minimales Testdouble — kein Model-Fake, sondern ein echtes Backend."""

    name = "Pin-Testdouble"

    def get_field_specs(self):
        return [FieldSpec(key="wert", label="Wert")]

    def build_dataset(self, facility, date_from, date_to):
        return DatasetResult(mode="rows", records=[], summary={}, completeness=[])


# ============================================================================
# _accumulate_age_cluster — Personen-Startwert je Cluster
# ============================================================================


class TestAccumulateAgeClusterStartwert:
    """``_accumulate_age_cluster``: der Default des Personen-Akkumulators ist 0."""

    def test_erster_cluster_eintrag_mit_null_personen_bleibt_null(self):
        """Pin: ``x__accumulate_age_cluster__mutmut_13`` — ``age_persons.get(cluster, 0)`` -> ``get(cluster, 1)``.

        Der Default greift nur beim **ersten** Auftreten eines Clusters. Traegt
        dieser erste Eintrag ``persons=0``, rechnet das Original
        ``max(0, 0) == 0``, der Mutant ``max(1, 0) == 1`` — eine um 1 zu hohe
        Personenzahl. Genau diese Zahl ist Suppressions-Eingabe des Sachbericht-
        PDF-Pfads (``require_person_counts``/``_suppress_small``), eine
        Kleinstzelle mit 0 Personen wuerde damit auf 1 gehoben.

        Abweichung beim Mutanten: ``by_age_cluster[0]["persons"] == 1``.
        """
        ergebnis = snapshot_mod._merge_stats(
            [
                {
                    "total_contacts": 2,
                    "by_contact_stage": {"anonym": 2, "identifiziert": 0, "qualifiziert": 0},
                    "persons_by_contact_stage": {"anonym": 2, "identifiziert": 0, "qualifiziert": 0},
                    "by_document_type": [],
                    "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 2, PERSONS_KEY: 0}],
                }
            ]
        )

        assert ergebnis["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 2, PERSONS_KEY: 0}]


# ============================================================================
# _merge_stats — Startwert der Kontaktstufen-Personen
# ============================================================================


class TestMergeStatsStagePersonsStartwert:
    """``_merge_stats``: ``stage_persons`` startet bei 0, nicht bei 1."""

    def test_stufe_ohne_personen_bleibt_null(self):
        """Pin: ``x__merge_stats__mutmut_11`` — ``dict.fromkeys(_STAGE_KEYS, 0)`` -> ``(..., 1)``.

        Solange das wahre Maximum je Stufe >= 1 ist, ist der Startwert
        unsichtbar. Liefern **alle** Segmente fuer eine Stufe 0, zeigt das
        Original 0, der Mutant faelschlich 1 (``max(1, 0)``) — dieselbe
        Schwellen-Anhebung wie oben, nur auf der Kontaktstufen-Achse.

        Abweichung beim Mutanten: ``persons_by_contact_stage ==
        {"anonym": 1, "identifiziert": 1, "qualifiziert": 1}``.
        """
        segment = {
            "total_contacts": 0,
            "by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": 0, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [],
            "by_age_cluster": [],
        }

        ergebnis = snapshot_mod._merge_stats([segment, segment])

        assert ergebnis["persons_by_contact_stage"] == {
            "anonym": 0,
            "identifiziert": 0,
            "qualifiziert": 0,
        }


# ============================================================================
# _load_live_statistics — top_clients faellt raus, fehlender Key ist tolerabel
# ============================================================================


class TestLoadLiveStatisticsTopClients:
    """``_load_live_statistics``: die Pseudonym-Rangliste verlaesst kein Segment."""

    def test_top_clients_wird_aus_dem_segment_entfernt(self, monkeypatch):
        """Pin: ``x__load_live_statistics__mutmut_10`` (``pop(None, None)``),
        ``__mutmut_13`` (``pop("XXtop_clientsXX", None)``),
        ``__mutmut_14`` (``pop("TOP_CLIENTS", None)``).

        Alle drei Mutanten treffen den echten Schluessel nie — das ``pop`` wird
        zum No-op und ``top_clients`` bleibt im Segment-Dict. ``get_statistics_trend``
        spreizt dieses Dict per ``**stats`` direkt in die Chart-/API-Antwort, die
        Rangliste wuerde damit pro Monatssegment mit ausgeliefert (dokumentiertes
        Reidentifizierungsrisiko, vgl. ``export.py``).

        Abweichung beim Mutanten: das Ergebnis ist
        ``{"total_contacts": 3, "top_clients": [["Test-ID-01", 3]]}``.
        """

        def _fake_get_statistics(facility, date_from, date_to, document_type=None):
            return {"total_contacts": 3, "top_clients": [["Test-ID-01", 3]]}

        monkeypatch.setattr(snapshot_mod, "get_statistics", _fake_get_statistics)

        ergebnis = snapshot_mod._load_live_statistics(None, date(2025, 1, 1), date(2025, 1, 31))

        assert ergebnis == {"total_contacts": 3}

    def test_fehlender_top_clients_key_ist_kein_fehler(self, monkeypatch):
        """Pin: ``x__load_live_statistics__mutmut_12`` — ``pop("top_clients", None)`` -> ``pop("top_clients", )``.

        Das Trailing-Komma macht aus dem Zwei-Argument-Aufruf einen
        Ein-Argument-Aufruf, also ``pop`` **ohne** Default. Bei vorhandenem
        Schluessel ist das identisch; fehlt er (fremder/alter Payload), liefert
        das Original den Default ``None`` und laeuft weiter, der Mutant wirft
        ``KeyError``.

        Abweichung beim Mutanten: ``KeyError: 'top_clients'`` statt eines
        Rueckgabewerts.
        """

        def _fake_get_statistics(facility, date_from, date_to, document_type=None):
            return {"total_contacts": 0}

        monkeypatch.setattr(snapshot_mod, "get_statistics", _fake_get_statistics)

        ergebnis = snapshot_mod._load_live_statistics(None, date(2025, 1, 1), date(2025, 1, 31))

        assert ergebnis == {"total_contacts": 0}


# ============================================================================
# _merge_sample_report_1_stats — Alt-Payloads ohne Top-Level-Schluessel
# ============================================================================


class TestMergeSampleReport1FehlendeSchluessel:
    """``_merge_sample_report_1_stats``: fehlende Top-Level-Keys sind neutral."""

    def test_segment_ohne_total_zaehlt_neutral(self):
        """Pin: ``x__merge_sample_report_1_stats__mutmut_11`` (``get("total", None)``),
        ``__mutmut_13`` (``get("total", )``), ``__mutmut_16`` (``get("total", 1)``).

        Fehlt ``total``, addiert das Original die neutrale 0. Mutant 11/13
        addieren ``None`` auf ein ``int`` (``TypeError``), Mutant 16 addiert
        eine stille 1 — eine erfundene Fallzahl im Sachbericht.

        Abweichung beim Mutanten: ``TypeError`` (11/13) bzw. ``total == 1`` (16).
        """
        ergebnis = snapshot_mod._merge_sample_report_1_stats([{"by_category": [], "by_age_cluster": []}])

        assert ergebnis["total"] == 0

    def test_segment_ohne_by_category_ist_leer(self):
        """Pin: ``x__merge_sample_report_1_stats__mutmut_18`` (``get("by_category", None)``),
        ``__mutmut_20`` (``get("by_category", )``).

        Fehlt ``by_category``, iteriert das Original ueber die leere Liste
        (No-op); beide Mutanten iterieren ueber ``None``.

        Abweichung beim Mutanten: ``TypeError: 'NoneType' object is not iterable``.
        """
        ergebnis = snapshot_mod._merge_sample_report_1_stats([{"total": 3, "by_age_cluster": []}])

        assert ergebnis["by_category"] == []
        assert ergebnis["total"] == 3

    def test_segment_ohne_by_age_cluster_ist_leer(self):
        """Pin: ``x__merge_sample_report_1_stats__mutmut_34`` (``get("by_age_cluster", None)``),
        ``__mutmut_36`` (``get("by_age_cluster", )``).

        Genau die Alt-Snapshot-Kompatibilitaet, die das Modul sonst explizit
        testet (vgl. ``test_merge_kommt_mit_alt_snapshot_json_ohne_personen_klar``),
        hier fuer den fehlenden **Top-Level**-Schluessel.

        Abweichung beim Mutanten: ``TypeError: 'NoneType' object is not iterable``.
        """
        ergebnis = snapshot_mod._merge_sample_report_1_stats([{"total": 3, "by_category": []}])

        assert ergebnis["by_age_cluster"] == []
        assert ergebnis["total"] == 3


# ============================================================================
# aggregate_trend_segments — Metadaten gehen nie in den Merge
# ============================================================================


class TestAggregateTrendSegmentsMetadatenFilter:
    """``aggregate_trend_segments``: ``label``/``source`` werden vor dem Merge verworfen."""

    def test_merge_bekommt_die_segmente_ohne_label_und_source(self, monkeypatch):
        """Pin: ``x_aggregate_trend_segments__mutmut_3`` (``"XXlabelXX"``),
        ``__mutmut_4`` (``"LABEL"``), ``__mutmut_5`` (``"XXsourceXX"``),
        ``__mutmut_6`` (``"SOURCE"``).

        Das Verwerfen der Segment-Metadaten ist das **einzige** Verhalten dieser
        Funktion; ihr Rueckgabewert allein kann es nicht bezeugen, weil
        ``_merge_stats`` unbekannte Schluessel strukturell ignoriert. Der Pin
        beobachtet deshalb den Merge-Eingang direkt (Spy auf das Modul-Global).

        Abweichung beim Mutanten: der Merge-Eingang traegt zusaetzlich
        ``"label"`` (3/4) bzw. ``"source"`` (5/6), z. B.
        ``[{"label": "2025-01", "total_contacts": 1}, ...]``.
        """
        gesehen = []

        def _spy(stats_list):
            gesehen.append(stats_list)
            return {"gemerged": True}

        monkeypatch.setattr(snapshot_mod, "_merge_stats", _spy)

        ergebnis = snapshot_mod.aggregate_trend_segments(
            [
                {"label": "2025-01", "source": "snapshot", "total_contacts": 1},
                {"label": "2025-02", "source": "live", "total_contacts": 2},
            ]
        )

        assert gesehen == [[{"total_contacts": 1}, {"total_contacts": 2}]]
        assert ergebnis == {"gemerged": True}


# ============================================================================
# _iter_segments — die Jahresklammer der Snapshot-Auswahl
# ============================================================================


@pytest.mark.django_db
class TestIterSegmentsJahresklammer:
    """``_iter_segments``: ein Segment liest nur den Snapshot **seines** Jahres."""

    @staticmethod
    def _snapshot_daten(total):
        return {
            "total_contacts": total,
            "by_contact_stage": {"anonym": total, "identifiziert": 0, "qualifiziert": 0},
            "persons_by_contact_stage": {"anonym": total, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [],
            "by_age_cluster": [],
            "unique_clients": total,
        }

    def test_gleicher_monat_verschiedene_jahre_bleiben_getrennt(self, facility):
        """Pin: ``x__iter_segments__mutmut_15`` — ``.filter(year=..., month=...)`` -> ``.filter(month=...)``.

        Ohne Jahresfilter liefert ``.first()`` bei ``ordering = ["-year", "-month"]``
        fuer **jede** Januar-Anfrage denselben (juengsten) Snapshot. Das Segment
        traegt dann stillschweigend die Zahlen eines fremden Jahres — inklusive
        falschem ``snapshot_as_of``-Stempel. Beide Richtungen werden geprueft,
        damit der Pin unabhaengig vom Default-Ordering greift.

        Abweichung beim Mutanten: die 2024er Abfrage liefert
        ``total_contacts == 5`` (der 2025er Snapshot) statt 77.
        """
        StatisticsSnapshot.objects.create(facility=facility, year=2024, month=1, data=self._snapshot_daten(77))
        StatisticsSnapshot.objects.create(facility=facility, year=2025, month=1, data=self._snapshot_daten(5))

        januar_2025 = snapshot_mod.get_statistics_hybrid(facility, date(2025, 1, 1), date(2025, 1, 31))
        januar_2024 = snapshot_mod.get_statistics_hybrid(facility, date(2024, 1, 1), date(2024, 1, 31))

        assert januar_2025["total_contacts"] == 5
        assert januar_2024["total_contacts"] == 77


# ============================================================================
# create_or_update_snapshot — Payload-Toleranz und Anreicherungs-Negativzweig
# ============================================================================


@pytest.mark.django_db
class TestCreateOrUpdateSnapshotPayload:
    """``create_or_update_snapshot``: Schreibpfad gegen fremde/luueckenhafte Payloads."""

    def test_payload_ohne_top_clients_wird_geschrieben(self, facility, monkeypatch):
        """Pin: ``x_create_or_update_snapshot__mutmut_56`` — ``stats.pop("top_clients", None)``
        -> ``stats.pop("top_clients", )``.

        Der Ein-Argument-``pop`` ohne Default verhaelt sich nur so lange wie das
        Original, wie ``get_statistics`` den Schluessel garantiert setzt. Faellt
        die Garantie (Alt-/Fremd-Payload, alternativer Statistik-Pfad), wirft der
        Mutant ``KeyError`` **bevor** irgendetwas persistiert ist — der
        Monats-Snapshot dieser Einrichtung entfaellt still.

        Abweichung beim Mutanten: ``KeyError: 'top_clients'`` statt eines
        geschriebenen ``StatisticsSnapshot``.
        """
        payload = _stats_payload()
        del payload["top_clients"]
        monkeypatch.setattr(snapshot_mod, "get_statistics", lambda *a, **kw: copy.deepcopy(payload))
        monkeypatch.setattr(snapshot_mod, "get_backends", list)

        snapshot_mod.create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.data["total_contacts"] == 1
        assert "top_clients" not in snap.data

    def test_unbekannter_dokumentationstyp_bekommt_leere_id(self, facility, monkeypatch):
        """Pin: ``x_create_or_update_snapshot__mutmut_84`` —
        ``entry["document_type_id"] = str(dt.id) if dt else ""`` -> ``else "XXXX"``.

        Der else-Zweig greift, wenn zu einem ``by_document_type``-Eintrag kein
        ``DocumentType`` der Einrichtung gefunden wird. Bestehende Tests decken
        ausschliesslich den Treffer-Fall ab; ein Platzhalter statt des leeren
        Strings waere eine sichtbar falsche ID im Bericht.

        Abweichung beim Mutanten: ``document_type_id == "XXXX"`` statt ``""``.
        """
        payload = _stats_payload(by_document_type=[{"name": "Nicht gefuehrter Typ", "count": 1}])
        monkeypatch.setattr(snapshot_mod, "get_statistics", lambda *a, **kw: copy.deepcopy(payload))
        monkeypatch.setattr(snapshot_mod, "get_backends", list)

        snapshot_mod.create_or_update_snapshot(facility, 2025, 1)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.data["by_document_type"] == [
            {"name": "Nicht gefuehrter Typ", "count": 1, "system_type": "", "document_type_id": ""}
        ]


# ============================================================================
# create_or_update_snapshot — die beiden Log-Vertraege (Refs #2070)
# ============================================================================


@pytest.mark.django_db
class TestCreateOrUpdateSnapshotLogVertraege:
    """Die Log-Zeilen sind der einzige Ausfall-/Erfolgs-Nachweis des Monats-Crons.

    ``create_or_update_snapshot`` hat keinen Rueckgabewert und wird im Cron ueber
    **alle** Einrichtungen geschleift
    (``core/management/commands/create_statistics_snapshots.py``). Beide
    Meldungen werden deshalb als **ganze, formatierte Zeichenkette** verglichen —
    ein Teilstring-Assert ueberlebt die ``XX...XX``-Varianten von mutmut.
    """

    @pytest.fixture
    def _isolierte_registry(self, monkeypatch):
        isolate_reporting_registry(monkeypatch)

    @pytest.fixture
    def _kaputtes_snapshot_backend(self, _isolierte_registry):
        class _KaputtesPinSnapshotBackend(_PinBasisBackend):
            name = "Kaputtes Pin-Snapshot-Backend"
            snapshot_enabled = True

            def build_dataset(self, facility, date_from, date_to):
                raise RuntimeError("snapshot-build kaputt (Pin)")

        return register_backend("kaputt_snapshot_pin")(_KaputtesPinSnapshotBackend)

    @staticmethod
    def _meldungen(caplog):
        return [record.getMessage() for record in caplog.records]

    def test_fehler_log_nennt_backend_einrichtung_und_monat(self, facility, caplog, _kaputtes_snapshot_backend):
        """Pin: ``x_create_or_update_snapshot__mutmut_42`` (``backend.slug`` -> ``None``),
        ``__mutmut_43`` (``facility.name`` -> ``None``),
        ``__mutmut_51`` (``"XXcreate_or_update_snapshot: ...XX"``),
        ``__mutmut_52`` (``"Backend"`` -> ``"backend"``).

        Refs #2070: der Ausfall eines Backends wird **laut** markiert. Der
        maschinenlesbare Teil steht in ``backend_data["_errors"]``, die
        Zuordnung „welches Backend, welche Einrichtung, welcher Monat" existiert
        nur hier — im Cron ueber alle Einrichtungen ist das die einzige Spur.

        Abweichung beim Mutanten: die formatierte Meldung lautet
        ``"... Backend None uebersprungen ..."`` (42),
        ``"... (facility=None year=2025 month=1)"`` (43),
        ``"XXcreate_or_update_snapshot: ...XX"`` (51) bzw.
        ``"create_or_update_snapshot: backend ..."`` (52).
        """
        modul_logger = logging.getLogger(MODUL_LOGGER)
        caplog.set_level(logging.ERROR, logger=MODUL_LOGGER)
        modul_logger.addHandler(caplog.handler)
        try:
            with translation.override("de"):
                snapshot_mod.create_or_update_snapshot(facility, 2025, 1)
        finally:
            modul_logger.removeHandler(caplog.handler)

        erwartet = (
            "create_or_update_snapshot: Backend kaputt_snapshot_pin uebersprungen "
            "(facility=Teststelle year=2025 month=1)"
        )
        assert erwartet in self._meldungen(caplog)

        snap = StatisticsSnapshot.objects.get(facility=facility, year=2025, month=1)
        assert snap.backend_data["_errors"] == {"kaputt_snapshot_pin": "RuntimeError"}

    def test_erfolgs_log_nennt_einrichtung_und_monat(self, facility, caplog, _isolierte_registry):
        """Pin: ``x_create_or_update_snapshot__mutmut_111`` (``facility.name`` -> ``None``),
        ``__mutmut_118`` (``"XXSnapshot created/updated: ...XX"``),
        ``__mutmut_119`` (``"Snapshot"`` -> ``"snapshot"``).

        Der abschliessende Info-Eintrag ist im Monats-Cron die einzige
        Erfolgsspur je Einrichtung; der Meldungstext ist zudem als stabiler
        Referenzstring im Repo verankert (``src/tests/test_logging.py`` prueft
        den PII-Scrubber gegen genau diese Zeile).

        Abweichung beim Mutanten: ``"Snapshot created/updated: facility=None ..."``
        (111), ``"XXSnapshot created/updated: ...XX"`` (118) bzw.
        ``"snapshot created/updated: ..."`` (119).
        """
        modul_logger = logging.getLogger(MODUL_LOGGER)
        caplog.set_level(logging.INFO, logger=MODUL_LOGGER)
        modul_logger.addHandler(caplog.handler)
        try:
            with translation.override("de"):
                snapshot_mod.create_or_update_snapshot(facility, 2025, 1)
        finally:
            modul_logger.removeHandler(caplog.handler)

        assert "Snapshot created/updated: facility=Teststelle year=2025 month=1" in self._meldungen(caplog)
