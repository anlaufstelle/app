"""Health endpoint for monitoring (no auth)."""

from __future__ import annotations

import contextlib
import logging
import math
import os
import shutil
import smtplib
import socket
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.mail.handler import DEFAULT_MAILER_ALIAS
from django.db import connection
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View

from core.services import ops_thresholds
from core.services.compliance import ComplianceStatus, cron_job_checks
from core.services.file_vault import ping as clamav_ping
from core.services.security.session_state import session_fully_authenticated
from core.services.system import rls_bypass_for_read
from core.services.system.health import last_backup_info
from core.utils.ratelimit import ratelimit

logger = logging.getLogger(__name__)

# Refs #796 (C-28) — Schwellen, die einen ``status=degraded`` ausloesen.
# Refs #1872: konsolidiert — der Wert (unveraendert 48 h) kommt jetzt aus
# ``core.services.ops_thresholds`` und ist dieselbe CRITICAL-Grenze, die auch
# das Compliance-Dashboard fuer die Backup-Zeile nutzt.
BACKUP_WARN_HOURS = ops_thresholds.BACKUP_CRITICAL_HOURS
DISK_WARN_PCT = 10
SMTP_TIMEOUT_SECONDS = 2

# A7.2 (Refs #1024): TTL für das Caching der teuren Detail-Checks (SMTP-CONNECT,
# Disk-Usage, Backup-Scan). Häufiges Monitoring-Polling soll nicht jeden Poll
# einen SMTP-Handshake + Filesystem-Scan auslösen.
_DETAIL_CACHE_TTL_SECONDS = 15

# Refs #2348: Refs #2342 änderte die Form dieses gecachten Werts von
# ``float | None`` auf ein ``dict`` (Zustand + Alter), ohne den Schlüssel zu
# wechseln. Ein im Deploy-Fenster noch vorhandener Eintrag der Vorversion
# hätte den detail-berechtigten Pfad mit 500 kippen können (s.
# ``_cached_backup_signal``). Der ``:v2``-Suffix sorgt dafür, dass ein
# künftiger Formwechsel denselben Schutz braucht — nicht nur den isinstance-
# Rückfall.
_BACKUP_SIGNAL_CACHE_KEY = "health:backup_signal:v2"


def _detail_authorized(request) -> bool:
    """A7.1 (Refs #1024): Darf dieser Caller die Health-Detailfelder sehen?

    True für interne/Token-Caller (Header ``X-Health-Token`` == gesetztes
    ``HEALTH_DETAIL_TOKEN``) oder vollständig angemeldete Sessions. Anonyme
    Caller bekommen nur den schlanken Liveness-Payload.

    Refs #2286: Der Sitzungspfad prüft ``session_fully_authenticated`` statt der
    blossen Anmeldung. ``/health/`` steht in den ``EXEMPT_URLS`` der
    MFA-Middleware — wer nur das Passwort besitzt, sah hier bis dahin Version,
    Backup-Alter, Plattenplatz, SMTP-Zustand und hängende Jobs, also genau den
    Betriebszustand, den der zweite Faktor abdecken soll. Der Token-Pfad bleibt
    unverändert: er hat mit dem Anmeldezustand nichts zu tun.
    """
    token = getattr(settings, "HEALTH_DETAIL_TOKEN", "") or ""
    if token and constant_time_compare(request.headers.get("X-Health-Token", ""), token):
        return True
    return session_fully_authenticated(request)


def _degrade(status: str, condition) -> str:
    """Eskalationsleiter-Schritt: hebt ``status`` von ``"ok"`` auf ``"degraded"``,
    wenn ``condition`` erfuellt ist — sonst bleibt ``status`` unveraendert.

    Refs #1827: ``HealthView.get`` wiederholte ``if <Bedingung> and status ==
    "ok": status = "degraded"`` 7x identisch (ClamAV, RLS-Bypass, Cache, SMTP,
    Backup-Alter, Disk-Frei, Scheduler-Staleness). ``status`` eskaliert nie
    zurueck — ein bereits ``"error"``/``"degraded"`` bleibt es, weil der
    Vergleich strikt gegen ``"ok"`` prueft (unveraendert gegenueber vorher).
    """
    return "degraded" if condition and status == "ok" else status


def _check_database() -> tuple[str, bool]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return "connected", True
    except Exception:
        logger.exception("health: database check failed")
        return "unavailable", False


def _query_runtime_role_posture() -> tuple[bool | None, bool | None]:
    """C-25 (Refs #793): ``(rolsuper, rolbypassrls)`` der DB-Rolle DIESER Connection.

    Misst bewusst die *laufende Verbindung* (``current_user``) und nicht die
    Env-Konfiguration: ``manage.py check_db_roles`` (Refs #902) verifiziert, dass
    App-/Admin-Rolle mit dem richtigen Attributprofil im Katalog **existieren** —
    ob die Django-Runtime auch tatsaechlich als die App-Rolle verbindet, sagt es
    nicht. Genau diese Luecke ist die Self-Hosting-Falle aus #793: laeuft die
    Runtime als Superuser (das offizielle ``postgres``-Image legt den per
    ``POSTGRES_USER`` angelegten Login ohne
    ``deploy/postgres-init/01-app-role.sh`` als Superuser an), werden die
    RLS-Policies aus Migration 0047/0084/0085 **still** umgangen — nichts
    schlaegt fehl, die Facility-Isolation ist aber weg.

    ``(None, None)`` = nicht ermittelbar (Nicht-PostgreSQL-Backend, Rolle nicht
    im Katalog, DB nicht erreichbar) und degradet bewusst NICHT.

    Kosten: ein einzelner indizierter Katalog-Lookup — billiger als die
    gecachten Detail-Checks (A7.2) und deshalb ungecacht, damit auch der anonyme
    Liveness-Pfad die Posture jederzeit korrekt bewertet.
    """
    if connection.vendor != "postgresql":
        return None, None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            row = cursor.fetchone()
    except Exception:
        logger.exception("health: db role posture check failed")
        return None, None
    if not row:
        return None, None
    return bool(row[0]), bool(row[1])


def _check_encryption_key() -> str:
    """Refs #796 (C-28): Roundtrip-Test gegen die aktive Fernet-Key-Konfiguration.

    Schlaegt der Test fehl, ist die App effektiv unbedienbar (keine Lese- noch
    Schreibvorgaenge auf verschluesselten Feldern) — kritisch.
    """
    try:
        from core.services.file_vault import decrypt_field, encrypt_field

        token = encrypt_field("health-roundtrip")
        decoded = decrypt_field(token)
        if decoded == "health-roundtrip":
            return "ok"
        return "error"
    except Exception:
        logger.exception("health: encryption key check failed")
        return "error"


def _check_smtp() -> dict:
    """SMTP CONNECT-Test mit kurzem Timeout. Returns ``{status, latency_ms?}``.

    Liefert ``status=disabled`` fuer Console-/Locmem-Backends oder leeren
    Host. Ein nicht erreichbarer Server -> ``status=unreachable`` plus
    Gesamtstatus ``degraded`` (Token-Invites scheitern lautlos sonst).

    Konfigurationsquelle ist seit Django 6.1 ``settings.MAILERS["default"]``
    (Refs #1662); die frueheren ``EMAIL_*``-Settings sind dort deprecated und
    ab Django 7.0 entfernt. Der Payload-Vertrag (``smtp``-Feld, Werte
    ``disabled``/``ok``/``unreachable``, optional ``latency_ms``) bleibt
    unveraendert — docs/monitoring-guide.md.
    """
    mailer = getattr(settings, "MAILERS", {}).get(DEFAULT_MAILER_ALIAS, {})
    # Fehlendes BACKEND heisst bei Django ``smtp`` (Default des Mailers).
    backend = mailer.get("BACKEND", "django.core.mail.backends.smtp.EmailBackend")
    options = mailer.get("OPTIONS", {})
    host = options.get("host", "")
    if "smtp" not in backend or not host:
        return {"status": "disabled"}
    port = int(options.get("port") or 25)
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=SMTP_TIMEOUT_SECONDS) as sock:
            # Banner lesen, damit wir wissen, dass es kein leerer TCP-Listen ist.
            sock.settimeout(SMTP_TIMEOUT_SECONDS)
            with contextlib.suppress(TimeoutError, OSError):
                sock.recv(64)
        latency_ms = int((time.monotonic() - started) * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except (TimeoutError, OSError, smtplib.SMTPException):
        return {"status": "unreachable"}


def _check_backup_signal() -> dict:
    """Backup-Zustand fuer ``/health/``: ein Verzeichnis-Scan, ein Urteil.

    Refs #2342: Der Payload braucht neben dem Alter auch den Fall „Zeitstempel
    in der Zukunft" — dort ist das Alter kein Messwert. Beides kommt aus
    derselben Ableitung wie Systembereich und Compliance-Karte
    (:func:`core.services.system.health.last_backup_info`); hier wird nur
    uebersetzt, nicht gerechnet. Ein eigener zweiter Scan waere genau die
    zweite Wahrheit, die Refs #2291 beseitigt hat.
    """
    info = last_backup_info()
    if info is None:
        return {"age_hours": None, "state": "missing"}
    if info.get("in_future"):
        return {"age_hours": None, "state": "future"}
    age_hours = info["age_hours"]
    return {"age_hours": age_hours, "state": "stale" if age_hours > BACKUP_WARN_HOURS else "ok"}


def _check_stale_jobs() -> list[str]:
    """Refs #1335 (Scheduler-Doku-Haertung): Keys der Hintergrundjobs mit Status ``critical``.

    Laeuft der Scheduler nicht (docs/ops-runbook.md § 3), passieren Backup,
    Retention, Breach-Detection etc. nie — still, ohne Fehlermeldung. Seit dem
    ``cron``-Sidecar (Refs #794) deckt der mitgelieferte Stack die
    manage.py-Jobs ab; Backup bleibt Host-Cron, und Deployments ohne unser
    Compose-File (k8s, eigene Orchestrierung) planen weiterhin alles selbst — der Check bleibt
    also der Wirksamkeits-Nachweis. Er wiederverwendet
    :func:`core.services.compliance.cron_job_checks` (bereits das
    Compliance-Dashboard), damit beide Stellen dieselbe Wahrheit zeigen.

    ``unknown`` (Job lief noch nie, z.B. frische Installation) darf NICHT
    degraden — das waere fuer jede frische Instanz ein falscher Alarm. Nur ein
    bestaetigtes ``critical`` (Job lief zuletzt vor laenger als der jeweilige
    Schwellwert) zeigt einen tatsaechlich ausgefallenen Scheduler an.

    ``cron_job_checks()`` liest die Cron-Marker aus ``AuditLog(facility=None)``
    — unter RLS (Migration 0047/0085) nur sichtbar mit SUPERUSER/BYPASSRLS-Rolle
    oder gesetztem GUC ``app.is_super_admin``. Der Token-/authentifizierte
    Monitoring-Caller dieses Endpoints (``_detail_authorized``) laeuft aber
    i.d.R. NICHT als super_admin-Browsersession — ohne
    :func:`~core.services.system.rls_bypass_for_read` waeren die Marker fuer
    ihn unsichtbar und jeder Job faelschlich ``unknown`` statt ``critical``
    (Refs #1335, das Kernszenario des Issues).
    """
    with rls_bypass_for_read():
        return [check.key for check in cron_job_checks() if check.status == ComplianceStatus.CRITICAL]


def _check_cache() -> dict:
    """Refs #795: welches Cache-Backend ist aktiv, und antwortet es?

    Seit ``docker-compose.prod.yml`` einen Redis mitliefert, gibt es zwei
    Backends im Feld — und einen stillen Uebergang zwischen ihnen: Ist
    ``REDIS_URL`` gesetzt, das ``redis``-Paket im Image aber nicht vorhanden
    (altes ``APP_VERSION``-Image mit neuem Compose-File), faellt ``prod.py``
    bewusst auf den DatabaseCache zurueck. ``backend`` macht diesen Fall von
    aussen pruefbar; ``status`` deckt den davon unabhaengigen Fall ab, dass das
    konfigurierte Backend nicht erreichbar ist (Rate-Limit-Schutz waere dann
    wirkungslos).

    Bewusst NICHT gecacht — ein Cache-Check durch den Cache waere zirkulaer.
    """
    backend = (settings.CACHES.get("default") or {}).get("BACKEND", "")
    try:
        cache.set("health:cache_probe", "1", _DETAIL_CACHE_TTL_SECONDS)
        alive = cache.get("health:cache_probe") == "1"
    except Exception:
        logger.exception("health: cache check failed")
        alive = False
    return {"backend": backend, "status": "ok" if alive else "error"}


def _cached_detail(key: str, producer, *, cache_alive: bool):
    """``cache.get_or_set`` fuer die teuren Detail-Checks — mit Not-Aus.

    A7.2 cacht SMTP-CONNECT/Disk-Scan/Backup-Scan, damit haeufiges
    Monitoring-Polling sie nicht jedes Mal ausloest. Ist der Cache selbst
    ausgefallen, wuerde ``get_or_set`` hier werfen und der ganze
    ``/health/``-Endpoint mit 500 aussteigen — ausgerechnet in dem Moment, in
    dem das Monitoring ihn braucht. Mit Redis als Standard-Backend ist
    „Cache tot, App gesund" ein realer Zustand (der DatabaseCache fiel immer
    zusammen mit der DB aus, die schon fuer sich 503 ausgeloest hat). Also:
    Cache tot -> Check direkt ausfuehren, ungecacht. Refs #795.
    """
    if not cache_alive:
        return producer()
    return cache.get_or_set(key, producer, _DETAIL_CACHE_TTL_SECONDS)


def _is_backup_signal(value: object) -> bool:
    """Genau die Formen, die :func:`_check_backup_signal` selbst erzeugt.

    Nicht nur die Schluessel, auch die Werte (Refs #2343): ``missing``/
    ``future`` ohne Alter, ``ok``/``stale`` mit einer endlichen, nicht
    negativen Zahl, deren Schwellenurteil zum Zustand passt. Ein
    formgueltiger, aber erfundener Eintrag im geteilten Cache — unbekannter
    Zustand, negatives oder textuelles Alter, ein nicht serialisierbares
    Objekt — wird damit neu berechnet statt in die Betriebsauskunft
    durchgereicht.
    """
    if not isinstance(value, dict) or set(value) != {"state", "age_hours"}:
        return False
    state, age_hours = value["state"], value["age_hours"]
    if state in ("missing", "future"):
        return age_hours is None
    if state not in ("ok", "stale"):
        return False
    if isinstance(age_hours, bool) or not isinstance(age_hours, (int, float)):
        return False
    try:
        normalized_age_hours = float(age_hours)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(normalized_age_hours) or normalized_age_hours < 0:
        return False
    return state == ("stale" if normalized_age_hours > BACKUP_WARN_HOURS else "ok")


def _cached_backup_signal(*, cache_alive: bool) -> dict:
    """Backup-Signal gecacht — robust gegen eine fremde Cache-Form (Refs #2348).

    Refs #2342 änderte die Form dieses gecachten Werts von ``float | None``
    auf ein ``dict`` (Zustand + Alter), ohne den Schlüssel zu wechseln. Ein im
    Deploy-Fenster noch vorhandener Eintrag der Vorversion (``12.5`` bzw.
    ``None``) hätte beim Zugriff auf ``["state"]`` eine ``TypeError``
    geworfen — der detail-berechtigte Pfad von ``/health/`` wäre mit 500
    ausgestiegen. Zwei Verteidigungslinien statt einer: ein versionierter
    Schlüssel (``_BACKUP_SIGNAL_CACHE_KEY``), der einen alten Eintrag gar
    nicht erst trifft, UND ein ``isinstance``-Rückfall auf „nicht gecacht"
    für jede andere fremde Form — die Zusage „kippt nie den Request-Cycle"
    (s. ``_cached_detail``) gilt auch für einen Cache-Eintrag, den diese
    Instanz nicht selbst geschrieben hat.
    """
    if not cache_alive:
        return _check_backup_signal()
    cached = cache.get(_BACKUP_SIGNAL_CACHE_KEY)
    if _is_backup_signal(cached):
        return cached
    value = _check_backup_signal()
    cache.set(_BACKUP_SIGNAL_CACHE_KEY, value, _DETAIL_CACHE_TTL_SECONDS)
    return value


def _check_disk_free_pct() -> float | None:
    """Freier Speicher (in %) auf MEDIA_ROOT; ``None`` falls nicht messbar."""
    media_root = getattr(settings, "MEDIA_ROOT", "")
    if not media_root:
        return None
    try:
        path = Path(media_root)
        if not path.exists():
            return None
        total, _used, free = shutil.disk_usage(path)
    except (OSError, TypeError, ValueError):
        logger.warning("health: disk usage check failed for MEDIA_ROOT=%s", media_root, exc_info=True)
        return None
    if total <= 0:
        logger.warning("health: disk usage check returned invalid total for MEDIA_ROOT=%s", path)
        return None
    return round(free / total * 100, 1)


@method_decorator(ratelimit(key="ip", rate="120/m", method="GET", block=True), name="get")
class HealthView(View):
    """GET /health/ -- Liveness + (für interne/Token-Caller) Detail.

    Wenn ``CLAMAV_ENABLED`` aktiv ist, wird zusätzlich die Erreichbarkeit des
    ClamAV-Daemons geprüft. Ein nicht erreichbarer Scanner wird als Warnung
    ausgewiesen, setzt den Gesamtstatus aber nicht auf ``error`` — die harte
    Fail-closed-Entscheidung trifft der Upload-Pfad im File-Vault.

    Refs #796 (C-28): Komponenten ``smtp``, ``encryption_key``,
    ``last_backup_age_hours``, ``disk_free_pct``.

    Refs #1335: ``stale_jobs`` listet die Keys der fuenf ueberwachten
    Hintergrundjobs (Backup, Retention, Statistik-Snapshots, Breach-Detection,
    MV-Refresh — die Teilmenge aus ``cron_job_checks()``), deren letzter Lauf
    laut Compliance-Dashboard ``critical`` ist — Indikator, dass der Scheduler
    (``cron``-Sidecar bzw. Host-Cron, docs/ops-runbook.md § 3) nicht laeuft.

    Refs #795: ``cache`` nennt Backend und Erreichbarkeit des Shared Cache —
    seit der Stack einen Redis mitliefert, ist der stille Rueckfall auf den
    DatabaseCache sonst von aussen nicht erkennbar.

    C-25 (Refs #793): ``db_user_is_superuser``/``db_role_posture`` melden, ob die
    DB-Rolle der laufenden Verbindung Row Level Security umgeht (Superuser oder
    ``BYPASSRLS``). Trifft das zu, ist die Facility-Isolation still wirkungslos —
    der Gesamtstatus faellt auf ``degraded`` (auch anonym, damit
    Uptime-Monitore anschlagen), das Detail steht im Log und im Token-Payload.

    A7.1/A7.2 (Refs #1024): Die Recon-relevanten Detailfelder (``version``,
    ``smtp``, ``last_backup_age_hours``, ``disk_free_pct``, ``stale_jobs``)
    werden nur an interne/Token-Caller ausgeliefert (``_detail_authorized``); anonyme Caller
    erhalten einen schlanken, leichtgewichtigen Liveness-Payload (kein
    SMTP-Handshake / Filesystem-Scan). Die teuren Detail-Checks sind zusätzlich
    kurz gecacht, der Endpoint ist rate-limitiert.
    """

    def get(self, request):
        db_status, db_ok = _check_database()
        status = "ok" if db_ok else "error"
        http_status = 200 if db_ok else 503

        # ClamAV (Refs #524, #798) — Subsystem-Status. Fliesst in den
        # Gesamtstatus ein; das Detailfeld selbst ist aber Recon (L9/N13, s.u.).
        if getattr(settings, "CLAMAV_ENABLED", False):
            if clamav_ping():
                virus_scanner, clamav_status = "connected", "ok"
            else:
                virus_scanner, clamav_status = "unavailable", "error"
                status = _degrade(status, True)
        else:
            virus_scanner, clamav_status = "disabled", "disabled"

        # Encryption-Key Roundtrip (Refs #796) — kritisch: degradet den Status
        # (und damit HTTP 503) auch fuer die anonyme Liveness, das Detailfeld
        # bleibt aber Token-only.
        enc_status = _check_encryption_key()
        if enc_status == "error":
            # Ohne lesbare Encryption-Keys keine sichtbaren Daten — kritisch.
            status = "error"
            http_status = 503

        # C-25 (Refs #793): Umgeht die DB-Runtime-Rolle Row Level Security?
        # Der Statuswechsel ist — wie bei ClamAV/Encryption-Key — auch anonym
        # sichtbar, damit Uptime-Monitore anschlagen; das konkrete Rollen-Detail
        # bleibt hinter dem Token-Gate (L9/N13) und steht ansonsten nur im Log.
        db_user_is_superuser, db_role_bypassrls = _query_runtime_role_posture()
        if db_user_is_superuser is None:
            db_role_posture = "unknown"
        elif db_user_is_superuser or db_role_bypassrls:
            db_role_posture = "bypasses_rls"
            logger.warning(
                "health: DB-Runtime-Rolle umgeht Row Level Security "
                "(rolsuper=%s, rolbypassrls=%s) — die Facility-Isolation aus "
                "Migration 0047/0084/0085 ist wirkungslos. Pruefen mit "
                "'manage.py check_db_roles', Erstinbetriebnahme via "
                "scripts/initial-setup.sh (Refs #793/#902).",
                db_user_is_superuser,
                db_role_bypassrls,
            )
            status = _degrade(status, True)
        else:
            db_role_posture = "ok"

        # L9/N13 (Refs #1375): Anonyme Caller erhalten NUR ``status`` (+ HTTP-Code);
        # Uptime-Monitore werten beides. Die Subsystem-Status (``database``,
        # ``virus_scanner``/``clamav``, ``encryption_key``) sind Recon und liegen
        # jetzt — wie version/smtp/backup/disk/stale_jobs (A7.1, Refs #1024) —
        # hinter ``_detail_authorized``. Frueher standen sie im anonymen Payload,
        # was den Betriebszustand einzelner Subsysteme unauthentifiziert
        # preisgab (N13). Interne Monitore nutzen ``X-Health-Token``.
        payload: dict = {"status": status}

        # --- Detailfelder nur intern/Token (A7.1 + L9/N13) ---
        if _detail_authorized(request):
            payload["database"] = db_status
            payload["virus_scanner"] = virus_scanner
            payload["clamav"] = clamav_status
            payload["encryption_key"] = enc_status
            # C-25 (Refs #793): ``db_user_is_superuser`` ist das im Issue
            # geforderte Feld; ``db_role_posture`` ergaenzt es um die heutige
            # Terminologie (BYPASSRLS zaehlt genauso, ADR-020/ADR-033).
            payload["db_user_is_superuser"] = db_user_is_superuser
            payload["db_role_posture"] = db_role_posture
            payload["version"] = os.environ.get("APP_VERSION", "dev")

            # Cache-Backend + Liveness (Refs #795). MUSS vor den gecachten
            # Checks stehen: das Ergebnis entscheidet, ob die ueberhaupt ueber
            # den Cache laufen duerfen.
            cache_payload = _check_cache()
            cache_alive = cache_payload["status"] == "ok"
            payload["cache"] = cache_payload
            # Ohne Cache zaehlt django-ratelimit nicht mehr — der
            # Bruteforce-Schutz auf Login/Sudo ist dann faktisch aus.
            status = _degrade(status, not cache_alive)

            # SMTP-CONNECT (Refs #796), gecacht (A7.2).
            smtp_payload = _cached_detail("health:smtp", _check_smtp, cache_alive=cache_alive)
            payload["smtp"] = smtp_payload
            status = _degrade(status, smtp_payload["status"] == "unreachable")

            # Backup-Alter (Refs #796), gecacht (A7.2; Refs #2348: eigener,
            # versionierter Schluessel + Formpruefung, siehe _cached_backup_signal).
            backup_signal = _cached_backup_signal(cache_alive=cache_alive)
            backup_status = backup_signal["state"]
            payload["last_backup_age_hours"] = backup_signal["age_hours"]
            backup_required = bool(getattr(settings, "HEALTH_BACKUP_REQUIRED", True))
            payload["backup_required"] = backup_required
            payload["backup"] = backup_status
            # Refs #2342: ``future`` degradet immer — ein nicht messbares Alter
            # ist kein gesunder Zustand, unabhaengig von HEALTH_BACKUP_REQUIRED
            # (die Option betrifft Installationen ohne Welt-B-Backups, nicht
            # eine kaputte Systemuhr).
            status = _degrade(
                status,
                (backup_required and backup_status == "missing") or backup_status in {"stale", "future"},
            )

            # Disk-Frei (Refs #796), gecacht (A7.2).
            disk_pct = _cached_detail("health:disk_free_pct", _check_disk_free_pct, cache_alive=cache_alive)
            payload["disk_free_pct"] = disk_pct
            disk_required = bool(getattr(settings, "HEALTH_DISK_REQUIRED", True))
            payload["disk_required"] = disk_required
            disk_status = "unknown" if disk_pct is None else ("low" if disk_pct < DISK_WARN_PCT else "ok")
            payload["disk"] = disk_status
            status = _degrade(
                status,
                (disk_required and disk_status == "unknown") or disk_status == "low",
            )

            # Scheduler-Staleness (Refs #1335), gecacht (A7.2). ``unknown`` degradet
            # bewusst nicht (frische Installation), nur bestaetigtes ``critical``.
            stale_jobs = _cached_detail("health:stale_jobs", _check_stale_jobs, cache_alive=cache_alive)
            payload["stale_jobs"] = stale_jobs
            status = _degrade(status, bool(stale_jobs))

        payload["status"] = status
        return JsonResponse(payload, status=http_status)
