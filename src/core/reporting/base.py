"""Abstraktes Backend-Interface fuer das Reporting-Plugin-System (M5, Refs #1061).

Ein ``DatasetBackend`` kapselt die Fachlogik eines Berichtstyps (z. B.
"Kontakte nach Monat", "Fallzahlen je Einrichtung") hinter einer festen
Schnittstelle, ueber die kuenftige Export-Pfade (CSV/PDF/JSON) und die
Snapshot-Speicherung backend-unabhaengig angesteuert werden koennen.
Registrierung/Discovery der konkreten Backends lebt in
:mod:`core.reporting.registry` — dieses Modul definiert nur den Vertrag.

``DatasetResult`` traegt zwei Sichten auf denselben Datenstand:

* ``records`` — die **tabellarische Projektion**: eine Liste von ``dict``s,
  deren Schluessel exakt den ``key``-Werten aus
  :meth:`DatasetBackend.get_field_specs` entsprechen. Das ist die Quelle
  fuer den CSV-Export — eine Zeile pro Eintrag, eine Spalte pro
  :class:`FieldSpec`.
* ``summary`` — das **vollstaendige Aggregat**: ein freies ``dict`` mit dem
  kompletten Snapshot-Payload (z. B. verschachtelte Zaehler, Zeitreihen),
  wie es in ``StatisticsSnapshot.backend_data`` abgelegt bzw. fuer den
  PDF-/JSON-Export verwendet wird. ``records`` ist eine Projektion daraus,
  kein Ersatz dafuer — ``summary`` kann Informationen enthalten, die keine
  sinnvolle Tabellenzeile ergeben.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

from core.reporting.data_source import ALL_EVENTS


@dataclass(frozen=True)
class FieldSpec:
    """Eine Spalten-/Record-Definition fuer die tabellarische Projektion."""

    key: str  # stabiler Spalten-/Record-Schlüssel, ^[a-z0-9_]+$
    # Anzeigename: als ``gettext_lazy`` deklarieren (Refs #1744). Der Wert ist
    # CSV-Kopfzeile, Panel-Tabellenüberschrift und JSON-``field_specs``-Label —
    # deutscher Klartext stünde in der englischen Oberfläche mitten in
    # übersetztem Text ("Locale-Leak"). Die Serialisierer casten selbst nach
    # ``str``; der Typ bleibt ``str``, weil ein ``gettext_lazy``-Proxy sich an
    # jeder Verwendungsstelle wie ein ``str`` verhält.
    label: str


@dataclass(frozen=True)
class CompletenessIssue:
    """Ein Vollstaendigkeits-/Datenqualitaets-Hinweis zu einem Datensatz.

    ``count`` traegt die Rohzahl, die in ``message`` beziffert ist — rein
    **interne Transformationsgrundlage** fuer :meth:`DatasetBackend.prepare_export`
    (K-Anon-Suppression braucht die Zahl maschinenlesbar, um sie mit der
    Schwelle zu vergleichen). Die Export-Serialisierer geben das Feld bewusst
    **nicht** aus: JSON/XML fuehren weiterhin nur ``code``/``message``/
    ``severity`` — sonst waere die gerade unterdrueckte Zahl wieder im
    Artefakt.
    """

    code: str  # maschinenlesbar, z. B. "unmapped_system_types"
    # Menschenlesbar und übersetzbar: ``gettext_lazy`` bzw. ``format_lazy`` für
    # bezifferte Fassungen (Refs #1744). ``.format()`` auf einem Lazy-Proxy wäre
    # eine eager Auswertung und schriebe die Sprache zu früh fest.
    message: str
    severity: Literal["info", "warning"] = "warning"
    count: int | None = None  # Rohwert hinter ``message``; nie serialisiert


@dataclass(frozen=True)
class DatasetResult:
    """Ergebnis eines Backend-Laufs — Records-Projektion + vollstaendiges Aggregat.

    Siehe Modul-Docstring fuer den ``records``-/``summary``-Vertrag.
    """

    mode: Literal["rows", "aggregate"]
    records: list[dict]  # tabellarische Projektion; Keys == FieldSpec.key
    summary: dict  # vollständiges Aggregat; Snapshot-Payload
    completeness: list[CompletenessIssue]


class DatasetBackend(ABC):
    """Basisklasse aller Reporting-Backends — via ``@register_backend`` einzubinden.

    **Kein Instanz-Zustand.** ``@register_backend`` legt genau EINE Instanz je
    Slug fuer die gesamte Prozesslebensdauer an (``registry._REGISTRY``); sie
    wird von allen Requests **und allen Einrichtungen** geteilt. Ein auf
    ``self`` gecachtes Zwischenergebnis (naheliegend, weil Panel und Export
    denselben Datensatz bauen) laege im naechsten Request einer anderen
    Einrichtung noch dort — in einer mandantenfaehigen Anwendung ein
    einrichtungsuebergreifender Datenleak. Alles, was ein Aufruf braucht, kommt
    als Argument herein (``facility``, Zeitraum).
    """

    slug: str = ""  # wird von @register_backend gesetzt/validiert
    name: str = ""  # Anzeigename in der Berichtsliste (gettext_lazy-fähig)
    # Refs #1804 (Konzept §3.3): EIN Satz „was dieser Bericht zaehlt", in der
    # Berichtsliste direkt unter dem Namen. Neun gleich praefigierte Namen ohne
    # Beschreibung in einem Dropdown waren keine Auswahl, sondern ein Ratespiel;
    # ``population`` sagt, WORUEBER gezaehlt wird, dieser Satz sagt, WAS. Als
    # ``gettext_lazy`` deklarieren (wie ``name``); leer = die Liste laesst die
    # Zeile weg, statt eine leere Zeile zu rendern.
    description: str = ""
    # **Vertrag von ``snapshot_enabled``** (M5 P3, Refs #1061): Das ``summary``
    # eines so markierten Backends muss **aggregatrein** sein — keine
    # Pseudonyme, keine Personenzeilen, keine Freitexte.
    #
    # Der Grund liegt ausserhalb dieses Moduls: Der Payload wird **roh** (also
    # ohne ``prepare_export`` und damit ohne K-Anon-Suppression) in
    # ``StatisticsSnapshot.backend_data`` persistiert, und diese Spalte ist im
    # PII-Residue-Sweep **pauschal** als non-PII klassifiziert
    # (``src/tests/_residue_expectations.py``) — die Klassifikation prueft den
    # Inhalt nicht je Backend nach. Ein Backend, dessen Aggregat diese Zusage
    # nicht halten kann, bleibt ``snapshot_enabled = False``; sonst wandert PII
    # an der Klassifikation vorbei in den Snapshot.
    snapshot_enabled: bool = False  # True → summary landet in StatisticsSnapshot.backend_data
    # K3-A3 (Refs #1770): Grundgesamtheit der ``records``-Projektion — der Code
    # aus :mod:`core.reporting.data_source`. Panel und Export weisen ihn aus
    # ("Grundgesamtheit: … · Quelle: …"), weil ein Bericht ohne diese Angabe
    # nicht nur undicht, sondern **irrefuehrend** ist: mehrere Tabellen ueber
    # verschiedenen Basismengen summieren sich zu verschiedenen Zahlen, und eine
    # Leserin hat keine Chance zu erkennen, warum. Der Default ist die weiteste
    # und haeufigste Menge; ein Backend, das enger zaehlt, MUSS ihn ueberschreiben.
    population: str = ALL_EVENTS

    @property
    def population_label(self):
        """Uebersetzter Klartext zu ``population`` — fuer die Berichtsliste.

        Refs #1804 (Konzept §3.3): Die Karte nennt die Grundgesamtheit im
        Klartext, nicht ihren stabilen Code. Der Code bleibt die Schnittstelle
        (JSON, Snapshot), die Uebersetzungstabelle lebt unveraendert in
        :mod:`core.reporting.data_source`.
        """
        from core.reporting.data_source import POPULATION_NOTES

        return POPULATION_NOTES.get(self.population, self.population)

    def is_available(self, facility) -> bool:  # Hook für facility-abhängige Backends
        """Ob dieses Backend fuer die gegebene Einrichtung waehlbar ist (Default: immer)."""
        return True

    @abstractmethod
    def get_field_specs(self) -> list[FieldSpec]:  # pragma: no cover - overridden
        """Spaltendefinitionen der tabellarischen Projektion (``DatasetResult.records``)."""
        ...

    @abstractmethod
    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:  # pragma: no cover - overridden
        """Baue den Datensatz fuer ``facility`` im Zeitraum ``[date_from, date_to]``."""
        ...

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Datenqualitaets-Checks vor der Anzeige/dem Export (Default: keine Checks)."""
        return []  # Default: keine Checks

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """Transformation vor Export/Panel-Anzeige (z. B. K-Anon-Suppression).

        Wird von ``export_dataset()`` und der Panel-Vorschau aufgerufen. Snapshots
        nutzen ``build_dataset()`` direkt (Roh-Payload). Default: unveraendert.
        """
        return result
