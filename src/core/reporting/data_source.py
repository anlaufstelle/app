"""Quellenausweis der Berichts-Artefakte (Refs #1743, Produktentscheid Option c).

Die extern zirkulierenden Berichts-Artefakte lesen **bewusst weiter aus
unterschiedlichen Quellen**: Berichts-Panel, generischer CSV-/JSON-/XML-Export
und der externe On-Screen-Bericht fragen bei jedem Aufruf live gegen die
Ereignisse ab; das Sachbericht-PDF liest hybrid ueber die Monats-Snapshots.
Beide Wege sind gewollt (der Snapshot ueberdauert die Aufbewahrungsfristen, die
Live-Abfrage zaehlt ueber den Gesamtzeitraum genauer) — statt sie zu
vereinheitlichen, **weist jedes Artefakt seine Quelle selbst aus**.

Dieses Modul haelt dafuer genau zwei Dinge zusammen:

* **stabile, sprachunabhaengige Codes** (:data:`LIVE`, :data:`SNAPSHOT_HYBRID`)
  fuer die Metadatenfelder der Maschinenformate — sie duerfen sich nie mit der
  Oberflaechensprache aendern, sonst waere ein Konsument gezwungen, deutschen
  Fliesstext zu parsen;
* die **uebersetzbaren Texte** dazu (:data:`DATA_SOURCE_NOTES` als Kurzform fuer
  Metadatenzeilen, :data:`DATA_SOURCE_EXPLANATIONS` als Erklaersatz fuer die
  sichtbaren Artefakte PDF/HTML).

Das Modul importiert bewusst nichts aus der App — es wird sowohl von
``core.reporting.export`` als auch von ``core.services.dashboard.external_report``
und dem PDF-Pfad genutzt und darf keine Importzyklen aufziehen.
"""

from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

# Live-Abfrage gegen die Ereignisse (Panel, CSV/JSON/XML, externer Bericht).
LIVE = "live"
# Hybrider Lesepfad ueber Monats-Snapshots mit Live-Fallback (Sachbericht-PDF).
SNAPSHOT_HYBRID = "snapshot_hybrid"

# Kurzform fuer Metadatenfelder/-zeilen — eine Nominalphrase, kein Satz.
DATA_SOURCE_NOTES = {
    LIVE: _("Live-Abfrage zum Erstellungszeitpunkt"),
    SNAPSHOT_HYBRID: _("Monats-Aggregate"),
}

# Erklaersatz fuer die sichtbaren Artefakte. Benennt genau die Konsequenz, die
# einen Empfaenger sonst ratlos zuruecklaesst: warum zwei Berichte desselben
# Zeitraums verschiedene Zahlen zeigen koennen.
DATA_SOURCE_EXPLANATIONS = {
    LIVE: _(
        "Die Zahlen werden bei jedem Aufruf neu aus den Ereignisdaten berechnet; "
        "Zeiträume, deren Ereignisse nach Ablauf der Löschfristen entfernt wurden, erscheinen leer."
    ),
    SNAPSHOT_HYBRID: _(
        "Die Zahlen stammen aus den gespeicherten Monats-Auswertungen und bleiben auch nach Ablauf "
        "der Löschfristen erhalten; live erzeugte Berichte desselben Zeitraums können deshalb abweichen."
    ),
}

# --- K3-A3 (Refs #1770): Grundgesamtheit je Tabelle -------------------------
#
# In jedem Artefakt stehen Aggregate ueber **verschiedenen Basismengen**
# nebeneinander, und bis hierher benannte kein Artefakt, welche Tabelle welche
# meint. Das ist nicht nur ein Leck (die Differenz zweier Grundgesamtheiten ist
# K3), sondern inhaltlich irrefuehrend: „Kontakte nach Altersgruppe" und
# „Kontakte nach Dokumentationstyp" summieren sich zu verschiedenen Zahlen, und
# eine Leserin hat keine Chance zu erkennen, warum.
#
# Stabile Codes fuer die Maschinenformate (JSON/XML), uebersetzte Nominalphrasen
# fuer die sichtbaren Artefakte — dieselbe Trennung wie bei ``data_source``.
ALL_EVENTS = "all_events"
EVENTS_WITH_PERSON = "events_with_person"
MAPPED_EVENTS = "mapped_events"
PERSONS = "persons"

POPULATION_NOTES = {
    ALL_EVENTS: _("alle Ereignisse im Zeitraum"),
    EVENTS_WITH_PERSON: _("Ereignisse mit Personenbezug im Zeitraum"),
    MAPPED_EVENTS: _("Ereignisse mit Berichtszuordnung im Zeitraum"),
    PERSONS: _("Personen mit mindestens einem Ereignis im Zeitraum"),
}


def table_note(population: str, data_source: str):
    """Ein Satzfragment „Grundgesamtheit: … · Quelle: …" je Tabelle.

    Setzt bewusst auf der ``data_source``-Infrastruktur aus
    #1743 auf statt sie
    zu duplizieren: Herkunft und Grundgesamtheit sind zusammen die Antwort auf
    „welche Zahlen sehe ich hier eigentlich", und getrennte Ausweise waeren zwei
    Halbaussagen. Lazy (``format_lazy``), damit der Ausdruck der Sprache der
    Empfaengerin folgt und nicht der des schreibenden Prozesses.
    """
    return format_lazy(
        _("Grundgesamtheit: {population} · Quelle: {source}"),
        population=POPULATION_NOTES[population],
        source=DATA_SOURCE_NOTES[data_source],
    )
