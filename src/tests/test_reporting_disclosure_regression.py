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


def _preimage_groups(publish) -> dict[tuple, list[dict[str, int]]]:
    groups: dict[tuple, list[dict[str, int]]] = defaultdict(list)
    for world in _worlds():
        groups[_observation(publish(world))].append(world)
    return groups


def _pinned_small_cells(groups: dict[tuple, list[dict[str, int]]], threshold: int) -> list[str]:
    """Unterdrueckte Kleinstfallzahlen, die ueber ihr Preimage eindeutig sind."""
    leaks = []
    for observation, worlds in groups.items():
        margin = observation[0]
        # AUFLAGE (security-notes § Regressionsschutz): nur Beobachtungen
        # werten, deren publizierte Randsumme <= Enumerationsgrenze ist. Ist die
        # Randsumme unterdrueckt, kennt der Empfaenger sie nicht — dann sind auch
        # Welten jenseits der Grenze konsistent, und ein hier „eindeutiges"
        # Preimage waere ein Artefakt der Aufzaehlung, kein Leak.
        if margin is None or margin > ENUMERATION_LIMIT:
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
            # unterdrueckt, um die Kleinstzelle zu schuetzen).
            if value >= threshold:
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
    * **K1** (bis jetzt): ohne Fixpunkt blieb nach einer Sekundaer-Suppression
      eine neue Rueckrechenbarkeit stehen, weil niemand ein zweites Mal
      nachgerechnet hat.
    """

    def test_keine_unterdrueckte_kleinstzelle_ist_eindeutig(self, facility):
        groups = _preimage_groups(lambda world: _publish_document_types(facility, world))

        leaks = _pinned_small_cells(groups, DEFAULT_K_THRESHOLD)

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
            assert sorted(unterdrueckt) == ["18_26", "u18"], unterdrueckt
