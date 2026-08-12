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

from django.db.models import Q

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


# --- Deklarative Typ-/Feld-Bindung (E1, Refs #1824) --------------------------
#
# Das Muster „welche Dokumentationstypen speisen diesen Bericht?" lag bis hierher
# **fuenfmal handkopiert** in drei verschiedenen Traegerstrukturen vor, und vier
# Backends hielten die Aufloesung sogar doppelt: einmal als Python-Funktion (fuer
# die Nachverarbeitung) und einmal als handgeschriebenes ``Q`` (fuer den
# SQL-Filter). Dass beide „dieselbe Antwort geben" muessen, stand nur im
# Docstring — genau in dieser Luecke fand das Review zu
# #1785 ein echtes
# Disclosure-Leck (der Zweitschluessel holte bewusst ausgeschlossene ``ban``-Typen
# ueber den Slug herein).
#
# Seit E1 deklariert ein Backend die Bindung EINMAL; ``resolve``, ``matching_q``,
# ``is_available``, das Schluessel-Vokabular und (ab E2) das Inventar sind
# **Ableitungen** daraus. Das Zwei-Wahrheiten-Problem verschwindet damit
# strukturell, nicht per Review-Disziplin.

# Reservierter Sentinel des Berichtsschluessel-Wertebereichs: „gehoert
# ausdruecklich in keinen Bericht" (E3, Konzept #1847 §2). Kein Backend darf ihn
# deklarieren — sonst waere „ausgeschlossen" zugleich eine Berichtszeile. In E1
# ist das eine reine Registry-Reservierung: kein Modellfeld, kein Formular, keine
# Migration.
EXCLUDED_KEY = "excluded"


@dataclass(frozen=True)
class BindungsEintrag:
    """Ein Berichtsschluessel und was er in *diesem* Backend bedeutet.

    ``label`` und ``ziel`` sind bewusst **getrennt**:

    * ``label`` ist einrichtungsweit-kanonisch je Schluessel — der Text, den
      E2/E3 im Vokabular, im Dropdown und in „erscheint in …" zeigen. Als
      ``gettext_lazy`` deklarieren (Refs #1744); die Registry erzwingt, dass
      derselbe Schluessel in allen Backends dasselbe Label traegt.
    * ``ziel`` ist backend-eigen und oft ein **persistierter Snapshot-Schluessel**
      (der Beispiel-Sachbericht legt „Kontakte"/„Beratung"/… in
      ``summary["by_category"]`` ab, monatsuebergreifend gemerged). Solche Werte
      sind Daten, keine UI-Strings — niemals ``gettext_lazy``.

    Ohne diese Trennung kollidierten ``sample_report_1`` (``contact`` →
    „Kontakte") und ``counseling_hours`` (``contact`` → ``"contact"``) im
    Label-Guard.

    ``slugs`` sind die Zweitschluessel-Slugs der Paket-Konvention (Refs #1741);
    leer heisst „ueber den Slug ist dieser Schluessel nicht erreichbar".
    """

    key: str
    label: str
    ziel: object = None
    slugs: tuple[str, ...] = ()

    @property
    def wert(self) -> object:
        """Was die Aufloesung liefert — ``ziel``, ersatzweise der Schluessel selbst."""
        return self.key if self.ziel is None else self.ziel


@dataclass(frozen=True)
class TypBindung:
    """Die Bindung eines Berichts an Dokumentationstypen — eine Deklaration, viele Sichten.

    ``slug_fallback=False`` schaltet den Zweitschluessel ab: die bewusste
    Abweichung von ``ban_overview`` (Refs #1785) ist damit ein **Feld** statt
    eines Fliesstext-Hinweises — sichtbar und testbar.

    ``bindet`` haelt fest, ueber welchen Schluessel gebunden wird: die
    Berichts-Backends wechseln in E3 auf ``report_key``, die Verhaltens-Berichte
    (``ban_overview``) bleiben dauerhaft an ``system_type`` (Konzept #1847 §3).
    In E1 hat das Feld noch keine Wirkung — es ist die Deklaration, die E3
    auswertet.
    """

    eintraege: tuple[BindungsEintrag, ...]
    slug_fallback: bool = True
    bindet: Literal["report_key", "system_type"] = "report_key"

    def _erststufe(self) -> str:
        """Der Modellfeldname der Erststufe — **der** E3-Schalter, eine Stelle.

        E3 macht daraus ``"report_key" if self.bindet == "report_key" else
        "system_type"``. Solange das Feld nicht existiert, binden alle
        Deklarationen ueber ``system_type``; ``bindet`` wird bereits korrekt
        deklariert, damit die Umstellung eine Aenderung an dieser Zeile ist.
        """
        return "system_type"

    def keys(self) -> tuple[str, ...]:
        """Die deklarierten Berichtsschluessel, in Deklarationsreihenfolge."""
        return tuple(eintrag.key for eintrag in self.eintraege)

    def ziele(self) -> tuple:
        """Die Aufloesungswerte, ordnungserhaltend dedupliziert.

        Mehrere Schluessel duerfen auf dieselbe Berichtszeile fallen
        (``crisis``/``counseling`` → „Beratung"). Die Reihenfolge ist die
        Ausgabeordnung eines Backends (K5, wertunabhaengig) — sie kommt aus der
        Deklaration, nicht aus den Daten.
        """
        return tuple(dict.fromkeys(eintrag.wert for eintrag in self.eintraege))

    def ziel_map(self) -> dict:
        """Schluessel → Aufloesungswert (Bestandsform ``CATEGORY_MAP``)."""
        return {eintrag.key: eintrag.wert for eintrag in self.eintraege}

    def slug_ziele(self) -> dict:
        """Slug → Aufloesungswert (Bestandsform ``SLUG_MAP``); leer ohne Fallback."""
        if not self.slug_fallback:
            return {}
        return {slug: eintrag.wert for eintrag in self.eintraege for slug in eintrag.slugs}

    def vokabular(self) -> dict[str, str]:
        """Schluessel → Label (E2-Inventar, E3-Dropdown)."""
        return {eintrag.key: eintrag.label for eintrag in self.eintraege}

    def values_fields(self, prefix: str = "") -> tuple[str, ...]:
        """Die Felder, die eine ``.values()``-Aggregation mitfuehren muss.

        Ohne den Zweitschluessel in der Gruppierung koennte er nicht greifen;
        ohne Fallback waere er nur eine ueberfluessige Gruppierungsspalte.
        """
        erststufe = (f"{prefix}{self._erststufe()}",)
        if not self.slug_fallback:
            return erststufe
        return (*erststufe, f"{prefix}slug")

    def _eintrag(self, *werte) -> "BindungsEintrag | None":
        """Der zutreffende Eintrag — die EINE Auflösungsentscheidung.

        Auflösungsordnung (Refs #1741): erst die Erststufe, dann — **nur** wenn
        sie gar keine Aussage trifft (leer/NULL) — der Slug. „Gewinnt immer"
        schliesst die **Ausschluesse** ein (Refs #1785): ein gesetzter, aber
        nicht deklarierter Erstschluessel (``note``/``ban``) ist eine
        vollstaendige Aussage und sperrt den Zweitschluessel. Andernfalls zoege
        ein Hausverbots-Typ mit dem Slug ``kontakt`` seine Ereignisse in den
        Bericht.

        :meth:`resolve` und :meth:`resolve_key` unterscheiden sich nur darin,
        WAS sie aus dem Eintrag lesen — nicht darin, WELCHEN sie waehlen.
        """
        erststufe = werte[0] if werte else None
        if erststufe:
            return {eintrag.key: eintrag for eintrag in self.eintraege}.get(erststufe)
        if not self.slug_fallback:
            return None
        slug = werte[1] if len(werte) > 1 else None
        nach_slug = {slug: eintrag for eintrag in self.eintraege for slug in eintrag.slugs}
        return nach_slug.get(slug or "")

    def resolve(self, *werte):
        """Aufloesung eines Typs auf sein ``ziel`` — ``None`` = nicht in diesem Bericht.

        Das ``ziel`` ist die **Berichtszeile** (bei ``snapshot_enabled`` ein
        persistierter Schluessel) und bewusst nicht invertierbar: mehrere
        Schluessel duerfen auf dieselbe Zeile fallen. Wer den Schluessel
        braucht, nimmt :meth:`resolve_key`.
        """
        eintrag = self._eintrag(*werte)
        return None if eintrag is None else eintrag.wert

    def resolve_key(self, *werte) -> str | None:
        """Aufloesung eines Typs auf seinen **Berichtsschluessel** (E2/E3).

        Dieselbe Entscheidung wie :meth:`resolve`, nur mit der anderen Ausgabe:
        ``crisis`` und ``counseling`` fallen im Sachbericht beide auf
        „Beratung" — aus dem ``ziel`` laesst sich der Schluessel deshalb nicht
        zurueckrechnen. „Dieser Typ erscheint in …" (E2) und das Inventar
        brauchen aber genau ihn, und sie duerfen ihn nicht ein zweites Mal
        herleiten: das waere wieder die zweite Wahrheit aus #1824.
        """
        eintrag = self._eintrag(*werte)
        return None if eintrag is None else eintrag.key

    def resolve_row(self, row, prefix: str = ""):
        """:meth:`resolve` fuer eine ``.values()``-Zeile — liest :meth:`values_fields`."""
        return self.resolve(*(row[feld] for feld in self.values_fields(prefix)))

    def resolve_key_row(self, row, prefix: str = "") -> str | None:
        """:meth:`resolve_key` fuer eine ``.values()``-Zeile — liest :meth:`values_fields`."""
        return self.resolve_key(*(row[feld] for feld in self.values_fields(prefix)))

    def matching_q(self, prefix: str = "") -> Q:
        """SQL-Entsprechung von :meth:`resolve` — ``prefix`` fuer JOIN-Pfade.

        ``matching_q()`` filtert auf ``DocumentType`` selbst,
        ``matching_q("document_type__")`` auf ``Event``. Beide Sichten lesen
        dieselben Ableitungen (:meth:`keys`, :meth:`slug_ziele`) — das ist die
        Invariante, die ``test_reporting_bindung.py`` ueber das gesamte
        Werte-Universum prueft.

        Der ``isnull``-Zweig ist bewusst redundant: die Erststufe ist NULL-bar,
        blankes SQL-``NOT IN`` faengt NULL-Zeilen nicht. ``slug`` ist NOT NULL
        (``blank=True``), dort gibt es diesen Fall nicht.
        """
        erststufe = f"{prefix}{self._erststufe()}"
        treffer = Q(**{f"{erststufe}__in": self.keys()})
        slug_ziele = self.slug_ziele()
        if slug_ziele:
            unset = Q(**{f"{erststufe}__isnull": True}) | Q(**{erststufe: ""})
            treffer |= unset & Q(**{f"{prefix}slug__in": tuple(slug_ziele)})
        return treffer

    def unmapped_q(self, prefix: str = "") -> Q:
        """„Ohne Berichtszuordnung" — die Negation von :meth:`matching_q`.

        Als Ableitung statt als zweites Praedikat: der Vollstaendigkeitshinweis
        muss exakt die Ereignisse zaehlen, die der Bericht nicht zeigt.
        """
        return ~self.matching_q(prefix)


@dataclass(frozen=True)
class FeldBindung:
    """Die Bindung eines Berichts an Feldvorlagen (Konzept #1847 §3).

    Gleiche Regel wie auf Typ-Ebene, **andere Kardinalitaet** (§3.1): mehrere
    Typen duerfen denselben Berichtsschluessel tragen (sie summieren in eine
    Zeile), zwei Felder desselben Typs mit demselben Feld-Schluessel hiessen
    dagegen doppelte Minuten oder vermischte Optionsraeume.

    ``bindet`` ist die Entsprechung zu :attr:`TypBindung.bindet`: Es haelt fest,
    ueber welchen Schluessel die Feldvorlage **nach E3** gebunden wird. Default
    ist ``"slug"`` — eine Deklaration ohne Meinung bleibt beim heutigen
    Verhalten. In E1 hat das Feld noch keine Wirkung; ausgewertet wird es von
    :meth:`_erststufe`, der einzigen Stelle, die den Feldnamen kennt.
    """

    eintraege: tuple[BindungsEintrag, ...]
    bindet: Literal["report_key", "slug"] = "slug"

    def _erststufe(self) -> str:
        """Der Modellfeldname der Erststufe — der E3-Schalter der Feld-Ebene.

        E3 macht daraus ``"report_key" if self.bindet == "report_key" else
        "slug"``. Solange ``FieldTemplate.report_key`` nicht existiert, binden
        alle Deklarationen ueber den Slug.
        """
        return "slug"

    def keys(self) -> tuple[str, ...]:
        """Die deklarierten Feld-Berichtsschluessel, in Deklarationsreihenfolge."""
        return tuple(eintrag.key for eintrag in self.eintraege)

    def vokabular(self) -> dict[str, str]:
        """Feld-Schluessel → Label (eigener Werteraum neben dem Typ-Vokabular)."""
        return {eintrag.key: eintrag.label for eintrag in self.eintraege}

    def slugs(self) -> tuple[str, ...]:
        """Die Feldvorlagen-Slugs, ordnungserhaltend dedupliziert."""
        return tuple(dict.fromkeys(slug for eintrag in self.eintraege for slug in eintrag.slugs))

    def key_for_slug(self, slug: str) -> str | None:
        """Feldvorlagen-Slug → Feld-Berichtsschluessel; ``None`` = nicht deklariert.

        Der Rueckweg zur Deklaration (E2: „diese Feldvorlage speist …"). Wie auf
        Typ-Ebene aus derselben Eintragsliste abgeleitet, nicht danebengepflegt.
        """
        return {slug: eintrag.key for eintrag in self.eintraege for slug in eintrag.slugs}.get(slug or "")

    def _erststufen_werte(self) -> tuple[str, ...]:
        """Die Werte, gegen die :meth:`template_q` filtert — passend zur Erststufe."""
        return self.keys() if self._erststufe() == "report_key" else self.slugs()

    def template_q(self, prefix: str = "fields__field_template__") -> Q:
        """„Diese Feldvorlage haengt aktiv am Typ" — ein ``filter()``-Argument.

        Feldname UND Werte kommen aus :meth:`_erststufe`; „slug" steht hier
        nicht mehr fest verdrahtet, sonst waere der E3-Wechsel wieder eine
        Aenderung an mehreren Stellen.

        **JOIN-Falle:** ueber eine Mehrfach-Relation erzeugt Django je
        ``filter()``-Aufruf einen eigenen JOIN. Zwei Bindungen gehoeren deshalb
        in ZWEI Aufrufe (dann heisst es „beide Felder am DEMSELBEN Typ"); in
        einem Aufruf muesste dieselbe Zeile beide Slugs zugleich tragen — nie
        erfuellbar.
        """
        return Q(
            **{
                f"{prefix}{self._erststufe()}__in": self._erststufen_werte(),
                f"{prefix}is_active": True,
            }
        )

    def data_json_keys(self) -> tuple[str, ...]:
        """Die Leseschluessel in ``Event.data_json`` — E3-Nachziehstelle.

        In E1 identisch mit :meth:`slugs`: ``data_json`` ist nach dem
        Feldvorlagen-Slug indiziert. Bleibt das so, aendert E3 hier nichts;
        soll der Berichtsschluessel den Lesepfad uebernehmen, ist dies die eine
        Stelle, an der er je Dokumentationstyp aufzuloesen ist.

        **Reiner Lesepfad.** Was ein Backend *veroeffentlicht* (Spaltenwerte,
        ``summary``-Schluessel) darf hier NICHT herkommen: sonst benennt der
        E3-Wechsel des Lesepfades still publizierte Schluessel um. Ein Backend,
        das einen Slug auch ausgibt, deklariert ihn dafuer getrennt (Muster
        ``Feldgruppe.feld_slug`` in ``service_distribution``).
        """
        return self.slugs()


@dataclass(frozen=True)
class Anforderung:
    """Eine Bedingung fuer ``is_available`` — generisch statt fuenfmal handgebaut.

    Erfuellt, wenn die Einrichtung mindestens EINEN aktiven Dokumentationstyp
    fuehrt, der zur Bindung passt und alle geforderten Feldvorlagen traegt.
    ``is_active`` gehoert genau hierher und **nie** in :meth:`TypBindung.matching_q`:
    die Konfigurationsfrage („bietet die Einrichtung das an?") ist eine andere
    als der Datenfilter („was ist damals passiert?") — ein heute umgelegter
    Schalter darf keine Vergangenheit umschreiben.
    """

    typ_bindung: TypBindung | None = None
    # Je Bindung ein EIGENER ``filter()``-Aufruf — siehe :meth:`FeldBindung.template_q`.
    feld_bindungen: tuple[FeldBindung, ...] = ()
    entity_type: str | None = None

    def erfuellt(self, facility) -> bool:
        """Prueft die Anforderung gegen die Konfiguration der Einrichtung."""
        # Lokaler Import: ``core.models`` zur Modul-Importzeit zoege den
        # Reporting-Vertrag in den Model-Importgraphen — genau der Zyklus, den
        # die Lazy-Discovery in ``registry`` vermeidet.
        from core.models import DocumentType

        typen = DocumentType.objects.for_facility(facility).filter(is_active=True)
        if self.entity_type is not None:
            typen = typen.filter(entity_type=self.entity_type)
        if self.typ_bindung is not None:
            typen = typen.filter(self.typ_bindung.matching_q())
        for bindung in self.feld_bindungen:
            typen = typen.filter(bindung.template_q())
        return typen.exists()


def typ_bindungen(backend) -> tuple[TypBindung, ...]:
    """Alle Typ-Bindungen eines Backends — Attribut UND Verfuegbarkeits-Deklaration.

    ``service_distribution`` traegt seine Bindungen je Feldgruppe und hat
    deshalb kein einzelnes ``typ_bindung``-Attribut; das Vokabular muss sie
    trotzdem sehen. Dedupliziert ueber Identitaet (dieselbe Deklaration in
    beiden Attributen zaehlt einmal).
    """
    kandidaten = (backend.typ_bindung, *(a.typ_bindung for a in backend.verfuegbarkeit))
    gefunden: list[TypBindung] = []
    for bindung in kandidaten:
        if bindung is not None and not any(bindung is bekannt for bekannt in gefunden):
            gefunden.append(bindung)
    return tuple(gefunden)


def feld_bindungen(backend) -> tuple[FeldBindung, ...]:
    """Alle Feld-Bindungen eines Backends — Attribut UND Verfuegbarkeits-Deklaration."""
    kandidaten = (
        *backend.feld_bindungen,
        *(bindung for a in backend.verfuegbarkeit for bindung in a.feld_bindungen),
    )
    gefunden: list[FeldBindung] = []
    for bindung in kandidaten:
        if not any(bindung is bekannt for bekannt in gefunden):
            gefunden.append(bindung)
    return tuple(gefunden)


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
    # E1 (Refs #1824): die deklarative Bindung. **Deklaration und Verfuegbarkeit
    # sind bewusst getrennt** — ``sample_report_1`` deklariert eine Bindung
    # (sie speist Aufloesung, Unmapped-Filter und Vokabular), hat aber
    # absichtlich KEIN ``is_available``: der Bericht bleibt immer waehlbar.
    # Haenge die Verfuegbarkeit an ``typ_bindung``, verschwaende er aus dem
    # Dropdown, sobald eine Einrichtung keinen passenden Typ fuehrt — und die
    # Panel-/Export-View liefert dann 404 (``get_available_backends``).
    typ_bindung: "TypBindung | None" = None
    feld_bindungen: "tuple[FeldBindung, ...]" = ()
    verfuegbarkeit: "tuple[Anforderung, ...]" = ()

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
        """Ob dieses Backend fuer die gegebene Einrichtung waehlbar ist.

        Abgeleitet aus :attr:`verfuegbarkeit` (E1, Refs #1824): ohne
        Deklaration immer waehlbar, sonst genuegt EINE erfuellte Anforderung.
        Die ODER-Semantik ist der Fall ``service_distribution`` — eine
        konfigurierte Feldgruppe reicht.
        """
        return not self.verfuegbarkeit or any(anforderung.erfuellt(facility) for anforderung in self.verfuegbarkeit)

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


def project_records(rows, field_specs: list[FieldSpec]) -> list[dict]:
    """Projiziert ``rows`` auf die von ``field_specs`` deklarierten Keys (Refs #1835).

    Wiederkehrendes Muster in ``prepare_export`` ueber alle Backends: die
    zentralen Suppressionsfunktionen (``_suppress_small`` & Co, siehe
    :mod:`core.services.dashboard.external_report`) haengen interne Flags wie
    ``suppressed``/``persons`` an, die keine eigene Spalte sind und sonst
    ungefiltert in CSV/JSON/XML landen wuerden. Gibt neue Dicts zurueck —
    ``rows`` bleibt unveraendert.
    """
    field_keys = [spec.key for spec in field_specs]
    return [{key: row[key] for key in field_keys} for row in rows]


def stamp_threshold(summary: dict, threshold: int) -> dict:
    """Neue ``summary``-Kopie mit gestempelter ``k_anonymity_threshold`` (Refs #1835).

    Wiederkehrendes Muster nach der Suppression in allen 9 Backends — der
    Schwellenwert gehoert in den Export, damit eine Empfaengerin sieht, ab
    welcher Zellengroesse unterdrueckt wurde. Gibt ein neues Dict zurueck —
    ``summary`` bleibt unveraendert.
    """
    out = dict(summary)
    out["k_anonymity_threshold"] = threshold
    return out
