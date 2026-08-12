"""Beispiel-Sachbericht als ``DatasetBackend`` (M5 P2, Refs #1061).

Migration der bisher hart kodierten Statistik aus
``core/services/system/export.py`` in das Plugin-System. Die Aggregation ist
**unveraendert** uebernommen (Verhaltensparitaet): ``summary`` traegt exakt das
bisherige Dict-Format — inklusive ``by_category`` als Liste von Tupeln, ueber
die ``core/export/sample_report_1_pdf.html`` iteriert und die
``_merge_sample_report_1_stats`` nach dem JSON-Roundtrip (Snapshots) wieder
normalisiert.

Neu ist allein :meth:`SampleReport1Backend.validate_completeness`: die Checks
brauchen die Rohdaten, die nicht im Datensatz landen (nicht zugeordnete
Dokumenttypen, Ereignisse ohne Personenbezug).

:meth:`SampleReport1Backend.prepare_export` legt die K-Anon-Suppression des
PDF-Pfads (:func:`~core.services.dashboard.external_report.suppress_sample_report_1_stats`,
Refs #1278) auf Export und Panel-Vorschau — ``build_dataset`` selbst bleibt
roh, damit die Snapshot-Payload unveraendert bleibt.
"""

import dataclasses
from collections import OrderedDict
from typing import Literal

from django.db.models import Count, Q
from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _

from core.models import Event
from core.reporting.base import (
    EXCLUDED_KEY,
    BindungsEintrag,
    CompletenessIssue,
    DatasetBackend,
    DatasetResult,
    FieldSpec,
    TypBindung,
    stamp_threshold,
)
from core.reporting.data_source import MAPPED_EVENTS
from core.reporting.registry import get_report_key_vocabulary, register_backend

# Modul-Ebenen-Import ist zyklusfrei: ``core.services.dashboard`` verweist
# nirgends zur Importzeit auf ``core.reporting`` — der einzige Rueckverweis
# (``core.services.system.export``) ist ein lokaler Import in der Funktion.
from core.services.dashboard.external_report import _get_threshold, suppress_sample_report_1_stats
from core.services.dashboard.statistics import PERSONS_KEY

# Die Typ-Bindung dieses Berichts (E1, Refs #1824): welcher Dokumentationstyp
# in welche Berichtskategorie faellt — EINE Deklaration, aus der Aufloesung
# (``resolve_category``), SQL-Filter (:data:`_UNMAPPED_Q`), Verfuegbarkeit und
# Schluessel-Vokabular abgeleitet werden. ``ban``/``note``/NULL fehlen bewusst;
# sie gehoeren nicht in den Bericht.
#
# **``ziel`` = deutscher Klartext, NIE ``gettext_lazy``** (Refs #1744): die
# Kategoriennamen landen als Schluessel in ``summary["by_category"]`` und damit
# im persistierten Snapshot-Payload (``StatisticsSnapshot.backend_data``) sowie
# im monatsuebergreifenden Merge (``_merge_sample_report_1_stats``). Ein
# locale-abhaengiger Wert machte den gespeicherten Bestand von der Sprache des
# schreibenden Prozesses abhaengig — zwei Monate, zwei Kategoriennamen, kein
# Merge. ``label`` ist dagegen der uebersetzte Name des SCHLUESSELS und hat mit
# der Kategorie nichts zu tun.
#
# **``slugs`` = Zweitschluessel** (Refs #1741, Entscheid in
# #1785).
# ``system_type`` ist ein Kern-Datenmodell-Enum und eingefroren (RFC #1748): ein
# einrichtungseigener Dokumentationstyp hat oft gar keinen — oder ``note``, was
# exportseitig identisch mit NULL ist. Genau diese Typen tauchten bisher
# ausnahmslos als „ohne Berichtszuordnung" im Vollstaendigkeitshinweis auf,
# obwohl ihr Slug sie eindeutig benennt.
#
# **Warum hart kodiert.** Die Bindung ist Fachlogik *dieses* Berichts und
# gehoert damit ins Backend, nicht in eine Einrichtungs-Einstellung: eine
# Config-UI machte Berichtszahlen per Klick verschiebbar (dasselbe Argument,
# mit dem ``category`` fuers Reporting ausgeschlossen ist, ADR-034/ADR-038).
# Eine spaetere Konfigurierbarkeit bleibt Ausbaupfad, nicht Bestand.
#
# **Globale Slug-Konvention, facility-agnostisch.** Die Slugs sind die
# Paket-Slugs aus ``core/packages/default.yaml`` — dort tragen sie die
# Bedeutung, die diese Bindung annimmt. Gematcht wird ausschliesslich der
# Slug-String, unabhaengig davon, ob der Typ aus dem Paket stammt oder in der
# Config-UI entstanden ist: es gibt am Datensatz keine Herkunftsangabe, und ein
# einrichtungseigener Typ mit dem Slug ``beratungsgespraech`` IST ein
# Beratungsgespraech. Die Slugs sind je Einrichtung eindeutig und nach
# Erstellung unveraenderlich (M2, ADR-037) — dieselbe Stabilitaetszusage wie
# bei ``system_type``.
#
# **Bewusst konservativ.** Nur die sieben Paket-Slugs, deren ``system_type``
# ohnehin deklariert ist. ``notiz`` und ``hausverbot`` fehlen aus demselben
# Grund, aus dem ``note``/``ban`` fehlen — der Zweitschluessel darf keine
# Kategorie hereinholen, die der Erstschluessel bewusst auslaesst.
TYP_BINDUNG = TypBindung(
    (
        BindungsEintrag("contact", _("Kontakt"), ziel="Kontakte", slugs=("kontakt",)),
        BindungsEintrag("crisis", _("Krisengespräch"), ziel="Beratung", slugs=("krisengespraech",)),
        BindungsEintrag("medical", _("Medizinische Versorgung"), ziel="Versorgung", slugs=("medizinische-versorgung",)),
        BindungsEintrag("needle_exchange", _("Spritzentausch"), ziel="Versorgung", slugs=("spritzentausch",)),
        BindungsEintrag("accompaniment", _("Begleitung"), ziel="Vermittlung", slugs=("begleitung",)),
        BindungsEintrag("counseling", _("Beratungsgespräch"), ziel="Beratung", slugs=("beratungsgespraech",)),
        BindungsEintrag("referral", _("Vermittlung"), ziel="Vermittlung", slugs=("vermittlung",)),
    )
)

# Bestandsnamen als **Ableitungen** der Deklaration (Refs #1824). Sie bleiben
# oeffentlich, weil sie ausserhalb dieses Moduls gelesen werden (Freeze-Test
# ``test_system_type.py``, Export-Tests) — aber es gibt sie nur noch einmal.
CATEGORY_MAP = TYP_BINDUNG.ziel_map()
SLUG_MAP = TYP_BINDUNG.slug_ziele()

# Berichtskategorie eines Dokumentationstyps — ``None`` = nicht in diesem
# Bericht. ``resolve_category(report_key, slug)``: erst der Erstschluessel, dann
# — nur bei leerem/NULL Erstschluessel — der Slug (Refs #1741/#1785).
#
# **Invariante seit E3** (Refs #1849/#1819, frueher „build_dataset und der
# Unmapped-Filter geben dieselbe Antwort"): Die Menge zerfaellt nicht mehr in
# zwei Teile, sondern in VIER (Konzept #1847 §2), und ``resolve_category is
# None`` ist die Vereinigung der letzten drei:
#
# 1. **zugeordnet** — ``resolve_category`` liefert eine Kategorie.
# 2. **ausdruecklich ausgeschlossen** — Schluessel gesetzt und bekannt, aber
#    nicht in diesem Bericht (``excluded`` oder ein Schluessel, den nur ein
#    anderes Backend deklariert). Vollstaendige Aussage, KEIN Hinweis.
# 3. **ohne Zuordnung** — Schluessel leer/NULL und auch ueber den Slug kein
#    Treffer. Das ist die echte Luecke: :data:`_UNMAPPED_Q`.
# 4. **unbekannter Schluessel** — gesetzt, aber kein Backend kennt ihn
#    (Fremdpaket, entferntes Backend, Tippfehler). Eigener Meldungs-Code
#    (Entscheid E-H): :func:`unknown_key_q`.
#
# Wer die Invariante als „deckungsgleich" liest, liest sie falsch — 2 und 4
# loesen ebenfalls zu ``None`` auf, erzeugen aber keinen bzw. einen ANDEREN
# Hinweis.
resolve_category = TYP_BINDUNG.resolve


# Anzeigelabels der Altersgruppen — lazy wie bisher, damit der Snapshot-Payload
# byte-gleich zum Bestand bleibt.
_AGE_LABELS = {
    "u18": _("Unter 18"),
    "18_26": _("18–26"),
    "27_plus": _("27+"),
    "unknown": _("Unbekannt"),
}


def _erststufen_feld(prefix: str) -> str:
    """Der Feldname der Erststufe — aus der Deklaration, nicht verdrahtet."""
    return TYP_BINDUNG.values_fields(prefix)[0]


def _unset_q(prefix: str) -> Q:
    """„Es wurde keine Aussage getroffen" — NULL **und** Leerstring.

    Der ``isnull``-Zweig ist bewusst redundant zum Leerstring-Zweig: blankes
    SQL-``NOT IN`` faengt NULL-Zeilen nicht, und die NULL-/Leerstring-Semantik
    ist der Kern des dritten Zustands (Refs #1819). Beim Umbau nicht
    wegoptimieren.
    """
    feld = _erststufen_feld(prefix)
    return Q(**{f"{feld}__isnull": True}) | Q(**{feld: ""})


def unmapped_q(prefix: str = "document_type__") -> Q:
    """Zustand 3: **ohne jede Zuordnung** — die echte Luecke (Refs #1819).

    Frueher war das schlicht die Negation von ``matching_q``; damit zaehlten
    auch die bewusst ausgeschlossenen Typen mit. Eine Einrichtung, die
    Hausverbote dokumentiert — also praktisch jede —, sah den Hinweis dauerhaft
    und hoerte auf hinzusehen. Ein **gesetzter** Schluessel ist eine
    vollstaendige Aussage, auch wenn sie „gehoert nicht in den Bericht" lautet:
    dieselbe Regel, die :meth:`TypBindung._eintrag` fuer die *Aufnahme* faehrt
    (Refs #1785), jetzt konsequent auch fuer die *Meldung*.

    Der Slug-Zweitschluessel bleibt einbezogen (``unmapped_q`` der Bindung):
    ein Typ ohne Schluessel, dessen Slug trifft, IST zugeordnet.
    """
    return TYP_BINDUNG.unmapped_q(prefix) & _unset_q(prefix)


def unknown_key_q(prefix: str = "document_type__") -> Q:
    """Zustand 4: Schluessel gesetzt, aber **kein Backend kennt ihn** (E-H).

    Entsteht auf genau drei Wegen — Fremdpaket mit anderem Vokabular,
    entferntes/umbenanntes Backend, Tippfehler in einer YAML. In allen dreien
    *glaubt* jemand, zugeordnet zu haben; das stille Nichts waere exakt die
    Falle, die dieser Umbau beseitigen soll.

    „Bekannt" heisst **registry-weit**, nicht backend-lokal: ein Schluessel, den
    nur ein anderes Backend deklariert, ist eine vollstaendige Aussage („dieser
    Typ gehoert in jenen Bericht") und keine Luecke. Das Vokabular wird
    deshalb bei jedem Aufruf frisch gelesen — es ist Registry-Zustand, kein
    Modul-Konstante.

    Gilt nur, wo die Erststufe der Berichtsschluessel ist; ein
    Verhaltens-Bericht (``ban_overview``) hat diesen Zustand nicht.
    """
    feld = _erststufen_feld(prefix)
    bekannt = sorted({*get_report_key_vocabulary(), EXCLUDED_KEY})
    return ~_unset_q(prefix) & ~Q(**{f"{feld}__in": bekannt})


# Ereignisse, deren Dokumenttyp gar keine Berichtszuordnung traegt.
# **Abgeleitet** aus derselben Deklaration wie :func:`resolve_category`
# (Refs #1824): das frueher handgeschriebene ``Q`` war die zweite Wahrheit, die
# driften konnte.
_UNMAPPED_Q = unmapped_q()

# Export-Fassung der Vollstaendigkeits-Meldungen (K6, Refs #1769). Sie ist die
# EINZIGE Fassung, die ein externer Empfaenger je zu sehen bekommt: der Block
# wird **datenunabhaengig** gebaut — beide Meldungen immer, nie mit Zahl.
#
# Die frueheren bezifferten Export-Vorlagen sind ersatzlos entfallen. Tragend war
# nicht, wie gross eine genannte Zahl ist, sondern dass sie ueberhaupt genannt
# wird: eine bezifferte Meldung publiziert implizit „mein Komplement >= k", also
# eine untere Schranke — selbst wenn jede Alterszelle unterdrueckt ist. Die
# Entscheidung darueber lief ueber eine Disjunktion auf Rohdaten und war damit
# selbst informativ.
#
# Die Formulierung sagt bewusst **nicht**, dass es solche Ereignisse gibt — der
# gleiche Text steht auch dann, wenn es gar keine Luecke gibt. Waeren beide
# Faelle unterscheidbar, verriete der Wortlaut selbst, ob die Luecke > 0 ist,
# und der Kanal waere durch die Hintertuer zurueck.
#
# **Uebersetzungshinweis (Refs #1744):** Die Ziffernfreiheit gilt je ``code`` und
# muss in **jeder** Sprache halten; eine Uebersetzung darf weder eine Zahl noch
# eine Bezugsgroesse ergaenzen.
_SUPPRESSED_MESSAGES = {
    "unmapped_system_types": _("Angabe zu Ereignissen ohne Berichtszuordnung im Zeitraum unterdrückt (k-Anonymität)"),
    "unknown_report_keys": _(
        "Angabe zu Ereignissen mit unbekanntem Berichtsschlüssel im Zeitraum unterdrückt (k-Anonymität)"
    ),
    "events_without_client": _(
        "Angabe zu Ereignissen ohne Personenbezug im Zeitraum unterdrückt (k-Anonymität) "
        "— nicht in Altersgruppen/Personenzahl enthalten"
    ),
}

# Severity der beiden Meldungen — im Export gibt es kein Original mehr, aus dem
# sie uebernommen werden koennte.
_ISSUE_SEVERITIES: dict[str, Literal["info", "warning"]] = {
    "unmapped_system_types": "warning",
    "unknown_report_keys": "warning",
    "events_without_client": "info",
}

# Kanonische Reihenfolge des Export-Blocks — Single Source of Truth fuer den
# datenunabhaengigen Aufbau in ``prepare_export``.
#
# Der Bestandsname ``unmapped_system_types`` BLEIBT, obwohl die Erststufe seit
# E3 ``report_key`` heisst: der Code ist eine Maschinenschnittstelle (JSON/XML,
# von ``monthly_trend`` importiert). Ein Rename waere ein Bruch fuer jeden
# Empfaenger, der auf ihm parst — und brachte keine Zeile Klarheit, die der
# Meldungstext nicht schon traegt.
_EXPORT_ISSUE_CODES = ("unmapped_system_types", "unknown_report_keys", "events_without_client")


def _base_queryset(facility, date_from, date_to):
    """Sichtbare Ereignisse der Einrichtung im Zeitraum (Basis aller Auswertungen)."""
    return Event.objects.for_facility(facility).filter(
        is_deleted=False,
        occurred_at__date__gte=date_from,
        occurred_at__date__lte=date_to,
    )


@register_backend("sample-report-1")
class SampleReport1Backend(DatasetBackend):
    """Kontakte/Beratung/Versorgung/Vermittlung je Zeitraum, plus Altersgruppen."""

    # Refs #1279 (sichtbares Label) und #1739 (interne Bezeichner): Modul, Slug
    # und Anzeigename sind bewusst traegerneutral — der Bericht ist eine frei
    # anpassbare Beispielvorlage, kein Formular einer bestimmten Behoerde.
    name = _("Beispiel-Sachbericht")
    description = _("Fasst die Ereignisse mit Berichtszuordnung zu den Kategorien eines Sachberichts zusammen.")
    snapshot_enabled = True
    # K3-A3 (Refs #1770): Die ``records``-Projektion sind die Berichtskategorien
    # — sie zaehlen nur Ereignisse mit Berichtszuordnung (:data:`TYP_BINDUNG`),
    # nicht alle Ereignisse des Zeitraums. Ohne diesen Ausweis liest sich
    # "Gesamtkontakte" wie eine Gesamtzahl, die es nicht ist.
    population = MAPPED_EVENTS
    # E1 (Refs #1824): Die Bindung wird deklariert, die Verfuegbarkeit bewusst
    # NICHT (``verfuegbarkeit`` bleibt leer). Der Sachbericht hat absichtlich
    # kein ``is_available``: er bleibt auch dann waehlbar, wenn eine Einrichtung
    # keinen zugeordneten Typ fuehrt — dann zeigt er eben Nullen und den
    # Vollstaendigkeitshinweis, statt aus dem Dropdown zu verschwinden (und
    # ueber ``get_available_backends`` einen 404 zu erzeugen).
    typ_bindung = TYP_BINDUNG

    def get_field_specs(self) -> list[FieldSpec]:
        """Spalten der tabellarischen Projektion (eine Zeile je Kategorie)."""
        return [
            FieldSpec("category", _("Kategorie")),
            FieldSpec("count", _("Anzahl")),
        ]

    def build_dataset(self, facility, date_from, date_to) -> DatasetResult:
        """Aggregiert Ereignisse zu Berichtskategorien und Altersgruppen."""
        base_qs = _base_queryset(facility, date_from, date_to)

        # Aggregation ueber die von der Bindung deklarierten Schluesselfelder
        # (``system_type`` UND ``slug``): beide muessen in der Gruppierung
        # stehen, sonst koennte der Zweitschluessel nicht greifen (Refs #1741).
        # Die Ordnung bleibt system_type-fuehrend (Bestandsformat des
        # Snapshot-Payloads); die publizierte Zeilenordnung stellt ohnehin die
        # Artefaktgrenze her (``ORDER_CATEGORY``, Refs #1767/#1772).
        schluessel_felder = TYP_BINDUNG.values_fields("document_type__")
        by_dt = base_qs.values(*schluessel_felder).annotate(count=Count("id")).order_by(*schluessel_felder)

        # Konsolidierung auf die Berichtskategorien
        category_counts: OrderedDict[str, int] = OrderedDict()
        total = 0
        for row in by_dt:
            cat = TYP_BINDUNG.resolve_row(row, "document_type__")
            if cat is None:
                # Nicht zugeordnete Typen still ueberspringen — auch ``total``
                # zaehlt sie nicht mit (Bestandsverhalten). Sichtbar werden sie
                # ueber ``validate_completeness``.
                continue
            category_counts[cat] = category_counts.get(cat, 0) + row["count"]
            total += row["count"]

        by_category = [(name, count) for name, count in category_counts.items()]

        # Altersgruppen (nur Ereignisse mit Personenbezug).
        #
        # K0-b (Refs #1770): jede Zeile traegt zusaetzlich die DISTINCT-
        # Personenzahl ihres Buckets — die Groesse, gegen die die k-Schwelle im
        # Export-/PDF-Pfad prueft. Sie ist Teil des **Snapshot-Payloads**
        # (Formataenderung; Bestand per Migration 0128 nachgetragen) und
        # verlaesst den Suppressionspfad nie (``_suppress_small`` entfernt sie).
        #
        # Die Buckets entstehen bewusst allein aus ``age_cluster`` —
        # **mapping-agnostisch**: sie zaehlen jedes Ereignis mit Personenbezug,
        # zugeordnet oder nicht. Der Slug-Zweitschluessel (Refs #1741) aendert
        # daran nichts; er verschiebt nur, was in ``by_category``/``total``
        # landet.
        by_age_cluster = [
            {
                "cluster": row["client__age_cluster"],
                "label": _AGE_LABELS.get(row["client__age_cluster"], ""),
                "count": row["count"],
                PERSONS_KEY: row["persons"],
            }
            for row in base_qs.exclude(client__isnull=True)
            .values("client__age_cluster")
            .annotate(count=Count("id"), persons=Count("client", distinct=True))
            .order_by("-count")
        ]

        unique_clients = base_qs.exclude(client__isnull=True).values("client").distinct().count()

        return DatasetResult(
            mode="aggregate",
            records=[{"category": name, "count": count} for name, count in by_category],
            summary={
                "total": total,
                "by_category": by_category,
                "by_age_cluster": by_age_cluster,
                "unique_clients": unique_clients,
            },
            completeness=self.validate_completeness(facility, date_from, date_to),
        )

    def validate_completeness(self, facility, date_from, date_to) -> list[CompletenessIssue]:
        """Beziffert, was der Bericht **nicht** zeigt.

        Drei Checks, alle gegen die Rohdaten statt gegen den Datensatz: die
        betroffenen Ereignisse fehlen dort per Definition, und Ereignisse ohne
        Personenbezug sind in den Kategorien enthalten, in
        Altersgruppen/Personenzahl aber nicht.

        **Bewusst ausgeschlossene Typen zaehlen NICHT mit** (Refs #1819): ein
        gesetzter Schluessel ist eine vollstaendige Aussage. Wer den Hinweis
        dauerhaft sieht, weil er Hausverbote dokumentiert, hoert auf hinzusehen
        — und dann traegt er nichts mehr.
        """
        # Alle vier Kennzahlen in *einer* Aggregation: ``build_dataset`` ruft
        # diese Methode immer mit, und der Hybrid-/Snapshot-Pfad ruft
        # ``build_dataset`` je Zeitsegment einmal auf — Einzel-``count()``s
        # wuerden sich dort vervielfachen.
        #
        # ``events_in_period`` statt ``total``: der gleichnamige Summary-Key
        # zaehlt nur die *gemappten* Ereignisse — hier ist die Bezugsgroesse
        # aber alles Sichtbare im Zeitraum.
        unbekannt_q = unknown_key_q()
        counts = _base_queryset(facility, date_from, date_to).aggregate(
            events_in_period=Count("id"),
            without_client=Count("id", filter=Q(client__isnull=True)),
            unmapped_events=Count("id", filter=_UNMAPPED_Q),
            unmapped_types=Count("document_type_id", filter=_UNMAPPED_Q, distinct=True),
            unknown_events=Count("id", filter=unbekannt_q),
            unknown_types=Count("document_type_id", filter=unbekannt_q, distinct=True),
        )
        issues: list[CompletenessIssue] = []

        # (a) Dokumenttypen ohne Berichtszuordnung (inkl. system_type NULL/leer).
        #
        # ``format_lazy`` statt f-String (Refs #1744): der Text erscheint in
        # Panel und JSON-/XML-Export und muss der Sprache des Empfaengers
        # folgen. Die Platzhalter sind benannt — eine Uebersetzung darf sie
        # umstellen, aber nicht weglassen.
        if counts["unmapped_events"]:
            issues.append(
                CompletenessIssue(
                    "unmapped_system_types",
                    format_lazy(
                        _("{events} Ereignisse in {types} Dokumentationstypen ohne Berichtszuordnung im Zeitraum"),
                        events=counts["unmapped_events"],
                        types=counts["unmapped_types"],
                    ),
                    "warning",
                    count=counts["unmapped_events"],
                )
            )

        # (a2) Gesetzter, aber registry-weit unbekannter Berichtsschluessel
        # (E-H). Eigener Code: „ich habe zugeordnet, es passiert nur nichts" ist
        # ein anderer Befund als „ich habe nichts gesagt" — und er verlangt eine
        # andere Reaktion (Schluessel korrigieren statt einen setzen).
        if counts["unknown_events"]:
            issues.append(
                CompletenessIssue(
                    "unknown_report_keys",
                    format_lazy(
                        _(
                            "{events} Ereignisse in {types} Dokumentationstypen mit einem Berichtsschlüssel, "
                            "den kein Bericht kennt"
                        ),
                        events=counts["unknown_events"],
                        types=counts["unknown_types"],
                    ),
                    "warning",
                    count=counts["unknown_events"],
                )
            )

        # (b) Ereignisse ohne Personenbezug — die Luecke in by_age_cluster
        # und unique_clients.
        if counts["without_client"]:
            share = round(counts["without_client"] * 100 / counts["events_in_period"])
            issues.append(
                CompletenessIssue(
                    "events_without_client",
                    format_lazy(
                        _(
                            "{without_client} von {events_in_period} Ereignissen im Zeitraum ohne "
                            "Personenbezug ({share} %) — nicht in Altersgruppen/Personenzahl enthalten"
                        ),
                        without_client=counts["without_client"],
                        events_in_period=counts["events_in_period"],
                        share=share,
                    ),
                    "info",
                    count=counts["without_client"],
                )
            )

        return issues

    def prepare_export(self, facility, result: DatasetResult) -> DatasetResult:
        """K-Anon-Suppression fuer Export und Panel-Vorschau (Refs #1278, T1).

        Dieselbe Maschinerie wie fuer das PDF (``suppress_sample_report_1_stats``):
        Zellen < Schwelle werden ``count=None``/``suppressed=True`` (inkl.
        sekundaerer Suppression gegen Randsummen-Rueckrechnung),
        ``unique_clients`` und die Randsumme ``total`` < Schwelle werden
        ``None`` (Security R14). ``records`` wird aus den bereits
        suppressierten Kategorie-Zeilen neu projiziert — sonst haette der
        CSV-Export die Rohzahlen weiterhin gezeigt.

        Der Vollstaendigkeits-Block ist **datenunabhaengig** (K6, Refs #1769):
        alle Meldungen immer, nie mit Zahl, in fester Reihenfolge — er wird
        aus ``_EXPORT_ISSUE_CODES`` gebaut und nicht aus ``result.completeness``
        abgeleitet. Damit sagt weder seine Gestalt noch sein Inhalt etwas ueber
        die Rohdaten aus. Der Hinweis auf die Luecke bleibt in jedem Fall
        stehen; ``build_dataset`` bleibt roh (Snapshot-Payload) und behaelt die
        bezifferten Fassungen fuer die interne Pruefung.

        Erwartet das **rohe** ``build_dataset``-Ergebnis: ``by_category``
        kommt als ``(name, count)``-Tupel herein und geht als Dict-Zeilen
        heraus — ein zweiter Durchlauf ueber dasselbe Ergebnis ist damit
        kein sinnvoller Aufruf.
        """
        threshold = _get_threshold(facility)

        summary = suppress_sample_report_1_stats(facility, result.summary)
        # Transparenz analog ``build_external_report``-Metadaten: der Empfaenger
        # soll erkennen, ab welcher Fallzahl unterdrueckt wurde.
        summary = stamp_threshold(summary, threshold)

        records = [{"category": row["name"], "count": row["count"]} for row in summary["by_category"]]

        # K6 (Refs #1769): fest verdrahtet statt aus ``result.completeness``
        # abgeleitet — jede Ableitung waere wieder eine datenabhaengige
        # Verzweigung. Codes, die ``validate_completeness`` kuenftig ergaenzt,
        # erreichen den Export damit nicht ungeprueft (fail-safe: lieber eine
        # Meldung zu wenig als eine Kleinstfallzahl zu viel).
        completeness = [
            CompletenessIssue(code, _SUPPRESSED_MESSAGES[code], _ISSUE_SEVERITIES[code], count=None)
            for code in _EXPORT_ISSUE_CODES
        ]

        return dataclasses.replace(result, records=records, summary=summary, completeness=completeness)
