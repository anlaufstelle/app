"""
Production settings for Anlaufstelle.
"""

import os
import sys
import warnings

import sentry_sdk
from django.core.exceptions import ImproperlyConfigured

from ._env import env_bool, env_bool_strict, env_csv, env_int
from ._sentry import before_breadcrumb as _sentry_before_breadcrumb
from ._sentry import before_send as _sentry_before_send
from ._sentry import before_send_transaction as _sentry_before_send_transaction
from .base import *  # noqa: F401, F403
from .base import LOGGING  # noqa: F401

# Refs #1827: 6x wiederholtes "collectstatic" not in sys.argv — collectstatic
# laeuft im Image-Build ohne die produktiven Secrets/Hosts (der Build-Schritt
# braucht nur STATIC_ROOT), die Fail-closed-Guards unten wuerden den Build
# sonst grundlos abbrechen und sind daher waehrend collectstatic ausgesetzt.
_IS_COLLECTSTATIC = "collectstatic" in sys.argv

# Reject undeclared audit keys by default in production (Refs #2529).
AUDIT_DETAIL_STRICT = env_bool_strict("DJANGO_AUDIT_DETAIL_STRICT", default=True)

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
        # Refs #1500: Performance-Transactions umgehen before_send in
        # sentry-sdk 2.x. Sie tragen denselben Request-Kontext (inkl.
        # query_string mit potenziellen Klient*innen-Namen als Suchbegriff),
        # Spans und Breadcrumbs — daher derselbe Scrub über einen eigenen Hook.
        before_send_transaction=_sentry_before_send_transaction,
        # Refs #2198: Der BreadcrumbHandler baut aus jeder Log-Zeile ab INFO
        # einen Breadcrumb mit der bereits interpolierten Message. Werte darin
        # (Einrichtungs-/Personennamen) sind musterbasiert nicht erkennbar —
        # dieser Hook reduziert Log-Breadcrumbs auf ihr Format-Template.
        before_breadcrumb=_sentry_before_breadcrumb,
    )

# --- Media (encrypted attachments) ---

MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))  # noqa: F405

DEBUG = False

# Fail-closed: Prod darf nicht mit unsicheren Defaults oder leerer Konfiguration starten.
if not _IS_COLLECTSTATIC and not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY muss in Produktion gesetzt sein.")

ALLOWED_HOSTS = env_csv("ALLOWED_HOSTS")
if not _IS_COLLECTSTATIC and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS muss in Produktion gesetzt sein (Kommaseparierte Liste).")
# Fail-closed (Refs #1376 C4/I4): "*" ist in Django ein Match-all-Eintrag und
# hebelt die einzige Huerde gegen Host-Header-Poisoning aus (z. B. gefaelschte
# Passwort-Reset-Links). Subdomain-Patterns wie ".example.com" sind KEIN
# Match-all und bleiben erlaubt.
if not _IS_COLLECTSTATIC and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS darf in Produktion kein Wildcard '*' enthalten (Match-all, "
        "hebelt die Host-Header-Validierung aus). Subdomain-Patterns wie '.example.com' "
        "bleiben erlaubt."
    )

# --- Passkeys/WebAuthn (ADR-032, Refs #1492) ---
# RP-ID = Registrable Domain (Hostname ohne Schema/Port), Origins = volle HTTPS-URLs.
# Default aus ALLOWED_HOSTS abgeleitet — fuer den Single-Domain-Regelfall ist keine
# separate Env-Var noetig. Explizit setzbar ueber OTP_WEBAUTHN_RP_ID /
# OTP_WEBAUTHN_ALLOWED_ORIGINS (z. B. wenn RP-ID eine Basis-Domain mehrerer
# Subdomains sein soll). Passkeys sind domaingebunden: eine spaetere RP-ID-Aenderung
# entwertet bestehende Credentials (Nutzer muessen neu registrieren).
# ``_concrete_hosts`` = konkrete (nicht-Wildcard) Hostnamen; ``_wildcard_bases`` =
# Basis-Domains aus Subdomain-Wildcards ('.example.com' -> 'example.com', eine
# gueltige Registrable Domain fuer alle Subdomains).
_concrete_hosts = [h for h in ALLOWED_HOSTS if not h.startswith(".")]
_wildcard_bases = [h[1:] for h in ALLOWED_HOSTS if h.startswith(".")]
# RP-ID-Fallback: bevorzugt ein konkreter Host, sonst die Basis-Domain eines
# Subdomain-Wildcards. So bleibt die RP-ID auch bei reiner Wildcard-Config nicht
# still leer (py_webauthn wuerfe sonst zur Laufzeit einen Fehler, die
# Passkey-Registrierung schluege lautlos fehl).
OTP_WEBAUTHN_RP_ID = os.environ.get("OTP_WEBAUTHN_RP_ID", "") or next(iter(_concrete_hosts + _wildcard_bases), "")
# Origins lassen sich nur aus konkreten Hosts ableiten — eine Wildcard nennt
# keinen konkreten Ursprung. Reine Wildcard-Deployments MUESSEN daher
# OTP_WEBAUTHN_ALLOWED_ORIGINS explizit setzen.
OTP_WEBAUTHN_ALLOWED_ORIGINS = env_csv("OTP_WEBAUTHN_ALLOWED_ORIGINS") or [f"https://{h}" for h in _concrete_hosts]

# --- Feste Basis-Origin fuer sicherheitsrelevante Links (Refs #1967) ---
# Einladungs-, Passwort-Reset- und Entsperr-Links duerfen nicht aus dem
# eingehenden Request entstehen (Begruendung in base.py und in
# core/services/security/origin.py). In Produktion ist EXTERNAL_ORIGIN deshalb
# Pflicht — aber nach demselben Muster ableitbar wie OTP_WEBAUTHN_RP_ID oben:
# fuehrt ALLOWED_HOSTS genau EINEN oeffentlichen Host, ist dessen HTTPS-Origin
# die einzig moegliche Antwort, und der Regelfall (eine Domain hinter Caddy)
# braucht keine zusaetzliche Env-Var. Bei mehreren oeffentlichen Hosts oder
# reiner Wildcard-Config gibt es keine eindeutige Antwort: dann fail-closed
# statt raten, denn eine falsch geratene Origin macht jeden Token-Link
# unbrauchbar.
#
# Loopback-Namen zaehlen dabei NICHT als oeffentlicher Host: sie stehen in
# ALLOWED_HOSTS ausschliesslich, weil der Container-Healthcheck (Dockerfile:
# ``http://localhost:8000/health/``) sonst einen DisallowedHost-400 bekaeme.
# Die ausgelieferten Deploy-Vorlagen (.env.demo.example,
# dev-ops/deploy/barbara/.env.barbara.example) fuehren genau diese Kombination
# aus Domain + localhost + 127.0.0.1 — zaehlten die Loopback-Eintraege mit,
# haette der Pflicht-Guard sie fuer mehrdeutig gehalten und den Start
# abgebrochen. Als Basis eines Token-Links kommen sie ohnehin nie in Frage:
# ein Link auf ``https://localhost`` waere fuer die Empfaengerin wertlos.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_public_hosts = [h for h in _concrete_hosts if h.lower() not in _LOOPBACK_HOSTS]
if not EXTERNAL_ORIGIN and len(_public_hosts) == 1:  # noqa: F405
    EXTERNAL_ORIGIN = f"https://{_public_hosts[0]}"
if not _IS_COLLECTSTATIC and not EXTERNAL_ORIGIN:  # noqa: F405
    raise ImproperlyConfigured(
        "EXTERNAL_ORIGIN muss in Produktion gesetzt sein (z. B. "
        "'https://anlaufstelle.example.de') — es ist die feste Basis der Einladungs-, "
        "Passwort-Reset- und Entsperr-Links. Automatisch abgeleitet wird sie nur, wenn "
        "ALLOWED_HOSTS genau einen oeffentlichen Hostnamen enthaelt (localhost, "
        "127.0.0.1 und ::1 zaehlen dabei nicht mit — sie stehen dort nur fuer den "
        "Container-Healthcheck)."
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
# /health/ vom HTTPS-Redirect ausnehmen: der Container-Healthcheck ruft
# http://localhost:8000/health/ ohne X-Forwarded-Proto auf. Ohne Exempt
# antwortet SecurityMiddleware mit 301 auf https://, und der Healthcheck-Client
# kann lokal kein TLS -> web wird nie healthy, caddy (condition:
# service_healthy) startet nie. Gehoert hierher und nicht nur in die Overlays:
# docker-compose.staging.yml pinnt anlaufstelle.settings.prod direkt. Refs #2077.
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
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

# --- Upload-Temp-Verzeichnis (RAM/tmpfs, Refs #1661, I7 aus #1376) ---
# Uploads oberhalb von FILE_UPLOAD_MAX_MEMORY_SIZE puffert Django als
# KLARTEXT-Tempdatei auf Platte, bevor der File-Vault sie scannt und
# verschlüsselt (core/services/file_vault/storage.py). Ohne explizites
# FILE_UPLOAD_TEMP_DIR fällt Django auf tempfile.gettempdir() zurück — in den
# mitgelieferten Compose-Dateien (docker-compose.prod/staging/demo/dev.yml)
# ist /tmp dort bereits ein tmpfs-Mount (RAM, Schreib-Scratch für den
# read_only-Root-FS, #1374), der implizite Fallback landet also ohnehin im
# RAM. Das Setting hier macht diese Annahme explizit/auditierbar statt sich
# auf Pythons TMPDIR-Ermittlungsreihenfolge zu verlassen (ein später
# gesetztes TMPDIR würde den impliziten Fallback sonst unbemerkt von tmpfs
# weg umlenken) — die Compose-Dateien setzen die Variable entsprechend auf
# den gemounteten tmpfs-Pfad. Leer/unset behält den Django-Default für
# Umgebungen ohne tmpfs (Fallback-Doku: docs/ops-runbook.md § 5.7a;
# core.W002 warnt beim Deploy-Check, falls die Variable fehlt).
_file_upload_temp_dir = os.environ.get("FILE_UPLOAD_TEMP_DIR", "").strip()
if _file_upload_temp_dir:
    FILE_UPLOAD_TEMP_DIR = _file_upload_temp_dir

# --- E-Mail (SMTP) ---
# Für Passwort-zurücksetzen und andere E-Mail-Funktionen.
# Konfiguration via Umgebungsvariablen — die Namen bleiben unverändert:
#   EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
#   EMAIL_USE_TLS, EMAIL_USE_SSL, DEFAULT_FROM_EMAIL
#
# Django 6.1 hat die gleichnamigen *Settings* zugunsten von ``MAILERS``
# deprecated (Entfernung in 7.0); ``EMAIL_*`` als Setting und ``MAILERS``
# schliessen sich gegenseitig aus (ImproperlyConfigured). Die
# *Umgebungsvariablen* sind Projekt-ENV und heissen weiterhin so — eine
# bestehende Betreiber-.env bleibt unverändert gültig.
_email_use_tls = env_bool_strict("EMAIL_USE_TLS", default=True)
_email_use_ssl = env_bool_strict("EMAIL_USE_SSL", default=False)
if _email_use_tls and _email_use_ssl:
    raise ImproperlyConfigured("EMAIL_USE_TLS und EMAIL_USE_SSL duerfen nicht gleichzeitig aktiviert sein.")
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": os.environ.get("EMAIL_HOST", "localhost"),
            "port": env_int("EMAIL_PORT", 587),
            "username": os.environ.get("EMAIL_HOST_USER", ""),
            "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
            "use_tls": _email_use_tls,
            "use_ssl": _email_use_ssl,
        },
    }
}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@anlaufstelle.app")

# --- Database ---

CONN_MAX_AGE = 60

# --- Shared Cache (A5.1, Refs #1024 / #1016; Redis-Service Refs #795 / #1657) ---
# Ohne expliziten Cache nutzt Django LocMemCache — pro Gunicorn-Worker isoliert.
# django-ratelimit (Login/Sudo/Health) und das Health-Detail-Caching (A7.2)
# zählen dann pro Worker getrennt, sodass Rate-Limits in Multi-Worker-Prod
# faktisch ge-N-facht werden.
#
# Standard seit dem vollen #795-Scope: ``REDIS_URL`` zeigt auf den in
# ``docker-compose.prod.yml`` mitgelieferten ``redis``-Service, Django spricht
# ihn über sein eingebautes RedisCache-Backend an (kein django-redis nötig).
# Redis ist hier eine reine Performance-Entscheidung: der DatabaseCache-Pfad
# unten ist funktional gleichwertig (ebenfalls prozessübergreifend), kostet
# aber pro Rate-Limit-Zähler und pro Health-Detail einen DB-Roundtrip inklusive
# Zeilen-Lock und Ablauf-Garbage-Collection.
#
# Fallback = DatabaseCache (unverändert seit #1024): greift, sobald
# ``REDIS_URL`` leer/ungesetzt ist. Bestands-Deployments ohne Redis und
# Betreiber, die bewusst keinen zusätzlichen Dienst wollen, laufen damit
# unverändert weiter. Die Tabelle wird per Migration 0092 angelegt
# (createcachetable-äquivalent) und läuft im One-Shot-Migrate-Job
# (``docker-migrate.sh``) mit hoch.
#
# Externes Redis: ``REDIS_URL`` in der ``.env`` überschreibt den Compose-Default,
# der mitgelieferte Service kann dann entfallen. Siehe docs/ops-runbook.md
# § 5.4a.
_redis_url = os.environ.get("REDIS_URL", "").strip()
if _redis_url:
    # Kompatibilitäts-Netz für gemischte Stände: Wer das neue Compose-File
    # zieht (setzt ``REDIS_URL`` ab Werk), aber per ``APP_VERSION`` noch ein
    # Image ohne ``redis-py`` fährt, bekäme sonst bei JEDEM Cache-Zugriff eine
    # Exception — also 500er auf allen rate-limitierten Views statt nur
    # langsamerer Caches. Fällt daher sichtbar (Warning im Container-Log,
    # ``cache.backend`` im /health/-Payload) auf den DatabaseCache zurück,
    # statt den Stack zu zerlegen. Refs #795.
    try:
        import redis  # noqa: F401
    except ImportError:
        warnings.warn(
            "REDIS_URL ist gesetzt, aber das Paket 'redis' fehlt im Image — "
            "Cache faellt auf DatabaseCache zurueck. Passiert typischerweise, "
            "wenn ein aelteres APP_VERSION-Image mit dem neuen "
            "docker-compose.prod.yml gefahren wird (Refs #795).",
            RuntimeWarning,
            stacklevel=2,
        )
        _redis_url = ""
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
else:
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
def _split_encryption_key_ring(value):
    """Pure settings-time twin of ``file_vault.keyring.split_key_ring``.

    Importing the service package while this settings module is still loading
    would import model-dependent modules before Django's app registry is ready.
    """
    return [entry.strip() for entry in (value or "").split(",") if entry.strip()]


_encryption_key_ring = _split_encryption_key_ring(ENCRYPTION_KEYS)  # noqa: F405
_encryption_single_key = str(ENCRYPTION_KEY or "").strip()  # noqa: F405
if not _IS_COLLECTSTATIC and not (_encryption_single_key or _encryption_key_ring):
    if ENCRYPTION_KEYS:  # noqa: F405
        _encryption_key_error = (
            "Schlüsselring enthält keinen Schlüssel (ENCRYPTION_KEYS) und ENCRYPTION_KEY ist nicht gesetzt. "
        )
    else:
        _encryption_key_error = "ENCRYPTION_KEY oder ENCRYPTION_KEYS muss in Produktion gesetzt sein. "
    raise ImproperlyConfigured(
        _encryption_key_error
        + 'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
    )

# --- Audit-Hash-Key (mandatory in production) (A4.2, Refs #1024 / #1016) ---
# ``services.audit.hash`` fällt ohne AUDIT_HASH_KEY still auf SHA256(SECRET_KEY)
# zurück (funktional für Dev/Test). In Produktion wäre das eine stille
# Defense-Erosion: ein SECRET_KEY-Leak könnte rückwirkend die Audit-Hashes
# (z.B. pseudonymisierte E-Mails in Passwort-Reset-Audits) knacken. Daher
# fail-closed — lieber Server-Start-Fehler als unbemerkt schwächere Audit-PII.
if not _IS_COLLECTSTATIC and not AUDIT_HASH_KEY:  # noqa: F405
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
# Refs #1591/#1962: Die Runtime prueft ihr App-Passwort und weist jedes
# privilegierte DB-Secret als Boundary-Verletzung ab. Nur ein explizit als
# ``maintenance`` markierter One-Shot/Sidecar darf Admin-/Bootstrap-Secrets
# sehen; dort sind sie Pflicht und unterliegen demselben Entropie-Guard.
_SECRET_MIN_LENGTH = 32
if not _IS_COLLECTSTATIC:
    _process_role = os.environ.get("ANLAUFSTELLE_PROCESS_ROLE", "runtime").strip().lower()
    if _process_role not in {"runtime", "maintenance"}:
        raise ImproperlyConfigured("ANLAUFSTELLE_PROCESS_ROLE muss 'runtime' oder 'maintenance' sein.")

    _privileged_db_values = {
        "POSTGRES_ADMIN_USER": os.environ.get("POSTGRES_ADMIN_USER", ""),
        "POSTGRES_ADMIN_PASSWORD": os.environ.get("POSTGRES_ADMIN_PASSWORD", ""),
        "POSTGRES_BOOTSTRAP_PASSWORD": os.environ.get("POSTGRES_BOOTSTRAP_PASSWORD", ""),
    }
    if _process_role == "runtime":
        _leaked = sorted(_name for _name, _value in _privileged_db_values.items() if _value)
        if _leaked:
            raise ImproperlyConfigured(
                "Privilegierte DB-Konfiguration im Runtime-Prozess gefunden: "
                f"{', '.join(_leaked)}. Diese Variablen duerfen nur der "
                "maintenance-Rolle bereitgestellt werden (Refs #1962)."
            )
    else:
        _missing = sorted(_name for _name, _value in _privileged_db_values.items() if not _value)
        if _missing:
            raise ImproperlyConfigured(
                f"Maintenance-Prozess ohne erforderliche DB-Konfiguration: {', '.join(_missing)} (Refs #1962)."
            )

    _checked_secrets = [
        ("DJANGO_SECRET_KEY", SECRET_KEY),  # noqa: F405
        ("DJANGO_AUDIT_HASH_KEY", AUDIT_HASH_KEY),  # noqa: F405
        ("POSTGRES_PASSWORD", os.environ.get("POSTGRES_PASSWORD", "")),
    ]
    if _process_role == "maintenance":
        _checked_secrets.extend(
            (
                ("POSTGRES_ADMIN_PASSWORD", _privileged_db_values["POSTGRES_ADMIN_PASSWORD"]),
                ("POSTGRES_BOOTSTRAP_PASSWORD", _privileged_db_values["POSTGRES_BOOTSTRAP_PASSWORD"]),
            )
        )
    for _name, _value in _checked_secrets:
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
CLAMAV_ENABLED = env_bool("CLAMAV_ENABLED", default=True)
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
if not env_bool("SUDO_MODE_ENABLED", default=True):
    raise ImproperlyConfigured(
        "SUDO_MODE_ENABLED muss in Produktion True sein. Test-Setting versehentlich uebernommen?"
    )
