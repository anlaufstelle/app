"""
Production settings for Anlaufstelle.
"""

import os
import sys

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured

from ._sentry import before_send as _sentry_before_send
from .base import *  # noqa: F401, F403
from .base import LOGGING  # noqa: F401

# --- Logging-Override für Produktion ---
# base.py setzt den ``core``-Logger auf DEBUG (dev-freundlich). In Produktion
# konservativer: INFO, damit keine Verbose-Detail-Statements mit potenzieller
# PII ins stdout/Sentry landen. PII-Scrubber im ``JsonFormatter`` greift
# zusätzlich als Defense-in-Depth (core/logging.py).
LOGGING["loggers"]["core"]["level"] = "INFO"

# --- Sentry (optional, via SENTRY_DSN env var) ---

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
        # Refs #1275: send_default_pii=False unterdrückt NICHT die
        # Exception-Local-Variablen (z. B. entschlüsselte Notizen in einem
        # Stack-Frame) oder Request-Bodies. include_local_variables=False
        # verhindert das Erfassen der Frame-Locals direkt im SDK, before_send
        # scrubt zusätzlich Request-Body/Cookies und sensible Felder, bevor ein
        # Event übertragen wird (Defense-in-Depth).
        include_local_variables=False,
        before_send=_sentry_before_send,
    )

# --- Media (encrypted attachments) ---

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))  # noqa: F405

DEBUG = False

# Fail-closed: Prod darf nicht mit unsicheren Defaults oder leerer Konfiguration starten.
if "collectstatic" not in sys.argv and not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY muss in Produktion gesetzt sein.")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
if "collectstatic" not in sys.argv and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS muss in Produktion gesetzt sein (Kommaseparierte Liste).")
# Fail-closed (Refs #1376 C4/I4): "*" ist in Django ein Match-all-Eintrag und
# hebelt die einzige Huerde gegen Host-Header-Poisoning aus (z. B. gefaelschte
# Passwort-Reset-Links). Subdomain-Patterns wie ".example.com" sind KEIN
# Match-all und bleiben erlaubt.
if "collectstatic" not in sys.argv and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS darf in Produktion kein Wildcard '*' enthalten (Match-all, "
        "hebelt die Host-Header-Validierung aus). Subdomain-Patterns wie '.example.com' "
        "bleiben erlaubt."
    )

# --- Security ---

# Trust-Boundary (Refs #841): Caddy als Reverse-Proxy strippt eingehendes
# X-Forwarded-Proto vom Client und setzt den Header selbst auf den tatsaechlich
# beobachteten Verbindungs-Status. Wenn Anlaufstelle direkt exponiert wird
# (ohne Reverse-Proxy), MUSS dieses Setting entfernt werden — sonst koennte
# ein Angreifer ``X-Forwarded-Proto: https`` setzen und HTTPS faken (request.is_secure()
# wuerde True liefern, obwohl die Verbindung Klartext ist).
# Siehe docs/ops-runbook.md § Trust-Boundary.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
# SESSION-Cookie "Lax" (= Django-Default) explizit: Top-Level-Navigationen
# (Bookmarks, E-Mail-Links) sollen die Session mitschicken — sonst
# UX-Regression durch permanente Re-Logins. "Strict" wäre sicherer, aber
# für ein Alltags-Fachsystem zu restriktiv.
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
# CSRF-Cookie "Strict": Token wird nur bei same-origin-Form-Submits
# mitgesendet. Refs #598, Entscheidung 2026-04-21.
CSRF_COOKIE_SAMESITE = "Strict"
# CSRF-Cookie HTTPOnly: Verhindert, dass JavaScript den Token aus dem Cookie
# lesen kann — XSS-Mitigation. Der CSRF-Token wird für HTMX (via
# body[hx-headers]), PWA-Offline-Module (via window.getCsrfToken() /
# <meta name="csrf-token">) und Forms (via {% csrf_token %}) aus dem
# gerenderten HTML gelesen, nicht aus dem Cookie. Refs #602.
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
# Explizit Referrer-Policy setzen — Django-Default wäre "same-origin", aber
# Caddy liefert "strict-origin-when-cross-origin". Wir setzen hier den
# strengeren Wert auch auf Django-Ebene, damit der Header auch bei direktem
# Zugriff ohne Caddy (z.B. lokale Prod-Simulation) gesetzt ist. Refs #635.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# --- Defense-in-Depth-Settings explizit (A5.5, Refs #1024 / #1016) ---
# Django-6-Defaults sind hier bereits sicher; wir setzen sie in prod.py
# trotzdem explizit, damit ein Default-Wechsel im Framework oder ein
# versehentliches Entfernen im Review auffällt (Auditierbarkeit /
# Regressionsschutz, kein offenes Loch). Der Guard-Test
# ``TestProdSettingsSecurityDefaultsGuard`` friert die Werte ein.
SESSION_COOKIE_HTTPONLY = True
# COOP "same-origin": isoliert den Browsing-Context von cross-origin-Fenstern
# (Schutz vor XS-Leaks / window.opener-Zugriff).
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
# Request-Body-Limits explizit verankern (DoS-Schutz). Datei-Uploads laufen
# über den File-Vault (Cap ``DEFAULT_MAX_FILE_SIZE_MB``, per Facility
# überschreibbar) und sind von DATA_UPLOAD_MAX_MEMORY_SIZE ohnehin
# ausgenommen — der Wert begrenzt also Nicht-Datei-Formdaten. Auf dem sicheren
# Django-Default (2.5 MB) belassen; FILE_UPLOAD_MAX_MEMORY_SIZE als
# RAM→Disk-Schwelle ebenfalls konservativ, damit große Vault-Uploads auf
# Temp-Disk gestreamt statt im Speicher gehalten werden.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MB

# --- E-Mail (SMTP) ---
# Für Passwort-zurücksetzen und andere E-Mail-Funktionen.
# Konfiguration via Umgebungsvariablen:
#   EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
#   EMAIL_USE_TLS, DEFAULT_FROM_EMAIL
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@anlaufstelle.app")

# --- Database ---

CONN_MAX_AGE = 60

# --- Shared Cache (A5.1, Refs #1024 / #1016) ---
# Ohne expliziten Cache nutzt Django LocMemCache — pro Gunicorn-Worker isoliert.
# django-ratelimit (Login/Sudo/Health) und das Health-Detail-Caching (A7.2)
# zählen dann pro Worker getrennt, sodass Rate-Limits in Multi-Worker-Prod
# faktisch ge-N-facht werden. DatabaseCache teilt den Zustand prozessübergreifend
# ohne neue Dependency (Redis-Vollausbau bleibt #795). Tabelle wird per Migration
# 0092 angelegt (createcachetable-äquivalent).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "anlaufstelle_cache",
    }
}
# django-ratelimit gegen den shared Cache laufen lassen (statt LocMem-Default).
RATELIMIT_USE_CACHE = "default"

# --- Encryption Key (mandatory in production) ---
# Akzeptiert ENCRYPTION_KEYS (Plural, MultiFernet-Rotation) oder ENCRYPTION_KEY (Singular, Legacy).
# Mindestens eins muss gesetzt sein.
if "collectstatic" not in sys.argv and not (ENCRYPTION_KEY or ENCRYPTION_KEYS):  # noqa: F405
    raise ImproperlyConfigured(
        "ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein. "
        'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

# --- Audit-Hash-Key (mandatory in production) (A4.2, Refs #1024 / #1016) ---
# ``services.audit.hash`` fällt ohne AUDIT_HASH_KEY still auf SHA256(SECRET_KEY)
# zurück (funktional für Dev/Test). In Produktion wäre das eine stille
# Defense-Erosion: ein SECRET_KEY-Leak könnte rückwirkend die Audit-Hashes
# (z.B. pseudonymisierte E-Mails in Passwort-Reset-Audits) knacken. Daher
# fail-closed — lieber Server-Start-Fehler als unbemerkt schwächere Audit-PII.
if "collectstatic" not in sys.argv and not AUDIT_HASH_KEY:  # noqa: F405
    raise ImproperlyConfigured(
        "DJANGO_AUDIT_HASH_KEY muss in Produktion gesetzt sein (separat von "
        'DJANGO_SECRET_KEY). Generate one with: python -c "import secrets; '
        'print(secrets.token_urlsafe(64))"'
    )

# --- Platzhalter-/Entropie-Guard (Security N6) ---
# .env.example lieferte frueher nicht-leere ``change-me``-Platzhalter; die
# Nur-leer-Guards oben liessen die durch. Ein oeffentlich bekannter
# SECRET_KEY macht Reset-/Invite-Token faelschbar, ein bekannter
# AUDIT_HASH_KEY die Audit-Pseudonyme brute-forcebar. Fail-closed.
_SECRET_MIN_LENGTH = 32
if "collectstatic" not in sys.argv:
    for _name, _value in (("DJANGO_SECRET_KEY", SECRET_KEY), ("DJANGO_AUDIT_HASH_KEY", AUDIT_HASH_KEY)):  # noqa: F405
        if _value.startswith("change-me"):
            raise ImproperlyConfigured(
                f"{_name} ist noch der Platzhalter aus .env.example — bitte echten "
                'Zufallswert setzen: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if _value and len(_value) < _SECRET_MIN_LENGTH:
            raise ImproperlyConfigured(
                f"{_name} ist kuerzer als {_SECRET_MIN_LENGTH} Zeichen — zu wenig Entropie fuer Produktion."
            )

# --- Virenscan (ClamAV ist in Produktion Pflicht, Refs #1267) ---
# base.py defaultet CLAMAV_ENABLED=false (Dev/Test-Bypass: ``virus_scan.scan_file``
# liefert dann ohne ClamAV-Kontakt „clean"). In Produktion ist der Scanner Pflicht
# und der Default daher „true". Ein explizites CLAMAV_ENABLED=false würde Uploads
# ungescannt durchwinken — daher fail-closed: lieber Server-Start-Fehler als
# stiller Scanner-Bypass (analog zu SUDO_MODE_ENABLED / AUDIT_HASH_KEY).
CLAMAV_ENABLED = os.environ.get("CLAMAV_ENABLED", "true").lower() in ("true", "1", "yes")
if not CLAMAV_ENABLED:
    raise ImproperlyConfigured(
        "CLAMAV_ENABLED muss in Produktion True sein. Der Virenscan darf nicht "
        "deaktiviert werden — sonst akzeptiert die Anwendung ungescannte Uploads."
    )

# --- Sudo-Mode (Refs #775) ---
# settings/test.py setzt SUDO_MODE_ENABLED=False, damit Tests nicht jedes
# Re-Auth-Form passieren muessen. Wenn dieses Test-Setting versehentlich nach
# Produktion uebernommen wird, kippt es MFA-Disable, DSGVO-Export und
# Pseudonym-Daten-Download in einem Schritt. Hier explizit fail-fast: lieber
# Server-Start-Fehler als stille Defense-Erosion.
if os.environ.get("SUDO_MODE_ENABLED", "true").lower() not in ("true", "1", "yes"):
    raise ImproperlyConfigured(
        "SUDO_MODE_ENABLED muss in Produktion True sein. Test-Setting versehentlich uebernommen?"
    )
