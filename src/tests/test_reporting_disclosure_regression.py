"""Regressionsschutz der Disclosure-Analyse (Refs #1770, Analyse [#1742]).

Einzelfall-Asserts haben diese Kanalklasse jahrelang nicht gefunden — jede
einzelne Zusicherung war fuer sich erfuellt, und trotzdem liess die *Kombination*
aus Randsumme, Zeilenordnung und Suppressionsentscheidung Kleinstfallzahlen
exakt zurueckrechnen. Deshalb drei strukturelle Tests statt weiterer Beispiele:

1. **Kleinst-Welt-Enumeration** — alle konsistenten Preimages einer publizierten
   Sicht aufzaehlen und pruefen, dass keine unterdrueckte Kleinstfallzahl
   eindeutig bestimmt ist. Mit Positiv-Kontrolle: der Stand VOR #1770 muss an
   derselben Aufzaehlung reissen, sonst misst der Test nichts.
2. **Ordnungstest an der Artefaktgrenze** — zwei Welten, die sich nur in der
   Zuordnung der unterdrueckten Werte unterscheiden, muessen in HTML, PDF-Daten,
   JSON und XML dieselbe Zeilenreihenfolge erzeugen. Der Bestandstest aus
   #1767 pinnt die
   Ordnung nur auf Service-Ebene; ein ``dictsort`` in einem Template oder ein
   ``sorted(...)`` in einem Renderer bliebe dort unbemerkt.
3. **Architektur-Guard** — jedes Artefakt mit Randsumme + Aufschluesselung muss
   dieselbe zentrale Suppressionsfunktion benutzen (siehe
   ``test_architecture_guards_services.py::TestZentraleSuppressionsfunktion``).
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import date
from typing import Any

import pytest

from core.services.dashboard import suppress_report_stats, suppress_sample_report_1_stats
from core.services.dashboard.external_report import DEFAULT_K_THRESHOLD

# Obergrenze der aufgezaehlten Randsumme. Sie ist der Grund fuer die Auflage
# unten: die Aufzaehlung kennt nur Welten mit Σ <= ENUMERATION_LIMIT, also ist
# eine Beobachtung mit unterdrueckter (= unbekannter) oder groesserer Randsumme
# NICHT vollstaendig abgedeckt — dort haette ein „nur ein Preimage" nichts mit
# der Sicherheit der Publikation zu tun, sondern nur mit der Grenze selbst
# (Schein-Pin). Der Wert ist bewusst klein: bei drei Zeilen und Σ <= 14 sind es
# 364 Welten, die Aufzaehlung ist damit exakt und nicht bloss eine Stichprobe,
# und alle interessanten Faelle um die Default-Schwelle 5 liegen darin.
ENUMERATION_LIMIT = 14

_ROW_LABELS = ("A", "B", "C")

# Zwei Weltraeume statt einem. Der zweite (vier Zeilen, Σ <= 16, 1820 Welten)
# ist kein Luxus: die Sekundaer-Suppression zeigt ihre Fixpunkt-Eigenschaft erst,
# wenn nach dem ersten Opfer noch mehr als eine sichtbare Zelle uebrig ist —
# bei drei Zeilen ist danach hoechstens eine uebrig. Beide Raeume bleiben exakt
# aufzaehlbar.
_WORLD_SHAPES = (
    pytest.param(_ROW_LABELS, ENUMERATION_LIMIT, id="3-zeilen-summe-14"),
    pytest.param(("A", "B", "C", "D"), 16, id="4-zeilen-summe-16"),
)


def _worlds(labels: tuple[str, ...] = _ROW_LABELS, limit: int = ENUMERATION_LIMIT) -> list[dict[str, int]]:
    """Alle Welten mit ``len(labels)`` nichtleeren Zeilen und Σ <= ``limit``.

    Nichtleer, weil eine GROUP-BY-Zeile nur fuer nichtleere Buckets entsteht —
    genau der Zeilen-Existenz-Kanal, den der Empfaenger kennt.
    """
    return [
        dict(zip(labels, counts, strict=True))
        for counts in itertools.product(range(1, limit + 1), repeat=len(labels))
        if sum(counts) <= limit
    ]


def _publish_document_types(facility, world: dict[str, int]) -> dict[str, Any]:
    """Die publizierte Sicht des Halbjahres-Sachberichts auf ``by_document_type``.

    Die uebrigen Aggregate sind bewusst neutral gesetzt (leere Alterstabelle,
    leere Kontaktstufen, grosse Personenzahl), damit die Aufzaehlung genau
    **einen** Kanal misst: Randsumme + Dokumenttyp-Aufschluesselung.
    """
    return suppress_report_stats(
        facility,
        {
            "total_contacts": sum(world.values()),
            "unique_clients": 50,
            "by_contact_stage": {},
            "persons_by_contact_stage": {},
            "by_document_type": sorted(
                ({"name": name, "category": "contact", "count": count} for name, count in world.items()),
                key=lambda row: -row["count"],
            ),
            "by_age_cluster": [],
        },
    )


def _observation(published: dict[str, Any]) -> tuple:
    """Alles, was der Empfaenger sieht — inklusive der Zeilenreihenfolge.

    Die Reihenfolge gehoert bewusst in die Beobachtung: waere sie eine
    Rangaussage (K5), lagen Welten mit vertauschten Werten in verschiedenen
    Klassen, die Preimages waeren kleiner und der Test wuerde genau daran
    reissen.
    """
    return (
        published["total_contacts"],
        tuple((row["name"], row["count"], row["suppressed"]) for row in published.get("by_document_type", ())),
    )


def _preimage_groups(publish, labels=_ROW_LABELS, limit=ENUMERATION_LIMIT) -> dict[tuple, list[dict[str, int]]]:
    groups: dict[tuple, list[dict[str, int]]] = defaultdict(list)
    for world in _worlds(labels, limit):
        groups[_observation(publish(world))].append(world)
    return groups


def _pinned_small_cells(
    groups: dict[tuple, list[dict[str, int]]], threshold: int, limit: int = ENUMERATION_LIMIT
) -> list[str]:
    """Unterdrueckte Kleinstfallzahlen, die ueber ihr Preimage eindeutig sind."""
    leaks = []
    for observation, worlds in groups.items():
        margin = observation[0]
        # AUFLAGE (security-notes § Regressionsschutz): nur Beobachtungen
        # werten, deren publizierte Randsumme <= Enumerationsgrenze ist. Ist die
        # Randsumme unterdrueckt, kennt der Empfaenger sie nicht — dann sind auch
        # Welten jenseits der Grenze konsistent, und ein hier „eindeutiges"
        # Preimage waere ein Artefakt der Aufzaehlung, kein Leak.
        if margin is None or margin > limit:
            continue
        for name, _count, suppressed in observation[1]:
            if not suppressed:
                continue
            values = {world[name] for world in worlds}
            if len(values) >= 2:
                continue
            (value,) = values
            # Ein exakt bestimmter Wert >= k ist keine Kleinstfallzahl (das
            # Sekundaer-Opfer darf rueckrechenbar sein — es wurde nur
            # unterdrueckt, um die Kleinstzelle zu schuetzen). Und ein Pin auf 0
            # ist keiner: „in diesem Bucket gab es keinen Fall" ist die
            # Abwesenheit von Faellen, keine Kleinstfallzahl — dieselbe Ausnahme
            # wie in ``disclosure.findings``. Relevant wird sie erst mit der
            # K3-A1-Restzeile: GROUP-BY-Zeilen koennen gar nicht 0 sein,
            # datenunabhaengig emittierte Zeilen sehr wohl.
            if value == 0 or value >= threshold:
                continue
            leaks.append(f"{observation}: {name} = {value}")
    return leaks


@pytest.mark.django_db
class TestKleinstWeltEnumeration:
    """„Keine unterdrueckte Kleinstfallzahl ist ueber das Preimage eindeutig."

    Der Test wuerde auf dem Stand VOR dieser Umsetzungswelle reissen, und zwar
    an mehreren Kanaelen gleichzeitig:

    * **K5** (bis #1767):
      die Zeilenreihenfolge war count-sortiert, also selbst eine Rangaussage.
      Weil sie hier Teil der Beobachtung ist, waeren die Preimage-Klassen
      feiner geschnitten — Welten mit vertauschten unterdrueckten Werten laegen
      in verschiedenen Klassen und jede Klasse pinnte ihre Kleinstzelle.
    * **K2** (bis jetzt): ``len(suppressed) != 1`` liess zwei unterdrueckte
      Zellen ungeschuetzt, die sich beide aus dem Residuum ergeben (R = 2 auf
      zwei Zellen ⇒ beide sind 1). Die Positiv-Kontrolle unten fuehrt genau
      diesen Stand nach und zeigt, dass er reisst.
    * **Fixpunkt** (K1/K2, bis jetzt): ohne Iteration bleibt nach der ersten
      Sekundaer-Suppression eine neue Rueckrechenbarkeit stehen, weil niemand
      ein zweites Mal nachrechnet. Auch dafuer gibt es eine Positiv-Kontrolle —
      keine Behauptung.
    """

    @pytest.mark.parametrize(("labels", "limit"), _WORLD_SHAPES)
    def test_keine_unterdrueckte_kleinstzelle_ist_eindeutig(self, facility, labels, limit):
        groups = _preimage_groups(lambda world: _publish_document_types(facility, world), labels, limit)

        leaks = _pinned_small_cells(groups, DEFAULT_K_THRESHOLD, limit)

        assert not leaks, (
            "Diese unterdrueckten Kleinstfallzahlen sind ueber die publizierte "
            "Sicht exakt rueckrechenbar:\n" + "\n".join(leaks[:10])
        )

    def test_die_aufzaehlung_deckt_ueberhaupt_unterdrueckte_zellen_ab(self, facility):
        """Anker gegen einen still leeren Test: haette die Aufzaehlung gar keine
        auswertbare Beobachtung mit unterdrueckten Zellen, waere der Test oben
        trivial gruen."""
        groups = _preimage_groups(lambda world: _publish_document_types(facility, world))

        auswertbar = [
            observation
            for observation in groups
            if observation[0] is not None and any(row[2] for row in observation[1])
        ]

        assert len(auswertbar) >= 20, f"nur {len(auswertbar)} auswertbare Beobachtungen mit Unterdrueckung"

    def test_positivkontrolle_k2_der_alte_stand_reisst(self, facility):
        """Der K2-Stand VOR #1770 muss an derselben Aufzaehlung scheitern.

        Sonst misst der Enumerationstest nichts — ein Regressionsschutz, der
        seinen eigenen Trefferfall nie beweist, kann still verrotten (dasselbe
        Muster wie ``test_synthetic_violation_is_detected`` im
        Encryption-Bypass-Guard). Gemessen: 54 gepinnte Kleinstzellen ueber 116
        Beobachtungsklassen, gegen 0 auf dem aktuellen Stand.
        """
        groups = _preimage_groups(lambda world: _legacy_publish(world, DEFAULT_K_THRESHOLD))

        leaks = _pinned_small_cells(groups, DEFAULT_K_THRESHOLD)

        assert leaks, "Die Aufzaehlung findet die bekannte K2-Luecke nicht mehr — Harness pruefen"

    def test_positivkontrolle_ohne_fixpunkt_reisst(self, facility):
        """Dritte Positiv-Kontrolle: dieselbe Suppression, aber das
        Machbarkeits-Audit laeuft nur **einen** Schritt statt bis zum Fixpunkt.

        Gemessen: 2 gepinnte Kleinstzellen im Weltraum „3 Zeilen, Σ <= 14" (im
        4-Zeilen-Raum 0 — die Kontrolle ist deshalb bewusst auf den kleineren
        Raum gescopt). Damit ist die Iteration belegt und nicht behauptet: nach
        dem ersten Opfer aendern sich die Schranken aller uebrigen Zellen, und
        genau dort entsteht die naechste Rueckrechenbarkeit.
        """
        leaks = _pinned_small_cells(
            _preimage_groups(lambda world: _single_pass_publish(world, DEFAULT_K_THRESHOLD)),
            DEFAULT_K_THRESHOLD,
        )

        assert leaks, "Ohne Fixpunkt muesste die zweite Runde fehlen und eine Zelle gepinnt bleiben"

    def test_positivkontrolle_k5_ohne_kanonische_ordnung_reisst(self, facility):
        """Zweite Positiv-Kontrolle: derselbe (neue) Suppressionsstand, aber die
        Ausgabereihenfolge wieder count-sortiert wie vor
        #1767.

        Damit ist belegt, dass die Aufzaehlung **beide** Kanaele sieht — und
        zugleich die Kopplungs-Warnung der Analyse: K2/K1 wirken nur zusammen
        mit K5. Gemessen: 250 gepinnte Kleinstzellen, obwohl Machbarkeits-Audit
        und Fixpunkt-Guard voll aktiv sind.
        """

        def query_order(world: dict[str, int]) -> dict[str, Any]:
            published = _publish_document_types(facility, world)
            rank = {name: index for index, (name, _c) in enumerate(sorted(world.items(), key=lambda kv: -kv[1]))}
            return {
                **published,
                "by_document_type": sorted(published["by_document_type"], key=lambda row: rank[row["name"]]),
            }

        leaks = _pinned_small_cells(_preimage_groups(query_order), DEFAULT_K_THRESHOLD)

        assert leaks, "Ohne kanonische Ordnung muesste die Zeilenposition die Kleinstzelle verraten"


def _single_pass_publish(world: dict[str, int], threshold: int) -> dict[str, Any]:
    """Die aktuelle Suppression, aber OHNE Fixpunkt (nur fuer die Positiv-Kontrolle).

    Primaersuppression, **ein** Schritt des Machbarkeits-Audits (kleinste
    sichtbare Zelle faellt), kanonische Ordnung, R14 — und dann Schluss. Nutzt
    denselben Rechenkern wie die Produktion, damit die Kontrolle wirklich nur
    die fehlende Iteration misst und nicht einen Nachbau.
    """
    from core.services.dashboard.disclosure import findings
    from core.services.dashboard.external_report import _local_partition

    total = sum(world.values())
    names = list(world)
    values = [world[name] for name in names]
    flags = [value < threshold for value in values]
    victims = [False] * len(values)
    margin = total if total >= threshold else None

    if margin is not None:
        partition = _local_partition(
            values, flags, victims, threshold, margin=margin, min_values=[1] * len(values), person_gated=False
        )
        if findings([partition], threshold=threshold):
            candidates = [index for index, flag in enumerate(flags) if not flag]
            if candidates:
                victim = min(candidates, key=lambda index: (values[index], index))
                flags[victim] = True

    rows = [
        {"name": names[index], "count": None if flags[index] else values[index], "suppressed": flags[index]}
        for index in range(len(names))
    ]
    rows.sort(key=lambda row: -(row["count"] or 0))
    visible = [row for row in rows if not row["suppressed"]]
    hidden = sorted((row for row in rows if row["suppressed"]), key=lambda row: row["name"])
    return {"total_contacts": margin, "by_document_type": [*visible, *hidden]}


def _legacy_publish(world: dict[str, int], threshold: int) -> dict[str, Any]:
    """Der Suppressionsstand VOR #1770 (nur fuer die Positiv-Kontrolle).

    Primaersuppression auf Ereigniszahlen, Sekundaersuppression genau dann, wenn
    **eine** Zelle unterdrueckt ist, und dann faellt die *kleinste* sichtbare
    Zelle. Die kanonische Ordnung aus #1767 ist bewusst schon drin, damit die
    Kontrolle allein den K2-Anteil zeigt.
    """
    total = sum(world.values())
    rows = [{"name": name, "count": count, "suppressed": count < threshold} for name, count in world.items()]
    rows.sort(key=lambda row: -row["count"])
    hidden = [row for row in rows if row["suppressed"]]
    visible = [row for row in rows if not row["suppressed"]]
    if len(hidden) == 1 and visible:
        min(visible, key=lambda row: row["count"])["suppressed"] = True
    return {
        "total_contacts": total if total >= threshold else None,
        "by_document_type": [
            {"name": row["name"], "count": None if row["suppressed"] else row["count"], "suppressed": row["suppressed"]}
            for row in sorted(rows, key=lambda row: (row["suppressed"], row["name"] if row["suppressed"] else ""))
        ],
    }


@pytest.mark.django_db
class TestKleinstWeltEnumerationBeispielSachbericht:
    """Dieselbe Invariante am zweiten Artefakt mit Randsumme + Aufschluesselung.

    Der Beispiel-Sachbericht akkumuliert ``total`` exakt aus denselben Zeilen,
    die ``by_category`` ausweist — dieselbe Partitions-Gleichung, anderer
    Wrapper. Ohne diesen zweiten Lauf wuerde der Test nur einen von zwei
    Codepfaden abdecken.
    """

    def _publish(self, facility, world: dict[str, int]) -> dict[str, Any]:
        out = suppress_sample_report_1_stats(
            facility,
            {
                "total": sum(world.values()),
                "unique_clients": 50,
                "by_category": [(name, count) for name, count in world.items()],
                "by_age_cluster": [],
            },
        )
        return {
            "total_contacts": out["total"],
            "by_document_type": out.get("by_category", ()),
        }

    def test_keine_unterdrueckte_kleinstzelle_ist_eindeutig(self, facility):
        groups = _preimage_groups(lambda world: self._publish(facility, world))

        leaks = _pinned_small_cells(groups, DEFAULT_K_THRESHOLD)

        assert not leaks, "\n".join(leaks[:10])


# --- Ordnungstest an der Artefaktgrenze ------------------------------------
#
# Zwei Welten, die sich NUR in der Zuordnung der unterdrueckten Werte
# unterscheiden (u18 = 1 / 18–26 = 4 gegen u18 = 4 / 18–26 = 1), muessen in
# jedem Artefakt dieselbe Zeilenreihenfolge erzeugen. ``#1767`` pinnt das auf
# Service-Ebene; hier steht die Grenze, an der das Artefakt entsteht — ein
# ``dictsort`` in einem Template oder ein ``sorted(...)`` in einem Renderer
# bliebe auf Service-Ebene unsichtbar.

_AGE_LABELS = ("Unter 18", "18–26", "27+")
_DOC_NAMES = ("Notiz", "Kontakt", "Beratung")


def _swapped_worlds() -> tuple[dict[str, Any], dict[str, Any]]:
    """Zwei Roh-Statistiken mit vertauschten Kleinstwerten."""

    def stats(u18_count: int, u18_persons: int, mid_count: int, mid_persons: int, kontakt: int, beratung: int):
        return {
            "total_contacts": 35,
            "unique_clients": 40,
            "by_contact_stage": {"anonym": 12, "identifiziert": 11, "qualifiziert": 12},
            "persons_by_contact_stage": {"anonym": 12, "identifiziert": 11, "qualifiziert": 12},
            # Query-Ordnung: absteigend nach Anzahl.
            "by_document_type": sorted(
                [
                    {"name": "Notiz", "category": "note", "count": 30},
                    {"name": "Kontakt", "category": "contact", "count": kontakt},
                    {"name": "Beratung", "category": "service", "count": beratung},
                ],
                key=lambda row: -row["count"],
            ),
            "by_age_cluster": sorted(
                [
                    {"cluster": "27_plus", "label": "27+", "count": 30, "persons": 9},
                    {"cluster": "u18", "label": "Unter 18", "count": u18_count, "persons": u18_persons},
                    {"cluster": "18_26", "label": "18–26", "count": mid_count, "persons": mid_persons},
                ],
                key=lambda row: -row["count"],
            ),
        }

    return stats(1, 1, 4, 4, 1, 4), stats(4, 4, 1, 1, 4, 1)


def _label_sequence(text: str, labels: tuple[str, ...]) -> list[str]:
    """Reihenfolge, in der die Labels im Artefakt auftauchen."""
    positions = [(text.index(label), label) for label in labels if label in text]
    return [label for _position, label in sorted(positions)]


@pytest.mark.django_db
class TestOrdnungAnDerArtefaktgrenze:
    """K5 an der Grenze, an der das Artefakt entsteht — nicht nur im Service."""

    def _rendered(self, template: str, context: dict[str, Any]) -> str:
        from django.template.loader import render_to_string

        return render_to_string(template, context)

    def test_externer_bericht_html(self, facility):
        from core.services.dashboard import suppress_report_stats

        welt_a, welt_b = _swapped_worlds()
        seiten = [
            self._rendered("core/statistics/external_report.html", {"report": suppress_report_stats(facility, welt)})
            for welt in (welt_a, welt_b)
        ]

        assert _label_sequence(seiten[0], _AGE_LABELS) == _label_sequence(seiten[1], _AGE_LABELS)
        assert _label_sequence(seiten[0], _DOC_NAMES) == _label_sequence(seiten[1], _DOC_NAMES)

    def test_halbjahres_sachbericht_pdf_datenpfad(self, facility):
        from core.services.dashboard import suppress_report_stats

        welt_a, welt_b = _swapped_worlds()
        seiten = [
            self._rendered("core/export/report_pdf.html", {"stats": suppress_report_stats(facility, welt)})
            for welt in (welt_a, welt_b)
        ]

        assert _label_sequence(seiten[0], _AGE_LABELS) == _label_sequence(seiten[1], _AGE_LABELS)
        assert _label_sequence(seiten[0], _DOC_NAMES) == _label_sequence(seiten[1], _DOC_NAMES)

    def test_beispiel_sachbericht_pdf_datenpfad(self, facility):
        from core.services.dashboard import suppress_sample_report_1_stats

        welt_a, welt_b = _swapped_worlds()
        seiten = [
            self._rendered(
                "core/export/sample_report_1_pdf.html",
                {
                    "stats": suppress_sample_report_1_stats(
                        facility,
                        {
                            "total": 35,
                            "unique_clients": 40,
                            "by_category": [("Kontakte", 30), ("Beratung", 5)],
                            "by_age_cluster": welt["by_age_cluster"],
                        },
                    )
                },
            )
            for welt in (welt_a, welt_b)
        ]

        assert _label_sequence(seiten[0], _AGE_LABELS) == _label_sequence(seiten[1], _AGE_LABELS)

    @pytest.mark.parametrize("renderer", ["json", "xml"])
    def test_berichts_export_json_und_xml(self, facility, renderer):
        """Export-Renderer: derselbe Datensatz, zwei Welten — gleiche Ordnung.

        Bewusst ueber die Renderer statt ueber ``export_dataset``: der
        Datensatz wird hier konstruiert, damit der Test genau die Vertauschung
        misst und nicht die Aggregation.
        """
        from django.utils import timezone

        from core.reporting.base import DatasetResult
        from core.reporting.export import _render_json, _render_xml
        from core.reporting.registry import get_backend

        backend = get_backend("sample-report-1")
        render = _render_json if renderer == "json" else _render_xml
        ausgaben = []
        for welt in _swapped_worlds():
            result = DatasetResult(
                mode="aggregate",
                records=[{"category": "Kontakte", "count": 30}, {"category": "Beratung", "count": 5}],
                summary={
                    "total": 35,
                    "unique_clients": 40,
                    "by_category": [("Kontakte", 30), ("Beratung", 5)],
                    "by_age_cluster": welt["by_age_cluster"],
                },
                completeness=[],
            )
            prepared = backend.prepare_export(facility, result)
            ausgaben.append(
                render(backend, date(2026, 1, 1), date(2026, 6, 30), prepared, timezone.now()).decode("utf-8")
            )

        assert _label_sequence(ausgaben[0], _AGE_LABELS) == _label_sequence(ausgaben[1], _AGE_LABELS)

    def test_die_vertauschung_ist_ueberhaupt_wirksam(self, facility):
        """Anker gegen einen still leeren Ordnungstest.

        Zwei Bedingungen muessen halten, sonst prueft der Test nichts: die
        **Eingabe**ordnung der beiden Welten muss sich unterscheiden (sonst
        waere gleiche Ausgabe trivial), und beide Welten muessen wirklich
        unterdrueckte Alterszeilen haben.
        """
        from core.services.dashboard import suppress_report_stats

        welt_a, welt_b = _swapped_worlds()
        assert [row["cluster"] for row in welt_a["by_age_cluster"]] != [
            row["cluster"] for row in welt_b["by_age_cluster"]
        ], "Die Query-Ordnung beider Welten ist identisch — der Ordnungstest waere trivial"

        for welt in (welt_a, welt_b):
            out = suppress_report_stats(facility, welt)
            unterdrueckt = [row["cluster"] for row in out["by_age_cluster"] if row["suppressed"]]
            # K3-A1 (Refs #1770): die Restzeile „ohne Personenbezug" laeuft mit
            # (hier W = 35 − 35 = 0) und ist deshalb ebenfalls unterdrueckt.
            assert sorted(unterdrueckt) == ["18_26", "__no_person__", "u18"], unterdrueckt


# --- Fail-safe-Pfad darf nicht irrefuehren ---------------------------------
#
# ``_drop_breakdowns`` entfernt im Fail-safe auch ``by_document_type`` — der
# Key ist dann weg, und die Templates liefen in ihren „keine Daten"-Zweig. Das
# ist genau die Irrefuehrung, die #1768
# fuer die Alterstabelle bereits verworfen hat: „Keine Daten im Zeitraum" ist
# eine **Aussage ueber die Rohdaten**, und sie ist hier falsch — es gab Daten,
# sie sind nur unterdrueckt.

_KOLLAPS_STATS = {
    "total_contacts": 15,
    "unique_clients": 20,
    "by_contact_stage": {"anonym": 5, "identifiziert": 5, "qualifiziert": 5},
    "persons_by_contact_stage": {"anonym": 5, "identifiziert": 5, "qualifiziert": 5},
    "by_document_type": [{"name": f"T{index}", "category": "contact", "count": 1} for index in range(15)],
    "by_age_cluster": [],
}


@pytest.mark.django_db
class TestFailSafePfadIstNichtIrrefuehrend:
    """Nichtirreführung (#1768-Doktrin) auch fuer den K1-Fail-safe (#1770).

    Der Zustand ist ueber den echten Wrapper erreichbar: 15 Dokumenttypen mit je
    einem Ereignis und drei gleich grosse Kontaktstufen. Jede Zeile ist dann
    zwingend „1" (Randsumme 15, Zeilen-Existenz), und die vollstaendig sichtbare
    Stufentabelle haelt die Randsumme rekonstruierbar — der Guard laesst deshalb
    beide Aufschluesselungen fallen.
    """

    def _stats(self, facility):
        from core.services.dashboard import suppress_report_stats

        out = suppress_report_stats(facility, dict(_KOLLAPS_STATS))
        assert "by_document_type" not in out, "Vorbedingung: der Fail-safe hat die Tabelle fallen lassen"
        return out

    def test_externer_bericht_html_weist_die_unterdrueckung_aus(self, facility):
        from django.template.loader import render_to_string

        seite = render_to_string("core/statistics/external_report.html", {"report": self._stats(facility)})
        abschnitt = seite.split("report-document-types")[-1].split("report-age")[0]

        assert "Keine Daten im Zeitraum." not in abschnitt
        assert "Angaben nach Dokumentationstyp unterdrückt (k-Anonymität)." in abschnitt

    def test_sachbericht_pdf_weist_die_unterdrueckung_aus(self, facility):
        from django.template.loader import render_to_string

        seite = render_to_string("core/export/report_pdf.html", {"stats": self._stats(facility)})
        abschnitt = seite.split("Kontakte nach Dokumentationstyp")[-1].split("Kontakte nach Altersgruppe")[0]

        assert "Keine Daten im gewählten Zeitraum." not in abschnitt
        assert "Angaben nach Dokumentationstyp unterdrückt (k-Anonymität)." in abschnitt

    def test_leerer_zeitraum_bekommt_denselben_hinweis(self, facility):
        """Bewusste Kehrseite, nicht Zufall: bei **leerem** Zeitraum ist
        ``total_contacts`` ebenfalls ``None`` (Randsummen-Regel R14 auf 0), also
        greift derselbe Zweig.

        Der Alternativtext waere „Keine Daten im Zeitraum" — und der publiziert
        genau die Zahl, die R14 verbirgt (Gesamtkontakte = 0). Von zwei
        unscharfen Aussagen ist die k-anonyme die richtige, und die Alterstabelle
        verhaelt sich seit #1768
        in derselben Lage identisch.
        """
        from django.template.loader import render_to_string

        from core.services.dashboard import suppress_report_stats

        leer = suppress_report_stats(
            facility,
            {
                "total_contacts": 0,
                "unique_clients": 0,
                "by_contact_stage": {},
                "persons_by_contact_stage": {},
                "by_document_type": [],
                "by_age_cluster": [],
            },
        )
        seite = render_to_string("core/statistics/external_report.html", {"report": leer})
        abschnitt = seite.split("report-document-types")[-1].split("report-age")[0]

        assert "Angaben nach Dokumentationstyp unterdrückt (k-Anonymität)." in abschnitt

    def test_gefuellte_tabelle_zeigt_weiterhin_ihre_zeilen(self, facility):
        """Gegenprobe: solange die Tabelle da ist, aendert sich nichts — weder
        der Marker je Zeile noch die sichtbaren Zahlen."""
        from django.template.loader import render_to_string

        from core.services.dashboard import suppress_report_stats

        out = suppress_report_stats(
            facility,
            {
                "total_contacts": 25,
                "unique_clients": 20,
                "by_contact_stage": {},
                "persons_by_contact_stage": {},
                "by_document_type": [
                    {"name": "Notiz", "category": "note", "count": 20},
                    {"name": "Kontakt", "category": "contact", "count": 4},
                    {"name": "Beratung", "category": "service", "count": 1},
                ],
                "by_age_cluster": [],
            },
        )
        seite = render_to_string("core/statistics/external_report.html", {"report": out})
        abschnitt = seite.split("report-document-types")[-1].split("report-age")[0]

        assert "Angaben nach Dokumentationstyp unterdrückt (k-Anonymität)." not in abschnitt
        assert "Notiz" in abschnitt and "unterdrückt" in abschnitt


# --- K3-A1: Enumeration ueber die Restzeilen-Welten ------------------------
#
# Die Alterstabelle traegt seit K3-A1 die Zeile „ohne Personenbezug"
# (W = ``total_contacts`` − Σ Altersgruppen). Die Analyse warnt ausdruecklich,
# dass eine **naive** Restzeile — emittiert, aber ohne die Randsumme, gegen die
# sie sich summiert — einen NEUEN exakten Kanal oeffnet. Deshalb wird dieser
# Weltraum eigens aufgezaehlt, mit zwei Positiv-Kontrollen:
#
# * der Stand **vor** K3-A1 (keine Restzeile): W ist aus den sichtbaren Zellen
#   exakt ausrechenbar, ohne dass je eine Suppressionsregel angesprochen haette
#   — das ist der Befund K3 selbst;
# * die **naive** Restzeile (Zeile da, ``margin=None``): das
#   Machbarkeits-Audit laeuft nicht, W bleibt exakt.
#
# Gewertet wird ausschliesslich W — die Groesse, die K3 benennt.

_AGE_WORLD_CLUSTERS = ("u18", "27_plus")
_NO_PERSON = "__no_person__"

# Neutralisierte Personenzahlen (analog ``_publish_document_types``): gross und
# uniform, damit die Aufzaehlung genau EINEN Kanal misst — die Ereignis-Randsumme
# der Alterstabelle — und nicht zusaetzlich das Personenkriterium (K0-b) oder die
# Personen-Randsumme.
_NEUTRAL_PERSONS = 50


def _age_worlds(limit: int = ENUMERATION_LIMIT) -> list[dict[str, int]]:
    """Zwei nichtleere Altersgruppen (>= 1) plus W >= 0, Σ <= ``limit``.

    W darf 0 sein — die Restzeile wird datenunabhaengig emittiert und ist damit
    genau **keine** GROUP-BY-Zeile.
    """
    worlds = []
    for first in range(1, limit + 1):
        for second in range(1, limit + 1):
            for rest in range(0, limit + 1):
                if first + second + rest <= limit:
                    worlds.append({"u18": first, "27_plus": second, _NO_PERSON: rest})
    return worlds


def _age_rows(world: dict[str, int]) -> list[dict[str, Any]]:
    """Die Altersgruppen-Zeilen in Query-Ordnung (absteigend nach Anzahl)."""
    return sorted(
        (
            {"cluster": cluster, "label": cluster, "count": world[cluster], "persons": _NEUTRAL_PERSONS}
            for cluster in _AGE_WORLD_CLUSTERS
        ),
        key=lambda row: -row["count"],
    )


def _publish_age_table(facility, world: dict[str, int]) -> dict[str, Any]:
    """Die publizierte Sicht des Halbjahres-Sachberichts auf die Alterstabelle."""
    return suppress_report_stats(
        facility,
        {
            "total_contacts": sum(world.values()),
            "unique_clients": _NEUTRAL_PERSONS,
            "by_contact_stage": {},
            "persons_by_contact_stage": {},
            "by_document_type": [],
            "by_age_cluster": _age_rows(world),
        },
    )


def _publish_age_table_without_margin(facility, world: dict[str, int], *, with_rest_row: bool) -> dict[str, Any]:
    """Positiv-Kontrollen: Alterstabelle **ohne** deklarierte Randsumme.

    ``with_rest_row=False`` ist der Stand vor K3-A1 (W existiert nur als
    Differenz), ``True`` die von der Analyse verworfene **naive** Fassung (Zeile
    da, aber kein Audit, weil keine Gleichung deklariert ist). Beide nutzen den
    echten Suppressionskern, damit die Kontrolle die fehlende Randsumme misst
    und nicht einen Nachbau.
    """
    from core.services.dashboard.external_report import (
        ORDER_AGE_CLUSTER,
        _get_threshold,
        _suppress_small,
        with_no_person_row,
    )

    threshold = _get_threshold(facility)
    total = sum(world.values())
    rows = _age_rows(world)
    if with_rest_row:
        rows = with_no_person_row(rows, total)
    return {
        "total_contacts": total if total >= threshold else None,
        "by_age_cluster": _suppress_small(rows, threshold, margin=None, order=ORDER_AGE_CLUSTER),
    }


def _age_observation(published: dict[str, Any]) -> tuple:
    return (
        published["total_contacts"],
        tuple((row["cluster"], row["count"], row["suppressed"]) for row in published.get("by_age_cluster", ()) or ()),
    )


def _age_preimage_groups(publish, limit: int = ENUMERATION_LIMIT) -> dict[tuple, list[dict[str, int]]]:
    groups: dict[tuple, list[dict[str, int]]] = defaultdict(list)
    for world in _age_worlds(limit):
        groups[_age_observation(publish(world))].append(world)
    return groups


def _pinned_rest_values(
    groups: dict[tuple, list[dict[str, int]]], threshold: int, limit: int = ENUMERATION_LIMIT
) -> list[str]:
    """Beobachtungen, in denen W ueber das Preimage eindeutig **und** klein ist.

    Dieselbe Auflage wie in :func:`_pinned_small_cells`: nur Beobachtungen mit
    publizierter Randsumme <= Enumerationsgrenze sind auswertbar. ``W = 0`` ist
    kein Befund (ein leerer Bucket schuetzt niemanden — dieselbe Ausnahme wie in
    ``disclosure.findings``), ``W >= k`` ebenfalls nicht.
    """
    leaks = []
    for observation, worlds in groups.items():
        margin = observation[0]
        if margin is None or margin > limit:
            continue
        values = {world[_NO_PERSON] for world in worlds}
        if len(values) >= 2:
            continue
        (value,) = values
        if value == 0 or value >= threshold:
            continue
        leaks.append(f"{observation}: W = {value}")
    return leaks


@pytest.mark.django_db
class TestKleinstWeltEnumerationRestzeile:
    """K3-A1: „W ist ueber das Preimage nicht eindeutig bestimmt."

    Die Analyse hat den Kanal an 8 820 Welten mit 0 < W < k belegt — ohne dass
    dabei je eine Suppressionsregel angesprochen haette. Der Test rechnet
    dasselbe an einem exakt aufzaehlbaren Kleinst-Weltraum nach und beweist
    seinen eigenen Trefferfall zweimal.
    """

    def test_w_ist_nicht_eindeutig_bestimmt(self, facility):
        groups = _age_preimage_groups(lambda world: _publish_age_table(facility, world))

        leaks = _pinned_rest_values(groups, DEFAULT_K_THRESHOLD)

        assert not leaks, (
            "Die Zahl der Ereignisse ohne Personenbezug ist ueber die publizierte "
            "Sicht exakt rueckrechenbar:\n" + "\n".join(leaks[:10])
        )

    def test_auch_die_altersgruppen_selbst_bleiben_ungepinnt(self, facility):
        """Gegenprobe auf derselben Aufzaehlung: die neue Randsummen-Gleichung
        darf keine Altersgruppen-Kleinstzelle pinnbar machen."""
        groups = _age_preimage_groups(lambda world: _publish_age_table(facility, world))

        # ``_pinned_small_cells`` liest Position 1 der Beobachtung als
        # Zeilenliste — die Alterstabelle hat exakt dieselbe Form.
        leaks = _pinned_small_cells(groups, DEFAULT_K_THRESHOLD)

        assert not leaks, "\n".join(leaks[:10])

    def test_die_aufzaehlung_deckt_ueberhaupt_unterdrueckte_restzeilen_ab(self, facility):
        """Anker gegen einen still leeren Test."""
        groups = _age_preimage_groups(lambda world: _publish_age_table(facility, world))

        auswertbar = [
            observation
            for observation in groups
            if observation[0] is not None and any(row[0] == _NO_PERSON and row[2] for row in observation[1])
        ]

        assert len(auswertbar) >= 20, f"nur {len(auswertbar)} auswertbare Beobachtungen mit unterdrueckter Restzeile"

    def test_positivkontrolle_ohne_restzeile_reisst(self, facility):
        """Der Stand VOR K3-A1: W steht nirgends, ist aber exakt bestimmbar.

        Genau das ist der Befund K3 — „kleine Zahlen aus grossen", ohne dass
        eine Suppressionsregel je angesprochen haette.
        """
        groups = _age_preimage_groups(
            lambda world: _publish_age_table_without_margin(facility, world, with_rest_row=False)
        )

        leaks = _pinned_rest_values(groups, DEFAULT_K_THRESHOLD)

        assert leaks, "Die Aufzaehlung findet den bekannten K3-Kanal nicht mehr — Harness pruefen"

    def test_positivkontrolle_naive_restzeile_reisst(self, facility):
        """Die von der Analyse verworfene naive Fassung: Zeile emittiert, aber
        keine Randsumme deklariert ⇒ das Machbarkeits-Audit laeuft nicht und W
        bleibt exakt zurueckrechenbar.

        Damit ist belegt, dass **die Zeile allein** nichts loest — tragend ist
        die Kombination aus datenunabhaengiger Emission, deklarierter Randsumme
        und K1/K2-Durchlauf.
        """
        groups = _age_preimage_groups(
            lambda world: _publish_age_table_without_margin(facility, world, with_rest_row=True)
        )

        leaks = _pinned_rest_values(groups, DEFAULT_K_THRESHOLD)

        assert leaks, "Ohne deklarierte Randsumme muesste W exakt rueckrechenbar bleiben"
