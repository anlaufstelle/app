import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

DEPLOY_SCRIPT = Path("dev-ops/deploy/deploy-demo.sh")
RESEED_SCRIPT = Path("dev-ops/deploy/demo-reseed.sh")


def _require_dev_ops() -> None:
    if not DEPLOY_SCRIPT.exists() or not RESEED_SCRIPT.exists():
        pytest.skip("dev-ops/ is excluded from this release tree")


def _seed_scales(text: str) -> set[str]:
    return set(re.findall(r"--scale[= ]([a-z]+)", text))


def test_reseed_script_seeds_the_scenario_catalogue() -> None:
    _require_dev_ops()
    code = "\n".join(
        line for line in RESEED_SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )

    assert _seed_scales(code) == {"demo"}


def test_deploy_script_names_only_the_seed_scale_the_reseed_timer_uses() -> None:
    _require_dev_ops()
    deploy = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert _seed_scales(deploy) <= {"demo"}, (
        "deploy-demo.sh tells the operator to seed with a different scale than the hourly reset "
        "(demo-reseed.sh: --scale=demo); a manual seed with another scale removes the scenario areas."
    )
