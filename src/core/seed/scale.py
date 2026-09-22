"""Scale configuration for the seed command."""

SCALE_CONFIG = {
    "small": {
        "facilities": 1,
        "users_per_facility": 6,
        "clients_per_facility": 7,
        "events_per_facility": 25,
        "cases": 3,
        "episodes": 0,
        "goals": 0,
        "work_items": 5,
        "retention_proposals": 4,
        "zeitraum_days": 80,
        "attachment_ratio": 0.5,
    },
    "medium": {
        "facilities": 2,
        "users_per_facility": 6,
        "clients_per_facility": 40,
        "events_per_facility": 750,
        "cases": 12,
        "episodes": 20,
        "goals": 15,
        "milestones_per_goal": 3,
        "work_items": 25,
        "deletion_requests": 5,
        "retention_proposals": 6,
        "zeitraum_days": 365,
        "attachment_ratio": 0.25,
    },
    "large": {
        "facilities": 5,
        "users_per_facility": 6,
        "clients_per_facility": 500,
        "events_per_facility": 10000,
        "cases": 50,
        "episodes": 80,
        "goals": 60,
        "milestones_per_goal": 4,
        "work_items": 100,
        "deletion_requests": 15,
        "retention_proposals": 12,
        "zeitraum_days": 3 * 365,
        "attachment_ratio": 0.1,
    },
    "solo": {
        "facilities": 1,
        "users_per_facility": 6,
        "clients_per_facility": 30,
        "events_per_facility": 500,
        "cases": 10,
        "episodes": 15,
        "goals": 10,
        "milestones_per_goal": 3,
        "work_items": 20,
        "deletion_requests": 4,
        "retention_proposals": 6,
        "zeitraum_days": 1000,
        "attachment_ratio": 0.25,
    },
    # Refs #2279: Datenvolumen fuer den Szenario-Katalog (``--scale demo``,
    # ``core.seed.scenarios.SCENARIOS``) — bewusst UNABHAENGIG von den
    # obigen Profilen. "facilities" ist hier nur dokumentarisch (Anzahl
    # Katalog-Eintraege); der Facility-Zweig fuer ``scale == "demo"`` in
    # ``core.management.commands.seed`` iteriert ueber ``SCENARIOS``, nicht
    # ueber ``range(cfg["facilities"])``. Ueberschaubare Menge je Facility,
    # damit ALLE sechs Bereiche gleichzeitig bereitstehen, ohne ``large``
    # vorauszusetzen (Ticket-Punkt 3).
    "demo": {
        "facilities": 6,
        "users_per_facility": 6,
        "clients_per_facility": 18,
        "events_per_facility": 120,
        "cases": 6,
        "episodes": 8,
        "goals": 6,
        "milestones_per_goal": 3,
        "work_items": 12,
        "deletion_requests": 2,
        "retention_proposals": 3,
        "zeitraum_days": 270,
        "attachment_ratio": 0.3,
    },
    # Refs #2279: reduziertes Profil NUR fuer die Solo-Facility innerhalb
    # von ``--scale demo`` (``DemoScenario.solo``) — kleineres, aber
    # plausibles Ein-Personen-Pensum statt eines Team-Volumens.
    "demo_solo": {
        "facilities": 1,
        "users_per_facility": 2,
        "clients_per_facility": 9,
        "events_per_facility": 60,
        "cases": 4,
        "episodes": 4,
        "goals": 3,
        "milestones_per_goal": 2,
        "work_items": 8,
        "deletion_requests": 1,
        "retention_proposals": 2,
        "zeitraum_days": 270,
        "attachment_ratio": 0.3,
    },
}

# Facility names used when creating more than one facility.
FACILITY_NAMES = [
    "Hauptstelle",
    "Zweigstelle Nord",
    "Zweigstelle Süd",
    "Außenstelle Ost",
    "Außenstelle West",
]

# Refs #2148 (R-L5c-1, Maintainer-Entscheid 2026-09-06, Option B): ein
# mitgeliefertes Paket je Facility-Index, index-gleich zu ``FACILITY_NAMES``.
# ``medium`` (2 Einrichtungen) ist ein Praefix von ``large`` (5) — die Tabelle
# gilt fuer beide Profile unveraendert. Nur Index 0 (Hauptstelle) und Index 1
# (Zweigstelle Nord, `beratungsstelle`) tragen handgeschriebene Event-Pools
# (core/seed/constants.py); Index 2-4 fuellen ausschliesslich ueber den
# Fallback-Generator (Option C = bewusst NICHT umgesetzt, siehe #2148).
FACILITY_PACKAGES = [
    "default",  # Hauptstelle
    "beratungsstelle",  # Zweigstelle Nord
    "awareness",  # Zweigstelle Süd
    "jugendarbeit",  # Außenstelle Ost
    "frauenhaus",  # Außenstelle West
]
