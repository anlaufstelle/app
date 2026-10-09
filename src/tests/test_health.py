"""Tests für den Health-Endpoint.

A7.1/A7.2 (Refs #1024): ``/health/`` liefert anonymen Callern nur einen
schlanken Liveness-Payload (Status-Indikatoren, keine Recon-Details). Die
Detailfelder ``version``, ``smtp``, ``last_backup_age_hours`` und
``disk_free_pct`` gibt es nur für interne/Token-Caller (Header
``X-Health-Token`` == ``HEALTH_DETAIL_TOKEN``) oder authentifizierte Sessions.
"""

import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog
from core.services.compliance import ComplianceCheck, ComplianceStatus
from core.services.system import bypass_replication_triggers
from core.views.health import _check_stale_jobs, _query_runtime_role_posture
from tests.test_rls_functional import (  # noqa: F401
    as_rls_role,
    rls_test_role,
)

DETAIL_TOKEN = "test-health-detail-token"  # noqa: S105 — Test-Fixture, kein echtes Secret

# C-25 (Refs #793): Pfad für den Monkeypatch der Rollen-Posture-Abfrage. Bewusst
# die Modul-Referenz in ``core.views.health`` — NICHT ``django.db.connection``
# (Proxy-Objekt; ein Instanz-Attribut darauf überschattet den Unblocker und
# vergiftet nachfolgende Tests mit wandernden Teardown-Fehlern).
_POSTURE_TARGET = "core.views.health._query_runtime_role_posture"


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """A7.2: die Detail-Checks (smtp/backup/disk) sind gecacht — pro Test leeren,
    damit Mocks nicht über Testgrenzen hinweg gecachte Werte sehen."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _measurable_media_root(settings, tmp_path):
    """Health-Tests messen Disk auf einem existierenden, isolierten Pfad."""
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture(autouse=True)
def _runtime_role_posture_secure():
    """C-25 (Refs #793): Für alle Tests eine RLS-treue Runtime-Rolle vortäuschen.

    Die *lokale* Test-/CI-Postgres-Rolle ist in aller Regel ein Superuser (das
    offizielle ``postgres``-Image legt den per ``POSTGRES_USER`` angelegten Login
    als Superuser an; die CI-Service-Container in ``.github/workflows/test.yml``
    ebenso). Ohne diesen Stub würde der neue Posture-Check den Gesamtstatus in
    JEDEM Health-Test auf ``degraded`` ziehen und dutzende bestehende
    ``status == "ok"``-Assertions kippen — obwohl auf den echten Deploy-Zielen
    (Drei-Rollen-Modell, ADR-020/ADR-033) genau der geprüfte Zustand herrscht.

    Die Tests, die den unsicheren Fall abbilden, patchen dieselbe Modul-Referenz
    innerhalb des Tests erneut (innerer ``patch`` gewinnt).
    """
    with patch(_POSTURE_TARGET, return_value=(False, False)):
        yield


@pytest.fixture
def detail_token(settings):
    """Setzt das Detail-Token und gibt es zurück (für Detail-Requests)."""
    settings.HEALTH_DETAIL_TOKEN = DETAIL_TOKEN
    return DETAIL_TOKEN


#: Pflichttabellen der Satzkennung (Vertrag aus ``scripts/ops/lib/backup-set.sh``).
_BACKUP_SET_TABLES = (
    "core_facility",
    "core_client",
    "core_event",
    "core_workitem",
    "core_auditlog",
    "core_attachment",
)


def _publish_backup_set(backup_dir, timestamp="2026-01-01_020000"):
    """Ein vollstaendig veroeffentlichter Backup-Satz (nur Datenbank).

    Refs #2291: Seit der Satzpruefung zaehlt ein blosser ``*.sql.gz.enc``-Dump
    NICHT mehr als Sicherung — ohne signiertes Manifest liefert
    ``last_backup_info`` ``None``. Die beiden Backup-Alter-Faelle unten legten
    frueher nur den Dump an und kamen allein deshalb durch, weil ein frueher
    gelaufener Test noch einen Wert im geteilten Detail-Cache hinterlassen
    hatte (isoliert waren sie rot). Refs #2342.
    """
    dump = backup_dir / f"anlaufstelle_{timestamp}.sql.gz.enc"
    dump.write_bytes(b"dump")
    dump.with_name(f"{dump.name}.hmac").write_text("a" * 64 + "\n", encoding="utf-8")
    manifest = backup_dir / f"anlaufstelle_{timestamp}.manifest"
    manifest.write_text(
        "".join(f"table_{table}=1\n" for table in _BACKUP_SET_TABLES)
        + f"backup_timestamp={timestamp}\n"
        + f"db_sha256={'b' * 64}\n",
        encoding="utf-8",
    )
    manifest.with_name(f"{manifest.name}.hmac").write_text("c" * 64 + "\n", encoding="utf-8")
    return manifest


def _detail_get(**extra):
    """GET /health/ mit gültigem Detail-Token -> voller Payload."""
    return Client().get("/health/", HTTP_X_HEALTH_TOKEN=DETAIL_TOKEN, **extra)


def _capture_health_warnings(caplog, call) -> list[str]:
    """Fuehrt ``call()`` aus und liefert die WARNING-Meldungen des Health-Loggers.

    Der ``core``-Logger hat ``propagate=False`` (``settings/base.py``) — der
    ``caplog``-Handler muss daher explizit an den Modul-Logger gehaengt werden.
    Weil fremde Records im Capture-Puffer liegen koennen, wird anschliessend
    gezielt nach Logger und Mindest-Level gefiltert."""
    module_logger = logging.getLogger("core.views.health")
    caplog.set_level(logging.WARNING)
    module_logger.addHandler(caplog.handler)
    try:
        call()
    finally:
        module_logger.removeHandler(caplog.handler)
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "core.views.health" and record.levelno >= logging.WARNING
    ]


@pytest.mark.django_db
class TestHealthLiveness:
    """Anonyme Caller: schlanke Liveness, keine Recon-Details (A7.1)."""

    def test_returns_200_and_json(self):
        response = Client().get("/health/")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_detail_caller_sees_database_connected(self, detail_token):
        """``database`` ist jetzt Token-only (L9/N13, Refs #1375)."""
        assert _detail_get().json()["database"] == "connected"

    def test_no_auth_required(self):
        assert Client().get("/health/").status_code == 200

    def test_content_type_is_json(self):
        assert Client().get("/health/")["Content-Type"] == "application/json"

    def test_anonymous_payload_omits_recon_detail_fields(self):
        """version, smtp, last_backup_age_hours, disk_free_pct nur intern/Token.

        L9/N13 (Refs #1375): zusaetzlich database/virus_scanner/clamav/
        encryption_key — die Subsystem-Status sind Recon und gehoeren hinter das
        Token-Gate (Anforderungsaenderung ggue. #1024 A7.1, s.u.)."""
        data = Client().get("/health/").json()
        for field in (
            "version",
            "smtp",
            "last_backup_age_hours",
            "disk_free_pct",
            "disk",
            "disk_required",
            "database",
            "virus_scanner",
            "clamav",
            "encryption_key",
        ):
            assert field not in data, f"{field} darf nicht an anonyme Caller geliefert werden (A7.1/N13)"

    def test_anonymous_payload_is_status_only(self):
        """L9/N13 (Refs #1375): anonymer Liveness-Payload enthaelt NUR ``status``
        (Uptime-Monitore werten HTTP-Code + status; Subsystem-Details brauchen
        das Token)."""
        data = Client().get("/health/").json()
        assert set(data.keys()) == {"status"}
        assert data["status"] == "ok"

    def test_detail_caller_keeps_health_indicators(self, detail_token):
        """status/database/virus_scanner/clamav/encryption_key bleiben fuer
        interne/Token-Caller sichtbar (Monitoring)."""
        data = _detail_get().json()
        for field in ("status", "database", "virus_scanner", "clamav", "encryption_key"):
            assert field in data

    def test_virus_scanner_disabled_by_default(self, detail_token):
        """In Tests ist CLAMAV_ENABLED=False — der Scanner gilt als ``disabled``.

        L9/N13 (Refs #1375): ``virus_scanner`` ist jetzt Token-only."""
        assert _detail_get().json()["virus_scanner"] == "disabled"

    def test_virus_scanner_connected_when_reachable(self, settings, detail_token):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            data = _detail_get().json()
        assert data["virus_scanner"] == "connected"
        assert data["status"] == "ok"

    def test_virus_scanner_unavailable_degrades_status(self, settings, detail_token):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            data = _detail_get().json()
        assert data["virus_scanner"] == "unavailable"
        assert data["status"] == "degraded"

    def test_virus_scanner_unavailable_degrades_anonymous_status(self, settings):
        """Der Gesamtstatus (nicht das Detailfeld) degradet auch anonym — der
        HTTP-Code/status bleibt fuer Uptime-Monitore aussagekraeftig."""
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            data = Client().get("/health/").json()
        assert data["status"] == "degraded"
        assert "virus_scanner" not in data

    def test_clamav_alias_disabled(self, detail_token):
        """Refs #798 (C-30): ``clamav``-Alias — die Doku nutzt ``jq '.clamav'``
        und erwartet ``ok``/``error``/``disabled``; jetzt Token-only (L9/N13)."""
        assert _detail_get().json()["clamav"] == "disabled"

    def test_clamav_alias_ok_when_connected(self, settings, detail_token):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=True):
            assert _detail_get().json()["clamav"] == "ok"

    def test_clamav_alias_error_when_unavailable(self, settings, detail_token):
        settings.CLAMAV_ENABLED = True
        with patch("core.views.health.clamav_ping", return_value=False):
            assert _detail_get().json()["clamav"] == "error"

    def test_encryption_key_ok_in_test_env(self, detail_token):
        assert _detail_get().json()["encryption_key"] == "ok"

    def test_encryption_key_error_is_critical(self, detail_token):
        """Encryption-Key bleibt ein kritischer Check (503 für alle); das
        Detailfeld ``encryption_key`` ist jetzt Token-only (L9/N13, Refs #1375),
        der 503/status-Effekt aber auch anonym sichtbar (s.
        ``test_encryption_key_error_is_critical_anonymously``)."""
        with patch("core.services.file_vault.encrypt_field", side_effect=RuntimeError("no key")):
            response = _detail_get()
        data = response.json()
        assert data["encryption_key"] == "error"
        assert data["status"] == "error"
        assert response.status_code == 503

    def test_encryption_key_error_is_critical_anonymously(self):
        """Auch anonym: status=error + HTTP 503, ohne das Detailfeld selbst."""
        with patch("core.services.file_vault.encrypt_field", side_effect=RuntimeError("no key")):
            response = Client().get("/health/")
        data = response.json()
        assert data["status"] == "error"
        assert response.status_code == 503
        assert "encryption_key" not in data


@pytest.mark.django_db
class TestHealthDetail:
    """Interne/Token-Caller: voller Payload inkl. Recon-Detailfelder (A7.1)."""

    def test_detail_token_unlocks_version(self, detail_token):
        assert _detail_get().json()["version"] == "dev"

    def test_wrong_token_stays_lean(self, detail_token):
        data = Client().get("/health/", HTTP_X_HEALTH_TOKEN="falsch").json()
        assert "version" not in data
        assert "smtp" not in data

    def test_authenticated_session_gets_detail(self, settings, admin_user):
        client = Client()
        client.force_login(admin_user)
        data = client.get("/health/").json()
        assert "version" in data
        assert "smtp" in data

    def test_smtp_disabled_for_locmem_backend(self, detail_token, settings):
        settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
        assert _detail_get().json()["smtp"]["status"] == "disabled"

    def test_smtp_disabled_without_host(self, detail_token, settings):
        """SMTP-Mailer ohne ``host``-OPTION bleibt ``disabled`` (Refs #1662)."""
        settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.smtp.EmailBackend"}}
        assert _detail_get().json()["smtp"]["status"] == "disabled"

    def test_smtp_unreachable_degrades(self, detail_token, settings):
        settings.MAILERS = {
            "default": {
                "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                "OPTIONS": {"host": "smtp.invalid.example", "port": 25},
            }
        }
        with patch("socket.create_connection", side_effect=OSError("offline")):
            data = _detail_get().json()
        assert data["smtp"]["status"] == "unreachable"
        assert data["status"] == "degraded"

    def test_smtp_ok_reports_latency(self, detail_token, settings):
        """Payload-Vertrag (Refs #1662): ``ok`` + ``latency_ms`` bleibt unveraendert."""
        settings.MAILERS = {
            "default": {
                "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                "OPTIONS": {"host": "smtp.example.org", "port": 2525},
            }
        }
        with patch("socket.create_connection") as create:
            create.return_value.__enter__.return_value.recv.return_value = b"220 ok"
            data = _detail_get().json()
        assert create.call_args.args[0] == ("smtp.example.org", 2525)
        assert data["smtp"]["status"] == "ok"
        assert isinstance(data["smtp"]["latency_ms"], int)

    def test_backup_age_field_populated_when_files_exist(self, detail_token, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        _publish_backup_set(tmp_path)
        assert _detail_get().json()["last_backup_age_hours"] is not None

    def test_backup_age_warns_above_48h(self, detail_token, tmp_path, settings):
        import os as os_mod
        from datetime import datetime, timedelta

        settings.BACKUP_DIR = tmp_path
        manifest = _publish_backup_set(tmp_path)
        ancient = (datetime.now() - timedelta(hours=72)).timestamp()
        os_mod.utime(manifest, (ancient, ancient))

        data = _detail_get().json()
        assert data["last_backup_age_hours"] is not None
        assert data["last_backup_age_hours"] > 48
        assert data["status"] == "degraded"

    def test_missing_backup_degrades_when_required(self, detail_token, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_BACKUP_REQUIRED = True

        data = _detail_get().json()

        assert data["last_backup_age_hours"] is None
        assert data["backup"] == "missing"
        assert data["backup_required"] is True
        assert data["status"] == "degraded"

    def test_missing_backup_stays_ok_when_not_required(self, detail_token, tmp_path, settings):
        settings.BACKUP_DIR = tmp_path
        settings.HEALTH_BACKUP_REQUIRED = False

        data = _detail_get().json()

        assert data["last_backup_age_hours"] is None
        assert data["backup"] == "missing"
        assert data["backup_required"] is False
        assert data["status"] == "ok"

    def test_disk_free_pct_field_populated(self, detail_token, tmp_path, settings):
        settings.MEDIA_ROOT = str(tmp_path)
        data = _detail_get().json()
        assert data["disk_free_pct"] is not None
        assert 0 <= data["disk_free_pct"] <= 100

    def test_disk_measurement_error_degrades_when_required(self, detail_token, tmp_path, settings, monkeypatch, caplog):
        def _raise_disk_error(_path):
            raise OSError("disk unavailable")

        settings.MEDIA_ROOT = tmp_path
        settings.HEALTH_DISK_REQUIRED = True
        monkeypatch.setattr("core.views.health.shutil.disk_usage", _raise_disk_error)

        responses = []
        messages = _capture_health_warnings(caplog, lambda: responses.append(_detail_get().json()))
        data = responses[0]

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["disk_required"] is True
        assert data["status"] == "degraded"
        assert any("disk usage check failed" in message and str(tmp_path) in message for message in messages)

    def test_invalid_disk_total_logs_warning_and_is_unknown(
        self, detail_token, tmp_path, settings, monkeypatch, caplog
    ):
        settings.MEDIA_ROOT = tmp_path
        settings.HEALTH_DISK_REQUIRED = True
        monkeypatch.setattr("core.views.health.shutil.disk_usage", lambda _path: (0, 0, 0))

        responses = []
        messages = _capture_health_warnings(caplog, lambda: responses.append(_detail_get().json()))
        data = responses[0]

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["status"] == "degraded"
        assert any("invalid total" in message and str(tmp_path) in message for message in messages)

    @pytest.mark.parametrize("media_root", ["", None])
    def test_empty_media_root_is_unknown(self, detail_token, settings, media_root):
        settings.MEDIA_ROOT = media_root
        settings.HEALTH_DISK_REQUIRED = True

        data = _detail_get().json()

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["status"] == "degraded"

    def test_invalid_media_root_type_is_unknown(self, detail_token, settings):
        settings.MEDIA_ROOT = object()
        settings.HEALTH_DISK_REQUIRED = True

        data = _detail_get().json()

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["status"] == "degraded"

    def test_disk_measurement_error_stays_ok_when_not_required(self, detail_token, tmp_path, settings, monkeypatch):
        def _raise_disk_error(_path):
            raise OSError("disk unavailable")

        settings.MEDIA_ROOT = tmp_path
        settings.HEALTH_DISK_REQUIRED = False
        monkeypatch.setattr("core.views.health.shutil.disk_usage", _raise_disk_error)

        data = _detail_get().json()

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["disk_required"] is False
        assert data["status"] == "ok"

    def test_missing_media_root_degrades_when_required(self, detail_token, tmp_path, settings):
        settings.MEDIA_ROOT = tmp_path / "missing"
        settings.HEALTH_DISK_REQUIRED = True

        data = _detail_get().json()

        assert data["disk"] == "unknown"
        assert data["disk_free_pct"] is None
        assert data["disk_required"] is True
        assert data["status"] == "degraded"

    def test_low_disk_space_degrades(self, detail_token, tmp_path, settings, monkeypatch):
        settings.MEDIA_ROOT = tmp_path
        settings.HEALTH_DISK_REQUIRED = True
        monkeypatch.setattr("core.views.health.shutil.disk_usage", lambda _path: (100, 95, 5))

        data = _detail_get().json()

        assert data["disk"] == "low"
        assert data["disk_free_pct"] == 5.0
        assert data["disk_required"] is True
        assert data["status"] == "degraded"

    def test_normal_disk_space_is_ok(self, detail_token, tmp_path, settings, monkeypatch):
        settings.MEDIA_ROOT = tmp_path
        settings.HEALTH_DISK_REQUIRED = True
        monkeypatch.setattr("core.views.health.shutil.disk_usage", lambda _path: (100, 50, 50))

        data = _detail_get().json()

        assert data["disk"] == "ok"
        assert data["disk_free_pct"] == 50.0
        assert data["disk_required"] is True
        assert data["status"] == "ok"

    def test_stale_jobs_empty_when_all_ok(self, detail_token):
        """Refs #1335: frische/gruene Cron-Marker -> leere stale_jobs-Liste, status ok."""
        ok_check = ComplianceCheck(
            key="backup_age",
            label="Letztes Backup",
            category="Backup",
            status=ComplianceStatus.OK,
            message="Backup juenger als 24h.",
        )
        with patch("core.views.health.cron_job_checks", return_value=[ok_check]):
            data = _detail_get().json()
        assert data["stale_jobs"] == []
        assert data["status"] == "ok"

    def test_stale_jobs_lists_critical_job_and_degrades(self, detail_token):
        """Refs #1335: ein ``critical``-Cron-Job -> Key in stale_jobs, status degraded."""
        critical_check = ComplianceCheck(
            key="breach_scan_last_run",
            label="Breach-Detection-Scan",
            category="Hintergrundjobs",
            status=ComplianceStatus.CRITICAL,
            message="Letzter Scan vor 30h — Cron ausgefallen?",
        )
        with patch("core.views.health.cron_job_checks", return_value=[critical_check]):
            data = _detail_get().json()
        assert data["stale_jobs"] == ["breach_scan_last_run"]
        assert data["status"] == "degraded"

    def test_stale_jobs_unknown_does_not_degrade(self, detail_token):
        """Refs #1335: ``unknown`` (frische Instanz, Job lief nie) darf NICHT degraden —
        sonst waere jede frische Installation sofort im degraded-Zustand."""
        unknown_check = ComplianceCheck(
            key="snapshot_last_run",
            label="Statistik-Snapshots",
            category="Hintergrundjobs",
            status=ComplianceStatus.UNKNOWN,
            message="Noch kein Snapshot-Lauf — Cron lief vielleicht nie.",
        )
        with patch("core.views.health.cron_job_checks", return_value=[unknown_check]):
            data = _detail_get().json()
        assert data["stale_jobs"] == []
        assert data["status"] == "ok"

    def test_stale_jobs_not_in_anonymous_payload(self):
        """A7.1: stale_jobs ist ein Recon-Detailfeld, nur intern/Token sichtbar."""
        data = Client().get("/health/").json()
        assert "stale_jobs" not in data

    def test_cache_field_reports_backend_and_liveness(self, detail_token):
        """Refs #795: ``cache.backend``/``cache.status`` machen sichtbar, WELCHES
        Cache-Backend tatsaechlich laeuft.

        Braucht es, seit ``docker-compose.prod.yml`` einen Redis mitliefert:
        prod.py faellt bei fehlendem ``redis``-Paket bewusst auf den
        DatabaseCache zurueck. Ohne dieses Feld waere der Unterschied zwischen
        „Redis laeuft" und „still auf den langsamen Fallback degradiert" von
        aussen nicht feststellbar."""
        data = _detail_get().json()
        assert data["cache"]["backend"] == settings.CACHES["default"]["BACKEND"]
        assert data["cache"]["status"] == "ok"

    def test_cache_unreachable_degrades_status(self, detail_token):
        """Refs #795: ein toter Cache degradiert, statt den Endpoint zu killen.

        Mit Redis als Standard-Backend ist „Cache weg, DB da" ein realer
        Zustand (vorher fiel der DatabaseCache immer mit der DB zusammen aus,
        die ohnehin schon 503 ausgeloest haette). Der Endpoint muss ihn melden
        — und darf dabei nicht selbst an den gecachten Detail-Checks
        (``cache.get_or_set``) scheitern."""
        with patch("core.views.health.cache") as mocked_cache:
            mocked_cache.set.side_effect = ConnectionError("redis down")
            mocked_cache.get.side_effect = ConnectionError("redis down")
            mocked_cache.get_or_set.side_effect = ConnectionError("redis down")
            response = _detail_get()
        data = response.json()
        assert data["cache"]["status"] == "error"
        assert data["status"] == "degraded"
        # Liveness bleibt 200 — die DB ist erreichbar, nur der Cache nicht.
        assert response.status_code == 200

    def test_cache_not_in_anonymous_payload(self):
        """A7.1/N13: das Cache-Backend ist Recon (verraet die Stack-Topologie)."""
        data = Client().get("/health/").json()
        assert "cache" not in data


@pytest.mark.django_db
class TestHealthDbRolePosture:
    """C-25 (Refs #793): Umgeht die DB-Runtime-Rolle Row Level Security?

    Die Self-Hosting-Postscript-Falle aus #793: laeuft die Django-Runtime als
    PostgreSQL-Superuser (oder mit ``BYPASSRLS``), sind die RLS-Policies aus
    Migration 0047/0084/0085 wirkungslos — die Facility-Isolation faellt still
    aus, ohne dass irgendetwas fehlschlaegt. Der Health-Endpoint macht das
    sichtbar: Gesamtstatus ``degraded`` (auch anonym, damit Uptime-Monitore
    anschlagen), Detailfelder nur fuer Token-/interne Caller (L9/N13).
    """

    def test_detail_reports_secure_runtime_role(self, detail_token):
        data = _detail_get().json()
        assert data["db_user_is_superuser"] is False
        assert data["db_role_posture"] == "ok"
        assert data["status"] == "ok"

    def test_superuser_runtime_role_degrades_status(self, detail_token):
        with patch(_POSTURE_TARGET, return_value=(True, False)):
            data = _detail_get().json()
        assert data["db_user_is_superuser"] is True
        assert data["db_role_posture"] == "bypasses_rls"
        assert data["status"] == "degraded"

    def test_bypassrls_runtime_role_degrades_status(self, detail_token):
        """Auch ohne Superuser-Flag: ``rolbypassrls`` umgeht RLS genauso."""
        with patch(_POSTURE_TARGET, return_value=(False, True)):
            data = _detail_get().json()
        assert data["db_user_is_superuser"] is False
        assert data["db_role_posture"] == "bypasses_rls"
        assert data["status"] == "degraded"

    def test_anonymous_caller_sees_degraded_status_without_detail(self):
        """L9/N13: der Statuswechsel ist anonym sichtbar (Monitoring), das
        konkrete Rollen-Detail bleibt hinter dem Token-Gate."""
        with patch(_POSTURE_TARGET, return_value=(True, True)):
            data = Client().get("/health/").json()
        assert data["status"] == "degraded"
        assert set(data.keys()) == {"status"}

    def test_unknown_posture_does_not_degrade(self, detail_token):
        """Nicht ermittelbar (Nicht-PostgreSQL / Rolle nicht im Katalog) darf
        NICHT degraden — sonst waere jede SQLite-/Fremdbackend-Installation
        dauerhaft falsch-alarmiert."""
        with patch(_POSTURE_TARGET, return_value=(None, None)):
            data = _detail_get().json()
        assert data["db_user_is_superuser"] is None
        assert data["db_role_posture"] == "unknown"
        assert data["status"] == "ok"

    def test_error_status_is_not_downgraded_to_degraded(self, detail_token):
        """Ein kritischer Encryption-Key-Fehler bleibt ``error`` (HTTP 503) —
        der Posture-Check darf ihn nicht auf ``degraded`` abschwaechen."""
        with (
            patch(_POSTURE_TARGET, return_value=(True, True)),
            patch("core.services.file_vault.encrypt_field", side_effect=RuntimeError("no key")),
        ):
            response = _detail_get()
        assert response.json()["status"] == "error"
        assert response.status_code == 503

    def test_bypassing_role_is_logged_with_actionable_hint(self, detail_token, caplog):
        """Das Detail gehoert ins Log (nicht in den anonymen Payload) — inklusive
        des Kommandos, mit dem Betreiber:innen die Posture pruefen.

        Der ``core``-Logger hat ``propagate=False`` (settings/base.py), deshalb
        haengen wir den ``caplog``-Handler explizit an den Modul-Logger."""
        with patch(_POSTURE_TARGET, return_value=(True, False)):
            messages = _capture_health_warnings(caplog, _detail_get)
        assert any("check_db_roles" in message for message in messages), messages

    def test_secure_role_logs_nothing(self, detail_token, caplog):
        messages = _capture_health_warnings(caplog, _detail_get)
        assert [m for m in messages if "Row Level Security" in m] == []

    def test_posture_fields_not_in_anonymous_payload(self):
        data = Client().get("/health/").json()
        assert "db_user_is_superuser" not in data
        assert "db_role_posture" not in data


@pytest.mark.django_db
class TestRuntimeRolePostureQuery:
    """Die Katalog-Abfrage selbst — gegen die echte Test-Datenbank.

    Der autouse-Stub oben ersetzt das *Modul-Attribut*
    ``core.views.health._query_runtime_role_posture``; der hier per ``from ...
    import`` gebundene Name zeigt weiterhin auf die echte Funktion. Diese Klasse
    testet also den ungestubbten Original-Pfad.
    """

    def test_returns_real_catalog_values_for_current_user(self):
        """Guard gegen ein kaputtes ``pg_roles``-Statement: die Abfrage muss fuer
        ``current_user`` zwei Booleans liefern, nicht ``(None, None)``."""
        is_superuser, bypassrls = _query_runtime_role_posture()
        assert isinstance(is_superuser, bool)
        assert isinstance(bypassrls, bool)

    def test_returns_unknown_when_query_fails(self):
        """DB-Ausfall darf den Health-Endpoint nicht mit einer Exception killen —
        ``(None, None)`` = unbekannt, kein Degrade."""
        with patch("core.views.health.connection") as fake_connection:
            fake_connection.vendor = "postgresql"
            fake_connection.cursor.side_effect = RuntimeError("db down")
            assert _query_runtime_role_posture() == (None, None)

    def test_returns_unknown_on_non_postgres_backend(self):
        with patch("core.views.health.connection") as fake_connection:
            fake_connection.vendor = "sqlite"
            assert _query_runtime_role_posture() == (None, None)
            fake_connection.cursor.assert_not_called()


@pytest.mark.django_db(transaction=True)
class TestStaleJobsRlsVisibility:
    """Refs #1335: ``_check_stale_jobs()`` liest die facility-losen Cron-Marker
    (``core_auditlog``, ``facility=NULL``) ueber ``cron_job_checks()``. Migration
    0047/0085 macht diese Zeilen NUR sichtbar, wenn die DB-Rolle SUPERUSER/
    BYPASSRLS ist oder das GUC ``app.is_super_admin`` auf ``true`` steht.

    Der reale Prod-Pfad fuer den Token-/authentifizierten Monitoring-Caller
    laeuft als App-Rolle NOBYPASSRLS (ops-runbook §9) und setzt (ausser fuer
    super_admin-Browsersessions) niemals ``app.is_super_admin``. Diese Tests
    laufen — anders als die gemockten Tests oben — ueber eine echte
    Nicht-Superuser/Nicht-Bypass-Postgres-Rolle (``rls_test_role``, s.
    ``tests.test_rls_functional``) und pruefen die tatsaechliche RLS-Sichtbarkeit."""

    def test_critical_marker_visible_under_non_bypass_role(self, rls_test_role):  # noqa: F811
        """Ohne RLS-Bypass-Read waere der Marker unsichtbar -> UNKNOWN statt
        CRITICAL -> stale_jobs bliebe fuer den Token-/App-Rollen-Pfad IMMER []
        (das #1335-Kernszenario: ein tatsaechlich ausgefallener Scheduler wird
        nie gemeldet)."""
        entry = AuditLog.objects.create(
            action=AuditLog.Action.BREACH_SCAN_COMPLETED, facility=None, target_type="BreachScanRun"
        )
        # transaction=True (fuer rls_test_role/SET ROLE) laeuft ohne umschliessende
        # Transaktion — SET LOCAL braucht einen expliziten transaction.atomic()-Block,
        # sonst verpufft es vor dem UPDATE (analog test_audit_chain.py).
        with transaction.atomic(), bypass_replication_triggers():
            AuditLog.objects.filter(pk=entry.pk).update(timestamp=timezone.now() - timedelta(hours=30))

        with as_rls_role(rls_test_role, facility_id=""):
            result = _check_stale_jobs()

        assert "breach_scan_last_run" in result

    def test_does_not_leak_super_admin_guc_after_read(self, rls_test_role):  # noqa: F811
        """Der fuer den Read transaktionslokal gesetzte ``app.is_super_admin``-GUC
        darf nach ``_check_stale_jobs()`` NICHT auf der (potenziell wiederverwendeten)
        Connection stehen bleiben — sonst laeuft ein spaeterer Aufrufer auf derselben
        Connection mit Superadmin-Sichtbarkeit weiter (latenter Privilege-Leak,
        analog ``run_system_detections`` Refs #1368)."""
        from django.db import connection

        with as_rls_role(rls_test_role, facility_id=""):
            _check_stale_jobs()

        with connection.cursor() as cur:
            cur.execute("SELECT current_setting('app.is_super_admin', true)")
            value = cur.fetchone()[0] or ""
        assert value != "true", f"app.is_super_admin blieb auf der Connection stehen: {value!r}"


# Refs #835 (C-68): SOURCE_CODE_URL ist im Footer ueber den Context-Processor.
class TestAGPLFooter:
    def test_default_source_code_url_in_footer(self, client, db):
        response = client.get("/login/")
        assert b"https://github.com/anlaufstelle/app" in response.content

    def test_custom_source_code_url_renders(self, client, db, settings):
        settings.SOURCE_CODE_URL = "https://git.example.org/fork/anlaufstelle"
        response = client.get("/login/")
        assert b"https://git.example.org/fork/anlaufstelle" in response.content
        assert b"github.com/anlaufstelle" not in response.content

    def test_source_code_version_is_not_rendered_in_authenticated_footer(self, client, staff_user, settings):
        """Refs #2532: the source version is retained for document footers only."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        client.force_login(staff_user)
        response = client.get(reverse("core:zeitstrom"))
        assert b"abcdef1" not in response.content

    def test_source_code_version_hidden_on_login_page(self, client, db, settings):
        """Refs #1901: anonyme Login-Seite zeigt SOURCE_CODE_VERSION nicht (Fingerprinting)."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get("/login/")
        assert b"abcdef1" not in response.content

    def test_source_code_version_hidden_on_404(self, client, db, settings):
        """Refs #1901: 404-Seite erbt von base.html und war ungegatet -- leakte den Stand anonym."""
        settings.SOURCE_CODE_VERSION = "abcdef1234567890"
        response = client.get("/dieser-pfad-existiert-nicht-1901/")
        assert response.status_code == 404
        assert b"abcdef1" not in response.content
