"""
Live-Dev settings for dev.anlaufstelle.app (Refs #671).

Erbt von ``prod.py`` und uebernimmt alle Security-Defaults
(HSTS, Secure-Cookies, ENCRYPTION_KEY-Pflicht, SUDO_MODE_ENABLED-Guard,
ALLOWED_HOSTS-Pflicht, fail-closed checks).

Einziger Unterschied zu prod: ``EMAIL_BACKEND`` faellt auf den
Console-Backend zurueck. Auf dev.anlaufstelle.app brauchen wir keinen
echten SMTP-Server — ausgehende Mails landen im stdout des web-Containers
und sind ueber ``docker compose logs web`` einsehbar.

Nicht zu verwechseln mit ``settings/dev.py`` (lokale Entwicklung,
DEBUG=True, console-mail, dev-DB).

Verwendung:
    DJANGO_SETTINGS_MODULE=anlaufstelle.settings.devlive
"""

import copy
import os
from urllib.parse import urlsplit

from .prod import *  # noqa: F401, F403

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# dev.anlaufstelle.app faehrt Welt-A-Backups (dump-*.pgc.enc); der
# Health-Glob kennt nur Welt-B-Artefakte (*.sql.gz.enc) und wuerde deshalb
# trotz vorhandener Backups dauerhaft falsch alarmieren (Refs #2021).
HEALTH_BACKUP_REQUIRED = False

# --- Umami Web-Analytics (Refs #1602, #1701) ---
# dev.anlaufstelle.app trackt die Post-Login-Seiten (base.html bindet
# partials/_umami.html ein). Die Login-Seite (login.html erbt NICHT von
# base.html) bleibt bewusst tracking-frei.
#
# Website-ID und Skript-URLs sind INSTANZ-Konfiguration und kommen aus der
# Deploy-Env (.env auf dem Host) — der Release-Quelltext traegt keine
# instanzspezifischen Hosts/IDs (Refs #1701). Ungesetzt/leer = kein Tracking,
# die CSP bleibt dann identisch zur strikten base-CSP.
UMAMI_WEBSITE_ID = os.environ.get("UMAMI_WEBSITE_ID") or None
UMAMI_SCRIPT_SRC = os.environ.get("UMAMI_SCRIPT_SRC", "")
# Session-Recorder/Heatmap — nur von der Demo (Consent-Banner) genutzt, hier
# mitgelesen, damit demo.py die Werte per Vererbung erhaelt.
UMAMI_RECORDER_SRC = os.environ.get("UMAMI_RECORDER_SRC", "")

if not (UMAMI_WEBSITE_ID and UMAMI_SCRIPT_SRC):
    # Halbkonfiguration (nur ID oder nur URL) zaehlt als AUS — sonst wuerde
    # base.html ein ``<script src="">`` einbinden bzw. ein Skript ohne
    # Website-ID laden.
    UMAMI_WEBSITE_ID = None
    UMAMI_SCRIPT_SRC = ""

if UMAMI_SCRIPT_SRC:
    # CSP um den Umami-Host (Origin des konfigurierten Tracker-Skripts)
    # erweitern: script-src (Tracker-Skript) UND connect-src (Umami POSTet die
    # Zaehl-/Event-Daten an denselben Host — ohne connect-src blockt die CSP
    # den Beacon und es wird NIE gezaehlt; Lehre aus webseite#9).
    #
    # DEEP-COPY statt In-Place-Mutation: ``CONTENT_SECURITY_POLICY`` ist ein
    # Modul-Level-Dict aus base.py, das via ``from .prod import *`` als DASSELBE
    # Objekt in diesem Namespace liegt. Eine In-Place-Aenderung wuerde das geteilte
    # base-Dict mutieren (Settings-Modul-Aliasing) und die strikte globale CSP
    # aller anderen Settings-Module (inkl. der Architektur-Guard-Pruefung gegen
    # base) aufweichen. Die Kopie bleibt lokal zu devlive.
    CONTENT_SECURITY_POLICY = copy.deepcopy(CONTENT_SECURITY_POLICY)  # noqa: F405
    _umami_split = urlsplit(UMAMI_SCRIPT_SRC)
    _UMAMI_HOST = f"{_umami_split.scheme}://{_umami_split.netloc}"
    CONTENT_SECURITY_POLICY["DIRECTIVES"]["script-src"] = [
        *CONTENT_SECURITY_POLICY["DIRECTIVES"]["script-src"],
        _UMAMI_HOST,
    ]
    CONTENT_SECURITY_POLICY["DIRECTIVES"]["connect-src"] = [
        *CONTENT_SECURITY_POLICY["DIRECTIVES"]["connect-src"],
        _UMAMI_HOST,
    ]

# /health/ vom HTTPS-Redirect ausnehmen, damit der Container-internal
# Healthcheck (`urlopen('http://localhost:8000/health/')`) nicht in einen
# 302→HTTPS-Loop laeuft. Caddy macht TLS-Termination, aber Django sieht
# beim Healthcheck-Request keinen X-Forwarded-Proto → wuerde sonst
# SSL_REDIRECT triggern und der Healthcheck-Client kann lokal kein TLS.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]

# dev.anlaufstelle.app laeuft mit Demo-Daten ('make dev-seed') — Seed trotz
# prod-Vererbung explizit freigeschaltet. Refs #1040 (S1).
SEED_ALLOWED = True
