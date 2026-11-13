"""Exercise E2E server allocation and cleanup without starting servers or browsers."""

import importlib.util
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def e2e_servers(monkeypatch):
    path = Path(__file__).parent / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_e2e_servers_under_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("port_base", [None, 8854, 8944])
@pytest.mark.parametrize("worker_num, worker_count", [(0, 1), (0, 4), (3, 4), (101, 128)])
def test_enforced_server_uses_separate_port_range(e2e_servers, monkeypatch, port_base, worker_num, worker_count):
    if port_base is None:
        monkeypatch.delenv("E2E_PORT_BASE", raising=False)
    else:
        monkeypatch.setenv("E2E_PORT_BASE", str(port_base))
    primary_base = 8844 if port_base is None else port_base
    request = SimpleNamespace(
        config=SimpleNamespace(workerinput={"workerid": f"gw{worker_num}", "workercount": worker_count})
    )
    process = Mock()
    start = Mock(return_value=process)
    cleanup = Mock()
    ready = Mock()
    monkeypatch.setattr(e2e_servers.subprocess, "Popen", start)
    monkeypatch.setattr(e2e_servers, "_kill_port", cleanup)
    monkeypatch.setattr(e2e_servers, "_wait_for_server", ready)
    fixture = e2e_servers.enforced_base_url.__wrapped__(request, f"http://127.0.0.1:{primary_base + worker_num}")

    url = next(fixture)
    try:
        port = int(url.rsplit(":", 1)[1])
        assert port == primary_base + max(100, worker_count) + worker_num
        assert port not in range(primary_base, primary_base + worker_count)
        command = start.call_args.args[0]
        assert command[command.index("--bind") + 1] == f"127.0.0.1:{port}"
        assert start.call_args.kwargs["env"]["E2E_MFA_ENFORCE_ROLES"] == "1"
        cleanup.assert_called_once_with(port)
        ready.assert_called_once_with(url)
    finally:
        with pytest.raises(StopIteration):
            next(fixture)
    process.send_signal.assert_called_once_with(signal.SIGTERM)
    process.wait.assert_called_once_with(timeout=10)


def test_port_cleanup_only_terminates_own_child(e2e_servers, monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline() == "ready\n"
        lookup = Mock(return_value=SimpleNamespace(stdout=f"{os.getpid()}\n{child.pid}\ninvalid\n"))
        original_kill = os.kill

        def kill_child(pid, sig):
            assert pid == child.pid, "Cleanup attempted to signal a foreign process"
            original_kill(pid, sig)

        kill = Mock(side_effect=kill_child)
        with monkeypatch.context() as cleanup_patch:
            cleanup_patch.setattr(e2e_servers.subprocess, "run", lookup)
            cleanup_patch.setattr(e2e_servers.os, "kill", kill)
            cleanup_patch.setattr(
                e2e_servers.time, "sleep", Mock(side_effect=AssertionError("Use process exit events"))
            )

            e2e_servers._kill_port(9044)

            kill.assert_called_once_with(child.pid, signal.SIGTERM)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()


def test_port_cleanup_without_lsof_does_not_use_broad_process_search(e2e_servers, monkeypatch):
    lookup = Mock(side_effect=FileNotFoundError)
    kill = Mock()
    monkeypatch.setattr(e2e_servers.subprocess, "run", lookup)
    monkeypatch.setattr(e2e_servers.os, "kill", kill)

    e2e_servers._kill_port(9044)

    assert lookup.call_count == 1
    kill.assert_not_called()
