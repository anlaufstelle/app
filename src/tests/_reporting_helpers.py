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

from core.models.slug import slug_base
from core.reporting.registry import get_backend


def get_sample_report_1_statistics(facility, date_from, date_to):
    """Rohes Sachbericht-Aggregat fuer ``facility`` im Zeitraum ``[date_from, date_to]``."""
    return get_backend("sample-report-1").build_dataset(facility, date_from, date_to).summary


# --- Backfill-Abbild fuer Testfixturen (E3, Refs #1849) ----------------------
#
# Seit E3 binden die Berichts-Backends ueber ``report_key``. Bestandsdaten
# bekommen ihn aus Migration ``0145``; Testfixturen legen ihre Zeilen aber NACH
# der Migration an und muessen ihn deshalb selbst mitgeben — sonst pruefen die
# Fachtests nicht mehr die Zahlen, sondern nur noch den Slug-Zweitschluessel.
#
# Bewusst als kleine Abbildung statt als Import der Migration: die Fixturen
# brauchen den Wert je Zeile, nicht den Massen-Backfill. Dass beide dasselbe
# Ergebnis liefern, prueft ``test_reporting_report_key_parity.py`` (dort laeuft
# die Migrationsfunktion selbst).
_REPORT_KEY_JE_SYSTEM_TYPE = {
    "contact": "contact",
    "crisis": "crisis",
    "medical": "medical",
    "needle_exchange": "needle_exchange",
    "accompaniment": "accompaniment",
    "counseling": "counseling",
    "referral": "referral",
    "note": "excluded",
    "ban": "excluded",
}

_FELD_REPORT_KEY_JE_SLUG = {
    "dauer": "dauer_minuten",
    "ausgabe": "ausgabe_stueck",
    "rueckgabe": "rueckgabe_stueck",
    "leistungen": "leistungen",
    "art-der-krise": "art_der_krise",
}


def report_key_fuer(system_type) -> str | None:
    """Der ``report_key``, den der Backfill diesem ``system_type`` gaebe."""
    return _REPORT_KEY_JE_SYSTEM_TYPE.get(str(system_type) if system_type else "")


def feld_report_key_fuer(name: str) -> str | None:
    """Der ``report_key``, den der Backfill der Feldvorlage mit diesem Namen gaebe.

    Der Slug entsteht in den Fixturen aus dem Namen (Modell-Automatik) — die
    Abbildung geht deshalb ueber denselben Weg.
    """
    return _FELD_REPORT_KEY_JE_SLUG.get(slug_base(name))
