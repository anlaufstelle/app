"""Convert per-spec E2E durations into validated, versioned weights."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src/tests/e2e"
DEFAULT_WEIGHTS = Path(__file__).with_name("e2e_weights.json")


def load_weights(path: Path, base: Path = BASE, *, require_existing: bool = True) -> dict[str, float]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict):
        raise ValueError("Weights must be an object with _meta metadata")
    weights = {}
    for name, value in data.items():
        if name == "_meta":
            continue
        spec = Path(name)
        if spec.is_absolute() or ".." in spec.parts or (require_existing and not (base / spec).is_file()):
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


def scale_factor(value: str) -> float:
    factor = float(value)
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError(f"Scale must be a finite positive number: {value}")
    return factor


def read_csv_durations(source: Path) -> Counter[str]:
    """Sum per-spec durations from a CSV with one row per test and several runs.

    Columns: ``test`` (``src.tests.e2e.<module>.<Class>::<test>[param]``) plus pairs
    ``pN``/``pN_s`` (status and seconds of run N). A test counts with the median of its
    passed runs; without any passed run it counts with the maximum of its runs.
    """
    weights: Counter[str] = Counter()
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        runs = [name[:-2] for name in fields if re.fullmatch(r"p\d+_s", name) and name[:-2] in fields]
        if "test" not in fields or not runs:
            raise ValueError(f"{source.name}: expected columns test and pN/pN_s pairs")
        for row in reader:
            spec = spec_path(row["test"] or "")
            passed: list[float] = []
            every: list[float] = []
            for run in runs:
                seconds = (row[f"{run}_s"] or "").strip()
                if not seconds:
                    continue
                value = duration(seconds)
                every.append(value)
                if (row[run] or "").strip() == "pass":
                    passed.append(value)
            if not every:
                raise ValueError(f"{source.name}: no duration for {row['test']}")
            weights[spec] += median(passed) if passed else max(every)
    return weights


def convert(
    junit: list[Path],
    durations: list[Path],
    output: Path,
    csv_files: list[Path] | None = None,
    *,
    scale: float = 1.0,
    measured_at: str | None = None,
    head: str | None = None,
) -> None:
    csv_files = csv_files or []
    weights: Counter[str] = Counter()
    for source in csv_files:
        weights.update(read_csv_durations(source))
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
    head = head or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    data: dict[str, object] = {
        # Rounded zero-duration cases still need a positive scheduling weight.
        name: max(round(seconds * scale, 6), 0.000001)
        for name, seconds in sorted(weights.items())
    }
    data["_meta"] = {
        "source": ", ".join(path.name for path in [*junit, *durations, *csv_files]),
        "measured_at": measured_at or datetime.now(UTC).isoformat(),
        "head": head,
        "scale": scale,
    }
    output.write_text(json.dumps(data, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-junit", type=Path, nargs="+", default=[])
    parser.add_argument("--from-durations", type=Path, nargs="+", default=[])
    parser.add_argument(
        "--from-csv", type=Path, nargs="+", default=[], help="CSV with test and pN/pN_s status/seconds columns"
    )
    parser.add_argument("--scale", default="1.0", help="Multiply all weights (e.g. runner ratio)")
    parser.add_argument("--measured-at", help="ISO date or time of the measurement (default: now)")
    parser.add_argument("--head", help="Measured commit (default: current HEAD)")
    parser.add_argument("--output", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    try:
        if args.validate:
            load_weights(args.validate)
        else:
            if args.measured_at:
                datetime.fromisoformat(args.measured_at)
            if args.head and not re.fullmatch(r"[0-9a-f]{7,40}", args.head):
                raise ValueError(f"Invalid commit: {args.head}")
            convert(
                args.from_junit,
                args.from_durations,
                args.output,
                args.from_csv,
                scale=scale_factor(args.scale),
                measured_at=args.measured_at,
                head=args.head,
            )
        return 0
    except (ValueError, OSError, TypeError, KeyError, ParseError, DefusedXmlException) as error:
        print(f"e2e_weights: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
