"""Advisory gate for the manually vendored JavaScript libraries.

``npm audit`` only reads ``package-lock.json``; SortableJS and axe-core are
vendored without a lock entry and stayed outside every advisory check.
``scripts/audit_vendored_js.py`` sends the versions from the shared vendor
inventory to the same registry endpoint ``npm audit`` uses and applies the same
threshold. The HTTP call is injected, so no test touches the network.

A check that could not be completed must never look like "0 findings": network,
HTTP and JSON errors as well as an unusable inventory exit with code 2.
"""

from __future__ import annotations

import datetime as dt
import http.client
import http.server
import importlib.util
import json
import shutil
import subprocess  # noqa: S404
import sys
import threading
import urllib.error
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "audit_vendored_js.py"
INVENTORY = REPO_ROOT / "scripts" / "vendor_js_inventory.json"
# Exceptions may run at most 90 days; fixtures expire well inside that window.
VALID_UNTIL = (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=30)).isoformat()


def _module():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("audit_vendored_js_under_test", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPT.parent))


def _advisory(severity: str, ghsa: str = "GHSA-aaaa-bbbb-cccc", advisory_id: int = 1100001) -> dict:
    return {
        "id": advisory_id,
        "url": f"https://github.com/advisories/{ghsa}",
        "title": "Controlled test advisory",
        "severity": severity,
        "vulnerable_versions": "<=99.0.0",
    }


class FakeRegistry:
    """Stands in for the HTTP call: records the request, returns a canned body or raises."""

    def __init__(self, response: object = None, *, raw: bytes | None = None, error: Exception | None = None):
        self.raw = raw if raw is not None else json.dumps(response if response is not None else {}).encode()
        self.error = error
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> bytes:
        self.requests.append((url, json.loads(body)))
        if self.error is not None:
            raise self.error
        return self.raw


def _exceptions_file(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "exceptions.json"
    path.write_text(json.dumps({"exceptions": entries}), encoding="utf-8")
    return path


def _run(
    tmp_path,
    fetch,
    *args,
    root: Path = REPO_ROOT,
    exceptions: list[dict] | None = None,
    level="moderate",
    module=None,
):
    exc_path = _exceptions_file(tmp_path, exceptions or [])
    argv = ["--root", str(root), "--audit-level", level, "--exceptions", str(exc_path), *args]
    return (module or _module()).main(argv, fetch=fetch)


def _inventory_packages() -> dict[str, str]:
    data = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return {lib["package"]: lib["version"] for lib in data["libraries"]}


def _tree(tmp_path: Path, inventory_text: str | None) -> Path:
    """Copy of the inventory files and vendored builds; ``inventory_text`` replaces the JSON."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "vendor_js_inventory.py", root / "scripts")
    shutil.copytree(REPO_ROOT / "src" / "static" / "js" / "vendor", root / "src" / "static" / "js" / "vendor")
    if inventory_text is not None:
        (root / "scripts" / "vendor_js_inventory.json").write_text(inventory_text, encoding="utf-8")
    return root


class _StubRegistryHandler(http.server.BaseHTTPRequestHandler):
    """Loopback stand-in for the registry: the bulk path answers with ``status``,
    every other path with an empty (clean) advisory list."""

    status = 302
    seen: list[tuple[str, str]] = []

    def _answer(self) -> None:
        type(self).seen.append((self.command, self.path))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if self.path.endswith("/bulk") and self.status != 200:
            self.send_response(self.status)
            self.send_header("Location", "/elsewhere/clean")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer  # noqa: N815 - name required by BaseHTTPRequestHandler
    do_POST = _answer  # noqa: N815 - name required by BaseHTTPRequestHandler

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def stub_registry():
    """Start the loopback stub; yields ``(handler_class, bulk_url)``. No outside network."""
    handler = type("Handler", (_StubRegistryHandler,), {"seen": []})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield handler, f"http://127.0.0.1:{server.server_port}/-/npm/v1/security/advisories/bulk"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestRedirectsAreNotFollowed:
    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    def test_redirect_of_the_endpoint_is_an_incomplete_check(self, tmp_path, capsys, stub_registry, status):
        handler, url = stub_registry
        handler.status = status
        module = _module()
        assert _run(tmp_path, module.http_post, "--url", url, "--timeout", "5", module=module) == 2
        err = capsys.readouterr().err
        assert "incomplete" in err and f"redirect ({status})" in err
        assert handler.seen == [("POST", "/-/npm/v1/security/advisories/bulk")]

    def test_direct_answer_through_the_real_transport_still_counts(self, tmp_path, capsys, stub_registry):
        handler, url = stub_registry
        handler.status = 200
        module = _module()
        assert _run(tmp_path, module.http_post, "--url", url, "--timeout", "5", module=module) == 0
        assert handler.seen == [("POST", "/-/npm/v1/security/advisories/bulk")]


class TestBlocking:
    def test_advisory_for_a_manually_vendored_library_blocks(self, tmp_path, capsys):
        registry = FakeRegistry({"sortablejs": [_advisory("high")]})
        assert _run(tmp_path, registry) == 1
        out = capsys.readouterr()
        assert "sortablejs@1.15.7" in out.out + out.err
        assert "GHSA-aaaa-bbbb-cccc" in out.out + out.err

    def test_request_carries_every_inventory_library_with_its_shipped_version(self, tmp_path):
        registry = FakeRegistry({})
        _run(tmp_path, registry)
        assert len(registry.requests) == 1
        url, body = registry.requests[0]
        assert url == "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk"
        assert body == {name: [version] for name, version in _inventory_packages().items()}
        assert {"sortablejs", "axe-core"} <= set(body)

    def test_clean_response_passes_and_reports_each_library(self, tmp_path, capsys):
        assert _run(tmp_path, FakeRegistry({})) == 0
        out = capsys.readouterr().out
        for name, version in _inventory_packages().items():
            assert f"{name}@{version}" in out

    def test_advisory_below_the_threshold_passes_but_is_reported(self, tmp_path, capsys):
        registry = FakeRegistry({"axe-core": [_advisory("low")]})
        assert _run(tmp_path, registry) == 0
        assert "GHSA-aaaa-bbbb-cccc" in capsys.readouterr().out

    @pytest.mark.parametrize(("level", "expected"), [("moderate", 1), ("high", 0), ("low", 1)])
    def test_threshold_follows_the_audit_level(self, tmp_path, level, expected):
        registry = FakeRegistry({"axe-core": [_advisory("moderate")]})
        assert _run(tmp_path, registry, level=level) == expected


class TestIncompleteCheckIsNeverGreen:
    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.URLError("connection refused"),
            urllib.error.HTTPError("https://registry", 503, "Service Unavailable", {}, None),
            TimeoutError("timed out"),
            http.client.IncompleteRead(b"{", 10),
            http.client.BadStatusLine("garbage"),
        ],
    )
    def test_transport_errors_fail(self, tmp_path, capsys, error):
        assert _run(tmp_path, FakeRegistry(error=error)) == 2
        assert "0 advisories" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        "raw",
        [
            b"<html>gateway</html>",
            b"[]",
            json.dumps({"sortablejs": {"id": 1}}).encode(),
            json.dumps({"sortablejs": [{"id": 1, "url": "x"}]}).encode(),
            json.dumps({"sortablejs": [_advisory("dramatic")]}).encode(),
            json.dumps({"left-pad": [_advisory("high")]}).encode(),
        ],
        ids=["not-json", "not-an-object", "not-a-list", "no-severity", "unknown-severity", "unrequested-package"],
    )
    def test_unusable_responses_fail(self, tmp_path, raw):
        assert _run(tmp_path, FakeRegistry(raw=raw)) == 2

    def test_unreachable_registry_through_the_real_transport_fails(self, tmp_path):
        exc_path = _exceptions_file(tmp_path, [])
        result = subprocess.run(  # noqa: S603
            [
                sys.executable,
                str(SCRIPT),
                "--audit-level",
                "moderate",
                "--exceptions",
                str(exc_path),
                "--url",
                "http://127.0.0.1:9/-/npm/v1/security/advisories/bulk",
                "--timeout",
                "5",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert "incomplete" in result.stderr

    @pytest.mark.parametrize(
        "inventory_text",
        [
            None,
            "{not json",
            json.dumps({"libraries": []}),
            json.dumps({"libraries": [{"package": "sortablejs", "path": "src/static/js/vendor/sortable.min.js"}]}),
        ],
        ids=["missing", "unreadable", "empty", "no-version"],
    )
    def test_unusable_inventory_fails_without_querying(self, tmp_path, inventory_text):
        root = _tree(tmp_path, inventory_text)
        if inventory_text is None:
            (root / "scripts" / "vendor_js_inventory.json").unlink(missing_ok=True)
        registry = FakeRegistry({})
        assert _run(tmp_path, registry, root=root) == 2
        assert registry.requests == []

    def test_inventory_version_that_differs_from_the_shipped_banner_fails(self, tmp_path):
        text = INVENTORY.read_text(encoding="utf-8").replace('"1.15.7"', '"1.15.6"')
        registry = FakeRegistry({})
        assert _run(tmp_path, registry, root=_tree(tmp_path, text)) == 2
        assert registry.requests == []

    def test_changed_file_body_with_unchanged_banner_fails_without_querying(self, tmp_path):
        root = _tree(tmp_path, INVENTORY.read_text(encoding="utf-8"))
        shipped = root / "src" / "static" / "js" / "vendor" / "sortable.min.js"
        shipped.write_bytes(shipped.read_bytes() + b"\n/* tampered */\n")
        registry = FakeRegistry({})
        assert _run(tmp_path, registry, root=root) == 2
        assert registry.requests == []

    def test_shipped_file_that_is_not_utf8_fails_without_querying(self, tmp_path):
        root = _tree(tmp_path, INVENTORY.read_text(encoding="utf-8"))
        (root / "src" / "static" / "js" / "vendor" / "sortable.min.js").write_bytes(b"/*! Sortable \xff\xfe */")
        registry = FakeRegistry({})
        assert _run(tmp_path, registry, root=root) == 2
        assert registry.requests == []

    def test_unknown_audit_level_is_rejected(self, tmp_path):
        with pytest.raises(SystemExit) as exc:
            _run(tmp_path, FakeRegistry({}), level="severe")
        assert exc.value.code == 2


class TestExceptions:
    def _entry(self, **overrides) -> dict:
        entry = {
            "package": "sortablejs",
            "advisory": "GHSA-aaaa-bbbb-cccc",
            "reason": "Affected API is not used; upstream fix pending.",
            "expires": VALID_UNTIL,
        }
        return entry | overrides

    def test_valid_exception_suppresses_the_finding_and_is_reported(self, tmp_path, capsys):
        registry = FakeRegistry({"sortablejs": [_advisory("critical")]})
        assert _run(tmp_path, registry, exceptions=[self._entry()]) == 0
        out = capsys.readouterr().out
        assert "GHSA-aaaa-bbbb-cccc" in out
        assert VALID_UNTIL in out
        assert "Affected API is not used" in out

    def test_expired_exception_blocks(self, tmp_path, capsys):
        registry = FakeRegistry({"sortablejs": [_advisory("critical")]})
        assert _run(tmp_path, registry, exceptions=[self._entry(expires="2000-01-01")]) == 1
        assert "expired" in capsys.readouterr().err

    def test_expired_exception_blocks_even_without_a_matching_finding(self, tmp_path):
        assert _run(tmp_path, FakeRegistry({}), exceptions=[self._entry(expires="2000-01-01")]) == 1

    def test_exception_for_another_advisory_does_not_apply(self, tmp_path):
        registry = FakeRegistry({"sortablejs": [_advisory("high")]})
        assert _run(tmp_path, registry, exceptions=[self._entry(advisory="GHSA-zzzz-zzzz-zzzz")]) == 1

    @pytest.mark.parametrize(
        "overrides",
        [{"reason": ""}, {"expires": "someday"}, {"advisory": ""}, {"package": ""}],
        ids=["no-reason", "bad-date", "no-advisory", "no-package"],
    )
    def test_malformed_exception_fails(self, tmp_path, overrides):
        assert _run(tmp_path, FakeRegistry({}), exceptions=[self._entry(**overrides)]) == 2

    def test_expiry_far_in_the_future_fails(self, tmp_path, capsys):
        registry = FakeRegistry({"sortablejs": [_advisory("critical")]})
        assert _run(tmp_path, registry, exceptions=[self._entry(expires="9999-12-31")]) == 2
        assert "more than 90 days" in capsys.readouterr().err
        assert registry.requests == []

    def test_expiry_far_in_the_future_fails_the_exceptions_check(self, tmp_path):
        path = _exceptions_file(tmp_path, [self._entry(expires="9999-12-31")])
        argv = ["--audit-level", "moderate", "--check-exceptions", "--exceptions", str(path)]
        assert _module().main(argv, fetch=FakeRegistry({})) == 2

    @pytest.mark.parametrize(("days", "valid"), [(0, True), (90, True), (91, False), (3650, False)])
    def test_expiry_is_capped_at_90_days_after_today(self, tmp_path, days, valid):
        module = _module()
        today = dt.date(2026, 11, 5)
        path = _exceptions_file(tmp_path, [self._entry(expires=(today + dt.timedelta(days=days)).isoformat())])
        if valid:
            assert module.load_exceptions(path, today)[0]["expires"] == today + dt.timedelta(days=days)
        else:
            with pytest.raises(module.IncompleteCheckError, match="more than 90 days"):
                module.load_exceptions(path, today)

    def test_missing_exceptions_file_fails(self, tmp_path):
        argv = ["--audit-level", "moderate", "--exceptions", str(tmp_path / "absent.json")]
        assert _module().main(argv, fetch=FakeRegistry({})) == 2

    def test_versioned_exceptions_file_is_valid_today(self):
        """The checked-in file parses and carries no expired entry."""
        assert _module().main(["--audit-level", "moderate", "--check-exceptions"], fetch=FakeRegistry({})) == 0
