"""cron gets egress for the breach webhook only through an overlay (Refs #2719).

The standard installation keeps cron on the egress-less ``internal`` network:
cron runs with the admin database role and receives the whole ``.env``.
``docker-compose.breach-webhook.yml`` adds exactly one egress network that
serves cron alone.

The merge is rebuilt here in YAML (Compose merges a service's ``networks`` by
union) so the guard runs without a Docker installation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "docker-compose.prod.yml"
OVERLAY = REPO_ROOT / "docker-compose.breach-webhook.yml"
EGRESS = "cron-egress"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _network_names(service: dict) -> set[str]:
    networks = service.get("networks") or []
    return set(networks if isinstance(networks, list) else networks.keys())


def _merged() -> dict:
    base, overlay = _load(BASE), _load(OVERLAY)
    services = {name: dict(service) for name, service in base["services"].items()}
    for name, extra in overlay.get("services", {}).items():
        merged = dict(services.get(name, {}))
        for key, value in extra.items():
            if key == "networks":
                merged["networks"] = sorted(_network_names(merged) | _network_names(extra))
            else:
                merged[key] = value
        services[name] = merged
    networks = {**base.get("networks", {}), **overlay.get("networks", {})}
    return {"services": services, "networks": networks}


def test_standard_compose_keeps_cron_on_internal_only() -> None:
    base = _load(BASE)
    assert _network_names(base["services"]["cron"]) == {"internal"}
    assert base["networks"]["internal"]["internal"] is True
    assert EGRESS not in base["networks"]


def test_overlay_adds_exactly_one_egress_network_to_cron() -> None:
    merged = _merged()
    assert _network_names(merged["services"]["cron"]) == {"internal", EGRESS}
    assert (merged["networks"][EGRESS] or {}).get("internal", False) is False


def test_no_other_service_joins_the_cron_egress_network() -> None:
    merged = _merged()
    joined = sorted(name for name, service in merged["services"].items() if EGRESS in _network_names(service))
    assert joined == ["cron"]


def test_overlay_opens_nothing_but_the_network() -> None:
    overlay = _load(OVERLAY)
    assert set(overlay) <= {"services", "networks"}
    assert set(overlay["services"]) == {"cron"}
    assert set(overlay["services"]["cron"]) == {"networks"}
    assert set(overlay["networks"]) == {EGRESS}
