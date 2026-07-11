"""Breach-Detection-Service fuer DSGVO Art. 33/34 (Refs #685).

Heuristik-basierte Detection: scannt den AuditLog nach Mustern, die auf
einen Datenschutzvorfall hindeuten, und schreibt fuer jedes Finding
einen ``AuditLog.Action.SECURITY_VIOLATION``-Eintrag plus optionalen
Webhook-Aufruf. Das Ops-Runbook beschreibt den manuellen 72h-Prozess
zur Aufsichtsbehoerde — der Code kann ihn nicht ersetzen, aber er
sichert die Detection-Spur.

Heuristiken (mit per-Setting konfigurierbaren Schwellen). Die per-User- und
facility-weiten Heuristiken laufen jeweils ueber ZWEI Fenster: das kurze
``BREACH_DETECTION_WINDOW_MINUTES``-Fenster (Default 60 min) und ein
sekundaeres Langzeitfenster ``BREACH_DETECTION_LONG_WINDOW_MINUTES`` (Default
24h) mit hoeheren Schwellen gegen low-and-slow-Muster (Refs #1368). Pro Subjekt
entsteht hoechstens EIN Finding je ``kind`` (kurzes Fenster hat Vorrang), damit
die Dedup sauber bleibt.

1. **Failed-Login-Burst** — mehr als ``BREACH_FAILED_LOGIN_THRESHOLD``
   ``LOGIN_FAILED``-Eintraege fuer denselben (bekannten) User. Heuristik fuer
   Brute-Force-Versuche, die durch das Login-Lockout schluepfen.

2. **Mass-Export** — mehr als ``BREACH_EXPORT_THRESHOLD`` ``EXPORT``-
   Eintraege durch denselben User. Heuristik fuer Insider-Datendiebstahl.

3. **Mass-Delete** — mehr als ``BREACH_DELETE_THRESHOLD`` ``DELETE``-
   Eintraege facility-weit. Heuristik fuer Account-Kompromittierung mit
   Schadens-Absicht.

4. **Mass-Client-Destruction** (Refs #1368) — mehr als
   ``BREACH_CLIENT_DESTRUCTION_THRESHOLD`` ``CLIENT_SOFT_DELETED`` /
   ``CLIENT_ANONYMIZED`` / ``DELETION_APPROVED``-Eintraege facility-weit. Diese
   schreiben KEIN ``Action.DELETE`` und blieben fuer (3) unsichtbar.

5. **Distributed-Login-Attack** (Refs #1372) — Fehlversuche gegen EINEN Account
   von mehr als ``BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD`` distinkten Quell-IPs
   (Victim-Lockout-/Distributed-Bruteforce-Signatur, Monitor-only).

6. **Anonymous-Login-Bursts** (Refs #1368, installationsweit) — Bursts gegen
   UNBEKANNTE Usernames (``user IS NULL``): pro Quell-IP
   (``BREACH_ANON_LOGIN_IP_THRESHOLD``) und Gesamtvolumen
   (``BREACH_ANON_LOGIN_TOTAL_THRESHOLD``). Diese Eintraege haben weder User-
   noch Facility-Zuordnung und sind daher fuer (1)-(5) unsichtbar; sie werden
   ueber ``run_system_detections`` erfasst und facility-los protokolliert.

Deduplikation: Vor dem Anlegen eines neuen SECURITY_VIOLATION-Eintrags
prueft die Funktion, ob fuer denselben Tatbestand (Action-Subtype +
betroffener Subject + 24h-Fenster) bereits ein Eintrag existiert.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone

from core.models import AuditLog
from core.services.audit import audit_security_violation

logger = logging.getLogger(__name__)


def _validate_webhook_url(url: str) -> str:
    """Refs #772 — SSRF-Schutz fuer ``BREACH_NOTIFICATION_WEBHOOK_URL``.

    Gibt bei Erfolg die aufgeloeste, oeffentlich-routbare IP als String zurueck
    — diese wird in ``_post_webhook`` gepinnt, damit zwischen Pruefung und
    Verbindungsaufbau kein DNS-Rebinding stattfinden kann (A5.2, Refs #1016).

    Wirft ``ValueError``, wenn:

    - Schema nicht ``https`` ist (kein ``http``, ``file``, ``gopher``, ``ftp``);
    - der Hostname nicht aufloesbar ist;
    - die aufgeloeste IP nicht oeffentlich-routbar ist (``not is_global``):
      Cloud-Metadata ``169.254.169.254``, RFC1918, ``127.0.0.0/8``, multicast,
      reserved und CGNAT ``100.64.0.0/10`` (RFC 6598).

    Die DNS-Aufloesung kostet ~50 ms, schliesst aber genau die SSRF-Wege,
    die die operatorseitige URL-Konfiguration sonst offen liesse.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Webhook scheme {parsed.scheme!r} not allowed (https only).")
    if not parsed.hostname:
        raise ValueError(f"Webhook URL has no hostname: {url!r}")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
    except (socket.gaierror, ValueError) as exc:
        raise ValueError(f"Webhook host unresolvable: {parsed.hostname!r}") from exc
    # A5.3 (Refs #1024 / #1016): nur oeffentlich-routbare Ziele zulassen.
    # ``not is_global`` deckt RFC1918, loopback, link-local, multicast, reserved
    # UND CGNAT (100.64.0.0/10, RFC 6598) ab — letzteres liess die fruehere
    # Einzel-Flag-Kette durch.
    if not ip.is_global:
        raise ValueError(f"Webhook target {ip} is not globally routable — refused (SSRF).")
    return str(ip)


def _get_threshold(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _window_cutoff(minutes: int):
    return timezone.now() - timedelta(minutes=minutes)


def _finding(kind: str, user_id, count: int, threshold: int, window_minutes: int, **extra) -> dict:
    finding = {
        "kind": kind,
        "user_id": user_id,
        "count": count,
        "threshold": threshold,
        "window_minutes": window_minutes,
    }
    finding.update(extra)
    return finding


def _classify_windowed(
    *,
    kind: str,
    user_id,
    short_count: int,
    long_count: int,
    short_threshold: int,
    long_threshold: int,
    short_minutes: int,
    long_minutes: int,
) -> dict | None:
    """Ein Finding pro Subjekt: das kurze Fenster hat Vorrang, sonst greift das
    sekundaere Langzeitfenster (Refs #1368 — low-and-slow unter der 60-min-
    Schwelle). Nie zwei Findings desselben ``kind`` fuers selbe Subjekt, damit die
    24h-Dedup (kind, user) sauber bleibt und kein Doppelalarm entsteht."""
    if short_count >= short_threshold:
        return _finding(kind, user_id, short_count, short_threshold, short_minutes)
    if long_count >= long_threshold:
        return _finding(kind, user_id, long_count, long_threshold, long_minutes)
    return None


def _detect_user_action_burst(facility, *, action, kind, short_threshold, long_threshold) -> list[dict]:
    """Pro-(bekanntem-)User-Burst einer Action ueber zwei Fenster (kurz + Langzeit).

    Eine einzige Aggregat-Query zaehlt beide Fenster (bedingtes ``Count`` ueber
    das kurze Fenster als Teilmenge des Langzeitfensters)."""
    short_minutes = _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)
    long_minutes = _get_threshold("BREACH_DETECTION_LONG_WINDOW_MINUTES", 1440)
    short_cutoff = _window_cutoff(short_minutes)
    rows = (
        AuditLog.objects.filter(
            facility=facility,
            action=action,
            timestamp__gte=_window_cutoff(long_minutes),
        )
        .values("user")
        .annotate(
            long_c=Count("id"),
            short_c=Count("id", filter=Q(timestamp__gte=short_cutoff)),
        )
    )
    findings = []
    for row in rows:
        if row["user"] is None:
            continue  # unbekannte Usernames erfasst detect_anonymous_login_bursts
        finding = _classify_windowed(
            kind=kind,
            user_id=row["user"],
            short_count=row["short_c"],
            long_count=row["long_c"],
            short_threshold=short_threshold,
            long_threshold=long_threshold,
            short_minutes=short_minutes,
            long_minutes=long_minutes,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _detect_facility_action_burst(facility, *, actions, kind, short_threshold, long_threshold) -> list[dict]:
    """Facility-weiter Burst einer Action-Menge ueber zwei Fenster (kurz + Langzeit)."""
    short_minutes = _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)
    long_minutes = _get_threshold("BREACH_DETECTION_LONG_WINDOW_MINUTES", 1440)
    short_cutoff = _window_cutoff(short_minutes)
    agg = AuditLog.objects.filter(
        facility=facility,
        action__in=actions,
        timestamp__gte=_window_cutoff(long_minutes),
    ).aggregate(
        long_c=Count("id"),
        short_c=Count("id", filter=Q(timestamp__gte=short_cutoff)),
    )
    finding = _classify_windowed(
        kind=kind,
        user_id=None,  # facility-weit
        short_count=agg["short_c"] or 0,
        long_count=agg["long_c"] or 0,
        short_threshold=short_threshold,
        long_threshold=long_threshold,
        short_minutes=short_minutes,
        long_minutes=long_minutes,
    )
    return [finding] if finding is not None else []


def detect_failed_login_burst(facility) -> list[dict]:
    """Mehr als N failed-Login-Versuche pro (bekanntem) User im Fenster.

    Zwei Fenster (kurz + sekundaeres Langzeitfenster gegen low-and-slow,
    Refs #1368). ``user IS NULL`` (unbekannte Usernames) faellt hier bewusst
    raus — diese Bursts erfasst ``detect_anonymous_login_bursts``."""
    return _detect_user_action_burst(
        facility,
        action=AuditLog.Action.LOGIN_FAILED,
        kind="failed_login_burst",
        short_threshold=_get_threshold("BREACH_FAILED_LOGIN_THRESHOLD", 20),
        long_threshold=_get_threshold("BREACH_FAILED_LOGIN_THRESHOLD_LONG", 60),
    )


def detect_mass_export(facility) -> list[dict]:
    """Mehr als N EXPORT-Aktionen pro User im Fenster (kurz + Langzeit)."""
    return _detect_user_action_burst(
        facility,
        action=AuditLog.Action.EXPORT,
        kind="mass_export",
        short_threshold=_get_threshold("BREACH_EXPORT_THRESHOLD", 10),
        long_threshold=_get_threshold("BREACH_EXPORT_THRESHOLD_LONG", 30),
    )


def detect_mass_delete(facility) -> list[dict]:
    """Mehr als N DELETE-Aktionen facility-weit im Fenster (kurz + Langzeit)."""
    return _detect_facility_action_burst(
        facility,
        actions=(AuditLog.Action.DELETE,),
        kind="mass_delete",
        short_threshold=_get_threshold("BREACH_DELETE_THRESHOLD", 50),
        long_threshold=_get_threshold("BREACH_DELETE_THRESHOLD_LONG", 150),
    )


def detect_mass_client_destruction(facility) -> list[dict]:
    """Massen-Client-Destruktion facility-weit (Refs #1368).

    Zaehlt ``CLIENT_SOFT_DELETED`` (Papierkorb), ``CLIENT_ANONYMIZED`` und
    ``DELETION_APPROVED`` gemeinsam — diese schreiben KEIN ``Action.DELETE`` und
    blieben daher fuer ``detect_mass_delete`` unsichtbar. Eigene, niedrigere
    Schwelle, weil gezieltes Vernichten von Personendaten schwerer wiegt als
    generische Loeschungen (kurz + Langzeit-Fenster)."""
    return _detect_facility_action_burst(
        facility,
        actions=(
            AuditLog.Action.CLIENT_SOFT_DELETED,
            AuditLog.Action.CLIENT_ANONYMIZED,
            AuditLog.Action.DELETION_APPROVED,
        ),
        kind="mass_client_destruction",
        short_threshold=_get_threshold("BREACH_CLIENT_DESTRUCTION_THRESHOLD", 20),
        long_threshold=_get_threshold("BREACH_CLIENT_DESTRUCTION_THRESHOLD_LONG", 60),
    )


def detect_distributed_login_attack(facility) -> list[dict]:
    """Fehlversuche gegen EINEN Account von VIELEN distinkten Quell-IPs (Refs #1372).

    Victim-Lockout-/Distributed-Bruteforce-Signatur: der bewusst beibehaltene
    10/h-Username-Ratelimit (Anti-Botnet, #598) wird NICHT verschaerft, aber die
    Signatur wird als Monitor-only-Alarm sichtbar gemacht. Gezaehlt wird die
    Anzahl distinkter Quell-IPs pro (bekanntem) User im Fenster."""
    ip_threshold = _get_threshold("BREACH_DISTRIBUTED_LOGIN_IP_THRESHOLD", 10)
    minutes = _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)
    rows = (
        AuditLog.objects.filter(
            facility=facility,
            action=AuditLog.Action.LOGIN_FAILED,
            timestamp__gte=_window_cutoff(minutes),
        )
        .values("user")
        .annotate(distinct_ips=Count("ip_address", distinct=True))
        .filter(distinct_ips__gte=ip_threshold)
    )
    findings = []
    for row in rows:
        if row["user"] is None:
            continue
        findings.append(_finding("distributed_login_attack", row["user"], row["distinct_ips"], ip_threshold, minutes))
    return findings


def detect_anonymous_login_bursts() -> list[dict]:
    """Installationsweite Bursts fehlgeschlagener Logins gegen UNBEKANNTE
    Usernames (``user IS NULL``) — Credential-Stuffing/Enumeration (Refs #1368).

    Diese Eintraege haben weder User- noch Facility-Zuordnung
    (``on_user_login_failed`` schreibt ``facility=None`` fuer unbekannte
    Usernames) und sind daher fuer die per-Facility-Heuristiken unsichtbar.
    Zwei Signale im kurzen Fenster:

    - **pro Quell-IP** (``BREACH_ANON_LOGIN_IP_THRESHOLD``): klassisches
      Enumerieren von einer Herkunft.
    - **Gesamtvolumen** (``BREACH_ANON_LOGIN_TOTAL_THRESHOLD``): verteiltes
      Enumerieren, bei dem jede einzelne IP unter der per-IP-Schwelle bleibt.

    Facility-los; Findings werden ueber ``record_system_finding`` als
    ``facility=None``-SECURITY_VIOLATION protokolliert."""
    ip_threshold = _get_threshold("BREACH_ANON_LOGIN_IP_THRESHOLD", 20)
    total_threshold = _get_threshold("BREACH_ANON_LOGIN_TOTAL_THRESHOLD", 100)
    minutes = _get_threshold("BREACH_DETECTION_WINDOW_MINUTES", 60)
    # Exakt die ``on_user_login_failed``-Semantik fuer unbekannte Usernames:
    # weder User- NOCH Facility-Zuordnung. ``facility__isnull=True`` schliesst
    # facility-gescopte Zeilen aus, deren User spaeter geloescht wurde
    # (``on_delete=SET_NULL`` -> user=NULL bei gesetzter Facility) — die gehoeren
    # NICHT in den anonymen Burst.
    base = AuditLog.objects.filter(
        action=AuditLog.Action.LOGIN_FAILED,
        user__isnull=True,
        facility__isnull=True,
        timestamp__gte=_window_cutoff(minutes),
    )
    findings = []
    per_ip = (
        base.exclude(ip_address__isnull=True).values("ip_address").annotate(c=Count("id")).filter(c__gte=ip_threshold)
    )
    for row in per_ip:
        findings.append(
            _finding("anonymous_login_burst", None, row["c"], ip_threshold, minutes, source_ip=row["ip_address"])
        )
    total = base.count()
    if total >= total_threshold:
        findings.append(_finding("anonymous_login_flood", None, total, total_threshold, minutes, source_ip=None))
    return findings


def _already_reported(facility, finding: dict) -> bool:
    """Pruefe, ob fuer dasselbe Finding in den letzten 24h schon ein
    SECURITY_VIOLATION-Eintrag steht — Deduplikation."""
    last_24h = timezone.now() - timedelta(hours=24)
    qs = AuditLog.objects.filter(
        facility=facility,
        action=AuditLog.Action.SECURITY_VIOLATION,
        timestamp__gte=last_24h,
    )
    if finding["user_id"] is not None:
        qs = qs.filter(user_id=finding["user_id"])
    return any((entry.detail or {}).get("kind") == finding["kind"] for entry in qs.iterator())


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """A5.2 (Refs #1024 / #1016): Redirects beim Webhook-POST nicht folgen.

    ``_validate_webhook_url`` prueft nur die initiale URL. Folgte urllib einem
    3xx-Redirect, koennte ein (kompromittierter/boesartiger) Webhook-Host auf
    eine interne Adresse (Cloud-Metadata, loopback) umleiten, die nie validiert
    wurde. ``redirect_request`` -> ``None`` unterbindet das Folgen; die
    3xx-Response wird stattdessen zurueckgegeben.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """A5.2 (Refs #1016): verbindet den Socket zur in der Validierung
    aufgeloesten IP, fuehrt den TLS-Handshake aber gegen den Hostnamen (SNI +
    Zertifikatspruefung). So kann zwischen ``_validate_webhook_url`` und dem
    Connect kein DNS-Rebinding auf eine interne Adresse (Cloud-Metadata,
    loopback) erfolgen. Nur direkter Aufbau (Webhooks laufen ohne Proxy)."""

    def __init__(self, host, *, pinned_ip, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib-Handler, der jede HTTPS-Verbindung auf die validierte IP pinnt."""

    def __init__(self, pinned_ip: str):
        super().__init__()
        self._pinned_ip = pinned_ip

    def https_open(self, req):
        return self.do_open(self._build_connection, req, context=self._context)

    def _build_connection(self, host, **kwargs):
        return _PinnedHTTPSConnection(host, pinned_ip=self._pinned_ip, **kwargs)


def _post_webhook(payload: dict) -> bool:
    """Optional: Webhook-Notification bei aktiver ``BREACH_NOTIFICATION_WEBHOOK_URL``.

    Refs #772 — vor jedem Aufruf wird die URL durch ``_validate_webhook_url``
    geprueft (https-only + public-IP). Verstoesse werden geloggt und
    fuehren zu ``return False`` statt einem stillen Aufruf gegen Cloud-
    Metadata oder den loopback. Redirects werden via ``_NoRedirectHandler``
    nicht gefolgt (A5.2).
    """
    url = getattr(settings, "BREACH_NOTIFICATION_WEBHOOK_URL", None)
    if not url:
        return False
    try:
        pinned_ip = _validate_webhook_url(url)
    except ValueError as exc:
        logger.warning("breach_webhook_url_rejected: %s", exc)
        return False
    try:
        req = urllib.request.Request(  # noqa: S310 — _validate_webhook_url SSRF-haerten
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # A5.2: Opener mit gepinnter IP (kein DNS-Rebinding) + ohne Redirect-Folgen.
        opener = urllib.request.build_opener(_NoRedirectHandler, _PinnedHTTPSHandler(pinned_ip))
        opener.open(req, timeout=5)  # noqa: S310 — validate + IP-Pin + NoRedirect gegen SSRF
        return True
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("breach_webhook_failed: %s", exc)
        return False


def record_finding(facility, finding: dict) -> AuditLog | None:
    """Schreibt SECURITY_VIOLATION-AuditLog + Webhook (sofern konfiguriert).

    Idempotent: deduplicate ueber 24h gegen denselben (kind, user)-
    Tatbestand. Returns None, wenn bereits gemeldet.

    Refs #901: nutzt den typed ``audit_security_violation``-Helper. Die
    User-Referenz wird ueber einen leichten ``filter(pk=…).first()``-Lookup
    aufgeloest — Findings sind selten genug, dass der Round-Trip
    unproblematisch ist; der Helper akzeptiert ``user=None``, falls der
    User zwischenzeitlich geloescht wurde.
    """
    from core.models import User

    if _already_reported(facility, finding):
        return None
    user = User.objects.filter(pk=finding["user_id"]).first()
    entry = audit_security_violation(
        facility,
        user,
        target_type="Facility",
        target_id=facility.pk,
        reason=finding["kind"],
        kind=finding["kind"],
        count=finding["count"],
        threshold=finding["threshold"],
        window_minutes=finding["window_minutes"],
    )
    _post_webhook(
        {
            "facility": facility.name,
            "kind": finding["kind"],
            "count": finding["count"],
            "threshold": finding["threshold"],
            "window_minutes": finding["window_minutes"],
            "user_id": finding["user_id"],
            "audit_id": str(entry.pk),
            "timestamp": entry.timestamp.isoformat(),
        }
    )
    return entry


def _already_reported_system(finding: dict) -> bool:
    """Dedup fuer installationsweite (``facility=None``) Findings — analog zu
    ``_already_reported``, aber facility-los und mit der Quell-IP als
    zusaetzlichem Diskriminator, damit Bursts verschiedener IPs sich nicht
    gegenseitig unterdruecken. Die 24h-/kind-Dedup-Garantie bleibt identisch."""
    last_24h = timezone.now() - timedelta(hours=24)
    qs = AuditLog.objects.filter(
        facility__isnull=True,
        action=AuditLog.Action.SECURITY_VIOLATION,
        timestamp__gte=last_24h,
    )
    source_ip = finding.get("source_ip")
    for entry in qs.iterator():
        detail = entry.detail or {}
        if detail.get("kind") == finding["kind"] and detail.get("source_ip") == source_ip:
            return True
    return False


def record_system_finding(finding: dict) -> AuditLog | None:
    """Schreibt einen installationsweiten (``facility=None``) SECURITY_VIOLATION-
    Eintrag + optionalen Webhook fuer facility-lose Findings (anonyme
    Login-Bursts, Refs #1368).

    Bewusst getrennt von ``record_finding``: Bursts gegen unbekannte Usernames
    haben weder Facility noch User und gehoeren als System-Event mit
    ``facility=None`` protokolliert. Die SSRF-gehaertete ``_post_webhook``-
    Mechanik wird unveraendert wiederverwendet. Idempotent ueber 24h
    (kind + Quell-IP)."""
    if _already_reported_system(finding):
        return None
    entry = audit_security_violation(
        None,
        None,
        target_type="System",
        target_id=None,
        reason=finding["kind"],
        kind=finding["kind"],
        count=finding["count"],
        threshold=finding["threshold"],
        window_minutes=finding["window_minutes"],
        source_ip=finding.get("source_ip"),
    )
    _post_webhook(
        {
            "facility": None,
            "kind": finding["kind"],
            "count": finding["count"],
            "threshold": finding["threshold"],
            "window_minutes": finding["window_minutes"],
            "user_id": None,
            "source_ip": finding.get("source_ip"),
            "audit_id": str(entry.pk),
            "timestamp": entry.timestamp.isoformat(),
        }
    )
    return entry


def run_all_detections(facility) -> list[AuditLog]:
    """Fuehrt alle facility-gescopten Heuristiken aus, schreibt Findings, gibt
    geschriebene Eintraege zurueck. Die installationsweiten (facility-losen)
    Heuristiken laufen separat ueber ``run_system_detections``."""
    new_entries: list[AuditLog] = []
    for detector in (
        detect_failed_login_burst,
        detect_mass_export,
        detect_mass_delete,
        detect_mass_client_destruction,
        detect_distributed_login_attack,
    ):
        for finding in detector(facility):
            entry = record_finding(facility, finding)
            if entry is not None:
                new_entries.append(entry)
    return new_entries


def _run_system_detections() -> list[AuditLog]:
    """Reine Detection-Schleife (ohne GUC-Handling) — der ``facility=NULL``-Read
    UND die facility-losen Writes muessen im ``app.is_super_admin``-Scope laufen
    (siehe ``run_system_detections``)."""
    new_entries: list[AuditLog] = []
    for finding in detect_anonymous_login_bursts():
        entry = record_system_finding(finding)
        if entry is not None:
            new_entries.append(entry)
    return new_entries


def run_system_detections() -> list[AuditLog]:
    """Fuehrt die installationsweiten (facility-losen) Heuristiken aus (Refs #1368).

    Einmal pro Scan-Lauf, NICHT je Facility. Setzt den ``app.is_super_admin``-
    GUC, damit die ``facility=None``-AuditLog-Zeilen (Fehlversuche gegen
    unbekannte Usernames) unter RLS sichtbar sind (Read) und der facility-lose
    SECURITY_VIOLATION geschrieben werden kann — dieselbe sanktionierte Bypass-
    Mechanik wie im ``/system/``-Bereich (Migration 0085). Unter einer
    SUPERUSER/BYPASSRLS-Cron-Rolle ist das GUC-Setzen ein harmloser No-Op.

    Der Bypass wird — analog zu ``chain._chain_read_visibility`` — TRANSAKTIONS-
    LOKAL (``SET LOCAL``/``is_local=true``) innerhalb einer ``transaction.atomic``
    gesetzt und im ``finally`` auf den Vorwert zurueckgesetzt. So bleibt das
    Superuser-GUC NICHT auf einer (gepoolten) Connection stehen — ``run_system_
    detections`` ist eine exportierte Funktion und koennte kuenftig auf einer
    Web-/Test-Connection laufen (latenter Privilege-Leak sonst). Read UND Writes
    laufen bewusst INNERHALB des GUC-Scopes, sonst saehen die ``facility=NULL``-
    Reads unter RLS nichts."""
    if connection.vendor != "postgresql":
        # Ohne Postgres kein RLS/GUC — direkt ausfuehren.
        return _run_system_detections()

    with connection.cursor() as cur:
        cur.execute("SELECT current_setting('app.is_super_admin', true)")
        previous = cur.fetchone()[0] or ""
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        try:
            return _run_system_detections()
        finally:
            # is_local=true: gilt fuer die laufende Transaktion; wird beim Commit
            # ohnehin verworfen — der explizite Restore spiegelt das chain.py-
            # Save/Restore-Muster und haelt den Vorwert waehrend der Transaktion.
            cur.execute("SELECT set_config('app.is_super_admin', %s, true)", [previous])
