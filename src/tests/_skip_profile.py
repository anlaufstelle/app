"""Record skip evidence and fail public runs on unapproved node/profile pairs."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path(__file__).with_name("public_skip_allowlist.json")
DEFAULT_PROFILE = (
    "public"
    if (ROOT / "docker-compose.prod.yml").is_file()
    and all(not (ROOT / name).exists() for name in ("dev-ops", "docs/dev", "AGENTS.md"))
    else "dev"
)


def pytest_addoption(parser):
    group = parser.getgroup("release profiles")
    group.addoption("--test-profile", choices=("dev", "public"), default=DEFAULT_PROFILE)
    group.addoption("--skip-allowlist", type=Path, default=DEFAULT_POLICY)
    group.addoption("--skip-inventory", type=Path)


class SkipInventory:
    def __init__(self, config):
        self.config = config
        self.skips = {}

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            reason = report.longrepr[2].removeprefix("Skipped: ")
            self.skips[(report.nodeid, reason)] = {
                "nodeid": report.nodeid,
                "profile": self.config.getoption("test_profile"),
                "reason": reason,
            }

    def pytest_collectreport(self, report):
        self.pytest_runtest_logreport(report)

    def pytest_sessionfinish(self, session):
        if hasattr(session.config, "workerinput"):
            return
        inventory = self.config.getoption("skip_inventory")
        records = sorted(self.skips.values(), key=lambda item: (item["nodeid"], item["reason"]))
        if inventory is not None:
            inventory.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        if self.config.getoption("test_profile") != "public":
            return
        terminal = self.config.pluginmanager.getplugin("terminalreporter")
        try:
            allowed = json.loads(self.config.getoption("skip_allowlist").read_text())
            if not isinstance(allowed, list) or any(
                not all(
                    isinstance(entry.get(key), str) and entry[key]
                    for key in ("nodeid", "profile", "reason", "exclusion", "rationale")
                )
                for entry in allowed
            ):
                raise ValueError("Every skip entry requires nodeid, profile, reason, exclusion and rationale")
            keys = {}
            for entry in allowed:
                key = (entry["nodeid"], entry["profile"], entry["reason"])
                if (self.config.rootpath / entry["exclusion"]).exists():
                    if any((record["nodeid"], record["profile"], record["reason"]) == key for record in records):
                        terminal.write_line(f"Skip prerequisite is present: {entry['exclusion']}", red=True)
                else:
                    keys[key] = entry
        except (OSError, ValueError, TypeError, AttributeError) as error:
            terminal.write_line(f"Public skip policy unavailable: {error}", red=True)
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return
        for record in records:
            if (record["nodeid"], record["profile"], record["reason"]) not in keys:
                terminal.write_line(
                    f"Unapproved skip [{record['profile']}]: {record['nodeid']} — {record['reason']}", red=True
                )
                session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_configure(config):
    if config.getoption("test_profile") == "public" and "s" not in config.option.reportchars:
        config.option.reportchars += "s"
    config.pluginmanager.register(SkipInventory(config), "release-skip-inventory")
