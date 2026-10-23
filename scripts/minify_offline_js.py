#!/usr/bin/env python3
"""Build or check versioned offline scripts using the pinned local esbuild."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = (
    "crypto",
    "offline-db",
    "offline-bundles",
    "offline-revocations",
    "offline-store",
    "offline-queue",
    "offline-client",
    "offline-edit",
    "sync-orchestrator",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check artifacts without writing files.")
    args = parser.parse_args(argv)
    esbuild = ROOT / "node_modules/.bin/esbuild"
    if not esbuild.is_file():
        print("Local esbuild is missing; run 'npm ci' first.", file=sys.stderr)
        return 2

    failed = False
    for name in SOURCES:
        source = ROOT / f"src/static/js/{name}.js"
        target = source.with_suffix(".min.js")
        # Transform each classic script independently. Keep global names and format.
        result = subprocess.run(
            [str(esbuild), "--minify-whitespace", "--minify-syntax", "--log-level=error"],
            input=source.read_bytes(),
            capture_output=True,
            check=False,
            cwd=ROOT,
        )
        if result.returncode:
            print(f"{source.name}: {result.stderr.decode()}", file=sys.stderr)
            return 2
        if args.check:
            if not target.is_file() or target.read_bytes() != result.stdout:
                print(f"{target.name}: build drift; run 'make minify-offline-js'.", file=sys.stderr)
                failed = True
            else:
                print(f"{target.name}: up to date")
        else:
            target.write_bytes(result.stdout)
            print(f"{source.name}: {source.stat().st_size} -> {len(result.stdout)} bytes")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
