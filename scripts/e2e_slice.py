#!/usr/bin/env python3
"""Partition E2E spec files deterministically by versioned runtime weights.

Files remain atomic because browser fixtures are module scoped. LPT assigns
heavier files first, breaking ties by path and then bucket load/count/index.
Missing measurements use the positive median of known weights (1s if empty).
On any selection error stdout stays empty to prevent accidental full runs.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from statistics import median

try:
    from scripts.ci.e2e_weights import DEFAULT_WEIGHTS, load_weights
except ModuleNotFoundError:
    from ci.e2e_weights import DEFAULT_WEIGHTS, load_weights

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_DIR = ROOT / "src" / "tests" / "e2e"

# Match pytest discovery without importing test modules.
TEST_FILE_GLOBS = ("test_*.py", "*_test.py")
TEST_CLASS_PREFIX = "Test"
TEST_FUNCTION_PREFIX = "test"


def count_tests(path: Path) -> int:
    """Count module-level tests and methods in pytest test classes statically."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return _count_in_body(tree.body)


def _count_in_body(body: list[ast.stmt]) -> int:
    total = 0
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith(TEST_FUNCTION_PREFIX):
                total += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith(TEST_CLASS_PREFIX):
            total += _count_in_body(node.body)
    return total


def discover(base_dir: Path) -> list[Path]:
    """Discover spec files recursively in path order."""
    found: set[Path] = set()
    for glob in TEST_FILE_GLOBS:
        found.update(p for p in base_dir.rglob(glob) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(found)


def distribute(weights: dict[Path, float], slices: int) -> list[list[Path]]:
    """Assign heaviest specs first; use bucket size to keep zero-weight slices nonempty."""
    if slices < 1:
        raise ValueError(f"--slices must be >= 1, got {slices}")
    if not weights:
        raise ValueError("No spec files found; an empty slice would run the full suite")
    if len(weights) < slices:
        raise ValueError(f"Cannot distribute {len(weights)} spec files into {slices} slices")

    buckets: list[list[Path]] = [[] for _ in range(slices)]
    loads = [0.0] * slices
    for path, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(slices), key=lambda i: (loads[i], len(buckets[i]), i))
        buckets[target].append(path)
        loads[target] += weight
    return [sorted(bucket) for bucket in buckets]


def file_weights(
    base_dir: Path, weights_file: Path = DEFAULT_WEIGHTS
) -> tuple[dict[Path, float], list[str], list[str]]:
    """Load current weights, identify fallback specs, and report stale entries."""
    if not base_dir.is_dir():
        raise ValueError(f"Not a directory: {base_dir}")
    known = load_weights(weights_file, base_dir, require_existing=False)
    stale = sorted(name for name in known if not (base_dir / name).is_file())
    known = {name: value for name, value in known.items() if name not in stale}
    fallback = median(known.values()) if known else 1.0
    files = discover(base_dir)
    missing = [
        path.relative_to(base_dir).as_posix() for path in files if path.relative_to(base_dir).as_posix() not in known
    ]
    return {path: known.get(path.relative_to(base_dir).as_posix(), fallback) for path in files}, missing, stale


def build_slices(base_dir: Path, slices: int, weights_file: Path = DEFAULT_WEIGHTS) -> list[list[Path]]:
    """Partition all discovered specs using measured or fallback durations."""
    weights, _, _ = file_weights(base_dir, weights_file)
    return distribute(weights, slices)


def _display(path: Path) -> str:
    """Use repository-relative paths where possible."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic runtime-weighted E2E partitioning.")
    parser.add_argument("--slices", type=int, required=True, help="Number of CI slices")
    parser.add_argument("--index", type=int, help="Slice index, starting at 1")
    parser.add_argument(
        "--summary", action="store_true", help="Print slice loads, missing measurements, and stale weights"
    )
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR, help="Spec directory (default: E2E suite)")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="Versioned spec durations")
    args = parser.parse_args(argv)

    if (args.index is None) == (not args.summary):
        parser.error("Specify exactly one of --index and --summary")

    try:
        weights, missing, stale = file_weights(args.base_dir, args.weights)
        buckets = distribute(weights, args.slices)
    except (ValueError, OSError, TypeError) as exc:
        print(f"e2e_slice: {exc}", file=sys.stderr)
        return 2

    if args.summary:
        loads = [sum(weights[path] for path in bucket) for bucket in buckets]
        ratio = max(loads) / (sum(loads) / args.slices)
        for number, (bucket, load) in enumerate(zip(buckets, loads, strict=True), start=1):
            print(f"Slice {number}/{args.slices}: {len(bucket)} files, {load:.2f}s")
        print(f"max/mean: {ratio:.3f} (target <= 1.15)")
        print(f"unmeasured: {len(missing)}")
        for name in missing:
            print(f"  {name}")
        print(f"stale: {len(stale)}")
        for name in stale:
            print(f"  {name}")
        return 0

    if not 1 <= args.index <= args.slices:
        print(f"e2e_slice: --index {args.index} is outside 1..{args.slices}", file=sys.stderr)
        return 2

    for path in buckets[args.index - 1]:
        print(_display(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
