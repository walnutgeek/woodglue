"""
Process-level tests for `wgl start` shutdown: a signal must stop the engines,
exit 0, and remove `wgl.pid`.

Each test runs the real CLI in a subprocess. Readiness is a completed HTTP
request, which proves the IOLoop is running, so the signal handlers are installed
and `wgl.pid` is written.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from textwrap import dedent

import pytest
from tornado.httpclient import AsyncHTTPClient

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX signals cannot be sent to a subprocess on Windows"
)

TIMEOUT = 20.0

CONFIG = dedent("""
    auth:
      enabled: false
    namespaces:
      app:
        run_engine: true
        entries:
          - nsref: hello
            gref: "woodglue.hello:hello"
    """).lstrip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wgl_cmd(data_dir: Path, *args: str) -> list[str]:
    return [sys.executable, "-m", "woodglue.cli", f"--data={data_dir}", *args]


async def _wgl(data_dir: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *_wgl_cmd(data_dir, *args),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    assert proc.returncode == 0, out.decode()
    return out.decode()


async def _wait_ready(proc: asyncio.subprocess.Process, port: int) -> None:
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise AssertionError("wgl start exited before listening")
        if line.startswith(b"Woodglue listening"):
            break
    response = await AsyncHTTPClient().fetch(f"http://127.0.0.1:{port}/", raise_error=False)
    assert response.code != 599, response.error


@asynccontextmanager
async def _running_server(
    data_dir: Path, port: int, sigint: signal.Handlers = signal.SIG_DFL
) -> AsyncIterator[asyncio.subprocess.Process]:
    """
    Start `wgl start` with SIGINT set to `sigint`. Set explicitly because the
    test runner itself may inherit SIGINT as ignored (e.g. a background job).
    """
    (data_dir / "woodglue.yaml").write_text(CONFIG)
    proc = await asyncio.create_subprocess_exec(
        *_wgl_cmd(data_dir, f"--port={port}", "start"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        preexec_fn=lambda: signal.signal(signal.SIGINT, sigint),
    )
    try:
        await asyncio.wait_for(_wait_ready(proc, port), TIMEOUT)
        assert (data_dir / "wgl.pid").read_text() == str(proc.pid)
        yield proc
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _finish(proc: asyncio.subprocess.Process) -> str:
    out, _ = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    return out.decode()


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
async def test_signal_shuts_down_cleanly(tmp_path: Path, sig: signal.Signals) -> None:
    async with _running_server(tmp_path, _free_port()) as proc:
        proc.send_signal(sig)
        out = await _finish(proc)
    assert proc.returncode == 0, out
    assert f"Received {sig.name}, shutting down" in out
    assert not (tmp_path / "wgl.pid").exists()


async def test_wgl_stop_then_status(tmp_path: Path) -> None:
    async with _running_server(tmp_path, _free_port()) as proc:
        assert "Server running" in await _wgl(tmp_path, "status")
        assert "Sending SIGTERM" in await _wgl(tmp_path, "stop")
        out = await _finish(proc)
    assert proc.returncode == 0, out
    assert "Server not running" in await _wgl(tmp_path, "status")


async def test_ignored_sigint_stays_ignored(tmp_path: Path) -> None:
    port = _free_port()
    async with _running_server(tmp_path, port, sigint=signal.SIG_IGN) as proc:
        proc.send_signal(signal.SIGINT)
        response = await asyncio.wait_for(
            AsyncHTTPClient().fetch(f"http://127.0.0.1:{port}/", raise_error=False), TIMEOUT
        )
        assert response.code != 599, "server died on an ignored SIGINT"
        proc.send_signal(signal.SIGTERM)
        out = await _finish(proc)
    assert proc.returncode == 0, out
    assert "SIGINT" not in out
