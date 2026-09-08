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
