"""Partition pytest's collected unit files by measured duration (Refs #2598)."""

import argparse
import json
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = Path(__file__).with_name("test_weights.json")
SELECTION = ["-m", "not e2e", "--ignore=src/tests/e2e"]
DEFAULT_WEIGHT = 1.0


def collected_files(output: str) -> Counter[str]:
    files: Counter[str] = Counter()
    for line in output.splitlines():
        path, separator, _ = line.strip().partition("::")
        if separator and path.endswith(".py"):
            files[path] += 1
    if not files:
        raise ValueError("No test files collected")
    return files


def validate_partition(files: Counter[str], buckets: list[list[str]]) -> None:
    flattened = [path for bucket in buckets for path in bucket]
    if not all(buckets) or Counter(flattened) != Counter(dict.fromkeys(files, 1)):
        raise ValueError("Invalid partition: missing, duplicate, unexpected files or empty slice")


def partition(files: Counter[str], weights: dict[str, float], slices: int) -> list[list[str]]:
    if slices < 1 or slices > len(files):
        raise ValueError("Slice count must be between 1 and the collected file count")
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("Weights must be finite, non-negative durations")
    buckets: list[list[str]] = [[] for _ in range(slices)]
    loads = [0.0] * slices
    for path in sorted(files, key=lambda path: (-weights.get(path, DEFAULT_WEIGHT), path)):
        index = min(range(slices), key=lambda index: (loads[index], len(buckets[index]), index))
        buckets[index].append(path)
        loads[index] += weights.get(path, DEFAULT_WEIGHT)
    for bucket in buckets:
        bucket.sort()
    validate_partition(files, buckets)
    return buckets


def update_weights(paths: list[Path], output: Path) -> None:
    weights: Counter[str] = Counter()
    pattern = re.compile(r"^\s*([\d.]+)s\s+(?:setup|call|teardown)\s+(.+?\.py)::")
    for path in paths:
        for line in path.read_text().splitlines():
            match = pattern.match(line)
            if match:
                weights[match[2]] += float(match[1])
    if not weights:
        raise ValueError("No pytest durations found")
    output.write_text(json.dumps({path: round(value, 6) for path, value in sorted(weights.items())}, indent=2) + "\n")


def validate_coverage(directory: Path, result: str | None, slices: int) -> None:
    # A matrix result is success only when every matrix child succeeded.
    if result != "success":
        raise ValueError(f"Unit matrix did not succeed: {result}")
    for index in range(1, slices + 1):
        path = directory / f"unit-coverage-{index}" / f".coverage.{index}"
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"Missing coverage artifact for slice {index}: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slices", type=int, default=3)
    parser.add_argument("--index", type=int)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--collection", type=Path)
    parser.add_argument("--weights", type=Path, default=WEIGHTS)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--durations", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=WEIGHTS)
    parser.add_argument("--coverage-dir", type=Path)
    parser.add_argument("--test-result")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--log", type=Path)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.durations:
            update_weights(args.durations, args.output)
            return 0
        if args.coverage_dir:
            validate_coverage(args.coverage_dir, args.test_result, args.slices)
            return 0
        if args.collection:
            output = args.collection.read_text()
        else:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", *SELECTION, "--collect-only", "-q", "-p", "no:cacheprovider"],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                sys.stderr.write(result.stdout + result.stderr)
                return result.returncode
            output = result.stdout
        files = collected_files(output)
        if args.validate:
            validate_partition(files, json.loads(args.validate.read_text()))
            return 0
        weights = json.loads(args.weights.read_text())
        buckets = partition(files, weights, args.slices)
        if args.summary:
            for index, bucket in enumerate(buckets, 1):
                seconds = sum(weights.get(path, DEFAULT_WEIGHT) for path in bucket)
                print(
                    f"Slice {index}: {len(bucket)} files, {sum(files[path] for path in bucket)} tests, "
                    f"{seconds / 60:.2f} weighted minutes"
                )
            return 0
        if args.index is None or not 1 <= args.index <= args.slices:
            raise ValueError("Specify --index within the slice count or --summary")
        bucket = buckets[args.index - 1]
        if args.run:
            extra = args.pytest_args
            if extra[:1] == ["--"]:
                extra = extra[1:]
            command = [sys.executable, "-m", "pytest", *bucket, *SELECTION, *extra]
            if args.log:
                with (
                    args.log.open("w") as log,
                    subprocess.Popen(
                        command,
                        cwd=ROOT,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    ) as process,
                ):
                    if process.stdout is None:
                        raise ValueError("Missing pytest output pipe")
                    for line in process.stdout:
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        log.write(line)
                    return process.wait()
            return subprocess.call(command, cwd=ROOT)
        print("\n".join(bucket))
        return 0
    except (ValueError, OSError, TypeError) as error:
        print(f"test_slices: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
