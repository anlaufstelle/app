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

import copy

from core.demo import DEMO_PASSWORD  # noqa: F401

from . import base as _base
from .devlive import *  # noqa: F401, F403

# Refs #2602: never deliver demo mail, even when EMAIL_HOST is configured.
# Replace the inherited mailer entirely so no SMTP options survive.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}
SILENCED_SYSTEM_CHECKS = ["mail.E001"]

# Schalter fuer Demo-Banner, Login-Zugangsdaten-Panel und Demo-Guard.
DEMO_MODE = True

# --- Umami Web-Analytics inkl. Recorder/Heatmap (Refs #1604, #1701) ---
# Website-ID (eigene der Demo, nicht die devlive-ID) und Skript-URLs kommen per
# Vererbung aus devlive und dort aus der Deploy-Env (.env.demo auf dem Host) —
# keine instanzspezifischen Werte im Release-Quelltext (Refs #1701).
# Consent-Opt-in bleibt hart verdrahtet: kein stilles Tracking — der Tracker
# wird erst nach expliziter Zustimmung client-seitig injiziert
# (umami-consent.js / partials/_umami_consent.html).
UMAMI_REQUIRE_CONSENT = True

# Rechtstexte der Demo: eigene Seiten der Demo-Instanz (Refs #1641). Die Demo
# verarbeitet mehr als die Marketing-Webseite (Umami-Opt-in inkl. Recording,
# geteilte Demo-Logins, stuendlicher Reset), daher eigene, darauf zugeschnittene
# Impressum-/Datenschutz-Seiten statt Links auf die Webseite (deren .html-URLs
# lieferten ausserdem 404). Die Views (DemoImprintView/DemoPrivacyView) sind nur
# bei DEMO_MODE erreichbar und oeffentlich ohne Login.
DEMO_LEGAL_IMPRINT_URL = "/impressum/"
DEMO_LEGAL_PRIVACY_URL = "/datenschutzerklaerung/"

# CSP zurueck auf STRIKT (frische Deep-Copy aus base): devlive gibt den
# Umami-Host GLOBAL frei — auf der oeffentlichen Demo mit Art-9-Offline-Daten
# (window.offlineStore, ADR-022 § (c)) darf die Lockerung NICHT global gelten.
# Sie ist statt dessen eng gescoped (DEMO_MODE + Post-Login) in
# DemoUmamiCSPRelaxMiddleware; die Login-/Anon-Seite bleibt strikt.
CONTENT_SECURITY_POLICY = copy.deepcopy(_base.CONTENT_SECURITY_POLICY)

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
DEMO_LOGINS = [
    {"username": "admin", "role": "facility_admin"},
    {"username": "emma", "role": "lead"},
    {"username": "miriam", "role": "staff"},
    {"username": "markus", "role": "staff"},
    {"username": "lena", "role": "assistant"},
    {"username": "felix", "role": "assistant"},
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
