"""Tests fuer external_report-Service (Refs #921).

Datenschutzfreundliche externe Berichte:
- Keine Pseudonyme im Output
- K-Anonymity-Schwelle auf Aggregate
- Datenschutzprofil-Metadaten im Report
"""

from __future__ import annotations

from datetime import date

import pytest
from django.utils import timezone

from core.models import Event


@pytest.fixture
def report_period():
    return date(2026, 1, 1), date(2026, 12, 31)


def _make_events(facility, client, doc_type, user, count: int):
    """Hilfsmethode: erzeugt ``count`` Events fuer einen Client."""
    return [
        Event.objects.create(
            facility=facility,
            client=client,
            document_type=doc_type,
            occurred_at=timezone.now(),
            data_json={"dauer": 10},
            created_by=user,
        )
        for _ in range(count)
    ]


class TestSecondarySuppression:
    """K2 (Refs #1770): Sekundär-Suppression an der **Machbarkeitsspanne**.

    Löst die Heuristik aus A6.1 (Refs #1024 / #1016) ab, die an der *Zellenzahl*
    hing (`len(suppressed) != 1`): bei ≥ 2 unterdrückten Zellen lief sie nie —
    obwohl gerade dann R = s (alle = 1) die Zellen vollständig bestimmt —, und
    wenn sie lief, opferte sie die *kleinste* sichtbare Zelle und minimierte
    damit das Residuum.

    Jetzt wird die Spanne gemessen: solange eine unterdrückte Zelle exakt
    zurückrechenbar ist, fällt die **größte** sichtbare Zelle (maximiert das
    Residuum), danach wird neu gerechnet — bis zum Fixpunkt.
    """

    def test_list_single_suppression_triggers_secondary(self):
        """Die Zusicherung „größte bleibt sichtbar" bleibt gültig (#1770).

        Geprüft wurde, ob der Wechsel auf das Machbarkeits-Audit sie bricht: das
        tut er nicht, weil die Opferwahl auf der **kleinsten** sichtbaren Zelle
        bleibt (User-Entscheid). Neu ist nur, dass hinter der Wahl kein
        Zellenzähler mehr steht, sondern eine gemessene Spanne — und dass die
        dabei publizierte Obergrenze („das Opfer liegt unter jeder verbliebenen
        sichtbaren Zelle") im Guard-Modell gespiegelt ist.
        """
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 8}, {"name": "C", "count": 3}]
        result = _suppress_small(rows, threshold=5, margin=21, order=ORDER_INPUT)
        by_name = {r["name"]: r for r in result}
        assert by_name["C"]["suppressed"] is True  # primär (<5)
        assert by_name["B"]["suppressed"] is True  # sekundär: die kleinste sichtbare fällt
        assert by_name["B"]["count"] is None
        assert by_name["A"]["suppressed"] is False  # größte bleibt sichtbar

    def test_list_two_already_suppressed_no_secondary(self):
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 3}, {"name": "C", "count": 2}]
        result = _suppress_small(rows, threshold=5, margin=15, order=ORDER_INPUT)
        by_name = {r["name"]: r for r in result}
        # Residuum 5 auf zwei Zellen aus [1, 4] -> beide offen, A bleibt sichtbar.
        assert by_name["A"]["suppressed"] is False
        assert by_name["B"]["suppressed"] is True
        assert by_name["C"]["suppressed"] is True

    def test_zwei_unterdrueckte_zellen_am_minimum_loesen_die_regel_aus(self):
        """Die Lücke der alten Heuristik: zwei unterdrückte Zellen, die sich
        beide aus dem Residuum ergeben (R = 2, also 1 + 1). `len(suppressed)
        != 1` ließ genau das durchgehen."""
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "A", "count": 20}, {"name": "B", "count": 1}, {"name": "C", "count": 1}]
        result = _suppress_small(rows, threshold=5, margin=22, order=ORDER_INPUT)
        by_name = {r["name"]: r for r in result}
        assert by_name["A"]["suppressed"] is True, "die Randsumme pinnt B und C auf je 1"
        assert by_name["B"]["suppressed"] is True
        assert by_name["C"]["suppressed"] is True

    def test_ohne_publizierte_randsumme_laeuft_das_audit_nicht(self):
        """`margin=None`: kein Artefakt publiziert eine Randsumme über diese
        Zeilen (`by_age_cluster`) — ohne Gleichung gibt es nichts
        zurückzurechnen, und jede zusätzliche Unterdrückung wäre reiner
        Utility-Verlust."""
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "A", "count": 20}, {"name": "B", "count": 1}, {"name": "C", "count": 1}]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_INPUT)
        by_name = {r["name"]: r for r in result}
        assert by_name["A"]["suppressed"] is False

    def test_list_no_suppression_below_count_leaves_all_visible(self):
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "A", "count": 10}, {"name": "B", "count": 8}]
        result = _suppress_small(rows, threshold=5, margin=18, order=ORDER_INPUT)
        assert all(r["suppressed"] is False for r in result)

    def test_stage_dict_single_suppression_triggers_secondary(self):
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 10, "identifiziert": 8, "qualifiziert": 3}
        result = _suppress_stage_dict(
            stages, threshold=5, persons={"anonym": 10, "identifiziert": 8, "qualifiziert": 3}, margin=21
        )
        assert result["qualifiziert"] is None  # primär
        assert result["identifiziert"] is None  # sekundär: die kleinste sichtbare fällt
        assert result["anonym"] == 10

    def test_opferwahl_und_a_priori_schranke_sind_gekoppelt(self):
        """Bedingung X (#1770): wer die kleinste sichtbare Zelle opfert, verrät
        damit eine **Obergrenze** — und die muss im Modell stehen.

        Das Opfer liegt per Konstruktion unter jeder verbliebenen sichtbaren
        Zelle; ein Empfänger, der den Algorithmus kennt (Open Source), weiß das.
        Bliebe die Schranke unmodelliert, rechnete der Guard gegen ein zu
        schwaches Angreifermodell — gemessen: zwei exakte Pins im Weltraum
        „3 Zeilen, Σ ≤ 14" (`test_reporting_disclosure_regression.py` ist die
        Gegenprobe). Dieser Test pinnt die Kopplung direkt an der Stelle, an der
        sie brechen würde.
        """
        from core.services.dashboard.external_report import _local_partition

        # A = 10 sichtbar, B = 8 Sekundär-Opfer, C = 3 primär unterdrückt.
        partition = _local_partition(
            [10, 8, 3],
            flags=[False, True, True],
            victims=[False, True, False],
            threshold=5,
            margin=21,
            min_value=1,
            person_gated=False,
        )
        opfer = partition.cells[1]
        assert (opfer.lo, opfer.hi) == (5, 10), "Sekundär-Opfer: [k, kleinste sichtbare Zelle]"
        primaer = partition.cells[2]
        assert (primaer.lo, primaer.hi) == (1, 4), "primär unterdrückt: [Zeilen-Existenz, k-1]"

    def test_ohne_sichtbare_zelle_hat_das_opfer_keine_obergrenze(self):
        """Ist nichts mehr sichtbar, gibt es keine ableitbare Obergrenze — die
        Opfer beschränken dann nur einander, was ein Box-Modell nicht ausdrückt.
        """
        from core.services.dashboard.external_report import _local_partition

        partition = _local_partition(
            [20, 1, 1],
            flags=[True, True, True],
            victims=[True, False, False],
            threshold=5,
            margin=22,
            min_value=1,
            person_gated=False,
        )
        assert (partition.cells[0].lo, partition.cells[0].hi) == (5, None)

    def test_stage_dict_leere_stufe_loest_keine_sekundaersuppression_aus(self):
        """Eine Stufe, die exakt auf 0 zurückrechenbar ist, schützt niemanden —
        ein leerer Bucket ist die Abwesenheit von Fällen, keine
        Kleinstfallzahl. Sonst fiele in jeder Einrichtung ohne anonyme Kontakte
        dauerhaft die gesamte Stufenverteilung."""
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 0, "identifiziert": 0, "qualifiziert": 31}
        result = _suppress_stage_dict(
            stages, threshold=5, persons={"anonym": 0, "identifiziert": 0, "qualifiziert": 8}, margin=31
        )
        assert result == {"anonym": None, "identifiziert": None, "qualifiziert": 31}


class TestKanonischeOrdnungUnterdrueckterZeilen:
    """K5 (Refs #1767): die Zeilenreihenfolge darf keine Rangaussage tragen.

    ``by_age_cluster`` und ``by_document_type`` entstehen absteigend nach Anzahl
    (``order_by("-count")``, im Hybrid-Pfad erneut so sortiert); ``_suppress_small``
    nullte bisher nur die Werte und liess die Reihenfolge unangetastet. Damit
    stand die unterdrueckte Kleinstzelle **immer** am Tabellenende — das Artefakt
    verriet ohne jede Arithmetik, WELCHE Altersgruppe bzw. WELCHER Dokumenttyp
    die Kleinstfallzahl traegt, und die Zuordnung von r unterdrueckten Werten zu
    Labels sank von r! auf 1.

    Unterdrueckte Zeilen stehen deshalb als Block in kanonischer,
    **wertunabhaengiger** Ordnung am Listenende (Altersgruppen in
    Modellreihenfolge, Dokumenttypen nach ``(category, name)``); sichtbare
    Zeilen duerfen count-sortiert bleiben, ihre Werte stehen ohnehin im Klartext.
    """

    AGE_LABELS = {"u18": "Unter 18", "18_26": "18–26", "27_plus": "27+", "unknown": "Unbekannt"}

    def _age_rows(self, counts: dict[str, int]) -> list[dict]:
        """Altersgruppen-Zeilen in der Query-Ordnung (absteigend nach Anzahl)."""
        rows = [{"cluster": key, "label": self.AGE_LABELS[key], "count": value} for key, value in counts.items()]
        return sorted(rows, key=lambda row: row["count"], reverse=True)

    def _doc_rows(self, counts: dict[tuple[str, str], int]) -> list[dict]:
        rows = [{"name": name, "category": category, "count": value} for (category, name), value in counts.items()]
        return sorted(rows, key=lambda row: row["count"], reverse=True)

    def test_unterdrueckte_altersgruppen_stehen_als_block_in_modellreihenfolge(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = self._age_rows({"27_plus": 20, "unknown": 9, "18_26": 3, "u18": 2})
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)

        assert [row["cluster"] for row in result] == ["27_plus", "unknown", "u18", "18_26"]
        assert [row["suppressed"] for row in result] == [False, False, True, True]

    def test_vertauschte_zellwerte_erzeugen_dieselbe_reihenfolge(self):
        """Ordnungstest (security-notes § Regressionsschutz (2)): zwei Welten, die
        sich nur in der Zuordnung der unterdrueckten Werte unterscheiden, muessen
        dieselbe Publikationsreihenfolge erzeugen — sonst ist die Position selbst
        die Aussage, welche Zelle die kleinere ist.
        """
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        kwargs = {"threshold": 5, "margin": None, "order": ORDER_AGE_CLUSTER}
        welt_a = _suppress_small(self._age_rows({"27_plus": 20, "18_26": 4, "u18": 1}), **kwargs)
        welt_b = _suppress_small(self._age_rows({"27_plus": 20, "18_26": 1, "u18": 4}), **kwargs)

        assert [row["cluster"] for row in welt_a] == [row["cluster"] for row in welt_b]
        assert [row["count"] for row in welt_a] == [row["count"] for row in welt_b]

    def test_unterdrueckte_dokumenttypen_nach_kategorie_und_name(self):
        from core.services.dashboard.external_report import ORDER_DOCUMENT_TYPE, _suppress_small

        rows = self._doc_rows(
            {
                ("contact", "Kontakt"): 20,
                ("note", "Notiz"): 9,
                ("service", "Beratung"): 3,
                ("contact", "Aufnahme"): 2,
            }
        )
        result = _suppress_small(rows, threshold=5, margin=34, order=ORDER_DOCUMENT_TYPE)

        assert [row["name"] for row in result] == ["Kontakt", "Notiz", "Aufnahme", "Beratung"]
        assert [row["suppressed"] for row in result] == [False, False, True, True]

    def test_sichtbare_zeilen_bleiben_count_sortiert(self):
        from core.services.dashboard.external_report import ORDER_DOCUMENT_TYPE, _suppress_small

        rows = self._doc_rows(
            {
                ("contact", "Aufnahme"): 7,
                ("note", "Notiz"): 30,
                ("service", "Beratung"): 12,
                ("contact", "Kontakt"): 1,
                ("service", "Vermittlung"): 2,
            }
        )
        result = _suppress_small(rows, threshold=5, margin=52, order=ORDER_DOCUMENT_TYPE)

        sichtbar = [row["name"] for row in result if not row["suppressed"]]
        assert sichtbar == ["Notiz", "Beratung", "Aufnahme"]

    def test_ohne_unterdrueckung_bleibt_die_reihenfolge_unveraendert(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = self._age_rows({"27_plus": 20, "u18": 9, "18_26": 7})
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)

        assert [row["cluster"] for row in result] == ["27_plus", "u18", "18_26"]

    def test_by_category_zeilen_ohne_kanonische_ordnung_bleiben_unberuehrt(self):
        """Dokumentierte Grenze: ``by_category`` traegt keinen Rangkanal.

        Die Kategoriezeilen des Beispiel-Sachberichts entstehen in fester
        Kategorien-Ordnung (Konsolidierung ueber ``system_type``), nicht nach
        Anzahl — ihre Position ist keine Rangaussage. Sie werden deshalb
        bewusst NICHT umsortiert (sonst braeche der Bestandstest
        ``test_records_spiegeln_die_suppressierten_zellen`` ohne Sicherheitsgewinn).

        Seit #1770 sagt die **Aufrufstelle** das mit ``order=ORDER_INPUT``
        ausdruecklich, statt dass eine Formheuristik es errät.
        """
        from core.services.dashboard.external_report import ORDER_INPUT, _suppress_small

        rows = [{"name": "Kontakte", "count": 12}, {"name": "Beratung", "count": 3}, {"name": "Versorgung", "count": 2}]
        result = _suppress_small(rows, threshold=5, margin=17, order=ORDER_INPUT)

        assert [row["name"] for row in result] == ["Kontakte", "Beratung", "Versorgung"]


@pytest.mark.django_db
class TestKanonischeOrdnungInDenWrappern:
    """K5 wirkt an EINEM Eingriffspunkt und damit in allen drei Wrappern —
    Templates, JSON und XML erben ihn ohne eigene Anpassung.
    """

    def test_suppress_report_stats_ordnet_beide_tabellen_kanonisch(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [
                {"name": "Notiz", "category": "note", "count": 30},
                {"name": "Beratung", "category": "service", "count": 4},
                {"name": "Aufnahme", "category": "contact", "count": 3},
            ],
            "persons_by_contact_stage": {"anonym": 12, "identifiziert": 9, "qualifiziert": 6},
            "by_age_cluster": [
                {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 11},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4, "persons": 4},
                {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
            ],
        }
        out = suppress_report_stats(facility, stats)

        assert [row["name"] for row in out["by_document_type"]] == ["Notiz", "Aufnahme", "Beratung"]
        assert [row["cluster"] for row in out["by_age_cluster"]] == ["27_plus", "u18", "unknown"]

    def test_suppress_sample_report_1_stats_ordnet_die_altersgruppen_kanonisch(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 12,
            "by_category": [("Kontakte", 30), ("Beratung", 4), ("Versorgung", 3)],
            "by_age_cluster": [
                {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 11},
                {"cluster": "unknown", "label": "Unbekannt", "count": 4, "persons": 4},
                {"cluster": "18_26", "label": "18–26", "count": 3, "persons": 3},
            ],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert [row["cluster"] for row in out["by_age_cluster"]] == ["27_plus", "18_26", "unknown"]

    def test_externer_bericht_publiziert_die_kleinstzelle_nicht_mehr_als_letzte_zeile(
        self, facility, client_qualified, doc_type_contact, doc_type_crisis, staff_user, report_period
    ):
        """Ende-zu-Ende ueber ``build_external_report``: der Dokumenttyp mit der
        Kleinstfallzahl (``Kontakt``, 2) darf nicht laenger daran erkennbar
        sein, dass er als letzte Zeile steht.
        """
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_crisis, staff_user, 9)
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 2)

        rows = build_external_report(facility, *report_period)["by_document_type"]

        # Query-Ordnung waere ["Krisengespräch", "Kontakt"] (9 vor 2). Kanonisch
        # steht ``Kontakt`` (category "contact") vor ``Krisengespräch``
        # (category "service") — beide sind unterdrueckt (primaer/sekundaer),
        # die Position sagt also nichts mehr ueber ihre Groesse.
        assert [row["name"] for row in rows] == ["Kontakt", "Krisengespräch"]
        assert all(row["suppressed"] for row in rows)


@pytest.mark.django_db
class TestPersonenabgeleiteteTabellenFailSafe:
    """K0-Fail-safe (Refs #1768): Personenzahl < k ⇒ personenabgeleitete Tabellen weg.

    Die k-Schwelle wird gegen **Ereignis**zahlen geprueft (``Count("id")``), nicht
    gegen Personen. Eine Alterszelle „Unter 18: 7" kann damit von einer einzigen
    Person stammen und wurde trotzdem publiziert; selbstbeweisend wurde der Fall,
    sobald ``unique_clients`` unterdrueckt war — dann stand im Artefakt, dass die
    Gesamtpersonenmenge < k ist, waehrend die Verteilung genau dieser Menge im
    Klartext danebenstand.

    Fail-safe: liegt ``unique_clients`` unter der Schwelle, faellt die **gesamte**
    personenabgeleitete Aufschluesselung (``by_age_cluster``, ``by_contact_stage``),
    nicht nur die Kennzahl. Ereignisabgeleitete Aggregate (``total_contacts``,
    ``by_document_type``) bleiben unberuehrt — sie zaehlen Ereignisse, nicht Personen.
    """

    def _clients(self, facility, staff_user, count: int):
        from core.models import Client

        return [
            Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.QUALIFIED,
                pseudonym=f"K0-{index:02d}",
                created_by=staff_user,
            )
            for index in range(count)
        ]

    def test_externer_bericht_laesst_personen_tabellen_ganz_fallen(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Eine Person, neun Ereignisse: die Ereigniszahlen liegen ueber der
        Schwelle, die Personenmenge (1) nicht — die Altersverteilung dieser
        einen Person darf im Artefakt nicht mehr auftauchen.
        """
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 9)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] is None
        assert "by_age_cluster" not in report
        assert "by_contact_stage" not in report
        # Ereignisabgeleitete Aggregate bleiben — sie zaehlen keine Personen.
        assert report["total_contacts"] == 9
        assert [row["count"] for row in report["by_document_type"]] == [9]

    def test_externer_bericht_behaelt_die_tabellen_ab_der_schwelle(
        self, facility, doc_type_contact, staff_user, report_period
    ):
        """Gegenprobe: fuenf Personen (= Default-Schwelle) ⇒ unveraendert."""
        from core.services.dashboard import build_external_report

        for person in self._clients(facility, staff_user, 5):
            _make_events(facility, person, doc_type_contact, staff_user, 2)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] == 5
        assert report["by_age_cluster"]
        assert report["by_contact_stage"]

    def test_suppress_report_stats_laesst_beide_personen_tabellen_fallen(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 3,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_report_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert "by_contact_stage" not in out
        assert out["unique_clients"] is None
        assert out["total_contacts"] == 40
        assert out["by_document_type"][0]["count"] == 40

    def test_suppress_report_stats_behaelt_die_tabellen_ab_der_schwelle(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 5,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "persons_by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40, "persons": 5}],
        }
        out = suppress_report_stats(facility, stats)

        assert out["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 40, "suppressed": False}]
        assert out["by_contact_stage"] == {"anonym": 20, "identifiziert": 12, "qualifiziert": 8}

    def test_suppress_sample_report_1_stats_laesst_die_altersgruppen_fallen(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 4,
            "by_category": [("Kontakte", 40)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert out["unique_clients"] is None
        # Die Kategorie-Aufschluesselung zaehlt Ereignisse und bleibt.
        assert out["by_category"][0]["count"] == 40

    def test_ohne_personenzahl_greift_der_fail_safe(self, facility):
        """``unique_clients`` fehlt/ist ``None`` ⇒ wie „unter der Schwelle" behandeln."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": None,
            "by_contact_stage": {"anonym": 40, "identifiziert": 0, "qualifiziert": 0},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)

        assert "by_age_cluster" not in out
        assert "by_contact_stage" not in out

    def test_schwelle_1_deaktiviert_auch_den_fail_safe(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Bei k=1 ist jede Personenmenge >= 1 gross genug — der Guard schweigt."""
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 1)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] == 1
        assert report["by_age_cluster"]
        assert report["by_contact_stage"]


@pytest.mark.django_db
class TestExternalReportContent:
    """Inhaltliche Tests: keine Pseudonyme, K-Anon-Schwelle wirkt."""

    def test_report_contains_no_pseudonyms(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 6)

        report = build_external_report(facility, *report_period)

        # Pseudonym darf NICHT im Output sein:
        report_str = str(report)
        assert client_qualified.pseudonym not in report_str
        # top_clients-Key darf nicht im Report enthalten sein:
        assert "top_clients" not in report

    def test_report_includes_total_contacts(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 7)

        report = build_external_report(facility, *report_period)

        assert "total_contacts" in report
        assert report["total_contacts"] == 7

    def test_report_includes_aggregates_above_threshold(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Aggregate >= k_anonymity_threshold (Default 5) bleiben unveraendert."""
        from core.services.dashboard import build_external_report

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 8)

        report = build_external_report(facility, *report_period)

        by_dt = report["by_document_type"]
        # 8 Events vom Dokumenttyp "Kontakt" -> ueber Schwelle 5 -> count bleibt
        kontakt_row = next((r for r in by_dt if r["name"] == "Kontakt"), None)
        assert kontakt_row is not None
        assert kontakt_row["count"] == 8

    def test_report_suppresses_aggregates_below_threshold(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Aggregate < k_anonymity_threshold werden unterdrueckt."""
        from core.services.dashboard import build_external_report

        # 3 Events -> unter Default-Schwelle 5
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 3)

        report = build_external_report(facility, *report_period)

        by_dt = report["by_document_type"]
        # Kategorie mit 3 Events: Count wird durch Sentinel ersetzt
        kontakt_row = next((r for r in by_dt if r["name"] == "Kontakt"), None)
        assert kontakt_row is not None
        # Suppressed-Sentinel: 0 oder None oder spezielles Token — wir nutzen `None`
        # damit der Fakt der Unterdrueckung explizit ist.
        assert kontakt_row["count"] is None
        assert kontakt_row.get("suppressed") is True


@pytest.mark.django_db
class TestExternalReportMetadata:
    """Datenschutzprofil-Metadaten im Report-Kopf."""

    def test_metadata_block_present(self, facility, report_period):
        from core.services.dashboard import build_external_report

        report = build_external_report(facility, *report_period)

        meta = report["metadata"]
        assert meta["facility"] == facility.name
        assert meta["date_from"] == report_period[0].isoformat()
        assert meta["date_to"] == report_period[1].isoformat()
        assert meta["k_anonymity_threshold"] == 5  # Default aus Settings
        assert "generated_at" in meta
        assert meta["privacy_profile"] == "external"

    def test_metadata_weist_die_live_datenquelle_aus(self, facility, report_period):
        """Refs #1743: der externe Bericht liest live — und sagt das im Artefakt.

        Stabiler Code fuer Maschinen (``?format=json``), uebersetzte Kurzform
        fuer den Kopf der HTML-Fassung.
        """
        from core.services.dashboard import build_external_report

        meta = build_external_report(facility, *report_period)["metadata"]

        assert meta["data_source"] == "live"
        assert meta["data_source_note"] == "Live-Abfrage zum Erstellungszeitpunkt"

    def test_metadata_uses_custom_threshold_from_settings(self, facility, report_period):
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(
            facility=facility,
            defaults={"k_anonymity_threshold": 10},
        )

        report = build_external_report(facility, *report_period)
        assert report["metadata"]["k_anonymity_threshold"] == 10


@pytest.mark.django_db
class TestExternalReportEdgeCases:
    """Edge-Cases: leere Periode, threshold=1."""

    def test_empty_period_suppresses_total_and_returns_empty_lists(self, facility, report_period):
        # Security R14: die Randsumme ``total_contacts`` unterliegt jetzt selbst
        # der k-Schwelle. Eine leere Periode liefert total_contacts=0 < 5 und
        # wird daher wie jede andere Kleinstfallzahl auf ``None`` unterdrueckt
        # (konsistent mit ``unique_clients``, das schon immer 0 -> None abbildet).
        from core.services.dashboard import build_external_report

        report = build_external_report(facility, *report_period)

        assert report["total_contacts"] is None
        assert report["by_document_type"] == []

    def test_threshold_1_disables_suppression(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        """Bei k=1 wird nichts unterdrueckt (alle Counts sichtbar)."""
        from core.models import Settings
        from core.services.dashboard import build_external_report

        Settings.objects.update_or_create(
            facility=facility,
            defaults={"k_anonymity_threshold": 1},
        )

        _make_events(facility, client_qualified, doc_type_contact, staff_user, 1)

        report = build_external_report(facility, *report_period)
        by_dt = report["by_document_type"]
        assert by_dt[0]["count"] == 1
        assert by_dt[0].get("suppressed", False) is False


@pytest.mark.django_db
class TestSampleReport1StatsSuppression:
    """Refs #1278 (T1): Das Sachbericht-PDF — das am ehesten externe Artefakt —
    muss derselben k-Anon-Kleinstfallzahl-Suppression unterliegen wie der
    externe On-Screen-Bericht. ``suppress_sample_report_1_stats`` leitet die
    Sachbericht-Aggregate durch dieselbe ``_suppress_small``-Logik (inkl.
    sekundaerer Suppression) und Nones ``unique_clients`` < k — die publizierte
    Randsumme ``total`` bleibt roh (analog ``total_contacts``).
    """

    def test_by_category_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [],
            "unique_clients": 11,
        }
        result = suppress_sample_report_1_stats(facility, stats)
        cats = {r["name"]: r for r in result["by_category"]}
        # Zwei Zellen < 5 -> beide primaer unterdrueckt, keine Sekundaer-Suppression.
        assert cats["Beratung"]["count"] is None and cats["Beratung"]["suppressed"] is True
        assert cats["Versorgung"]["count"] is None and cats["Versorgung"]["suppressed"] is True
        # Zelle >= 5 bleibt sichtbar.
        assert cats["Kontakte"]["count"] == 12 and cats["Kontakte"]["suppressed"] is False

    def test_by_age_cluster_cells_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 20,
            "by_category": [],
            "by_age_cluster": [
                {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
                {"cluster": "18_26", "label": "18–26", "count": 2, "persons": 2},
                {"cluster": "27_plus", "label": "27+", "count": 15, "persons": 6},
            ],
            "unique_clients": 11,
        }
        result = suppress_sample_report_1_stats(facility, stats)
        ages = {r["cluster"]: r for r in result["by_age_cluster"]}
        assert ages["u18"]["count"] is None and ages["u18"]["suppressed"] is True
        assert ages["18_26"]["count"] is None and ages["18_26"]["suppressed"] is True
        assert ages["27_plus"]["count"] == 15 and ages["27_plus"]["suppressed"] is False
        # Labels bleiben fuer die Template-Darstellung erhalten.
        assert ages["u18"]["label"] == "Unter 18"

    def test_unique_clients_and_total_below_threshold_both_suppressed(self, facility):
        # Security R14: Ist die publizierte Randsumme ``total`` selbst < k, ist
        # sie eine Kleinstfallzahl und wird ebenfalls unterdrueckt (frueher blieb
        # sie hier roh — genau der von R14 adressierte Leak).
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 3, "by_category": [], "by_age_cluster": [], "unique_clients": 2}
        result = suppress_sample_report_1_stats(facility, stats)
        assert result["unique_clients"] is None  # 2 < 5
        assert result["total"] is None  # 3 < 5 -> Randsumme selbst unterdrueckt

    def test_unique_clients_above_threshold_kept(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 30, "by_category": [], "by_age_cluster": [], "unique_clients": 9}
        assert suppress_sample_report_1_stats(facility, stats)["unique_clients"] == 9

    def test_does_not_mutate_input(self, facility):
        import copy

        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 18,
            "by_category": [("Kontakte", 12), ("Beratung", 4), ("Versorgung", 2)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 3}],
            "unique_clients": 11,
        }
        snapshot = copy.deepcopy(stats)
        suppress_sample_report_1_stats(facility, stats)
        assert stats == snapshot

    def test_custom_threshold_from_settings_disables_suppression(self, facility):
        from core.models import Settings
        from core.services.dashboard import suppress_sample_report_1_stats

        Settings.objects.update_or_create(facility=facility, defaults={"k_anonymity_threshold": 1})
        stats = {"total": 1, "by_category": [("Kontakte", 1)], "by_age_cluster": [], "unique_clients": 1}
        result = suppress_sample_report_1_stats(facility, stats)
        assert result["by_category"][0]["count"] == 1
        assert result["by_category"][0]["suppressed"] is False
        assert result["unique_clients"] == 1
        # Bei k=1 ist auch die Randsumme (1 >= 1) nicht unterdrueckt.
        assert result["total"] == 1


@pytest.mark.django_db
class TestMarginTotalSuppression:
    """Security R14: die Randsumme selbst ist eine Kleinstfallzahl, wenn sie
    unter der Schwelle liegt — dann None statt roher Wert.

    ``build_external_report`` (``total_contacts``), ``suppress_sample_report_1_stats``
    (``total``) und ``suppress_report_stats`` (``total_contacts``) publizierten die
    Gesamtsumme bislang roh. Liegt sie selbst < k, ist sie eine Kleinstfallzahl —
    dann ``None`` + Template-Marker. Randsummen >= Schwelle bleiben roh (die
    sekundaere Suppression schuetzt bereits die Rueckrechnung einzelner Zellen).
    """

    def test_external_report_total_below_threshold_suppressed(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        # 2 Events gesamt -> Randsumme 2 < Default-Schwelle 5.
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 2)

        report = build_external_report(facility, *report_period)
        assert report["total_contacts"] is None

    def test_external_report_total_at_or_above_threshold_stays_raw(
        self, facility, client_qualified, doc_type_contact, staff_user, report_period
    ):
        from core.services.dashboard import build_external_report

        # 7 Events gesamt -> Randsumme 7 >= Schwelle 5 bleibt roh.
        _make_events(facility, client_qualified, doc_type_contact, staff_user, 7)

        report = build_external_report(facility, *report_period)
        assert report["total_contacts"] == 7

    def test_sample_report_1_total_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {"total": 3, "unique_clients": 0, "by_category": [], "by_age_cluster": []}
        out = suppress_sample_report_1_stats(facility, stats)
        assert out["total"] is None

    def test_report_stats_total_below_threshold_suppressed(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 3,
            "unique_clients": 0,
            "by_contact_stage": {},
            "by_document_type": [],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)
        assert out["total_contacts"] is None

    @pytest.mark.parametrize("total_contacts", [5, 12])
    def test_report_stats_total_at_or_above_threshold_stays_raw(self, facility, total_contacts):
        """Final-Review Befund 4: die ``>=``-Vergleichsgrenze selbst (Randsumme
        exakt gleich der Default-Schwelle 5) und ein Wert deutlich darueber (12)
        bleiben beide roh — nur ``< k`` ist eine Kleinstfallzahl, ``>= k`` nicht,
        auch nicht direkt an der Grenze."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": total_contacts,
            "unique_clients": 0,
            "by_contact_stage": {},
            "by_document_type": [],
            "by_age_cluster": [],
        }
        out = suppress_report_stats(facility, stats)
        assert out["total_contacts"] == total_contacts


class TestPersonenbasiertesZellkriterium:
    """K0-b (Refs #1770): die k-Schwelle prueft die DISTINCT-Personenzahl.

    Bis #1770 verglich ``_suppress_small`` die Schwelle ausschliesslich gegen
    eine **Ereignis**zahl (``Count("id")``). Eine Alterszelle „Unter 18: 7"
    konnte damit von einer einzigen Person stammen und wurde trotzdem
    publiziert — die Aequivalenzklasse, die k-Anonymitaet schuetzen soll, hatte
    Groesse 1. Personenabgeleitete Zeilen tragen deshalb die DISTINCT-
    Personenzahl ihres Buckets mit; unterdrueckt wird, sobald die **strengere**
    der beiden Zahlen unter der Schwelle liegt (min-Logik).

    Die Personenzahl ist reine Suppressions-*Eingabe* und darf nie in einem
    Artefakt landen — sonst waere sie selbst ein neuer Kleinstfallzahl-Kanal.
    """

    def test_zelle_mit_vielen_ereignissen_einer_person_wird_unterdrueckt(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 7, "persons": 1},
        ]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True
        assert by_cluster["u18"]["count"] is None

    def test_ereigniszahl_bleibt_das_strengere_kriterium_wenn_sie_kleiner_ist(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 3, "persons": 3},
        ]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True

    def test_zelle_mit_genug_personen_bleibt_sichtbar(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 8, "persons": 6},
        ]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)

        assert all(row["suppressed"] is False for row in result)

    def test_unbekannte_personenzahl_wird_unterdrueckt(self):
        """``persons=None`` = nicht rekonstruierbar ⇒ fail-safe unterdruecken."""
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 20, "persons": None},
        ]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)
        by_cluster = {row["cluster"]: row for row in result}

        assert by_cluster["u18"]["suppressed"] is True

    def test_personenzahl_verlaesst_die_suppression_nie(self):
        from core.services.dashboard.external_report import ORDER_AGE_CLUSTER, _suppress_small

        rows = [
            {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
            {"cluster": "u18", "label": "Unter 18", "count": 7, "persons": 1},
        ]
        result = _suppress_small(rows, threshold=5, margin=None, order=ORDER_AGE_CLUSTER)

        assert all("persons" not in row for row in result)

    def test_ereignisabgeleitete_zeilen_ohne_personenfeld_bleiben_ereignisbasiert(self):
        """``by_document_type``/``by_category`` zaehlen Ereignisse — kein Personenfeld,
        keine Personenpruefung (sonst waere jede Dokumenttyp-Zeile unterdrueckt)."""
        from core.services.dashboard.external_report import ORDER_DOCUMENT_TYPE, _suppress_small

        rows = [{"name": "Kontakt", "category": "contact", "count": 30}]
        result = _suppress_small(rows, threshold=5, margin=30, order=ORDER_DOCUMENT_TYPE)

        assert result[0]["suppressed"] is False

    def test_stage_dict_unterdrueckt_bei_zu_wenigen_personen(self):
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 40, "identifiziert": 30, "qualifiziert": 20}
        result = _suppress_stage_dict(
            stages, threshold=5, persons={"anonym": 40, "identifiziert": 30, "qualifiziert": 2}, margin=90
        )

        assert result["qualifiziert"] is None
        # Das Machbarkeits-Audit opfert zusaetzlich die kleinste sichtbare Stufe
        # (K2, Refs #1770) — dieselbe Opferwahl wie vorher, neues Kriterium.
        assert result["identifiziert"] is None
        assert result["anonym"] == 40

    def test_stage_dict_ohne_personenzahlen_unterdrueckt_alles(self):
        """Alt-Format ohne ``persons_by_contact_stage`` ⇒ unbekannt ⇒ fail-safe."""
        from core.services.dashboard.external_report import _suppress_stage_dict

        stages = {"anonym": 40, "identifiziert": 30, "qualifiziert": 20}
        result = _suppress_stage_dict(stages, threshold=5, persons=None, margin=90)

        assert result == {"anonym": None, "identifiziert": None, "qualifiziert": None}


@pytest.mark.django_db
class TestPersonenkriteriumInDenWrappern:
    """K0-b wirkt in allen drei Wrappern — und Alt-Format ist fail-safe."""

    def test_externer_bericht_unterdrueckt_zelle_einer_einzelnen_person(
        self, facility, doc_type_contact, doc_type_crisis, staff_user, report_period
    ):
        """Acht Personen (⇒ ``unique_clients`` >= k, der #1768-Guard schweigt),
        aber zwei Altersgruppen mit vielen Ereignissen je EINER Person.

        Die Alterstabelle bleibt stehen (die Gesamtpersonenmenge ist gross
        genug), ihre Einzelpersonen-Zeilen fallen. Die **Personen-Randsumme**
        faellt dabei mit (Auflage 1 zu K0-b, Refs #1770): Σ ``persons`` =
        ``unique_clients`` ist eine publizierte Partition, und sichtbar bliebe
        sie nur, solange keine Zeile personenbedingt unterdrueckt ist.
        """
        from core.models import Client
        from core.services.dashboard import build_external_report

        solo_a = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.U18,
            pseudonym="K0b-solo-a",
            created_by=staff_user,
        )
        _make_events(facility, solo_a, doc_type_contact, staff_user, 7)
        solo_b = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.UNKNOWN,
            pseudonym="K0b-solo-b",
            created_by=staff_user,
        )
        _make_events(facility, solo_b, doc_type_contact, staff_user, 6)
        for index in range(6):
            person = Client.objects.create(
                facility=facility,
                contact_stage=Client.ContactStage.QUALIFIED,
                age_cluster=Client.AgeCluster.AGE_27_PLUS,
                pseudonym=f"K0b-{index:02d}",
                created_by=staff_user,
            )
            _make_events(facility, person, doc_type_crisis, staff_user, 3)

        report = build_external_report(facility, *report_period)

        assert report["unique_clients"] is None, "Personen-Randsumme faellt mit der ersten Personen-Zelle"
        by_cluster = {row["cluster"]: row for row in report["by_age_cluster"]}
        assert by_cluster["u18"]["suppressed"] is True, "Altersgruppe mit nur einer Person muss fallen"
        assert by_cluster["unknown"]["suppressed"] is True
        assert by_cluster["27_plus"]["suppressed"] is False, "6 Personen/18 Ereignisse bleiben sichtbar"
        assert all("persons" not in row for row in report["by_age_cluster"])

    def test_suppress_report_stats_behandelt_alt_format_ohne_personenzahlen_fail_safe(self, facility):
        """Alt-Snapshot-Format: ``by_age_cluster`` ohne ``persons``,
        kein ``persons_by_contact_stage`` ⇒ alles unterdrueckt."""
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_report_stats(facility, stats)

        assert out["by_age_cluster"][0]["suppressed"] is True
        assert out["by_contact_stage"] == {"anonym": None, "identifiziert": None, "qualifiziert": None}
        # Ereignisabgeleitete Aggregate bleiben unberuehrt.
        assert out["by_document_type"][0]["count"] == 40

    def test_suppress_report_stats_gibt_die_personenzahlen_nicht_weiter(self, facility):
        from core.services.dashboard import suppress_report_stats

        stats = {
            "total_contacts": 40,
            "unique_clients": 12,
            "by_contact_stage": {"anonym": 20, "identifiziert": 12, "qualifiziert": 8},
            "persons_by_contact_stage": {"anonym": 9, "identifiziert": 7, "qualifiziert": 6},
            "by_document_type": [{"name": "Kontakt", "category": "contact", "count": 40}],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40, "persons": 12}],
        }
        out = suppress_report_stats(facility, stats)

        assert "persons_by_contact_stage" not in out
        assert out["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 40, "suppressed": False}]

    def test_suppress_sample_report_1_stats_behandelt_alt_format_fail_safe(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        stats = {
            "total": 40,
            "unique_clients": 12,
            "by_category": [("Kontakte", 40)],
            "by_age_cluster": [{"cluster": "u18", "label": "Unter 18", "count": 40}],
        }
        out = suppress_sample_report_1_stats(facility, stats)

        assert out["by_age_cluster"][0]["suppressed"] is True
        assert out["by_category"][0]["count"] == 40

    def test_sample_report_1_backend_liefert_personenzahlen(
        self, facility, doc_type_contact, staff_user, report_period
    ):
        """Der Snapshot-Payload traegt ``persons`` je Cluster (Formatänderung).

        Bewusst mapping-agnostisch: die Buckets entstehen weiterhin allein aus
        ``system_type``/``age_cluster``, damit der Slug-Zweitschluessel aus
        #1741 hier konfliktfrei aufsetzen kann.
        """
        from core.models import Client, DocumentType
        from core.reporting.registry import get_backend

        doc_type_contact.system_type = DocumentType.SystemType.CONTACT
        doc_type_contact.save()
        person = Client.objects.create(
            facility=facility,
            contact_stage=Client.ContactStage.QUALIFIED,
            age_cluster=Client.AgeCluster.U18,
            pseudonym="K0b-payload",
            created_by=staff_user,
        )
        _make_events(facility, person, doc_type_contact, staff_user, 4)

        summary = get_backend("sample-report-1").build_dataset(facility, *report_period).summary

        assert summary["by_age_cluster"] == [{"cluster": "u18", "label": "Unter 18", "count": 4, "persons": 1}]


@pytest.mark.django_db
class TestResidualGuardEskalation:
    """K1 (Refs #1770): die Eskalationsleiter von ``apply_residual_guard``.

    Der Guard ist die **zweite** Verteidigungslinie hinter dem tabellenlokalen
    Machbarkeits-Audit — und genau deshalb feuert er in normalen Berichten fast
    nie: das Audit hat die Rückrechenbarkeit meist schon aufgelöst, bevor der
    Guard überhaupt hinsieht. Ein Deckungslauf über die Reporting-Testfläche
    zeigte, dass `_escalate` dort **null**-mal aufgerufen wurde; Leiter,
    Randsummen-Drop, Fail-safe und Iterationsgrenze waren damit unverifiziert.

    Diese Tests rufen den Guard deshalb direkt auf konstruierten publizierten
    Sichten auf. Zwei davon (Cross-Partition und Fail-safe) sind **keine**
    Konstruktion, sondern ein realer, über `suppress_report_stats` erreichbarer
    Zustand.
    """

    def _breakdowns(self):
        from core.services.dashboard.external_report import _EXTERNAL_REPORT_BREAKDOWNS

        return _EXTERNAL_REPORT_BREAKDOWNS

    def test_opferzelle_faellt_wenn_eine_unterdrueckte_zelle_gepinnt_ist(self):
        """(a) Genau eine unterdrückte Zeile ⇒ sie IST das Residuum (12 von 15).

        Der Guard opfert die kleinste sichtbare Zelle — dieselbe Regel wie das
        Audit — und rechnet danach neu: zwei Unbekannte aus [1, ∞) mit Summe 15
        sind nicht mehr eindeutig.
        """
        from core.services.dashboard.external_report import apply_residual_guard

        report = {
            "total_contacts": 15,
            "unique_clients": 40,
            "by_document_type": [
                {"name": "Gross", "category": "note", "count": 12, "suppressed": False},
                {"name": "Klein", "category": "contact", "count": None, "suppressed": True},
            ],
        }
        apply_residual_guard(report, 5, self._breakdowns())

        assert [row["suppressed"] for row in report["by_document_type"]] == [True, True]
        assert report["total_contacts"] == 15, "die Randsumme bleibt — es gab eine Zelle zu opfern"

    def test_randsumme_faellt_wenn_keine_sichtbare_zelle_mehr_da_ist(self):
        """(b) Fünf unterdrückte Zeilen, Randsumme 5 ⇒ jede Zeile ist zwingend 1
        (Zeilen-Existenz). Es gibt keine sichtbare Zelle mehr zu opfern, also
        fällt die Randsumme — ohne sie gibt es keine Gleichung."""
        from core.services.dashboard.external_report import apply_residual_guard

        report = {
            "total_contacts": 5,
            "unique_clients": 40,
            "by_document_type": [
                {"name": f"T{index}", "category": "contact", "count": None, "suppressed": True} for index in range(5)
            ],
        }
        apply_residual_guard(report, 5, self._breakdowns())

        assert report["total_contacts"] is None
        assert len(report["by_document_type"]) == 5, "die Zeilen selbst bleiben stehen"

    def test_stufen_dict_opfert_die_kleinste_sichtbare_stufe(self):
        """(a) für die Dict-Variante: dieselbe Leiter, andere Datenform."""
        from core.services.dashboard.external_report import apply_residual_guard

        report = {
            "total_contacts": 30,
            "unique_clients": 40,
            "by_contact_stage": {"anonym": 18, "identifiziert": 9, "qualifiziert": None},
        }
        apply_residual_guard(report, 5, self._breakdowns())

        assert report["by_contact_stage"]["identifiziert"] is None, "kleinste sichtbare Stufe fällt"
        assert report["by_contact_stage"]["anonym"] == 18

    def test_vollstaendig_sichtbare_schwestertabelle_stellt_die_randsumme_wieder_her(self):
        """(c) Cross-Partition-Rekonstruktion.

        ``by_document_type`` und ``by_contact_stage`` partitionieren **dieselbe**
        Randsumme. Ist die Stufentabelle vollständig sichtbar, ist ihre Summe die
        Randsumme — das Fallenlassen von ``total_contacts`` allein nützt dann
        nichts, weil der Empfänger sie nebenan addiert. Der Guard rechnet das mit
        und eskaliert weiter, bis nichts mehr rückrechenbar ist.
        """
        from core.services.dashboard.external_report import apply_residual_guard

        report = {
            "total_contacts": 15,
            "unique_clients": 40,
            "by_document_type": [
                {"name": f"T{index}", "category": "contact", "count": None, "suppressed": True} for index in range(15)
            ],
            "by_contact_stage": {"anonym": 5, "identifiziert": 5, "qualifiziert": 5},
        }
        apply_residual_guard(report, 5, self._breakdowns())

        assert report["total_contacts"] is None
        # Fail-safe: die Randsumme allein reicht nicht, weil die Stufen sie
        # rekonstruieren — und in den Stufen gibt es keinen Befund, an dem die
        # Leiter ansetzen könnte. Also fallen beide Aufschlüsselungen.
        assert "by_document_type" not in report
        assert "by_contact_stage" not in report
        assert report["unique_clients"] is None, "die Personen-Randsumme faellt im Fail-safe mit"

    def test_fail_safe_kollabiert_das_reale_artefakt(self, facility):
        """(d) Derselbe Zustand über den echten Wrapper — kein Laboraufbau.

        15 Dokumenttypen mit je einem Ereignis, drei gleich große Kontaktstufen:
        der Bericht verliert alles außer den (unterdrückten) Kennzahlen. Das ist
        die dokumentierte Fail-safe-Richtung, nicht ein Fehler — der Preis dafür,
        dass 15 Zeilen mit Randsumme 15 jede für sich zwingend „1" sind, und dass
        die vollständig sichtbare Stufentabelle die Randsumme rekonstruierbar
        hält.

        Dass dieser Zustand über den Wrapper erreichbar ist, ist zugleich die
        Begründung für den „unterdrückt"-Zweig in den Templates: „Keine Daten im
        Zeitraum" wäre hier sachlich falsch.
        """
        from core.services.dashboard import suppress_report_stats

        out = suppress_report_stats(
            facility,
            {
                "total_contacts": 15,
                "unique_clients": 20,
                "by_contact_stage": {"anonym": 5, "identifiziert": 5, "qualifiziert": 5},
                "persons_by_contact_stage": {"anonym": 5, "identifiziert": 5, "qualifiziert": 5},
                "by_document_type": [{"name": f"T{index}", "category": "contact", "count": 1} for index in range(15)],
                "by_age_cluster": [],
            },
        )

        assert out == {"total_contacts": None, "unique_clients": None}

    def test_iterationsgrenze_faellt_fail_safe_auf_die_ganze_aufschluesselung(self, monkeypatch):
        """(e) Wird die harte Obergrenze erreicht, fällt die Aufschlüsselung.

        Die Grenze ist im Normalbetrieb unerreichbar (jeder Schritt unterdrückt
        monoton eine weitere Zelle oder Randsumme). Sie ist die Sicherung gegen
        eine künftige Erweiterung, die diese Monotonie verliert — und genau
        deshalb muss ihr Verhalten geprüft sein und nicht nur behauptet.
        """
        from core.services.dashboard import external_report

        monkeypatch.setattr(external_report, "MAX_GUARD_ITERATIONS", 1)
        report = {
            "total_contacts": 5,
            "unique_clients": 40,
            "by_document_type": [
                {"name": f"T{index}", "category": "contact", "count": None, "suppressed": True} for index in range(5)
            ],
        }
        external_report.apply_residual_guard(report, 5, external_report._EXTERNAL_REPORT_BREAKDOWNS)

        assert "by_document_type" not in report
        assert report["total_contacts"] is None
