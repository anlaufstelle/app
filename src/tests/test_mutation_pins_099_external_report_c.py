"""Pin-Tests der Welle L (0.99, Refs #2174/#2193) — ``core.services.dashboard.external_report``,
Batch ``external_report-c`` (Vollmutation-Survivor-Triage, 10 Survivors in
``_is_below_threshold``, ``_order_tiebreak``, ``_document_type_key``,
``_category_key``, ``_audit_feasible_span``, ``_finalize_report``).

Quelle: ``welle-l-0.99/reports/m-scratch/batch-external_report-c.json`` (Triage
``reports/m-scratch/pins-external_report-c/triage-099-external_report-c.json``).

Alle sechs Funktionen sind Teil der K-Anonymity-Suppressionskette (K0-K5,
Refs #1770): ``_is_below_threshold`` entscheidet je Zeile, ob sie unter der
Schwelle liegt, ``_order_tiebreak``/``_document_type_key``/``_category_key``
liefern die wertunabhaengige K5-Opferordnung fuer Wertgleichstaende,
``_audit_feasible_span`` ist das tabellenlokale K2-Machbarkeits-Audit der
Sekundaer-Suppression, ``_finalize_report`` verdrahtet den K1-Fixpunkt-Guard
am Berichtsabschluss. Alle Tests rufen die privaten Funktionen direkt auf
(reiner Rechenkern, keine DB-Zugriffe noetig) — ``django_db`` liegt trotzdem
modulweit an (Lehre 112: der Mutantenbaum hat keine ``.env`` und damit keinen
``ENCRYPTION_KEY``, ``_encryption_key`` muss vor jedem Import-Pfad greifen).
"""

from __future__ import annotations

import pytest

from core.services.dashboard.external_report import (
    ORDER_DOCUMENT_TYPE,
    ORDER_INPUT,
    _audit_feasible_span,
    _category_key,
    _document_type_key,
    _finalize_report,
    _is_below_threshold,
    _order_tiebreak,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("_encryption_key")]


class TestIsBelowThresholdFehlenderCountKey:
    """``_is_below_threshold`` mit Zeilen ohne ``count_key`` (Default-Zweig ``row.get(count_key, 0)``).

    Die drei Survivors dieser Klasse aendern ausschliesslich den Default von
    ``row.get(count_key, ...)`` (``0`` → ``None`` bzw. ``1``). Ueber
    ``_get_threshold`` ist die reale Schwelle nie <= 0 (``0 or
    DEFAULT_K_THRESHOLD`` ersetzt eine 0-Einstellung fail-safe durch 5) — die
    Divergenz ist deshalb nur per Direktaufruf des privaten Rechenkerns mit
    gezielt gewaehlter Schwelle beobachtbar (im Zweifel pinnen).
    """

    def test_schwelle_null_haelt_zeile_ohne_count_key_fuer_nicht_unterdrueckt(self):
        """Killt core.services.dashboard.external_report.x__is_below_threshold__mutmut_6
        und core.services.dashboard.external_report.x__is_below_threshold__mutmut_8:
        beide ersetzen den Default ``0`` durch ``None`` (``row.get(count_key, None)``
        bzw. das aequivalente ``row.get(count_key, )``). Bei Schwelle 0 ist
        ``_below_threshold(0, 0)`` False, ``_below_threshold(None, 0)`` aber
        wegen des Fail-safe-``is None``-Zweigs immer True — die Zeile faellt
        unter der Mutante faelschlich unter die Schwelle.
        """
        row: dict[str, object] = {}

        assert _is_below_threshold(row, "count", 0) is False

    def test_schwelle_eins_unterdrueckt_zeile_ohne_count_key(self):
        """Killt core.services.dashboard.external_report.x__is_below_threshold__mutmut_9:
        ersetzt den Default ``0`` durch ``1`` (``row.get(count_key, 1)``). Bei der
        real erreichbaren Schwelle 1 (``k_anonymity_threshold=1`` bleibt nach
        ``_get_threshold`` unveraendert) ist ``_below_threshold(0, 1)`` True
        (Zeile ohne Zaehler gilt als unterdrueckt), ``_below_threshold(1, 1)``
        dagegen False — die Mutante liesse eine Zeile ohne Zaehlwert
        faelschlich sichtbar.
        """
        row: dict[str, object] = {}

        assert _is_below_threshold(row, "count", 1) is True


class TestOrderTiebreakKanonischeOrdnung:
    """``_order_tiebreak`` liefert den wertunabhaengigen K5-Zweitschluessel der
    Opferwahl (Sekundaer-Suppression bei Wertgleichstand, Refs #1767/#1770).
    """

    def test_bekannte_ordnung_bindet_wertschluessel_und_index_an_den_tiebreak(self):
        """Killt core.services.dashboard.external_report.x__order_tiebreak__mutmut_1
        (setzt ``key = None`` fest, ignoriert die tatsaechliche ``order`` — der
        Tiebreak faellt fuer JEDE Ordnung auf reinen Index zurueck) und
        core.services.dashboard.external_report.x__order_tiebreak__mutmut_4
        (der ``key``-aufgeloeste Zweig liefert ``None`` statt
        ``(key(rows[index]), index)`` — der Tiebreak wird unbenutzbar).
        """
        rows = [{"name": "b"}, {"name": "a"}]

        tiebreak = _order_tiebreak(rows, ORDER_DOCUMENT_TYPE)

        assert tiebreak(0) == (("b",), 0)
        assert tiebreak(1) == (("a",), 1)

    def test_eingabeordnung_liefert_reinen_indextupel(self):
        """Killt core.services.dashboard.external_report.x__order_tiebreak__mutmut_3:
        im ``key is None``-Zweig (``ORDER_INPUT``, feste Eingabeordnung ohne
        Wertkanal) liefert die Mutante ``None`` statt ``(index,)`` — ein
        nachgelagerter Vergleich zweier Tiebreaks bei Wertgleichstand wuerde
        ``TypeError: '<' not supported between instances of 'NoneType' and
        'NoneType'`` werfen.
        """
        tiebreak = _order_tiebreak([{}, {}, {}], ORDER_INPUT)

        assert tiebreak(0) == (0,)
        assert tiebreak(2) == (2,)


class TestDocumentTypeUndCategoryKeyOhneNamen:
    """``_document_type_key``/``_category_key`` sind wertunabhaengige K5-Sortierschluessel
    ueber ``name`` — der Leerstring-Default fuer fehlenden Namen ist Teil der
    kanonischen, totalen Ordnung (Refs #1749/ADR-040).
    """

    def test_document_type_key_ohne_namen_faellt_auf_leerstring_zurueck(self):
        """Killt core.services.dashboard.external_report.x__document_type_key__mutmut_6:
        ersetzt den Default ``""`` durch ``"XXXX"`` (``row.get("name") or
        "XXXX"``) — eine Zeile ohne Dokumenttyp-Namen sortierte damit nicht
        mehr kanonisch an den Anfang, sondern irgendwo zwischen ``"X..."`` und
        ``"Y..."``.
        """
        assert _document_type_key({}) == ("",)
        assert _document_type_key({"name": ""}) == ("",)

    def test_category_key_ohne_namen_faellt_auf_leerstring_zurueck(self):
        """Killt core.services.dashboard.external_report.x__category_key__mutmut_6:
        identische Default-Aenderung (``""`` → ``"XXXX"``) wie bei
        ``_document_type_key``, hier fuer die Berichtskategorien-Ordnung.
        """
        assert _category_key({}) == ("",)
        assert _category_key({"name": ""}) == ("",)


class TestAuditFeasibleSpanOpferliste:
    """``_audit_feasible_span`` ist das tabellenlokale K2-Machbarkeits-Audit: es
    initialisiert die Opferliste ``victims`` leer und traegt nur Zellen ein,
    die DIESER Lauf als Sekundaer-Opfer auswaehlt (Refs #1770).
    """

    def test_bereits_unterdrueckte_person_gated_zelle_bekommt_keine_falsche_obergrenze(self):
        """Killt core.services.dashboard.external_report.x__audit_feasible_span__mutmut_4:
        initialisiert ``victims = [True] * len(values)`` statt ``[False] *
        len(values)``. Zelle 2 (Wert 6, bereits primaer unterdrueckt, aber
        personenbedingt — nicht als Sekundaer-Opfer dieses Laufs) bekaeme so
        schon in der ersten Partition faelschlich die Obergrenze
        ``hi=visible_min`` einer echten Opferzelle statt ``hi=None``
        („nach oben offen", K0-b). Das bindet nicht nur Zelle 2, sondern ueber
        die gemeinsame Residual-Rechnung auch Zelle 3 exakt — das Audit findet
        einen Befund, den es unter der Originalimplementierung nicht gibt, und
        opfert beide verbliebenen sichtbaren Zellen zusaetzlich (reiner
        Utility-Verlust ohne Schutzgewinn, empirisch am Rechenkern verifiziert
        mit einer aus dem Mutantenbaum geladenen Kopie).
        """
        values = [10, 10, 6, 3]
        flags = [False, False, True, True]
        rows = [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]
        tiebreak = _order_tiebreak(rows, ORDER_DOCUMENT_TYPE)

        _audit_feasible_span(
            values,
            flags,
            5,
            margin=34,
            min_values=[1, 1, 1, 1],
            person_gated=True,
            tiebreak=tiebreak,
        )

        assert flags == [False, False, True, True]


class TestFinalizeReportReichtAgeRowsDurch:
    """``_finalize_report`` verdrahtet die drei Abschlussschritte des K0/K1-Pfads
    in fester Reihenfolge und muss ``age_rows`` unveraendert an den
    K1-Residual-Guard (:func:`apply_residual_guard`) weiterreichen.
    """

    def test_age_rows_wird_als_keyword_an_apply_residual_guard_weitergereicht(self, monkeypatch):
        """Killt core.services.dashboard.external_report.x__finalize_report__mutmut_20:
        ruft ``apply_residual_guard(report, threshold, breakdowns, )`` — die
        trailing-comma-Variante von ``apply_residual_guard(report, threshold,
        breakdowns)`` — statt ``apply_residual_guard(report, threshold,
        breakdowns, age_rows=age_rows)`` auf. ``age_rows`` faellt damit auf den
        Funktions-Default ``()`` zurueck: der K1-Guard prueft dann die
        Personen-Randsumme (``by_age_cluster``/``unique_clients``) nicht mehr
        gegen die echten Altersgruppen-Zeilen. Spy statt vollem Report-Aufbau
        (Verhaltens-Injektion): die anderen beiden Abschlussschritte werden
        neutralisiert, ``apply_residual_guard`` zeichnet nur seine Aufrufargumente auf.
        """
        import core.services.dashboard.external_report as external_report_module

        captured: dict[str, object] = {}

        def _spy_apply_residual_guard(report, threshold, breakdowns, *, age_rows=()):
            captured["age_rows"] = age_rows

        monkeypatch.setattr(external_report_module, "_drop_person_derived_breakdowns", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            external_report_module,
            "drop_person_margin_on_person_suppression",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(external_report_module, "apply_residual_guard", _spy_apply_residual_guard)

        sentinel_age_rows = [{"persons": 3}]
        report: dict[str, object] = {}

        result = _finalize_report(report, 10, sentinel_age_rows, 5, ())

        assert captured["age_rows"] is sentinel_age_rows
        assert result is report
