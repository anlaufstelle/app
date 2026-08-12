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
from typing import Literal, cast

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

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

    ``slugs`` sind die Paket-Slugs der Konvention (Refs #1741). Seit E3
    (Refs #1849) sind sie **nur noch auf Feld-Ebene** wirksam
    (:meth:`FeldBindung.key_for_slug`); auf Typ-Ebene ist der
    Slug-Zweitschluessel ersatzlos entfallen (Entscheid E-E) — dort trifft
    ausschliesslich der ``report_key``.
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

    **Ein Schluessel, nicht zwei** (Entscheid E-E, Konzept #1847). Bis E3 gab es
    neben dem Erstschluessel einen Slug-Zweitschluessel (Refs #1741): ein Typ
    ohne ``system_type``, dessen aus dem Namen abgeleiteter Slug einer der
    Paket-Slugs war, wurde ebenfalls zugeordnet. Der Weg war unsichtbar — man
    musste ihn aus dem Slug erschliessen — und hat in
    #1785 ein
    Disclosure-Leck erzeugt (ein Hausverbots-Typ mit dem Slug ``kontakt`` zog
    seine Ereignisse in den Bericht). Mit ``report_key`` gibt es eine
    **sichtbare, ausdrueckliche** Wahl; der Zweitschluessel ist damit ersatzlos
    entfallen, und mit ihm die Regel „Erstschluessel gewinnt inklusive seiner
    Ausschluesse", die das Leck ueberhaupt erst noetig machte.

    ``bindet`` haelt fest, ueber welchen Schluessel gebunden wird: die
    Berichts-Backends ueber ``report_key``, die Verhaltens-Berichte
    (``ban_overview``) dauerhaft ueber ``system_type`` (Konzept #1847 §3).
    """

    eintraege: tuple[BindungsEintrag, ...]
    bindet: Literal["report_key", "system_type"] = "report_key"

    def _erststufe(self) -> str:
        """Der Modellfeldname der Erststufe — **der** Schalter, eine Stelle.

        Seit E3 (Refs #1849) binden die Berichts-Backends ueber
        ``DocumentType.report_key``; ``ban_overview`` bleibt als
        Verhaltens-Bericht dauerhaft an ``system_type`` (Konzept #1847 §3).
        Beide Sichten — Python-Aufloesung und ``Q``-Praedikat — lesen diese eine
        Zeile, deshalb war die Umstellung hier eine Aenderung und nicht fuenf.
        """
        return "report_key" if self.bindet == "report_key" else "system_type"

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

    def vokabular(self) -> dict[str, str]:
        """Schluessel → Label (E2-Inventar, E3-Dropdown)."""
        return {eintrag.key: eintrag.label for eintrag in self.eintraege}

    def values_fields(self, prefix: str = "") -> tuple[str, ...]:
        """Die Felder, die eine ``.values()``-Aggregation mitfuehren muss.

        Seit E3 (Refs #1849) genau eines — der Zweitschluessel ist entfallen.
        Die Methode bleibt trotzdem: sie ist der EINE Ort, an dem steht, was die
        Aufloesung liest, und ``resolve_row``/``resolve_key_row`` haengen daran.
        """
        return (f"{prefix}{self._erststufe()}",)

    def _eintrag(self, *werte) -> "BindungsEintrag | None":
        """Der zutreffende Eintrag — die EINE Auflösungsentscheidung.

        Seit dem Wegfall des Slug-Zweitschluessels (Entscheid E-E) ist das eine
        schlichte Nachschlage: ein gesetzter, deklarierter Erstschluessel
        trifft, alles andere nicht. Die frueher noetige Regel „ein gesetzter,
        aber nicht deklarierter Erstschluessel sperrt den Zweitschluessel"
        (Refs #1785) hat damit keinen Gegenstand mehr — es gibt nichts zu
        sperren.

        ``*werte`` bleibt variadisch: die Aufrufer reichen
        :meth:`values_fields` durch, und ein Backend mit einer breiteren
        Aufloesung braucht die Signatur nicht zu aendern.

        :meth:`resolve` und :meth:`resolve_key` unterscheiden sich nur darin,
        WAS sie aus dem Eintrag lesen — nicht darin, WELCHEN sie waehlen.
        """
        erststufe = werte[0] if werte else None
        if not erststufe:
            return None
        return {eintrag.key: eintrag for eintrag in self.eintraege}.get(erststufe)

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
        dieselbe Ableitung (:meth:`keys`) — das ist die Invariante, die
        ``test_reporting_bindung.py`` ueber das gesamte Werte-Universum prueft.

        NULL-Zeilen faengt ``IN`` ohnehin nicht; einen ``isnull``-Zweig braucht
        es hier deshalb nicht (wohl aber im Unmapped-Filter des Sachberichts,
        wo NULL die gesuchte Menge IST).
        """
        return Q(**{f"{prefix}{self._erststufe()}__in": self.keys()})

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
        """Der Modellfeldname der Erststufe — der Schalter der Feld-Ebene.

        Seit E3 (Refs #1849) binden die Feldwert-Backends ueber
        ``FieldTemplate.report_key``. Der Default ``"slug"`` bleibt als
        Rueckfallebene fuer Deklarationen ohne Meinung stehen.
        """
        return "report_key" if self.bindet == "report_key" else "slug"

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

    def key_for_template(self, field_template) -> str | None:
        """Feldvorlage → Feld-Berichtsschluessel; ``None`` = nicht gebunden.

        Liest die Erststufe der Deklaration statt fest den Slug (E3, Refs
        #1849) — sonst liefe die „erscheint in …"-Auskunft am tatsaechlichen
        Bindungsschluessel vorbei. Bindet die Deklaration ueber ``report_key``,
        ist der Schluessel der Wert selbst, sofern er deklariert ist.
        """
        wert = getattr(field_template, self._erststufe())
        if self._erststufe() == "report_key":
            return wert if wert in self.keys() else None
        return self.key_for_slug(wert)

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

    def leseschluessel(self, facility, typ_bindung: "TypBindung | None" = None) -> "Leseschluessel":
        """Der ``data_json``-Lesepfad dieser Bindung, aufgeloest **je Dokumentationstyp**.

        ``data_json`` ist nach dem Feldvorlagen-**Slug** indiziert, gebunden wird
        seit E3 aber ueber den ``report_key`` (Refs #1849). Beides faellt nur im
        Standardpaket zusammen: Wer sein Zahlenfeld „Gesprächsdauer" nennt und
        ihm den Schluessel ``dauer_minuten`` gibt, speichert unter
        ``gespraechsdauer`` — genau der Fall, den dieser Umbau erst moeglich
        macht. Ein fest verdrahteter Slug laese dort dauerhaft nichts und waere
        schlimmer als der Zustand davor (das Backend waere jetzt *verfuegbar*
        und zeigte trotzdem Nullen).

        **Reiner Lesepfad.** Was ein Backend *veroeffentlicht* (Spaltenwerte,
        ``summary``-Schluessel) darf hier NICHT herkommen: sonst benennte der
        Lesepfad still publizierte Schluessel um. Ein Backend, das einen Slug
        auch ausgibt, deklariert ihn dafuer getrennt (Muster
        ``Feldgruppe.feld_slug`` in ``service_distribution``).

        Nur **aktive** Feldvorlagen: eine deaktivierte Vorlage wird in
        Formularen nicht mehr angeboten und ist dieselbe Konfigurationsfrage,
        die :meth:`template_q` stellt.

        ``typ_bindung`` schraenkt auf die Dokumentationstypen ein, die dieser
        Bericht ueberhaupt zaehlt — **Pflicht, sobald das Ergebnis eine Meldung
        traegt**: die Doppelbindungs-Menge waere sonst einrichtungsweit, und ein
        ausdruecklich ausgeschlossener Typ mit zwei gleich geschluesselten
        Vorlagen erzeugte einen Hinweis in einem Bericht, den er nichts angeht
        (dieselbe Vermengung, die Refs #1819 fuer den Vollstaendigkeitshinweis
        beseitigt hat). Ohne Angabe bleibt die Menge einrichtungsweit — fuer
        Aufrufer ohne Typ-Bindung.
        """
        # Lokaler Import wie in ``Anforderung.erfuellt``: ``core.models`` zur
        # Modul-Importzeit zoege den Reporting-Vertrag in den Model-Importgraphen.
        from core.models import DocumentTypeField

        treffer: dict[object, set[str]] = {}
        zuordnungen = DocumentTypeField.objects.filter(
            document_type__facility=facility,
            field_template__is_active=True,
            **{f"field_template__{self._erststufe()}__in": self._erststufen_werte()},
        )
        if typ_bindung is not None:
            zuordnungen = zuordnungen.filter(typ_bindung.matching_q("document_type__"))
        for document_type_id, slug in zuordnungen.values_list("document_type_id", "field_template__slug"):
            treffer.setdefault(document_type_id, set()).add(slug)

        je_typ = {typ_id: next(iter(slugs)) for typ_id, slugs in treffer.items() if len(slugs) == 1}
        doppelt = tuple(sorted((typ_id for typ_id, slugs in treffer.items() if len(slugs) > 1), key=str))
        return Leseschluessel(je_typ, doppelt)


@dataclass(frozen=True)
class Leseschluessel:
    """Ergebnis von :meth:`FeldBindung.leseschluessel` (E3, Refs #1849).

    ``je_typ`` bildet die Dokumentationstyp-ID auf den ``data_json``-Schluessel
    ab; ein Typ ohne Eintrag traegt fuer diese Bindung kein Feld.

    ``doppelt`` sind die Dokumentationstypen, an denen **mehrere** Feldvorlagen
    denselben Feld-Berichtsschluessel tragen (Entscheid E-J, Konzept #1847
    §3.1). Sie fehlen bewusst in ``je_typ``: welches der beiden Felder gemeint
    ist, kann das Backend nicht wissen, und „irgendeines nehmen" hiesse je nach
    Zeilenordnung mal die eine, mal die andere Zahl. Fail-safe heisst hier:
    keines zaehlen und den Zustand melden — nie doppelte Minuten.
    """

    je_typ: dict[object, str]
    doppelt: tuple[object, ...] = ()


# Feld-Kardinalitaet (Entscheid E-J, Konzept #1847 §3.1). Auf Typ-Ebene ist
# Mehrfachbelegung der Zweck der Uebung — mehrere Typen mit demselben Schluessel
# summieren in eine Berichtszeile. Auf Feld-Ebene liest das Backend Werte JE
# EREIGNIS: zwei am selben Typ gebundene Felder mit demselben Feld-Schluessel
# hiessen doppelte Minuten oder vermischte Optionsraeume.
#
# Die Zuordnung wird deshalb validiert (Formular, Service, Paket-Loader).
# Entsteht die Doppelbindung trotzdem — Import, Paketlauf, Race —, zaehlt das
# Backend KEINES der beteiligten Felder und meldet: Fehler sichtbar statt still,
# nie doppelt gezaehlt.
#
# Ziffernfrei und ohne ``count`` (K6-Auflage, Refs #1769): der Hinweis haengt an
# der KONFIGURATION, nicht am Bestand — seine blosse Existenz verraet nichts
# ueber Fallzahlen.
DOPPELBINDUNG_CODE = "duplicate_field_binding"
# ``cast`` wie an :attr:`CompletenessIssue.message` begruendet: der Wert IST ein
# ``gettext_lazy``-Proxy (er muss der Sprache der Empfaengerin folgen), verhaelt
# sich aber an jeder Verwendungsstelle wie ein ``str``.
DOPPELBINDUNG_MESSAGE = cast(
    "str",
    _(
        "Mindestens ein Dokumentationstyp trägt mehrere Feldvorlagen mit demselben Berichtsschlüssel — "
        "deren Werte bleiben in diesem Bericht unberücksichtigt"
    ),
)


def doppelbindungs_issues(*leseschluessel: "Leseschluessel") -> list["CompletenessIssue"]:
    """Ein Hinweis, sobald IRGENDEINE Bindung eine Doppelbindung gefunden hat (E-J).

    Genau ein Hinweis, nicht einer je Bindung: der Befund ist derselbe
    („Konfiguration klaeren"), und mehrere gleichlautende Zeilen im Artefakt
    verrieten nur, wie viele Bindungen betroffen sind.
    """
    if any(schluessel.doppelt for schluessel in leseschluessel):
        return [CompletenessIssue(DOPPELBINDUNG_CODE, DOPPELBINDUNG_MESSAGE, "warning", count=None)]
    return []


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
