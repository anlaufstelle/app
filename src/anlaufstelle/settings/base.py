"""
Django base settings for Anlaufstelle.

Gemeinsame Konfiguration für dev und prod.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from ._env import env_bool, env_csv, env_int, env_origin, env_tokens
from ._mfa_roles import parse_enforced_role_sets

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# A7.3 (Refs #1024 / #1016): .env nur laden, wenn explizit erlaubt UND
# vorhanden. In Produktion wird die Konfiguration über die Orchestrierungs-Env
# (Docker/systemd) gesetzt; eine versehentlich ins Image/Volume geratene .env
# soll diese nicht still ergänzen. ``load_dotenv(override=False)`` füllt zwar nur
# Lücken, aber wir machen die Absicht explizit und per ``DJANGO_LOAD_DOTENV``
# abschaltbar (Default: laden, damit Dev/Test unverändert funktionieren; in
# Prod ``DJANGO_LOAD_DOTENV=false`` setzen bzw. keine .env mounten).
_DOTENV_PATH = BASE_DIR.parent / ".env"
if env_bool("DJANGO_LOAD_DOTENV", default=True) and _DOTENV_PATH.exists():
    load_dotenv(_DOTENV_PATH)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")

# AUDIT_HASH_KEY (Refs #791) — separater HMAC-Schluessel fuer Audit-Hashes
# (z.B. ``hmac_hash_email`` in Passwort-Reset-Audits). Bewusst getrennt von
# SECRET_KEY, damit ein potenzieller SECRET_KEY-Leak nicht ruckwirkend
# Audit-Hashes knacken laesst. Fallback in services.audit.hash:
# SHA256(SECRET_KEY) — funktional, aber operatorseitig sollte
# DJANGO_AUDIT_HASH_KEY explizit gesetzt sein.
AUDIT_HASH_KEY = os.environ.get("DJANGO_AUDIT_HASH_KEY", "")

# AUDIT_HWM_PATH (R16, Refs #1476/#1665) — Pfad einer externen, HMAC-signierten
# High-Water-Mark-Datei fuer die Audit-Kette. Ist er gesetzt, erkennt
# ``verify_audit_chain`` zusaetzlich Tail-Truncation und whole-chain wipe
# (der zuletzt verankerte juengste ``entry_hash`` muss noch in der Kette liegen)
# und ruckt den Anker nach jedem gruenen Lauf vor. Leer ⇒ Feature aus (Verhalten
# unveraendert). Der Pfad sollte ausserhalb des DB-Volumes liegen; die
# betriebliche Verankerung (Offsite-Backup, Cron-Frequenz) ist Deployment-Sache.
AUDIT_HWM_PATH = os.environ.get("DJANGO_AUDIT_HWM_PATH", "")

# AUDIT_DETAIL_STRICT (Refs #1944) — bricht ein nicht deklarierter
# ``AuditLog.detail``-Schluessel den Schreibvorgang ab? Im Betrieb bewusst
# ``False``: der Verstoss wird auf ERROR gemeldet, die Zeile aber geschrieben.
# Ein **fehlender** Audit-Eintrag waere schlimmer als ein zu breiter — das Log
# ist der Rechenschaftsnachweis (Art. 5 Abs. 2 DSGVO), und die schaerfsten
# Schreibpunkte (SECURITY_VIOLATION, LOGIN_FAILED) laufen genau dann, wenn
# ohnehin etwas schiefgeht. dev.py/test.py stellen scharf, damit ein neuer
# Schreibpunkt vor dem Deploy auffaellt. Betreiber, die den Abbruch wollen,
# setzen ``DJANGO_AUDIT_DETAIL_STRICT=true``.
# Allowlist: core/services/audit/detail_policy.py.
AUDIT_DETAIL_STRICT = env_bool("DJANGO_AUDIT_DETAIL_STRICT", default=False)

# HEALTH_DETAIL_TOKEN (A7.1, Refs #1024) — optionaler Token, mit dem ein
# interner Monitoring-Caller die Detailfelder von ``/health/`` (version, smtp,
# Backup-Alter, Disk-frei) abrufen darf. Ohne Token bzw. ohne authentifizierten
# Request liefert ``/health/`` nur einen schlanken Liveness-Payload — anonyme
# Caller bekommen keine Recon-Details. Header: ``X-Health-Token``.
HEALTH_DETAIL_TOKEN = os.environ.get("DJANGO_HEALTH_DETAIL_TOKEN", "")

# L4 (Refs #1375): Passwort-Reset-Tokens kurzlebig halten. Der Django-Default
# ist 3 Tage (259200 s) — fuer einen Reset-Link (starkes Recovery-Primitiv) zu
# lang. Default hier 2 h; per ENV anpassbar. Invites sind davon ENTKOPPELT
# (eigener Generator + INVITE_TOKEN_TIMEOUT), sonst wuerde der kurze Reset-Timeout
# auch die Einladungs-Gueltigkeit verkuerzen.
PASSWORD_RESET_TIMEOUT = env_int("DJANGO_PASSWORD_RESET_TIMEOUT", 2 * 60 * 60)

# L4 (Refs #1375): Getrennte Gueltigkeit fuer Einladungs-Setup-Links. Ein neu
# eingeladener Mitarbeitender braucht realistisch Tage, um den Link zu oeffnen —
# daher laenger als der Reset-Timeout. Default 3 Tage; per ENV anpassbar.
INVITE_TOKEN_TIMEOUT = env_int("DJANGO_INVITE_TOKEN_TIMEOUT", 3 * 24 * 60 * 60)

# AGPL §13 (Refs #835): Quellcode-Link im Footer. Forks/Self-Hoster MUESSEN
# SOURCE_CODE_URL auf ihren eigenen Quellcode-Spiegel zeigen lassen, sonst
# liefert der Footer eine technisch falsche § 13-Network-Use-Disclosure.
# SOURCE_CODE_VERSION ist optional (Commit-SHA) und wird im Footer truncated
# angezeigt — hilfreich fuer Forensik / Reproducible-Build-Hints.
SOURCE_CODE_URL = os.environ.get("SOURCE_CODE_URL", "https://github.com/anlaufstelle/app")
SOURCE_CODE_VERSION = os.environ.get("SOURCE_CODE_VERSION", "")

DEBUG = False

ALLOWED_HOSTS = []

# --- Feste Basis-Origin fuer sicherheitsrelevante Links (Refs #1967) ---
# Einladungs-, Passwort-Reset- und Entsperr-Links tragen ein Token und duerfen
# deshalb nicht aus dem eingehenden Request entstehen: ``ALLOWED_HOSTS`` sperrt
# in Produktion zwar den Match-all-Eintrag "*", laesst Subdomain-Wildcards
# (".example.org") aber ausdruecklich zu — jeder davon erfasste Host kaeme sonst
# ungeprueft in einen Token-Link. EXTERNAL_ORIGIN ist die einzige Quelle fuer
# Schema und Host solcher Links (core/services/security/origin.py).
#
# Format: "https://host[:port]", ohne Pfad und ohne abschliessenden
# Schraegstrich; ein unbrauchbarer Wert bricht den Start ab (env_origin).
# Leer heisst hier nur "hier nicht festgelegt": prod.py macht das Setting zur
# Pflicht (mit Ableitung aus ALLOWED_HOSTS), dev.py/e2e.py belegen es mit dem
# jeweiligen lokalen Server vor.
try:
    EXTERNAL_ORIGIN = env_origin("EXTERNAL_ORIGIN")
except ValueError as exc:  # pragma: no cover - Startabbruch, in Subprozess-Tests geprueft
    raise ImproperlyConfigured(str(exc)) from exc

# --- Apps ---

INSTALLED_APPS = [
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # staticfiles mit ignore_patterns: schliesst die Tailwind-Quelle
    # css/input.css von collectstatic aus (Refs #1480, siehe anlaufstelle/apps.py).
    "anlaufstelle.apps.ProjectStaticFilesConfig",
    # Notwendig für GinIndex auf Client.pseudonym (Trigram-Suche). Seit
    # Django 6.0 prüft postgres.E005 strikt, dass diese App registriert ist.
    "django.contrib.postgres",
    # Third-party
    "django_htmx",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    # Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492). Credentials
    # sind regulaere django-otp-Devices; der WebAuthnBackend wird bewusst NICHT
    # in AUTHENTICATION_BACKENDS aufgenommen (kein passwordless Login, Etappe A).
    "django_otp_webauthn",
    # Project
    "core",
]

# --- Middleware ---

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # MaintenanceModeMiddleware liegt direkt nach SecurityMiddleware,
    # damit sie 503 antworten kann, BEVOR Sessions/Auth/CSP geprueft
    # werden — ein laufendes Deploy darf nicht an einer Auth-Pruefung
    # haengen bleiben. Refs #700.
    "core.middleware.maintenance.MaintenanceModeMiddleware",
    # AdminCSPRelaxMiddleware muss VOR CSPMiddleware stehen, damit sie in
    # Outbound-Reihenfolge den von CSPMiddleware gesetzten Header noch ändern
    # kann (Django ruft Response-Verarbeitung in umgekehrter Listen-Reihenfolge).
    "core.middleware.admin_csp_relax.AdminCSPRelaxMiddleware",
    # DemoUmamiCSPRelaxMiddleware ebenfalls VOR CSPMiddleware (Header-Rewrite in
    # Outbound-Reihenfolge). No-Op ausserhalb DEMO_MODE — lockert die CSP nur
    # auf der Demo, Post-Login, fuer den Umami-Host. Refs #1604.
    "core.middleware.demo_umami_csp_relax.DemoUmamiCSPRelaxMiddleware",
    "csp.middleware.CSPMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Refs #1342: Blanket-no-store fuer JEDE Response an einen authentifizierten
    # User (DSGVO-Haertung, Browser-/bfcache-Schutz vor PII). Direkt nach
    # AuthenticationMiddleware, weil sie request.user braucht; setdefault laesst
    # bereits explizit gesetzte Cache-Control-Header (z.B. Offline-CSRF-Endpoint)
    # unangetastet.
    "core.middleware.no_store_cache.NoStoreCacheMiddleware",
    # Refs #1858: Erneuert das Session-Fenster bei authentifizierter Aktivitaet
    # (Inaktivitaets- statt Absolut-Timeout), gedrosselt auf einen Write je
    # Bruchteil der Laufzeit. Nach AuthenticationMiddleware (braucht
    # request.user); den Save uebernimmt SessionMiddleware ausgangsseitig.
    "core.middleware.session_idle.SessionIdleRefreshMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "core.middleware.htmx_session.HtmxSessionMiddleware",
    "core.middleware.facility_scope.FacilityScopeMiddleware",
    "core.middleware.user_language.UserLanguageMiddleware",
    "core.middleware.password_change.ForcePasswordChangeMiddleware",
    "core.middleware.mfa.MFAEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Refs #1062: Demo-Guard nach MessageMiddleware (nutzt Flash-Messages),
    # No-Op ausserhalb DEMO_MODE.
    "core.middleware.demo_guard.DemoGuardMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

# --- URLs ---

ROOT_URLCONF = "anlaufstelle.urls"

# --- Templates ---

# Deploy-Overlay (Refs #1701): ein per Env benanntes Template-Verzeichnis wird
# VOR den Projekt-Templates aufgeloest. Damit liefern Instanzen eigene Inhalte
# zur Deploy-Zeit (z.B. die echten Demo-Rechtstexte ``core/legal/*.html`` als
# ro-Volume-Mount), ohne dass diese im oeffentlichen Release-Quelltext liegen.
_EXTRA_TEMPLATE_DIR = os.environ.get("EXTRA_TEMPLATE_DIR", "")

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": ([Path(_EXTRA_TEMPLATE_DIR)] if _EXTRA_TEMPLATE_DIR else []) + [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.workitem_counts",
                "core.context_processors.source_code",
                "core.context_processors.demo",
                "core.context_processors.mfa_setup_reminder",
                "core.context_processors.umami",
            ],
        },
    },
]

# --- WSGI ---

WSGI_APPLICATION = "anlaufstelle.wsgi.application"

# --- Database ---

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "anlaufstelle"),
        "USER": os.environ.get("POSTGRES_USER", "anlaufstelle"),
        # Kein Default (Refs #1591): das schwache Literal "anlaufstelle" ist
        # entfernt, damit ein prod-Deploy ohne gesetztes POSTGRES_PASSWORD nicht
        # still auf ein bekanntes Passwort faellt. dev.py setzt fuer die lokale
        # Entwicklung ohne .env-Wert einen eigenen Fallback.
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# --- Auth ---

AUTH_USER_MODEL = "core.User"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# --- Two-Factor Authentication (TOTP) ---

OTP_TOTP_ISSUER = "Anlaufstelle"

# --- Passkeys/WebAuthn als zweiter Faktor (ADR-032, Refs #1492) ---
# NUR zweiter Faktor: OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN bleibt hart False
# (kein passwordless Login — Etappe A). Der WebAuthnBackend wird nicht in
# AUTHENTICATION_BACKENDS registriert; die Assertion-Views laufen ueber unsere
# bereits authentifizierte Session und markieren sie als ``mfa_verified``.
#
# RP-ID = Hostname ohne Schema/Port (domaingebundene Credentials); Origin = volle
# URL inkl. Schema/Port. Per-Umgebung ueberschrieben: dev/test/e2e -> localhost,
# prod/devlive/demo -> aus ALLOWED_HOSTS abgeleitet (prod.py). Passkeys sind
# domaingebunden — dev/demo/prod-Credentials sind bewusst nicht uebertragbar.
OTP_WEBAUTHN_RP_NAME = "Anlaufstelle"
OTP_WEBAUTHN_RP_ID = os.environ.get("OTP_WEBAUTHN_RP_ID", "")
OTP_WEBAUTHN_ALLOWED_ORIGINS = env_csv("OTP_WEBAUTHN_ALLOWED_ORIGINS")
# Der Passkey ist bei uns ein Zweitfaktor NACH dem Passwort. In diesem
# Second-Factor-Zuschnitt verlangt django-otp-webauthn keine User-Verification
# (die Assertion laeuft in einer bereits per Passwort authentifizierten Session)
# — das haelt aeltere Security-Keys ohne PIN/Biometrie im Feld nutzbar (ADR-032).
OTP_WEBAUTHN_ALLOW_PASSWORDLESS_LOGIN = False

# Rollen-Default-Enforcement (A3.1, Refs #1019; erweitert Refs #689/#1639):
# erzwingt MFA allein ueber die Rolle (s. ``User.is_mfa_enforced`` und
# ``core.models.user.mfa_enforced_roles``). In Produktion an (hier in base.py);
# dev/test/e2e schalten es bewusst aus (dev.py -> kaskadiert nach test/e2e),
# damit Seed-Logins MFA-frei bleiben.
MFA_ENFORCE_PRIVILEGED_ROLES = True

# Welche Rollen der Rollen-Default-Zwang umfasst, ist ueber zwei Kommalisten
# konfigurierbar (Refs #1639), per Env ueberschreibbar:
# * ``MFA_ENFORCED_ROLES_IMMEDIATE`` — SOFORTIGE Erzwingung ohne Grace-Frist
#   (Default super_admin/facility_admin): ein privilegierter Account ohne
#   zweiten Faktor wird sofort zum Setup geleitet.
# * ``MFA_ENFORCED_ROLES_GRACE`` — Erzwingung erst nach der 30-Tage-Grace-Frist
#   (Default lead/staff/assistant): LEAD ist seit #1639 per Default pflichtig
#   (wer Fachkraft/Assistenz erzwingt, erst recht die hoehere Leitung), aber mit
#   Grace, damit Bestandsnutzer beim Aktivieren nicht ausgesperrt werden.
# Fail-loud: unbekannter Rollenname oder Ueberschneidung beider Mengen bricht
# den Start ab (``parse_enforced_role_sets``). Leere Listen sind erlaubt und
# schalten die jeweilige Ebene bewusst ab.
MFA_ENFORCED_ROLES_IMMEDIATE, MFA_ENFORCED_ROLES_GRACE = parse_enforced_role_sets(
    os.environ.get("MFA_ENFORCED_ROLES_IMMEDIATE", "super_admin,facility_admin"),
    os.environ.get("MFA_ENFORCED_ROLES_GRACE", "lead,staff,assistant"),
)

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        # 12 Zeichen passend zu BSI/NIST-Empfehlung fuer §203-/Art.-9-Daten
        # (Refs #789). services/password.py Initial-Passwort-Generator nutzt
        # bereits 12 Zeichen — der Validator zog vorher den Django-Default 8.
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Session ---

# --- SudoMode (Refs #683) ---
# Re-Auth-Fenster fuer sensible Aktionen (MFA-Disable, DSGVO-Export).
# In Tests deaktiviert (settings/test.py setzt False), in Prod/Dev aktiv.
SUDO_MODE_ENABLED = True
SUDO_MODE_TTL_SECONDS = env_int("SUDO_MODE_TTL_SECONDS", 900)

# --- Breach-Detection (Refs #685) ---
BREACH_DETECTION_WINDOW_MINUTES = env_int("BREACH_DETECTION_WINDOW_MINUTES", 60)
BREACH_FAILED_LOGIN_THRESHOLD = env_int("BREACH_FAILED_LOGIN_THRESHOLD", 20)
BREACH_EXPORT_THRESHOLD = env_int("BREACH_EXPORT_THRESHOLD", 10)
BREACH_DELETE_THRESHOLD = env_int("BREACH_DELETE_THRESHOLD", 50)
# Sekundaeres Langzeitfenster gegen low-and-slow (Refs #1368): dieselben
# per-User-/facility-weiten Heuristiken laufen zusaetzlich ueber ein breiteres
# Fenster mit hoeheren Schwellen, damit Angriffe unter der 60-min-Schwelle nicht
# durchrutschen. Default 24h.
BREACH_DETECTION_LONG_WINDOW_MINUTES = env_int("BREACH_DETECTION_LONG_WINDOW_MINUTES", 1440)
BREACH_FAILED_LOGIN_THRESHOLD_LONG = env_int("BREACH_FAILED_LOGIN_THRESHOLD_LONG", 60)
BREACH_EXPORT_THRESHOLD_LONG = env_int("BREACH_EXPORT_THRESHOLD_LONG", 30)
BREACH_DELETE_THRESHOLD_LONG = env_int("BREACH_DELETE_THRESHOLD_LONG", 150)
# Massen-Client-Destruktion facility-weit (Refs #1368): CLIENT_SOFT_DELETED /
# CLIENT_ANONYMIZED / DELETION_APPROVED schreiben KEIN Action.DELETE und blieben
# fuer detect_mass_delete unsichtbar. Eigene, niedrigere Schwelle (gezieltes
# Vernichten von Personendaten wiegt schwerer als generische Loeschungen).
BREACH_CLIENT_DESTRUCTION_THRESHOLD = env_int("BREACH_CLIENT_DESTRUCTION_THRESHOLD", 20)
BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG = env_int("BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG", 60)
# Anonyme Login-Bursts (Refs #1368): fehlgeschlagene Logins gegen UNBEKANNTE
# Usernames (user IS NULL, facility IS NULL) — Credential-Stuffing/Enumeration.
# Pro Quell-IP + installationsweites Gesamtvolumen.
BREACH_ANON_LOGIN_IP_THRESHOLD = env_int("BREACH_ANON_LOGIN_IP_THRESHOLD", 20)
BREACH_ANON_LOGIN_TOTAL_THRESHOLD = env_int("BREACH_ANON_LOGIN_TOTAL_THRESHOLD", 100)
# Verteilter Login-Angriff auf EINEN Account von vielen IPs (Refs #1372):
# Victim-Lockout-/Distributed-Bruteforce-Signatur. Monitor-only-Gegenstueck zum
# bewusst beibehaltenen 10/h-Username-Ratelimit (Anti-Botnet, #598). Zahl
# distinkter Quell-IPs je User im Fenster.
BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD = env_int("BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD", 10)
# Optional: Webhook fuer externe Eskalation. Leer = kein Webhook.
BREACH_NOTIFICATION_WEBHOOK_URL = os.environ.get("BREACH_NOTIFICATION_WEBHOOK_URL") or None

# --- CSRF (Refs #699) ---
# Custom CSRF-Failure-View liefert ``403_csrf.html`` (Tailwind, deutsch)
# statt Djangos Built-in-Default-Seite.
CSRF_FAILURE_VIEW = "core.views.errors.csrf_failure"

# --- Maintenance-Mode (Refs #700) ---
# Solange diese Datei existiert, antwortet MaintenanceModeMiddleware mit 503.
# Aktivieren: ``touch $MAINTENANCE_FLAG_FILE``. Default ``None`` =
# Middleware ist No-Op (kein Disk-Check).
MAINTENANCE_FLAG_FILE = os.environ.get("MAINTENANCE_FLAG_FILE") or None
# Komma-separierte IP-Liste fuer Ops-Zugriff waehrend der Wartung.
MAINTENANCE_ALLOW_IPS = env_csv("MAINTENANCE_ALLOW_IPS")
# Caching der Flag-Datei-Pruefung (Sekunden).
MAINTENANCE_CACHE_TTL = env_int("MAINTENANCE_CACHE_TTL", 5)
# Retry-After-Header-Wert (Sekunden).
MAINTENANCE_RETRY_AFTER = env_int("MAINTENANCE_RETRY_AFTER", 600)

SESSION_COOKIE_AGE = 1800  # 30 Minuten
# False reduziert DB-Write-Amplifikation bei HTMX-Microrequests
# (Polling, Autocomplete) — Session wird nur bei tatsaechlichen
# Aenderungen geschrieben. Das Inaktivitaets-Fenster liefert NICHT dieses
# Flag: ohne weitere Session-Writes wirkte der Timeout absolut ab Login
# (Refs #1858). SessionIdleRefreshMiddleware erneuert das Fenster bei
# authentifizierter Aktivitaet, gedrosselt auf einen Write je
# RENEWAL_FRACTION-Anteil der Laufzeit. Refs #733.
SESSION_SAVE_EVERY_REQUEST = False

# R18 (Refs #1476/#1666): Obergrenze fuer den Session-Scan der Offline-Revocation
# (``core.signals.offline_invalidation._flush_user_sessions``). Djangos
# DB-Session-Backend hat keinen queryable User-Index, daher muss jede aktive
# Session dekodiert werden — der Deckel bounded den Worst-Case je Revocation.
# Default weit ueber realistischem Bestand; bei Ueberschreitung wird laut geloggt.
OFFLINE_REVOCATION_SESSION_SCAN_LIMIT = env_int("OFFLINE_REVOCATION_SESSION_SCAN_LIMIT", 10000)

# --- Trusted Proxy Hops (Client-IP-Ermittlung) ---
# Anzahl der vertrauenswürdigen Proxy-Hops vor der Django-App. Bestimmt, welcher
# Eintrag aus X-Forwarded-For als echte Client-IP interpretiert wird.
#
#   0 → REMOTE_ADDR direkt verwenden (kein Reverse-Proxy, spoofing-sicher)
#   1 → Caddy-only (Default): split(",")[-1]
#   2 → CDN + Caddy (z.B. Cloudflare → Caddy): split(",")[-2]
#   N → Allgemein: split(",")[-N]
#
# X-Forwarded-For-Konvention: "client, proxy1, proxy2" — jeder Proxy hängt die IP
# an, von der er den Request bekommen hat. Bei N vertrauenswürdigen Proxies ist
# der N-te Eintrag von rechts die echte Client-IP.
#
# WARNUNG (L8, Refs #1375): Der Wert MUSS exakt der Anzahl der TATSÄCHLICH
# vorgeschalteten, vertrauenswürdigen Reverse-Proxys entsprechen. Ist er HÖHER
# als die reale Hop-Zahl, kann ein Client X-Forwarded-For spoofen (eigene
# Fake-Hops anhängen), die dann als „vertrauenswürdig" gezählt werden — das
# unterläuft IP-Ratelimits, Login-Lockout (N9) und die Audit-Client-IP. Jeder
# gezählte Hop muss ein bekannter Proxy sein. Ein Django-System-Check
# (``core.checks.check_trusted_proxy_hops``) warnt bei ungewöhnlich hohen Werten.
TRUSTED_PROXY_HOPS = env_int("TRUSTED_PROXY_HOPS", 1)

# --- Vertrauenswürdige Proxy-Bereiche (Refs #1963) ---
# Dieselbe Variable füllt die Caddy-Direktive ``trusted_proxies static`` (siehe
# den globalen ``servers``-Block der Caddyfiles) und dieses Setting. Caddy
# übernimmt ein eingehendes ``X-Forwarded-For`` NUR von einem Peer aus dieser
# Liste; von jedem anderen Peer ersetzt es den Header durch dessen IP. Ohne
# Einträge kommt bei der App also immer genau EIN Eintrag an — jedes
# ``TRUSTED_PROXY_HOPS > 1`` griffe dann daneben und fiele auf REMOTE_ADDR
# (die Proxy-IP) zurück. Leer = Caddy ist der einzige Proxy (Regelfall).
# Format: CIDR-Bereiche, nackte Adressen oder Caddys Schlüsselwort
# ``private_ranges``, getrennt durch LEERZEICHEN — die Caddy-Schreibweise, und
# zwar die einzige: Der Caddyfile-Parser zerlegt Direktiven-Argumente nicht am
# Komma (deshalb ``env_tokens`` statt ``env_csv``). Ein kommagetrennter Wert
# käme dort als ein einziger, unbrauchbarer Bereich an und ließe Caddy den
# Start verweigern; ``core.checks.check_trusted_proxy_cidrs`` (core.E003) meldet
# ihn deshalb schon hier — mit demselben Regelwerk, das Caddy anwendet.
TRUSTED_PROXY_CIDRS = env_tokens("TRUSTED_PROXY_CIDRS")

# django-ratelimit: die per-IP-Eimer (key="ip") sollen auf der ECHTEN Client-IP
# schlüsseln, nicht auf REMOTE_ADDR. Hinter dem Caddy-Reverse-Proxy ist
# REMOTE_ADDR immer Caddys Container-IP — ohne diesen Resolver kollabierten alle
# Login-/Reset-/Recovery-Limits auf einen einzigen Eimer fürs ganze Internet
# (globaler DoS, keine echte Brute-Force-Isolierung). Der Resolver leitet die
# Client-IP über get_client_ip (TRUSTED_PROXY_HOPS-bewusst) ab. Security N1.
# In test/e2e ist RATELIMIT_ENABLE=False, der Wert ist dort inert.
RATELIMIT_IP_META_KEY = "core.signals.audit.client_ip_for_ratelimit"

# --- i18n ---

LANGUAGE_CODE = "de"

LANGUAGES = [
    ("de", "Deutsch"),
    ("en", "English"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

# --- Static files ---

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# --- Media files (encrypted attachments — NOT served via Django URL) ---

MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

# --- Backup-Verzeichnis (Refs #796) ---
# Pfad, in dem ``scripts/ops/backup.sh`` die Daily-Files (.sql.gz.enc) ablegt.
# Wird vom Health-Endpoint zur Berechnung von ``last_backup_age_hours``
# gelesen. Default = ``<repo>/backups`` passend zur Default-Convention im
# Backup-Script.
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", BASE_DIR.parent / "backups"))

# --- Encryption ---
# MultiFernet key rotation: ENCRYPTION_KEYS is a comma-separated list of Fernet keys.
# The first key is used for encrypting, all keys are tried for decrypting.
# Fallback: if only ENCRYPTION_KEY (singular) is set, it is used as the single key.

ENCRYPTION_KEYS = os.environ.get("ENCRYPTION_KEYS", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

# --- Virenscan (ClamAV, Issue #524) ---
# Prüft jede hochgeladene Datei VOR der Verschlüsselung gegen einen ClamAV-Daemon.
# In Dev/Test per Default deaktiviert; prod.py aktiviert es.
# Bei aktivem Scan und unerreichbarem Daemon wird der Upload abgewiesen (fail-closed).
CLAMAV_ENABLED = env_bool("CLAMAV_ENABLED", default=False)
CLAMAV_HOST = os.environ.get("CLAMAV_HOST", "clamav")
CLAMAV_PORT = env_int("CLAMAV_PORT", 3310)
# Refs #1283: MUSS unter dem Gunicorn-Worker-Timeout (GUNICORN_TIMEOUT, Default
# 30s — siehe docker-entrypoint.sh) bleiben, inkl. Marge für die
# Fehlerbehandlung. scan_file erzwingt diesen Wert als harte Wall-Clock-Deadline;
# liegt er >= dem Worker-Timeout, killt Gunicorn den Worker bei langsamem clamd
# zuerst → 500 statt fail-closed "Scanner nicht erreichbar".
CLAMAV_TIMEOUT = env_int("CLAMAV_TIMEOUT", 15)

# --- File-Vault Upload-Limits (Refs #1268) ---
# Service-Layer-Obergrenzen als Defense-in-Depth gegen authentifizierten
# Memory-Exhaustion-DoS. Sie greifen UNABHÄNGIG von der per-Facility
# konfigurierbaren Form-Größe (``Settings.max_file_size_mb``) auch für
# programmatische Aufrufer, die das Formular umgehen — der Service-Layer ist die
# letzte Bastion (Extension/Magic/Virus + jetzt Größe/Pixel/Anzahl). Bewusst in
# base.py (nicht prod.py), um Kollisionen mit laufenden prod.py-Änderungen zu
# vermeiden; per Env-Var überschreibbar.
#
# * FILE_VAULT_MAX_UPLOAD_BYTES — harte Obergrenze pro Datei; greift VOR jeder
#   Voll-Pufferung (Virenscan/Encrypt), damit eine Riesendatei nicht erst in den
#   RAM gelesen wird.
# * FILE_VAULT_MAX_UPLOAD_FILES — Obergrenze für die Datei-Anzahl pro File-Feld
#   (MultipleFileField) → bremst Masse-Uploads pro Request.
# * FILE_VAULT_MAX_IMAGE_PIXELS — Decompression-Bomb-Schutz: maximale (aus dem
#   Header gelesene, nicht dekodierte) Pixelzahl eines Bild-Uploads; setzt
#   zugleich ``PIL.Image.MAX_IMAGE_PIXELS``.
# * FILE_VAULT_MAX_ARCHIVE_BYTES / _RATIO — Zip-Bomb-Schutz (#1310, S4) fuer
#   ZIP/OOXML-Container: maximale Summe der UNkomprimierten Eintraege bzw.
#   maximales Expansions-Verhaeltnis (unkomprimiert/komprimiert), aus dem
#   Zip-Directory gelesen — OHNE Entpacken.
# * FILE_VAULT_MAX_ARCHIVE_ENTRIES — Zip-Bomb-Schutz (#1310, S4): maximale Anzahl
#   Eintraege, billig aus dem EOCD-Record gelesen, BEVOR zipfile.ZipFile() pro
#   Eintrag ein ZipInfo materialisiert — sonst waere der Archiv-Guard selbst ein
#   Memory-DoS (~1 Mio Mini-Eintraege => hunderte MB transient) auf RAM-limitierten Hosts.
FILE_VAULT_MAX_UPLOAD_BYTES = env_int("FILE_VAULT_MAX_UPLOAD_BYTES", 50 * 1024 * 1024)
FILE_VAULT_MAX_UPLOAD_FILES = env_int("FILE_VAULT_MAX_UPLOAD_FILES", 20)
FILE_VAULT_MAX_IMAGE_PIXELS = env_int("FILE_VAULT_MAX_IMAGE_PIXELS", 40_000_000)
FILE_VAULT_MAX_ARCHIVE_BYTES = env_int("FILE_VAULT_MAX_ARCHIVE_BYTES", 200 * 1024 * 1024)
FILE_VAULT_MAX_ARCHIVE_RATIO = env_int("FILE_VAULT_MAX_ARCHIVE_RATIO", 100)
FILE_VAULT_MAX_ARCHIVE_ENTRIES = env_int("FILE_VAULT_MAX_ARCHIVE_ENTRIES", 10000)

# --- Default PK ---

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Logging ---

LOG_FORMAT = os.environ.get("LOG_FORMAT", "text")  # "text" or "json"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        # Default-Textausgabe mit PII-Scrub (Refs #1500). Ohne den
        # Scrubbing-Formatter würde bei ``LOG_FORMAT=text`` (Default) rohe PII
        # in stdout/Log-Dateien landen; nur ``LOG_FORMAT=json`` scrubbte bisher.
        "verbose": {
            "()": "core.logging.VerboseScrubbingFormatter",
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "json": {
            "()": "core.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "core": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}

# --- Content Security Policy (django-csp, Defense-in-Depth neben Caddy) ---

CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        # script-src ist seit v0.10.2 strikt: kein 'unsafe-eval', kein
        # 'unsafe-inline'. Die @alpinejs/csp-Migration (Refs #672, PR #690)
        # ist abgeschlossen — alle Alpine.js-Komponenten sind als
        # Alpine.data()-Registrierungen unter src/static/js/alpine/ gebündelt
        # (Refs #911: featureweise gesplittet in base-layout/widgets/auth/
        # forms/dashboards), Templates nutzen ausschließlich Komponenten-
        # Namen wie `x-data="globalSearch"` (kein Inline-Object).
        #
        # Ausnahme: die Django-Admin-UI (`/admin-mgmt/`) lädt das gevendor'te
        # django-unfold mit klassischem Alpine.js-Build, das `unsafe-eval`
        # benötigt. Statt die globale CSP aufzuweichen, ergänzt
        # `core.middleware.admin_csp_relax.AdminCSPRelaxMiddleware` per
        # Response-Header-Rewrite `unsafe-eval` nur für Admin-Routes.
        # Defense-in-Depth dort: MFA-Pflicht für Admin/Lead, Rate-Limit,
        # AuditLog auf Schreib-Aktionen, RLS gegen Cross-Facility-Leaks.
        # Folgemigration auf reinen `@alpinejs/csp`-Build im Admin: Issue #695.
        "script-src": ["'self'"],
        # style-src ist seit Refs #1660 (I5 aus #1376) strikt: kein
        # 'unsafe-inline'. Damit blockt der Browser jedes `style="…"`-Attribut
        # und jeden nicht freigegebenen `<style>`-Block — ein zusaetzlicher
        # Riegel gegen CSS-basierte Hebel (UI-Redressing, Exfiltration ueber
        # Attribut-Selektoren), falls irgendwo eine HTML-Injektion gelaenge.
        #
        # Nicht betroffen ist die CSSOM-API: `el.style.setProperty(...)` bzw.
        # `el.style.display = …` sind CSP-frei — genau darueber setzen Alpine
        # (`x-show`/`x-cloak`/`x-transition`) und Chart.js ihre Styles.
        # Bewusst NICHT gesetzt ist 'unsafe-hashes'; die Hashes unten gelten
        # deshalb ausschliesslich fuer `<style>`-Elemente, nie fuer Attribute.
        #
        # Templates duerfen keine Inline-Styles mehr enthalten — Guard:
        # tests/test_architecture_guards_templates.py::TestNoInlineStylesGuard.
        # Zwei dokumentierte Ausnahmen:
        #
        # (a) `core/export/*_pdf.html` rendern serverseitig ueber WeasyPrint zu
        #     PDF und erreichen nie einen Browser — dort gilt keine CSP.
        # (b) Die drei selbsttragenden Sonderseiten unten muessen auch dann
        #     korrekt aussehen, wenn `/static/` nicht ausgeliefert wird
        #     (500 = Serverfehler, 503 = Wartungsmodus, Static ggf. mit
        #     abgeraeumt) oder wenn das Netz weg ist (offline.html liegt im
        #     Service-Worker-Precache). Ihr `<style>`-Block bleibt inline und
        #     ist per sha256 gepinnt. Hash statt Nonce, weil der SW gecachtes
        #     HTML samt gecachtem Header ausliefert — eine Nonce im Header
        #     passte dann nicht mehr zum gecachten Markup.
        #
        # Die Hashes werden aus dem Template-Quelltext berechnet (Inhalt
        # zwischen `<style>` und `</style>`, UTF-8, byte-genau). Aendert sich
        # dort auch nur ein Leerzeichen, faellt das im Guard-Test
        # `test_hashed_style_blocks_are_pinned_in_csp` auf — nicht erst im
        # Browser als ungestylte Fehlerseite.
        "style-src": [
            "'self'",
            # src/templates/500.html
            "'sha256-ihZyCwioNqKY8Y5RQ80S5h0NmUocyt17nt67LWBDkRo='",
            # src/templates/503.html
            "'sha256-smcD4VNUZNP7lE3q3hqIklcptwARfdDqbtDrd3iaqu8='",
            # src/templates/offline.html
            "'sha256-8Nca09dL0IQR+QxkyIdvyuGA1Vhg0qhdZUqzFccOUls='",
        ],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        # CSP-Reporting (Refs #684, Refs #733): Browser POSTet Violations
        # an /csp-report/ — strukturiertes Logging als WARNING fuer
        # Detection. Der Endpoint ist rate-limited (10/min/IP) und
        # csrf_exempt, weil Reports vom Browser-CSP-Layer kommen.
        "report-uri": ["/csp-report/"],
    }
}

# --- Umami Web-Analytics (Refs #1602, #1604, #1701) ---
# Standardmaessig AUS: lokale dev/test/prod-Instanzen bleiben tracking-frei.
# Der Tracker wird ausschliesslich eingebunden, wenn UMAMI_WEBSITE_ID gesetzt
# ist (siehe partials/_umami.html + core.context_processors.umami). Konkrete
# Werte (Website-ID, Skript-URLs) sind Instanz-Konfiguration und kommen aus
# der Deploy-Env der Betreiber-Instanzen — eingelesen NUR in devlive.py
# (dev-Instanz, Refs #1602; vererbt an demo.py, Refs #1604), dort auch die
# CSP-Freigabe des konfigurierten Hosts. Der Release-Quelltext traegt keine
# instanzspezifischen Hosts/IDs (Refs #1701).
UMAMI_WEBSITE_ID = None
UMAMI_SCRIPT_SRC = ""
# Session-Recorder/Heatmap-Skript (nur demo.anlaufstelle.app, Refs #1604).
UMAMI_RECORDER_SRC = ""
# Consent-Gate: bei True wird der Tracker NICHT direkt eingebunden, sondern
# erst nach expliziter Opt-in-Zustimmung client-seitig injiziert (DSGVO). Nur
# die Demo setzt dies auf True; devlive trackt ohne Consent (interne Instanz).
UMAMI_REQUIRE_CONSENT = False

# Rechtstext-Verlinkung (Impressum/Datenschutz) im Demo-Footer + Consent-Banner.
# Leer = kein Link. Nur die Demo setzt konkrete URLs (auf die Webseite). Refs #1604.
DEMO_LEGAL_IMPRINT_URL = ""
DEMO_LEGAL_PRIVACY_URL = ""

# --- Django Unfold ---

UNFOLD = {
    "SITE_TITLE": "Anlaufstelle",
    "SITE_HEADER": "Anlaufstelle",
    "SITE_URL": "/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "COLORS": {
        "primary": {
            "50": "oklch(96.2% .018 272.314)",
            "100": "oklch(93% .042 272.804)",
            "200": "oklch(87% .082 274.713)",
            "300": "oklch(78.5% .145 274.713)",
            "400": "oklch(67.3% .222 274.713)",
            "500": "oklch(58.5% .269 274.713)",
            "600": "oklch(51.1% .262 276.966)",
            "700": "oklch(45.7% .24 277.023)",
            "800": "oklch(39.8% .195 277.366)",
            "900": "oklch(35.9% .152 278.697)",
            "950": "oklch(26.2% .112 280.572)",
        },
    },
}

# 'manage.py seed' ist fail-closed gesperrt: Der Seed legt Demo-Logins mit
# dokumentiertem Passwort an (auf Prod ein Backdoor) und loescht mit --flush
# den kompletten Datenbestand. Nur dev/test/e2e/devlive schalten frei.
# Produktions-Ersteinrichtung laeuft ueber 'manage.py create_super_admin'.
# Refs #1040 (S1).
SEED_ALLOWED = False
