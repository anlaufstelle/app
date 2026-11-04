#!/usr/bin/env python3
"""Run the nightly guards and report every verdict before exiting non-zero.

The guards cover the failures that surfaced only at the release cut of wave U
(each costing a dry run plus a stage rehearsal):

* ``perf-medium`` / ``perf-large`` -- the statistics cards and the hybrid PDF
  against a disposable seed database of that scale, judged by the median per
  report and by every card publishing at least one number.
* ``demo`` -- ``make test-e2e-demo`` with the complete demo spec list.
* ``public-skips`` -- the unit suite in the public skip profile against a
  local public snapshot (tracked files minus the release strip list), so an
  unapproved skip is caught before the public CI sees it.
* ``docs-screens`` -- ``make docs-screens`` into a scratch directory; the
  screenshot command itself fails on an offline fallback or an error page.

Every guard runs even after a red one; the summary at the end lists all
verdicts and the exit code is 1 if any guard failed. A guard that cannot run
(no database, no strip list) is reported as skipped with its reason and does
not fail the run unless ``--fail-on-skip`` is given.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BUILD_RELEASE = ROOT / "dev-ops/release/build-release.sh"
SANITIZER = ROOT / "dev-ops/release/sanitize_release_tree.py"
PERF_SCRIPT = SCRIPTS / "check_statistics_performance.py"
PERF_SETTINGS = "anlaufstelle.settings.test"
STAMP_PREFIX = "nightly-seed"
STAMP_RE = re.compile(
    r"^nightly-seed scale=(?P<scale>\w+) seeded=(?P<seeded>\S+)(?: head=(?P<head>\S+))?(?: code=(?P<code>\w+))?$"
)
#: Files whose content decides what ``manage.py seed`` generates; a change forces a reseed.
SEED_CODE_GLOBS = (
    "src/core/seed/*.py",
    "src/core/management/commands/seed.py",
    # What the seed imports and the data it loads: package catalogues (YAML + loader), the facility
    # bootstrap, the demo scenario module, file encryption, contact-stage ordering and constants.
    "src/core/packages/**/*.yaml",
    "src/core/packages/**/*.py",
    "src/core/services/facility_bootstrap.py",
    "src/core/services/dynamic_protection.py",
    "src/core/services/file_vault/**/*.py",
    "src/core/services/events/fields.py",
    "src/core/demo.py",
    "src/core/constants.py",
)
GUARD_ORDER = ("perf-medium", "perf-large", "demo", "public-skips", "docs-screens")
PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
NO_DATABASE = "skipped: no database"


@dataclass
class Result:
    name: str
    status: str
    seconds: float = 0.0
    detail: str = ""
    log: Path | None = None


@dataclass
class SeedStamp:
    scale: str
    seeded: datetime
    head: str = ""
    code: str = ""

    def render(self) -> str:
        head = f" head={self.head}" if self.head else ""
        code = f" code={self.code}" if self.code else ""
        return f"{STAMP_PREFIX} scale={self.scale} seeded={self.seeded.isoformat()}{head}{code}"


def parse_seed_stamp(comment: str | None) -> SeedStamp | None:
    """Read the seed stamp stored as the database comment; ``None`` if absent or foreign."""
    if not comment:
        return None
    match = STAMP_RE.match(comment.strip())
    if match is None:
        return None
    try:
        seeded = datetime.fromisoformat(match["seeded"])
    except ValueError:
        return None
    if seeded.tzinfo is None:
        seeded = seeded.replace(tzinfo=UTC)
    return SeedStamp(match["scale"], seeded, match["head"] or "", match["code"] or "")


def seed_code_hash() -> str:
    """Short hash over the seed modules (path and content); changes whenever the seed output can."""
    digest = hashlib.sha256()
    for pattern in SEED_CODE_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def seed_is_fresh(
    stamp: SeedStamp | None,
    scale: str,
    max_age_days: float,
    now: datetime | None = None,
    code: str | None = None,
) -> bool:
    """A seed is reusable when it exists, has the wanted scale, matches the seed code and is young enough.

    With ``code`` given, a stamp without or with another code hash is stale.
    """
    if stamp is None or stamp.scale != scale:
        return False
    if code is not None and stamp.code != code:
        return False
    now = now or datetime.now(UTC)
    return now - stamp.seeded <= timedelta(days=max_age_days)


def exit_code(results: list[Result], *, fail_on_skip: bool = False) -> int:
    """Red when a guard failed; skipped guards only count with ``fail_on_skip``."""
    statuses = {result.status for result in results}
    if FAIL in statuses or (fail_on_skip and SKIP in statuses):
        return 1
    return 0


def summary_lines(results: list[Result]) -> list[str]:
    lines = ["== nightly guards =="]
    width = max((len(result.name) for result in results), default=4)
    for result in results:
        log = f"  [{result.log}]" if result.log else ""
        lines.append(f"{result.name.ljust(width)}  {result.status}  {result.seconds:7.0f} s  {result.detail}{log}")
    return lines


def environment() -> dict[str, str]:
    """The process environment over the ``.env`` file, as the Django settings load it."""
    env = {key: value for key, value in dotenv_values(ROOT / ".env").items() if value is not None}
    env.update(os.environ)
    return env


def database_params() -> dict[str, str]:
    """Connection parameters the way the development settings resolve them."""
    env = environment()
    return {
        "host": env.get("POSTGRES_HOST", "localhost"),
        "port": env.get("POSTGRES_PORT", "5432"),
        "user": env.get("POSTGRES_USER", "anlaufstelle"),
        "password": env.get("POSTGRES_PASSWORD") or "anlaufstelle",
    }


def maintenance_connection():
    """Autocommit connection to the ``postgres`` maintenance database, or ``None``."""
    try:
        import psycopg
    except ImportError:
        return None
    try:
        return psycopg.connect(dbname="postgres", autocommit=True, connect_timeout=5, **database_params())
    except psycopg.OperationalError:
        return None


def public_run_env(snapshot: Path) -> dict[str, str]:
    """Environment of the public run: the process environment over the ``.env`` values of the dev tree.

    The values live only in the process, never as a file in the snapshot. ``.env`` sets
    ``DJANGO_SETTINGS_MODULE`` for development, which would override the test settings of the
    snapshot's ``pyproject.toml``; those are set explicitly.
    """
    import tomllib

    options = tomllib.loads((snapshot / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    return {**environment(), "DJANGO_SETTINGS_MODULE": options["DJANGO_SETTINGS_MODULE"]}


def resolve_python(python: str) -> str:
    """Absolute interpreter path: the public snapshot runs with another working directory."""
    if "/" in python:
        return str(Path(python).absolute())
    return shutil.which(python) or python


def ensure_database(connection, database: str) -> bool:
    """Create ``database`` if it is missing, as the E2E conftest does; True when created."""
    from psycopg import sql

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
        if cursor.fetchone():
            return False
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    return True


def git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


@contextlib.contextmanager
def host_lock(path: Path, timeout: float) -> Iterator[None]:
    """The shared host lock of the ``-locked`` make targets, waited for up to ``timeout`` seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open("w") as handle:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"host lock {path} busy for {timeout:.0f} s") from None
                time.sleep(2)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.python = resolve_python(args.python)
        self.state = Path(args.state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.seed_max_age_days = args.seed_max_age_days
        self.perf_limit = args.perf_limit
        self.perf_runs = args.perf_runs
        # Same chain as the Makefile: CI_LOCK, else XDG_RUNTIME_DIR, TMPDIR or the system temp dir.
        lock_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self.lock_path = Path(os.environ.get("CI_LOCK") or Path(lock_dir) / "anlaufstelle-ci.lock")
        self.lock_timeout = float(os.environ.get("CI_LOCK_TIMEOUT", "14400"))

    # -- process helpers -------------------------------------------------

    def run(
        self,
        command: list[str],
        log: Path,
        *,
        env: dict[str, str] | None = None,
        cwd: Path = ROOT,
        append: bool = False,
    ) -> int:
        """Run a command, mirroring its output to the terminal and to ``log``."""
        print(f"$ {' '.join(command)}", flush=True)
        with log.open("a" if append else "w") as handle:
            handle.write(f"$ {' '.join(command)}\n")
            handle.flush()
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace"
            )
            for line in process.stdout or ():
                handle.write(line)
                sys.stdout.write(line)
            sys.stdout.flush()
            return process.wait()

    def make(self, target: str, log: Path, *extra: str) -> int:
        return self.run(["make", target, f"PYTHON={self.python}", *extra], log, env=dict(os.environ))

    # -- perf guard ------------------------------------------------------

    def perf(self, scale: str) -> Result:
        name = f"perf-{scale}"
        database = f"anlaufstelle_nightly_{scale}"
        log = self.state / f"{name}.log"
        connection = maintenance_connection()
        if connection is None:
            return Result(name, SKIP, detail=NO_DATABASE)
        with connection, self.lock():
            stamp = self.seed_stamp(connection, database)
            if seed_is_fresh(stamp, scale, self.seed_max_age_days, code=seed_code_hash()):
                age = datetime.now(UTC) - stamp.seeded
                seed_note = f"seed reused ({age.days} d old)"
                # A migration added since the seed was built must not be measured against the old schema.
                migrate_log = self.state / f"{name}-migrate.log"
                code = self.run(
                    [self.python, "src/manage.py", "migrate", "--run-syncdb"],
                    migrate_log,
                    env={**os.environ, "POSTGRES_DB": database, "DJANGO_SETTINGS_MODULE": PERF_SETTINGS},
                )
                if code != 0:
                    return Result(
                        name, FAIL, detail=f"migrate on the reused seed failed with exit {code}", log=migrate_log
                    )
            else:
                why = "missing" if stamp is None else f"{stamp.scale}, {(datetime.now(UTC) - stamp.seeded).days} d old"
                if stamp is not None and stamp.scale == scale and stamp.code != seed_code_hash():
                    why += ", seed code changed"
                print(f"[{name}] rebuilding {database}: seed {why}", flush=True)
                seed_log = self.state / f"{name}-seed.log"
                code = self.rebuild_seed(connection, database, scale, seed_log)
                if code != 0:
                    return Result(name, FAIL, detail=f"seed --scale {scale} failed with exit {code}", log=seed_log)
                seed_note = "seed rebuilt"
            output = self.state / f"{name}.json"
            command = [
                self.python,
                str(PERF_SCRIPT),
                "--scale",
                scale,
                "--runs",
                str(self.perf_runs),
                "--max-seconds",
                str(self.perf_limit),
                "--verdict",
                "median",
                "--every-card",
                "--output",
                str(output),
            ]
            env = {**os.environ, "POSTGRES_DB": database, "DJANGO_SETTINGS_MODULE": PERF_SETTINGS}
            code = self.run(command, log, env=env)
        detail = f"{self.perf_detail(output)}; {seed_note}"
        return Result(name, PASS if code == 0 else FAIL, detail=detail, log=log)

    def lock(self):
        return host_lock(self.lock_path, self.lock_timeout)

    @staticmethod
    def seed_stamp(connection, database: str) -> SeedStamp | None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s", (database,)
            )
            row = cursor.fetchone()
        return parse_seed_stamp(row[0]) if row else None

    def rebuild_seed(self, connection, database: str, scale: str, log: Path) -> int:
        from psycopg import sql

        identifier = sql.Identifier(database)
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(identifier))
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(identifier))
        env = {**os.environ, "POSTGRES_DB": database, "DJANGO_SETTINGS_MODULE": PERF_SETTINGS}
        manage = [self.python, "src/manage.py"]
        log.write_text("")
        for command in ([*manage, "migrate", "--run-syncdb"], [*manage, "seed", "--flush", "--scale", scale]):
            code = self.run(command, log, env=env, append=True)
            if code != 0:
                return code
        stamp = SeedStamp(scale, datetime.now(UTC).replace(microsecond=0), git_head(), seed_code_hash())
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(identifier, sql.Literal(stamp.render())))
        return 0

    @staticmethod
    def perf_detail(output: Path) -> str:
        if not output.is_file():
            return "no measurement written"
        data = json.loads(output.read_text())
        reports = data.get("reports", [])
        if not reports:
            return "no report measured"
        slowest = max(reports, key=lambda row: row["median"])
        empty = [row["report"] for row in reports if not row.get("record_numeric_visible")]
        slow = [row["report"] for row in reports if not row["passed"]]
        detail = f"{len(reports)} reports, slowest median {slowest['median']:.2f} s ({slowest['report']})"
        if slow:
            detail += f"; over {data['limit']} s: {', '.join(slow)}"
        if empty:
            detail += f"; no published number: {', '.join(empty)}"
        return detail

    # -- make-backed guards ----------------------------------------------

    def demo(self) -> Result:
        if maintenance_connection() is None:
            return Result("demo", SKIP, detail=NO_DATABASE)
        log = self.state / "demo.log"
        code = self.make("test-e2e-demo", log)
        return Result("demo", PASS if code == 0 else FAIL, detail=self.pytest_detail(log), log=log)

    def docs_screens(self) -> Result:
        connection = maintenance_connection()
        if connection is None:
            return Result("docs-screens", SKIP, detail=NO_DATABASE)
        # make docs-screens migrates the E2E database but does not create it;
        # a fresh host has it only after a pytest E2E run created it.
        with connection:
            ensure_database(connection, environment().get("E2E_DATABASE_NAME", "anlaufstelle_e2e"))
        target = self.state / "screenshots"
        log = self.state / "docs-screens.log"
        code = self.make("docs-screens", log, f"DOCS_SCREENS_DIR={target}")
        match = re.search(r"Screenshots erzeugt: (\d+) ok / (\d+) fehlgeschlagen", log.read_text())
        detail = f"{match[1]} ok / {match[2]} failed, {target}" if match else f"make exit {code}, {target}"
        return Result("docs-screens", PASS if code == 0 else FAIL, detail=detail, log=log)

    @staticmethod
    def pytest_detail(log: Path) -> str:
        """The last pytest result line, e.g. ``6 passed, 2 skipped``."""
        text = log.read_text() if log.is_file() else ""
        matches = re.findall(
            r"(\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|warnings?)[^\n=]*?) in [\d.]+s", text
        )
        return matches[-1] if matches else "no pytest summary"

    # -- public skip profile -----------------------------------------------

    def public_skips(self) -> Result:
        name = "public-skips"
        if not BUILD_RELEASE.is_file():
            return Result(name, SKIP, detail="skipped: no strip list (public tree)")
        if maintenance_connection() is None:
            return Result(name, SKIP, detail=NO_DATABASE)
        snapshot = self.state / "public-tree"
        stripped = self.build_public_snapshot(snapshot)
        print(f"[{name}] public snapshot at {snapshot}: {stripped} stripped paths", flush=True)
        # The release build sanitizes the stripped tree (repository links, ticket
        # numbers); guards that compare against the public form need that step.
        sanitize_log = self.state / f"{name}-sanitize.log"
        code = self.run([self.python, str(SANITIZER), str(snapshot)], sanitize_log, env=dict(os.environ))
        if code != 0:
            return Result(name, FAIL, detail=f"sanitizer failed with exit {code}", log=sanitize_log)
        self.commit_snapshot(snapshot)
        inventory = self.state / "public-skips.json"
        log = self.state / f"{name}.log"
        command = [
            self.python,
            "-m",
            "pytest",
            "src/tests",
            "--ignore=src/tests/e2e",
            "-x",
            "-q",
            "-rs",
            "-n",
            "auto",
            "-p",
            "no:cacheprovider",
            "--test-profile",
            "public",
            "--skip-inventory",
            str(inventory),
        ]
        with self.lock():
            code = self.run(command, log, env=public_run_env(snapshot), cwd=snapshot)
        text = log.read_text()
        unapproved = re.findall(r"^Unapproved skip \[public\]: (.*)$", text, re.M)
        detail = self.pytest_detail(log)
        if unapproved:
            detail += f"; unapproved: {'; '.join(unapproved)}"
        detail += f"; inventory {inventory}"
        return Result(name, PASS if code == 0 else FAIL, detail=detail, log=log)

    @staticmethod
    def commit_snapshot(snapshot: Path) -> None:
        """One commit, as the public clone has one: several guards call git in the tree."""
        git = ["git", "-c", "user.name=nightly", "-c", "user.email=nightly@localhost", "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "init", "-q"], cwd=snapshot, check=True)
        subprocess.run([*git, "add", "-A"], cwd=snapshot, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "public snapshot"], cwd=snapshot, check=True, capture_output=True)

    @staticmethod
    def build_public_snapshot(snapshot: Path) -> int:
        """Tracked files (as in the working tree) minus the release strip list: what a release would contain."""
        sys.path.insert(0, str(SCRIPTS))
        from verify_release_test_guard import parse_stripped_paths_from_build_release

        if snapshot.exists():
            shutil.rmtree(snapshot)
        snapshot.mkdir(parents=True)
        listing = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        for relative in filter(None, listing.stdout.decode().split("\0")):
            source = ROOT / relative
            if not source.is_file():
                continue
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        stripped = 0
        for relative in parse_stripped_paths_from_build_release(BUILD_RELEASE):
            path = snapshot / relative
            if path.is_dir():
                shutil.rmtree(path)
                stripped += 1
            elif path.exists():
                path.unlink()
                stripped += 1
        # Build artefacts are ignored by git but needed by a few guards. No .env file is copied: the
        # values a run needs come from the environment at run time (see public_run_env).
        for relative in ("node_modules", "src/static/css/styles.css"):
            source = ROOT / relative
            if source.exists():
                (snapshot / relative).symlink_to(source)
        return stripped

    # -- dispatch ----------------------------------------------------------

    def guards(self) -> dict[str, Callable[[], Result]]:
        return {
            "perf-medium": lambda: self.perf("medium"),
            "perf-large": lambda: self.perf("large"),
            "demo": self.demo,
            "public-skips": self.public_skips,
            "docs-screens": self.docs_screens,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "guards", nargs="*", metavar="GUARD", help=f"subset to run (default: all): {', '.join(GUARD_ORDER)}"
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--state-dir", default=str(Path.home() / ".local/state/anlaufstelle-nightly"))
    parser.add_argument("--seed-max-age-days", type=float, default=7)
    parser.add_argument("--perf-limit", type=float, default=2, help="median budget per report in seconds")
    parser.add_argument("--perf-runs", type=int, default=3)
    parser.add_argument("--fail-on-skip", action="store_true", help="a skipped guard also ends the run red")
    parser.add_argument("--list", action="store_true", help="print the guard names and exit")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(GUARD_ORDER))
        return 0
    unknown = sorted(set(args.guards) - set(GUARD_ORDER))
    if unknown:
        parser.error(f"unknown guard(s): {', '.join(unknown)} -- known: {', '.join(GUARD_ORDER)}")
    runner = Runner(args)
    selected = [name for name in GUARD_ORDER if not args.guards or name in args.guards]
    results: list[Result] = []
    for name in selected:
        print(f"\n### {name}", flush=True)
        started = time.monotonic()
        try:
            result = runner.guards()[name]()
        except KeyboardInterrupt:
            results.append(Result(name, FAIL, time.monotonic() - started, "interrupted"))
            break
        except Exception as error:  # noqa: BLE001 -- one broken guard must not hide the others
            result = Result(name, FAIL, detail=f"{type(error).__name__}: {error}")
        result.seconds = time.monotonic() - started
        results.append(result)
        print(f"[{name}] {result.status}: {result.detail}", flush=True)
    print()
    print("\n".join(summary_lines(results)), flush=True)
    return exit_code(results, fail_on_skip=args.fail_on_skip)


if __name__ == "__main__":
    raise SystemExit(main())
