#!/usr/bin/env python3
"""Require ShellCheck for maintained and shipped shell scripts."""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        print(
            "ShellCheck is required; installation: "
            ".venv/bin/python -m pip install --require-hashes -r requirements-dev.txt; "
            "add .venv/bin to PATH."
        )
        return 1
    scripts = sorted((args.root / "scripts").rglob("*.sh"))
    scripts += [path for name in ("docker-entrypoint.sh", "docker-migrate.sh") if (path := args.root / name).exists()]
    if not scripts:
        print("No shell scripts found; refusing an empty ShellCheck gate.")
        return 1
    result = subprocess.run([shellcheck, "--severity=error", *map(str, scripts)], check=False)
    return int(result.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
