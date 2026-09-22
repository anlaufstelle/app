"""Gemeinsamer Katalog fachlicher Demo-Szenarien (Refs #2279).

Diese Datei ist die EINZIGE Quelle des Szenario-Katalogs. Sie wird von drei
Stellen konsumiert:

* ``core.management.commands.seed`` (Zweig ``--scale demo``): legt je
  Szenario eine eigene Facility mit dem hinterlegten Fachpaket an.
* ``dev-ops/deploy/demo-reseed.sh`` (stündlicher Reset auf
  ``demo.anlaufstelle.app``): ruft denselben ``--scale demo``-Zweig auf, es
  gibt keinen separaten Reset-Katalog.
* Die Besucherauswahl aus #2285 (``core/views/demo_scenarios.py``): iteriert
  ``SCENARIOS`` für die Anzeige (Name/Beschreibung/Einstiegshinweis) und
  matcht die gewählte Facility über ``DemoScenario.facility_name`` gegen die
  vom Seed angelegte ``Facility`` — löst KEINEN Reseed aus, liest nur.

Bewusst reine Metadaten ohne Datenvolumen (Refs #2279, Punkt 3 „Datenvolumen
von der Szenarioauswahl lösen"): wie VIEL Demo-Daten je Facility entstehen,
bestimmt ausschließlich ``core.seed.scale.SCALE_CONFIG["demo"]`` bzw.
``["demo_solo"]`` — nie diese Datei.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import User


@dataclass(frozen=True)
class DemoScenario:
    """Ein fachlicher Demo-Bereich mit eigener Beispieleinrichtung."""

    key: str
    """Stabiler Schlüssel (URL-/Test-tauglich), z. B. ``"solo"``."""

    display_name: str
    """Sichtbarer Name auf der Auswahlseite (#2285)."""

    description: str
    """Kurzbeschreibung des fachlichen Alltags (ein bis zwei Sätze)."""

    package: str
    """Fachpaket-Schlüssel aus ``core.packages.catalog.list_packages()``."""

    facility_name: str
    """Name der vom Seed angelegten Beispieleinrichtung."""

    demo_roles: tuple[str, ...]
    """Erlaubte Demo-Rollen (``User.Role``-Werte) für den Login unter
    diesem Szenario — bewusst NUR die Rollen, für die dieses Szenario auch
    tatsächlich Konten anlegt (Solo z. B. keine ``lead``/``assistant``)."""

    intro_hint: str
    """Einstiegshinweis für die Auswahlseite: was zeigt die Demo hier?"""

    solo: bool = False
    """``True``: reduzierter Alltag einer allein arbeitenden Fachkraft
    (``core.seed.users.seed_users_solo`` statt ``seed_users``, eigener
    Datenvolumen-Eintrag ``SCALE_CONFIG["demo_solo"]``)."""


_TEAM_ROLES: tuple[str, ...] = (
    User.Role.FACILITY_ADMIN,
    User.Role.LEAD,
    User.Role.STAFF,
    User.Role.ASSISTANT,
)

_SOLO_ROLES: tuple[str, ...] = (
    User.Role.STAFF,
    User.Role.FACILITY_ADMIN,
)

SCENARIOS: tuple[DemoScenario, ...] = (
    DemoScenario(
        key="kontaktladen",
        display_name="Kontaktladen Wärmestube",
        description=(
            "Tagesoffene, niedrigschwellige Anlaufstelle mit Komm-Struktur: "
            "viele kurze Kontakte, Versorgung vor Ort, wenig Terminbindung."
        ),
        package="default",
        facility_name="Kontaktladen Wärmestube",
        demo_roles=_TEAM_ROLES,
        intro_hint=(
            "Zeigt den Standardkatalog im Alltag eines offenen Kontaktladens: "
            "Kurzkontakte, Sachleistungen, gelegentliche Krisen- und "
            "Vermittlungsgespräche."
        ),
    ),
    DemoScenario(
        key="beratungsstelle",
        display_name="Beratungsstelle Falkenweg",
        description=("Offene Sprechstunde und Sozialberatung mit Terminvergabe, Fallverläufen und Wirkungszielen."),
        package="beratungsstelle",
        facility_name="Beratungsstelle Falkenweg",
        demo_roles=_TEAM_ROLES,
        intro_hint=(
            "Zeigt ein Team in der Sozialberatung: Beratungsgespräche, "
            "Fallakten, Episoden und Auswertung über einen längeren Zeitraum."
        ),
    ),
    DemoScenario(
        key="solo",
        display_name="Beratungspraxis Lindenhof",
        description=(
            "Allein arbeitende Fachkraft mit eigener Klientel — Kontakte, "
            "Aufgaben/Wiedervorlagen, Verläufe und Auswertung ohne Team-"
            "Schichtbetrieb. Eine Vertretung sichert Freigaben (z. B. "
            "Löschbestätigungen) ab, ohne im Tagesgeschäft mitzuarbeiten."
        ),
        package="beratungsstelle",
        facility_name="Beratungspraxis Lindenhof",
        demo_roles=_SOLO_ROLES,
        intro_hint=(
            "Zeigt den Solo-Betrieb: eine Fachkraft führt Beratung, "
            "Dokumentation und Auswertung allein; eine Vertretung mit "
            "Freigabebefugnis bleibt sichtbar, aber im Hintergrund."
        ),
        solo=True,
    ),
    DemoScenario(
        key="awareness",
        display_name="Awareness-Team Nachtfalter",
        description=("Awareness-Team bei Veranstaltungen: mobile, anlassbezogene Einsätze."),
        package="awareness",
        facility_name="Awareness-Team Nachtfalter",
        demo_roles=_TEAM_ROLES,
        intro_hint=("Zeigt Awareness-Arbeit bei Veranstaltungen: kurze, oft anonyme Kontakte während Einsätzen."),
    ),
    DemoScenario(
        key="jugendarbeit",
        display_name="Jugendtreff Wilhelmshöhe",
        description=("Mobile und offene Jugendarbeit mit wiederkehrenden Kontakten und Gruppenangeboten."),
        package="jugendarbeit",
        facility_name="Jugendtreff Wilhelmshöhe",
        demo_roles=_TEAM_ROLES,
        intro_hint=("Zeigt offene Jugendarbeit: wiederkehrende Besuche, Gruppenangebote, altersgerechte Themen."),
    ),
    DemoScenario(
        key="frauenhaus",
        display_name="Frauenhaus Amalie",
        description=(
            "Frauenhaus / Gewalt-Fachberatung mit hohem Schutzbedarf — "
            "Klarname der Einrichtung bewusst symbolisch, wie in der "
            "Praxis aus Diskretionsgründen üblich."
        ),
        package="frauenhaus",
        facility_name="Frauenhaus Amalie",
        demo_roles=_TEAM_ROLES,
        intro_hint=("Zeigt Fachberatung mit hohem Schutzbedarf: enge Zugriffsgrenzen, sensible Dokumentation."),
    ),
)

_BY_KEY: dict[str, DemoScenario] = {scenario.key: scenario for scenario in SCENARIOS}


def get_scenario(key: str) -> DemoScenario:
    """Ein Szenario per Schlüssel nachschlagen.

    Wirft ``KeyError`` bei unbekanntem Schlüssel — bewusst kein stiller
    Fallback (analog ``UnknownPackageError``-Fail-Loud-Stil im Paket-Katalog).
    """
    return _BY_KEY[key]


def scenario_keys() -> tuple[str, ...]:
    """Alle Katalog-Schlüssel in fester Katalogreihenfolge."""
    return tuple(scenario.key for scenario in SCENARIOS)
