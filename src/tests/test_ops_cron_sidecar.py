"""Guards fuer den Cron-Sidecar und den Redis-Cache-Service (Refs #794, #795).

Beide Features leben in Dateien, die keine Test-Suite sonst anfasst
(``docker-compose.prod.yml``, ``deploy/crontab``, ``Dockerfile``,
``deploy/fetch_supercronic.py``). Sie tragen aber harte Betriebs- und
DSGVO-Invarianten:

* Die Jobs im Sidecar MUESSEN als BYPASSRLS-Admin-Rolle laufen, sonst filtert
  RLS sie auf 0 Zeilen bzw. sie brechen fail-loud ab (Refs #1016 A1.1) — die
  Retention (Art. 5 Abs. 1 lit. e) liefe dann jede Nacht ins Leere.
* Die Schedules in ``deploy/crontab`` MUESSEN mit der autoritativen Liste in
  ``docs/ops-runbook.md`` § 3 uebereinstimmen — sonst laufen Betriebs-Doku und
  ausgelieferte Realitaet auseinander.
* supercronic MUSS versions- und sha256-gepinnt ins Image kommen (dieselbe
  Integritaetszusage wie ``pip wheel --require-hashes``).
* Der Redis-Service darf keinen Host-Port und keine Persistenz haben.

Bewusst textbasiert (kein YAML-Parser): identisch zu allen anderen Ops-Guards
im Repo (``test_architecture_guards_ops.py``) und unabhaengig davon, ob PyYAML
gerade transitiv im Dev-Env liegt.
"""

from __future__ import annotations

import importlib.util
import re
from functools import cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
CRONTAB = REPO_ROOT / "deploy" / "crontab"
DOCKERFILE = REPO_ROOT / "Dockerfile"
RUNBOOK = REPO_ROOT / "docs" / "ops-runbook.md"


@cache
def _supercronic():
    """``deploy/`` ist kein Python-Paket und liegt nicht auf dem PYTHONPATH
    (der zeigt auf ``src/``) — das Build-Skript daher direkt ueber seinen Pfad
    laden, statt es kuenstlich importierbar zu machen.

    Bewusst *lazy* (nicht auf Modulebene): ``deploy/`` liegt ausserhalb von
    ``src/`` und wird im mutmut-``mutants/``-Tree weggestrippt. Ein
    Import-Zeit-Zugriff wuerde dort schon das Test-Listing sprengen und den
    Mutation-Run auf null Mutanten bringen (Refs #1600, Guard in
    ``test_architecture_guards_mutation_isolation.py``).
    """
    path = REPO_ROOT / "deploy" / "fetch_supercronic.py"
    if not path.exists():  # pragma: no cover — nur im gestrippten mutants/-Tree
        pytest.skip(f"{path} nicht vorhanden (gestrippter Baum)")
    spec = importlib.util.spec_from_file_location("_fetch_supercronic", path)
    assert spec is not None and spec.loader is not None, f"nicht ladbar: {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Der ausgelieferte Zeitplan, eingefroren gegen docs/ops-runbook.md § 3.1.
# Backup fehlt hier bewusst: es faehrt ``docker compose exec`` und braeuchte
# den Docker-Socket im Sidecar (root-aequivalent auf dem Host) — es bleibt
# Host-Cron, siehe Runbook § 3.3.
EXPECTED_JOBS = {
    "enforce_retention": "0 3 * * *",
    "verify_audit_chain": "30 3 * * *",
    "create_statistics_snapshots": "0 4 1 * *",
    "cleanup_orphan_storage_files": "30 4 * * *",
    "detect_breaches": "30 * * * *",
    "refresh_statistics_view": "15 * * * *",
}


def _service_block(compose_text: str, service: str) -> str:
    """Schneidet den YAML-Block eines Compose-Service heraus (Einrueckung 2).

    Kommentarzeilen fliegen raus: die Compose-Dateien dieses Repos sind stark
    kommentiert, und ein Guard darf nicht auf die Prosa ueber einer Einstellung
    anschlagen ("KEIN ports:" haette sonst als ``ports:`` gezaehlt).
    """
    match = re.search(
        rf"^  {re.escape(service)}:\n(?P<body>(?:(?:    .*)?\n)*)",
        compose_text,
        re.MULTILINE,
    )
    assert match is not None, f"Service '{service}' fehlt in docker-compose.prod.yml"
    return "\n".join(line for line in match.group("body").splitlines() if not line.lstrip().startswith("#"))


def _crontab_job_lines() -> list[str]:
    """Nicht-Kommentar-, nicht-Leerzeilen aus ``deploy/crontab``."""
    return [
        line
        for line in CRONTAB.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


class TestShippedCrontab:
    """``deploy/crontab`` — die ausgelieferte Job-Definition des Sidecars."""

    def test_every_line_is_a_five_field_cron_entry(self) -> None:
        """supercronic erwartet das klassische 5-Felder-Format OHNE User-Feld.

        Ein versehentlich uebernommenes 6. Feld (System-``/etc/crontab``-Format,
        wie es in Host-Anleitungen ueblich ist) wuerde supercronic als Teil des
        Kommandos interpretieren: der Job scheitert dann bei jedem Tick, aber
        der Container laeuft weiter — ein Ausfall, den niemand sieht."""
        for line in _crontab_job_lines():
            fields = line.split(maxsplit=5)
            assert len(fields) == 6, f"keine 5 Zeitfelder + Kommando: {line!r}"
            schedule, command = fields[:5], fields[5]
            for field in schedule:
                assert re.fullmatch(r"[\d*/,\-]+", field), f"ungueltiges Cron-Feld {field!r} in {line!r}"
            assert command.startswith("python manage.py "), (
                f"Kommando muss ein manage.py-Command im Container sein: {command!r}"
            )

    def test_schedules_match_the_runbook(self) -> None:
        """Genau die Jobs und Zeiten aus docs/ops-runbook.md § 3.1 — nicht mehr, nicht weniger."""
        found = {}
        for line in _crontab_job_lines():
            *schedule, command = line.split(maxsplit=5)
            found[command.removeprefix("python manage.py ").strip()] = " ".join(schedule)
        assert found == EXPECTED_JOBS

    def test_no_docker_socket_dependent_job(self) -> None:
        """Kein ``backup.sh``/``docker`` im Sidecar.

        Das Backup faehrt ``docker compose exec`` (pg_dump im db-Container,
        tar im web-Container). Damit es im Sidecar liefe, muesste der
        Docker-Socket hineingemountet werden — das ist root-aequivalent auf dem
        Host und wuerde ``cap_drop: ALL`` / ``no-new-privileges`` des gesamten
        Stacks entwerten. Backup bleibt daher Host-Cron (Runbook § 3.3)."""
        for line in _crontab_job_lines():
            assert "docker" not in line, f"Sidecar-Job darf kein docker aufrufen: {line!r}"
            assert "backup.sh" not in line, f"Backup gehoert nicht in den Sidecar: {line!r}"

    def test_runbook_documents_every_shipped_job(self) -> None:
        """Doku und ausgelieferte Datei duerfen nicht auseinanderlaufen."""
        runbook = RUNBOOK.read_text(encoding="utf-8")
        for command in EXPECTED_JOBS:
            assert command in runbook, f"{command} laeuft im Sidecar, steht aber nicht im Runbook"


class TestCronSidecarService:
    """``cron``-Service in docker-compose.prod.yml."""

    def test_runs_jobs_as_admin_db_role(self) -> None:
        """DSGVO-kritisch (Refs #1016 A1.1 / #1335): BYPASSRLS-Rolle statt App-Rolle.

        Ausserhalb eines HTTP-Requests setzt niemand die GUC
        ``app.current_facility_id``. Als RLS-gefilterte App-Rolle saehe
        ``enforce_retention`` 0 Zeilen; der Guard in ``enforce_retention.py``
        bricht deshalb fail-loud ab — die Aufbewahrungsfristen wuerden nie
        durchgesetzt. Genau dieselbe Umschaltung macht der Dev-Pfad in
        ``dev-ops/deploy/run-as-admin.sh``."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "cron")
        assert "POSTGRES_USER: ${POSTGRES_ADMIN_USER}" in block
        assert "POSTGRES_PASSWORD: ${POSTGRES_ADMIN_PASSWORD}" in block

    def test_uses_supercronic_with_the_repo_crontab(self) -> None:
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "cron")
        assert "/usr/local/bin/supercronic" in block
        assert "./deploy/crontab:/etc/anlaufstelle/crontab:ro" in block

    def test_is_hardened_and_has_no_ports(self) -> None:
        """Gleiche Runtime-Haertung wie web (Refs #1374); ein Scheduler ohne
        Netz-Endpunkt darf keinen Port exponieren."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "cron")
        assert '"no-new-privileges:true"' in block
        assert "read_only: true" in block
        assert "cap_drop:" in block
        assert "ports:" not in block

    def test_does_not_tmpfs_over_the_static_manifest(self) -> None:
        """Anders als web laeuft im Sidecar KEIN Entrypoint-collectstatic.

        Ein ``/app/staticfiles``-tmpfs (aus dem web-Service kopiert) wuerde das
        im Image gebackene Staticfiles-Manifest verdecken; jedes Template mit
        ``{% static %}`` — z.B. in einer Breach-Benachrichtigung — wuerfe dann
        einen ValueError."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "cron")
        assert "/app/staticfiles" not in block

    def test_sets_an_explicit_timezone(self) -> None:
        """Ohne TZ liefe der Container in UTC — "03:00" waere je nach Jahreszeit
        04:00/05:00 Ortszeit und die im Runbook zugesagte Reihenfolge
        (Backup 02:00 vor Retention 03:00) waere nicht mehr garantiert."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "cron")
        assert "TZ: ${TZ:-Europe/Berlin}" in block


class TestRedisCacheService:
    """``redis``-Service in docker-compose.prod.yml (Refs #795)."""

    def test_image_is_digest_pinned(self) -> None:
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "redis")
        assert re.search(r"image: redis:\d+-alpine@sha256:[0-9a-f]{64}", block), (
            "Redis-Image muss wie die uebrigen Images per @sha256 gepinnt sein (Refs #1373)"
        )

    def test_has_no_host_port(self) -> None:
        """Der Cache haengt nur im ``internal``-Netz und hat bewusst kein
        ``requirepass`` — ein Host-Port waere damit ein offener,
        unauthentifizierter Redis."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "redis")
        assert "ports:" not in block

    def test_persistence_is_disabled(self) -> None:
        """Reiner Cache: RDB und AOF aus, kein Volume.

        Persistenz waere hier ein Datenschutz-Minus — sie schriebe
        Rate-Limit-Schluessel (Client-IPs, User-IDs) auf die Platte, wo keine
        Retention sie erfasst."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "redis")
        assert "--save" in block
        assert "--appendonly" in block
        assert "volumes:" not in block

    def test_is_hardened(self) -> None:
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), "redis")
        assert '"no-new-privileges:true"' in block
        assert "read_only: true" in block
        assert "user: redis" in block

    @pytest.mark.parametrize("service", ["web", "cron"])
    def test_app_services_point_at_the_shipped_redis(self, service: str) -> None:
        """``${REDIS_URL-...}`` (EIN Bindestrich) statt ``:-``: so bleibt ein
        bewusst LEER gesetztes REDIS_URL leer und ``prod.py`` faellt auf den
        DatabaseCache zurueck. Mit ``:-`` wuerde der Default auch die
        Abschaltung ueberschreiben."""
        block = _service_block(COMPOSE_PROD.read_text(encoding="utf-8"), service)
        assert "REDIS_URL: ${REDIS_URL-redis://redis:6379/1}" in block


class TestSupercronicPinning:
    """``deploy/fetch_supercronic.py`` + Dockerfile-Verdrahtung."""

    def test_version_and_checksums_are_pinned(self) -> None:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", _supercronic().SUPERCRONIC_VERSION)
        assert set(_supercronic().SUPERCRONIC_SHA256) == {"amd64", "arm64"}, (
            "Release-Workflow baut linux/amd64 + linux/arm64 — beide brauchen einen Pin"
        )
        for arch, digest in _supercronic().SUPERCRONIC_SHA256.items():
            assert re.fullmatch(r"[0-9a-f]{64}", digest), f"kein sha256 fuer {arch}: {digest!r}"

    def test_download_url_is_the_official_release_asset(self) -> None:
        module = _supercronic()
        assert module.download_url("amd64") == (
            "https://github.com/aptible/supercronic/releases/download/"
            f"{module.SUPERCRONIC_VERSION}/supercronic-linux-amd64"
        )

    def test_dockerfile_builds_and_copies_the_pinned_binary(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        assert "deploy/fetch_supercronic.py" in text
        assert "ARG TARGETARCH" in text, "ohne TARGETARCH zieht der arm64-Build das amd64-Binary"
        assert "COPY --from=builder /build/supercronic /usr/local/bin/supercronic" in text

    def test_fetch_rejects_a_tampered_artifact(self, tmp_path: Path) -> None:
        """Kern der Zusage: ein umgehaengtes Release-Tag / MITM bricht den Build ab."""

        class _FakeResponse:
            def read(self) -> bytes:
                return b"not-the-pinned-binary"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        target = tmp_path / "supercronic"
        with pytest.raises(SystemExit, match="sha256-Mismatch"):
            _supercronic().fetch("amd64", target, opener=lambda url: _FakeResponse())
        assert not target.exists(), "beschaedigtes Artefakt darf nicht liegen bleiben"

    def test_fetch_rejects_unpinned_architecture(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="Checksumme"):
            _supercronic().fetch("riscv64", tmp_path / "supercronic", opener=lambda url: None)

    def test_fetch_writes_executable_on_checksum_match(self, tmp_path: Path) -> None:
        import hashlib

        payload = b"pretend-supercronic"
        digest = hashlib.sha256(payload).hexdigest()

        class _FakeResponse:
            def read(self) -> bytes:
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        module = _supercronic()
        target = tmp_path / "nested" / "supercronic"
        original = module.SUPERCRONIC_SHA256["amd64"]
        module.SUPERCRONIC_SHA256["amd64"] = digest
        try:
            module.fetch("amd64", target, opener=lambda url: _FakeResponse())
        finally:
            module.SUPERCRONIC_SHA256["amd64"] = original
        assert target.read_bytes() == payload
        assert target.stat().st_mode & 0o111, "Binary muss ausfuehrbar sein"
