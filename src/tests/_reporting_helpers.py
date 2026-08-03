"""Gemeinsamer Testzugriff auf Roh-Aggregate der Reporting-Backends (M5 P3, Refs #1061).

Bis M5 P3 lieferte eine eigene Funktion in ``core.services.system.export`` das
Sachbericht-Aggregat; sie ist mit der Umstellung auf die Backend-Registry
ersatzlos entfallen. Bestandstests pruefen weiterhin dieselben Zahlen — nur die
Bezugsquelle wechselt. Der Helfer buendelt diesen Wechsel an einer Stelle,
statt ihn in jeder Testdatei zu wiederholen.

Bewusst ``build_dataset(...).summary`` und **nicht** ``prepare_export``: das ist
der rohe, unsuppressierte Datenstand, den die Bestandstests erwarten. Die
k-Anon-Fassung des Export-/Panel-Pfads hat mit ``test_reporting_sample_report_1.py``
eine eigene Testfamilie.
"""

from core.reporting.registry import get_backend


def get_sample_report_1_statistics(facility, date_from, date_to):
    """Rohes Sachbericht-Aggregat fuer ``facility`` im Zeitraum ``[date_from, date_to]``."""
    return get_backend("sample-report-1").build_dataset(facility, date_from, date_to).summary
