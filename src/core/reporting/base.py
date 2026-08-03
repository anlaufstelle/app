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


@dataclass(frozen=True)
class FieldSpec:
    """Eine Spalten-/Record-Definition fuer die tabellarische Projektion."""

    key: str  # stabiler Spalten-/Record-Schlüssel, ^[a-z0-9_]+$
    label: str  # Anzeigename (deutsch; via str(gettext_lazy) beim Rendern)


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
    message: str  # menschenlesbar (deutsch)
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
    """Basisklasse aller Reporting-Backends — via ``@register_backend`` einzubinden."""

    slug: str = ""  # wird von @register_backend gesetzt/validiert
    name: str = ""  # Anzeigename im UI-Dropdown (gettext_lazy-fähig)
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
