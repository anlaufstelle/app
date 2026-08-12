"""Golden-Pin der Berichtszahlen ueber die ``report_key``-Umstellung (E3, Refs #1849).

**Wozu.** Etappe E3 (Konzept #1847)
tauscht den Erstschluessel der Berichtszuordnung aus: statt ``system_type``
entscheidet kuenftig ``DocumentType.report_key``, statt des Feldvorlagen-Slugs
der ``FieldTemplate.report_key``. Das ist eine Aenderung an der
Aufloesungsschicht — und damit an genau der Stelle, an der ein Fehler nicht
kracht, sondern still **andere Zahlen** produziert.

**Warum Golden-Pin und nicht Vorher/Nachher.** Ein Vorher/Nachher-Vergleich im
selben Lauf kann diese Klasse Fehler nicht finden: beide Haelften liefen durch
denselben (geaenderten) Code. Dieser Test schreibt deshalb die **Ist-Zahlen vor
der Umstellung** als Literale fest — jede Abweichung waehrend E3 ist ein
Befund, keine Erwartungsanpassung.

**Abdeckung.** Alle sechs Backends mit Typ-Bindung (``sample-report-1``,
``monthly-trend``, ``counseling-hours``, ``needle-exchange-balance``,
``service-distribution``, ``ban-overview``), jeweils ``build_dataset`` **und**
``prepare_export``. ``summary["by_category"]`` wird inklusive **Reihenfolge**
gepinnt: die Kategoriennamen sind persistierte Snapshot-Schluessel, und der
monatsuebergreifende Merge (``_merge_sample_report_1_stats``) haengt an ihnen —
verschoebe die Umstellung sie, zerrisse der Merge ueber die Umstellung hinweg.

**Datenbasis.** Die Einrichtung kommt aus dem Standardpaket
(``core/packages/default.yaml`` ueber ``seed_document_types`` — derselbe Weg wie
der Seed), die Ereignisse sind deterministisch konstruiert und liegen bewusst
alle **ueber** der k-Anonymitaets-Schwelle (Default 5): der Pin misst die
Aufloesung, nicht die Suppressionsarithmetik. Genau eine Zelle je Backend
verlaesst diesen Rahmen bewusst nicht.
"""

import importlib
from datetime import date, datetime

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.utils import timezone

from core.models import Client, Event, Facility, Organization, User
from core.reporting import registry
from core.seed.doc_types import seed_document_types
from core.services.dashboard.snapshot import _merge_sample_report_1_stats

pytestmark = pytest.mark.django_db

# Der Bestands-Backfill selbst (E3, Refs #1849) — nicht nachgebaut, sondern
# aufgerufen: die Paritaets-Aussage lautet „Backfill plus Umstellung aendern die
# Zahlen nicht", und eine zweite Kopie der Regel im Test koennte genau dort
# abweichen, wo es darauf ankommt. Die Testdatenbank ist beim Anlegen der
# Fixture-Zeilen laengst durchmigriert; der Backfill hat sie also nie gesehen.
_BACKFILL = importlib.import_module("core.migrations.0145_backfill_report_key")

MAI_VON = date(2025, 5, 1)
MAI_BIS = date(2025, 5, 31)
JUNI_VON = date(2025, 6, 1)
JUNI_BIS = date(2025, 6, 30)
ZEITRAUM_VON = MAI_VON
ZEITRAUM_BIS = JUNI_BIS

# Ereignisplan je Monat: (Typ-Slug, Anzahl, ``data_json``).
#
# Die Zahlen sind so gewaehlt, dass jede publizierte Zelle >= 5 liegt (Default-
# Schwelle) und die Kategorie-Summen paarweise verschieden sind — eine
# Verschiebung in der Aufloesung faellt damit als geaenderte Zahl auf, nicht
# bloss als andere Zeilenordnung.
_MAI = (
    ("kontakt", 6, {"dauer": 10, "leistungen": ["beratung", "essen"]}),
    ("krisengespraech", 5, {"dauer": 20, "art-der-krise": "gewalt"}),
    ("beratungsgespraech", 5, {"dauer": 30}),
    ("medizinische-versorgung", 5, {}),
    ("spritzentausch", 5, {"ausgabe": 10, "rueckgabe": 4}),
    ("begleitung", 5, {}),
    ("vermittlung", 5, {}),
    ("notiz", 5, {}),
    ("hausverbot", 5, {"aktiv": True}),
)
_JUNI = (
    ("kontakt", 5, {"dauer": 10, "leistungen": ["beratung", "essen"]}),
    ("krisengespraech", 5, {"dauer": 20, "art-der-krise": "suizidal"}),
    ("beratungsgespraech", 5, {"dauer": 30}),
    ("medizinische-versorgung", 5, {}),
    ("spritzentausch", 5, {"ausgabe": 10, "rueckgabe": 4}),
    ("begleitung", 5, {}),
    ("vermittlung", 5, {}),
    ("notiz", 5, {}),
    ("hausverbot", 5, {"bis": "2025-06-15"}),
)

# Personenzuordnung: die ersten 41 Ereignisse tragen eine U18-Person, die
# naechsten 30 eine 18-26-Person, die letzten 20 eine 27+-Person. Sechs Personen
# je Cluster — damit liegen sowohl Ereignis- als auch Personenzahl jeder
# Alterszeile ueber der Schwelle, und die drei Zeilenzahlen sind verschieden
# (die Zeilenordnung ist ``-count`` und waere bei Gleichstand nicht definiert).
_CLUSTER_PLAN = (
    (Client.AgeCluster.U18, 41),
    (Client.AgeCluster.AGE_18_26, 30),
    (Client.AgeCluster.AGE_27_PLUS, 20),
)
_PERSONEN_JE_CLUSTER = 6


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Registry je Test isolieren, echte Backends bleiben verfuegbar (Doku § 10)."""
    registry._discover()
    monkeypatch.setattr(registry, "_REGISTRY", dict(registry._REGISTRY))
    monkeypatch.setattr(registry, "_discovered", True)
    yield


@pytest.fixture
def paket_facility(db):
    """Einrichtung mit dem **Standardpaket** — der Seed-Weg, nicht handgebaute Typen.

    Anschliessend laeuft der Bestands-Backfill aus Migration ``0145`` ueber die
    frisch angelegten Zeilen: so sieht eine Instanz nach dem Update aus, und
    genau darueber macht dieser Test seine Aussage. Sobald das Standardpaket
    ``report_key`` selbst mitbringt, ist der Aufruf ein No-op (der Backfill
    fasst nur leere Schluessel an) — dass beide Wege dasselbe Ergebnis liefern
    muessen, ist die tragende Zusage aus Konzept #1847 §E3.8.
    """
    org = Organization.objects.create(name="Paritaets-Org")
    facility = Facility.objects.create(organization=org, name="Paritaets-Stelle")
    seed_document_types(facility)
    with connection.schema_editor() as schema_editor:
        _BACKFILL.backfill_report_key(django_apps, schema_editor)
    return facility


@pytest.fixture
def paritaets_daten(paket_facility):
    """Deterministischer Ereignisbestand ueber zwei Monate."""
    facility = paket_facility
    user = User.objects.create_user(
        username="paritaet-user",
        password="x",  # noqa: S106 - Testfixture
        facility=facility,
        role=User.Role.STAFF,
    )
    doc_types = {dt.slug: dt for dt in facility.document_types.all()}

    personen = []
    for cluster, _anzahl in _CLUSTER_PLAN:
        personen.append(
            [
                Client.objects.create(
                    facility=facility,
                    pseudonym=f"PA-{cluster}-{index}",
                    age_cluster=cluster,
                    created_by=user,
                )
                for index in range(_PERSONEN_JE_CLUSTER)
            ]
        )

    def _person(index: int):
        grenze = 0
        for cluster_index, (_cluster, anzahl) in enumerate(_CLUSTER_PLAN):
            if index < grenze + anzahl:
                return personen[cluster_index][(index - grenze) % _PERSONEN_JE_CLUSTER]
            grenze += anzahl
        raise AssertionError("Ereignisplan und Cluster-Plan sind auseinandergelaufen.")

    index = 0
    for monat, plan in ((5, _MAI), (6, _JUNI)):
        for slug, anzahl, data in plan:
            for _ in range(anzahl):
                Event.objects.create(
                    facility=facility,
                    client=_person(index),
                    document_type=doc_types[slug],
                    occurred_at=timezone.make_aware(datetime(2025, monat, 12, 10, 0)),
                    data_json=dict(data),
                    created_by=user,
                )
                index += 1
    assert index == sum(anzahl for _cluster, anzahl in _CLUSTER_PLAN)
    return facility


def _dataset(slug, facility, date_from=ZEITRAUM_VON, date_to=ZEITRAUM_BIS):
    return registry.get_backend(slug).build_dataset(facility, date_from, date_to)


def _export(slug, facility, date_from=ZEITRAUM_VON, date_to=ZEITRAUM_BIS):
    backend = registry.get_backend(slug)
    return backend.prepare_export(facility, backend.build_dataset(facility, date_from, date_to))


class TestSampleReport1:
    """Der Bericht, an dem die Kategoriennamen als Snapshot-Schluessel haengen."""

    def test_by_category_inklusive_reihenfolge(self, paritaets_daten):
        summary = _dataset("sample-report-1", paritaets_daten).summary

        assert summary["by_category"] == [
            ("Vermittlung", 20),
            ("Kontakte", 11),
            ("Beratung", 20),
            ("Versorgung", 20),
        ]
        assert summary["total"] == 71

    def test_altersgruppen_und_personenzahl(self, paritaets_daten):
        summary = _dataset("sample-report-1", paritaets_daten).summary

        assert [(row["cluster"], row["count"], row["persons"]) for row in summary["by_age_cluster"]] == [
            ("u18", 41, 6),
            ("18_26", 30, 6),
            ("27_plus", 20, 6),
        ]
        assert summary["unique_clients"] == 18

    def test_records_spiegeln_die_kategorien(self, paritaets_daten):
        records = _dataset("sample-report-1", paritaets_daten).records

        assert records == [
            {"category": "Vermittlung", "count": 20},
            {"category": "Kontakte", "count": 11},
            {"category": "Beratung", "count": 20},
            {"category": "Versorgung", "count": 20},
        ]

    def test_vollstaendigkeitshinweis_beziffert_die_luecke(self, paritaets_daten):
        issues = _dataset("sample-report-1", paritaets_daten).completeness

        # Notiz (10) und Hausverbot (10) tragen keine Berichtszuordnung.
        assert [(issue.code, issue.count) for issue in issues] == [("unmapped_system_types", 20)]

    def test_export_bleibt_unsuppressiert(self, paritaets_daten):
        result = _export("sample-report-1", paritaets_daten)

        # Der Export kanonisiert die Zeilenordnung (``ORDER_CATEGORY``, K5,
        # Refs #1770) — die datenabhaengige Einfuegereihenfolge aus
        # ``build_dataset`` verlaesst das Haus nie.
        assert [(row["name"], row["count"]) for row in result.summary["by_category"]] == [
            ("Beratung", 20),
            ("Kontakte", 11),
            ("Vermittlung", 20),
            ("Versorgung", 20),
        ]
        assert result.summary["total"] == 71
        assert result.summary["unique_clients"] == 18
        assert [(row["cluster"], row["count"]) for row in result.summary["by_age_cluster"]] == [
            ("u18", 41),
            ("18_26", 30),
            ("27_plus", 20),
        ]

    def test_monatsmerge_traegt_dieselben_kategorienamen(self, paritaets_daten):
        mai = _dataset("sample-report-1", paritaets_daten, MAI_VON, MAI_BIS).summary
        juni = _dataset("sample-report-1", paritaets_daten, JUNI_VON, JUNI_BIS).summary

        assert dict(mai["by_category"]) == {"Vermittlung": 10, "Kontakte": 6, "Beratung": 10, "Versorgung": 10}
        assert dict(juni["by_category"]) == {"Vermittlung": 10, "Kontakte": 5, "Beratung": 10, "Versorgung": 10}

        merged = _merge_sample_report_1_stats([mai, juni])

        # Der Merge laeuft ueber die Kategoriennamen: verschoebe die Umstellung
        # sie, entstuenden hier acht statt vier Zeilen.
        assert dict(merged["by_category"]) == {
            "Vermittlung": 20,
            "Kontakte": 11,
            "Beratung": 20,
            "Versorgung": 20,
        }
        assert merged["total"] == 71


class TestMonthlyTrend:
    def test_zeilen_je_monat_und_kategorie(self, paritaets_daten):
        result = _dataset("monthly-trend", paritaets_daten)

        assert result.records == [
            {"monat": "2025-05", "kategorie": "Beratung", "count": 10},
            {"monat": "2025-05", "kategorie": "Kontakte", "count": 6},
            {"monat": "2025-05", "kategorie": "Vermittlung", "count": 10},
            {"monat": "2025-05", "kategorie": "Versorgung", "count": 10},
            {"monat": "2025-06", "kategorie": "Beratung", "count": 10},
            {"monat": "2025-06", "kategorie": "Kontakte", "count": 5},
            {"monat": "2025-06", "kategorie": "Vermittlung", "count": 10},
            {"monat": "2025-06", "kategorie": "Versorgung", "count": 10},
        ]
        assert result.summary["total"] == 71
        assert [(issue.code, issue.count) for issue in result.completeness] == [("unmapped_system_types", 20)]

    def test_export_bleibt_unsuppressiert(self, paritaets_daten):
        result = _export("monthly-trend", paritaets_daten)

        assert [row["count"] for row in result.records] == [10, 6, 10, 10, 10, 5, 10, 10]
        assert result.summary["total"] == 71


class TestCounselingHours:
    def test_zeilen_und_minutensummen(self, paritaets_daten):
        result = _dataset("counseling-hours", paritaets_daten)

        assert [(row["kategorie"], row["events"], row["minuten"]) for row in result.records] == [
            ("contact", 11, 110),
            ("counseling", 10, 300),
            ("crisis", 10, 200),
        ]
        assert result.summary == {"events_gesamt": 31, "minuten_gesamt": 610}

    def test_export_bleibt_unsuppressiert(self, paritaets_daten):
        result = _export("counseling-hours", paritaets_daten)

        assert result.records == [
            {"kategorie": "contact", "events": 11, "minuten": 110},
            {"kategorie": "counseling", "events": 10, "minuten": 300},
            {"kategorie": "crisis", "events": 10, "minuten": 200},
        ]
        assert result.summary["events_gesamt"] == 31
        assert result.summary["minuten_gesamt"] == 610


class TestNeedleExchangeBalance:
    def test_summen_und_saldo(self, paritaets_daten):
        summary = _dataset("needle-exchange-balance", paritaets_daten).summary

        assert summary == {
            "events": 10,
            "summe_ausgabe": 100,
            "summe_rueckgabe": 40,
            "saldo": 60,
            "events_ohne_werte": 0,
        }

    def test_export_zeigt_die_summen(self, paritaets_daten):
        result = _export("needle-exchange-balance", paritaets_daten)

        assert result.records == [{"events": 10, "summe_ausgabe": 100, "summe_rueckgabe": 40, "saldo": 60}]


class TestServiceDistribution:
    def test_optionsverteilung_je_feldgruppe(self, paritaets_daten):
        result = _dataset("service-distribution", paritaets_daten)

        assert result.records == [
            {"feld": "leistungen", "option": "beratung", "count": 11},
            {"feld": "leistungen", "option": "essen", "count": 11},
            {"feld": "art-der-krise", "option": "gewalt", "count": 5},
            {"feld": "art-der-krise", "option": "suizidal", "count": 5},
        ]
        assert result.summary == {"events_leistungen": 11, "events_art_der_krise": 10}

    def test_export_bleibt_unsuppressiert(self, paritaets_daten):
        result = _export("service-distribution", paritaets_daten)

        assert [(row["feld"], row["option"], row["count"]) for row in result.records] == [
            ("leistungen", "beratung", 11),
            ("leistungen", "essen", 11),
            ("art-der-krise", "gewalt", 5),
            ("art-der-krise", "suizidal", 5),
        ]
        assert result.summary["events_leistungen"] == 11
        assert result.summary["events_art_der_krise"] == 10


class TestBanOverview:
    """Bleibt dauerhaft an ``system_type=ban`` gebunden (Konzept #1847 §3)."""

    def test_kennzahlen(self, paritaets_daten):
        result = _dataset("ban-overview", paritaets_daten)

        assert result.summary == {"ausgesprochen": 10, "aktiv_aktuell": 5, "abgelaufen_im_zeitraum": 5}
        assert result.records == [
            {"kennzahl": "ausgesprochen", "wert": 10},
            {"kennzahl": "aktiv_aktuell", "wert": 5},
            {"kennzahl": "abgelaufen_im_zeitraum", "wert": 5},
        ]

    def test_export_bleibt_unsuppressiert(self, paritaets_daten):
        result = _export("ban-overview", paritaets_daten)

        assert [(row["kennzahl"], row["wert"]) for row in result.records] == [
            ("ausgesprochen", 10),
            ("aktiv_aktuell", 5),
            ("abgelaufen_im_zeitraum", 5),
        ]


class TestVerfuegbarkeit:
    def test_alle_sechs_backends_sind_fuer_die_paketeinrichtung_waehlbar(self, paritaets_daten):
        """Die Verfuegbarkeit ist selbst eine Ableitung der Bindung (E1) — mitpinnen."""
        verfuegbar = {backend.slug for backend in registry.get_available_backends(paritaets_daten)}

        assert {
            "sample-report-1",
            "monthly-trend",
            "counseling-hours",
            "needle-exchange-balance",
            "service-distribution",
            "ban-overview",
        } <= verfuegbar
