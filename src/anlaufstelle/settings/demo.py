"""
Demo-Settings fuer demo.anlaufstelle.app (Refs #1062).

Erbt von ``devlive`` (prod-Security-Defaults + Console-Mail + SEED_ALLOWED
+ ``/health/``-Redirect-Exempt) und ergaenzt nur das Demo-Spezifische.

Oeffentliche Demo-Instanz mit den dokumentierten Seed-Logins (bekanntes
Passwort) — bewusst akzeptiert, abgesichert durch stuendlichen Seed-Reset
und Console-Mail (kein echter Versand). Banner / Login-Zugangsdaten-Panel /
Demo-Guard folgen in eigener Iteration und werten ``DEMO_MODE`` aus.

Verwendung:
    DJANGO_SETTINGS_MODULE=anlaufstelle.settings.demo
"""

from .devlive import *  # noqa: F401, F403

# Schalter fuer Demo-Banner, Login-Zugangsdaten-Panel und Demo-Guard.
DEMO_MODE = True

# devlive erbt von prod -> MFA_ENFORCE_PRIVILEGED_ROLES=True. Auf der
# oeffentlichen Demo muessen superadmin/facility_admin OHNE TOTP-Einrichtung
# sofort nutzbar sein, sonst sind die admin-Logins der Demo unbrauchbar
# (vgl. #1218). Bewusste Abschwaechung — nur auf der Wegwerf-Demo mit
# synthetischen Daten.
MFA_ENFORCE_PRIVILEGED_ROLES = False

# Oeffentlich kommunizierte Seed-Logins fuer das Login-Zugangsdaten-Panel
# (Refs #1062). Passwort fuer alle Konten gleich. scale=medium legt eine 2.
# Einrichtung mit Suffix _1 an (admin_1, emma_1, ...).
#
# L11 (Refs #1375): Das ``superadmin``-Konto wird bewusst NICHT mehr im
# oeffentlichen Panel beworben. Ein systemweiter Super-Admin mit publiziertem
# Passwort + deaktivierter MFA-Pflicht (``MFA_ENFORCE_PRIVILEGED_ROLES=False``)
# gaebe jedem Demo-Besucher installationsweite Rechte (Recon-/Missbrauchs-
# flaeche, die der stuendliche Reset nicht mindert). Das Konto darf per Seed
# weiter existieren — es wird nur nicht mehr oeffentlich mit Zugangsdaten
# angezeigt.
DEMO_PASSWORD = "anlaufstelle2026"  # noqa: S105 — oeffentliches Demo-Passwort, bewusst
DEMO_LOGINS = [
    {"username": "admin", "role": "Einrichtungs-Admin"},
    {"username": "emma", "role": "Leitung"},
    {"username": "miriam", "role": "Fachkraft"},
    {"username": "markus", "role": "Fachkraft"},
    {"username": "lena", "role": "Assistenz"},
    {"username": "felix", "role": "Assistenz"},
]

# Demo-Guard: POST auf diese Pfade wird bei DEMO_MODE gesperrt (Refs #1062).
# Nur Aktionen, die die Demo fuer ALLE lahmlegen (Wartungsmodus — vom Reset
# NICHT heilbar, da Flag-Datei) oder ein geteiltes Konto bis zum stuendlichen
# Reset aussperren (2FA aktivieren, Passwort aendern, User deaktivieren/
# Rolle/Loeschen). Nur POST — GET bleibt erlaubt, Besucher koennen die UI also
# ansehen, nur nicht ausfuehren.
DEMO_GUARD_BLOCKED_PREFIXES = (
    "/system/maintenance/",
    "/mfa/setup/",
    "/password-change/",
    "/admin-mgmt/core/user/",
)
