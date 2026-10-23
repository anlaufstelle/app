"""Convert per-spec E2E durations into validated, versioned weights."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src/tests/e2e"
DEFAULT_WEIGHTS = Path(__file__).with_name("e2e_weights.json")


def load_weights(path: Path, base: Path = BASE) -> dict[str, float]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict):
        raise ValueError("Weights must be an object with _meta metadata")
    weights = {}
    for name, value in data.items():
        if name == "_meta":
            continue
        spec = Path(name)
        if spec.is_absolute() or ".." in spec.parts or not (base / spec).is_file():
            raise ValueError(f"Unknown spec path: {name}")
        if not (spec.name.startswith("test_") or spec.name.endswith("_test.py")) or spec.suffix != ".py":
            raise ValueError(f"Not a spec file: {name}")
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Weight must be a finite positive duration: {name}")
        weights[name] = float(value)
    return weights


def spec_path(value: str) -> str:
    """Resolve pytest node paths or xunit2 dotted classnames to existing specs."""
    value = value.split("::", 1)[0].replace("\\", "/")
    for prefix in ("src/tests/e2e/", "tests/e2e/", "src.tests.e2e.", "tests.e2e."):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if "/" not in value and not value.endswith(".py"):
        parts = value.split(".")
        for end in range(len(parts), 0, -1):
            candidate = "/".join(parts[:end]) + ".py"
            if (BASE / candidate).is_file():
                value = candidate
                break
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not (BASE / path).is_file() or path.suffix != ".py":
        raise ValueError(f"Cannot resolve E2E spec: {value}")
    if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
        raise ValueError(f"Not an E2E spec: {value}")
    return path.as_posix()


def duration(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"Invalid duration: {value}")
    return seconds


def convert(junit: list[Path], durations: list[Path], output: Path) -> None:
    weights: Counter[str] = Counter()
    for source in junit:
        for case in ElementTree.parse(source).getroot().iter("testcase"):
            name = spec_path(case.get("file") or case.get("classname", ""))
            weights[name] += duration(case.attrib["time"])
    pattern = re.compile(r"^\s*([\d.]+)s\s+(?:setup|call|teardown)\s+(.+?\.py)::")
    for source in durations:
        for line in source.read_text().splitlines():
            match = pattern.match(line)
            if match:
                weights[spec_path(match[2])] += duration(match[1])
    if not weights:
        raise ValueError("No E2E durations found")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    data: dict[str, object] = {
        # Rounded zero-duration cases still need a positive scheduling weight.
        name: max(round(seconds, 6), 0.000001)
        for name, seconds in sorted(weights.items())
    }
    data["_meta"] = {
        "source": ", ".join(path.name for path in [*junit, *durations]),
        "measured_at": datetime.now(UTC).isoformat(),
        "head": head,
    }
    output.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-junit", type=Path, nargs="+", default=[])
    parser.add_argument("--from-durations", type=Path, nargs="+", default=[])
    parser.add_argument("--output", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    try:
        if args.validate:
            load_weights(args.validate)
        else:
            convert(args.from_junit, args.from_durations, args.output)
        return 0
    except (ValueError, OSError, TypeError, KeyError, ParseError, DefusedXmlException) as error:
        print(f"e2e_weights: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
