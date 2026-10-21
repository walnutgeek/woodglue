"""
Supervising the processes behind Process-backed UIs.

For each UI with `kind` or `command` (see `woodglue.config.UiEntry`),
`UiProcesses` runs the process on a free loopback port and keeps it running:

- Started by `wgl start` after the engines, and stopped (SIGTERM, then SIGKILL
  after a grace period) with them.
- Ready once an HTTP request to `ready_path` gets any non-5xx answer; until
  then the proxy answers 503.
- Restarted after it exits, waiting 1s, 2s, 4s, ... up to a minute between
  attempts; the wait resets once a run has stayed up for a minute. While it is
  down its state is `failed`, which `system.list_uis` reports.
- Its stdout and stderr go to the log as `woodglue.ui.<name>`.

The process environment adds the UI's `env` and `WOODGLUE_URL`, the loopback
address of the woodglue API.

An `upstream` UI has no process; `UiProcesses.upstream` returns its URL as is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import sys
import time
from collections.abc import Mapping
from enum import StrEnum

import tornado.httpclient

from woodglue.config import UiEntry, UiKind

logger = logging.getLogger(__name__)

_STOP_GRACE_SECONDS = 5.0
_STARTUP_TIMEOUT_SECONDS = 60.0
_MAX_BACKOFF_SECONDS = 60.0
_STABLE_SECONDS = 60.0


class UiState(StrEnum):
    starting = "starting"
    ready = "ready"
    failed = "failed"
    stopped = "stopped"


def ui_command(name: str, entry: UiEntry, port: int) -> list[str]:
    """
    The command line for a supervised UI.

    >>> e = UiEntry(command=["srv", "--port={port}", "--root={base_path}"])
    >>> ui_command("dash", e, 8601)
    ['srv', '--port=8601', '--root=/ui/dash']
    >>> cmd = ui_command("dash", UiEntry(kind="streamlit", app="app.py"), 8601)
    >>> cmd[1:5], cmd[5:8]
    (['-m', 'streamlit', 'run', 'app.py'], ['--server.port=8601', '--server.address=127.0.0.1', '--server.baseUrlPath=ui/dash'])
    """
    base_path = f"/ui/{name}"
    if entry.kind == UiKind.streamlit:
        assert entry.app is not None
        dev = "true" if entry.dev else "false"
        return [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            entry.app,
            f"--server.port={port}",
            "--server.address=127.0.0.1",
            f"--server.baseUrlPath={base_path.lstrip('/')}",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
            f"--server.runOnSave={dev}",
            f"--server.fileWatcherType={'auto' if entry.dev else 'none'}",
        ]
    assert entry.command is not None
    return [arg.format(port=port, base_path=base_path) for arg in entry.command]


def ready_path(name: str, entry: UiEntry) -> str:
    """
    >>> ready_path("dash", UiEntry(kind="streamlit", app="a.py"))
    '/ui/dash/_stcore/health'
    >>> ready_path("x", UiEntry(command=["srv"]))
    '/ui/x/'
    """
    if entry.ready_path:
        return entry.ready_path
    if entry.kind == UiKind.streamlit:
        return f"/ui/{name}/_stcore/health"
    return f"/ui/{name}/"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class UiProcess:
    """One supervised UI process across its restarts."""

    name: str
    entry: UiEntry
    woodglue_url: str
    state: UiState
    port: int | None
    pid: int | None
    generation: int
    """How many times the process has become ready."""
    _log: logging.Logger
    _proc: asyncio.subprocess.Process | None
    _task: asyncio.Task[None] | None
    _stopping: bool
    _changed: asyncio.Condition

    def __init__(self, name: str, entry: UiEntry, woodglue_url: str) -> None:
        self.name = name
        self.entry = entry
        self.woodglue_url = woodglue_url
        self.state = UiState.stopped
        self.port = None
        self.pid = None
        self.generation = 0
        self._log = logging.getLogger(f"woodglue.ui.{name}")
        self._proc = None
        self._task = None
        self._stopping = False
        self._changed = asyncio.Condition()

    @property
    def upstream(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.state == UiState.ready else None

    async def _set_state(self, state: UiState) -> None:
        async with self._changed:
            self.state = state
            if state == UiState.ready:
                self.generation += 1
            self._changed.notify_all()

    async def wait_ready(self) -> None:
        async with self._changed:
            await self._changed.wait_for(lambda: self.state == UiState.ready)

    async def wait_restarted(self) -> None:
        """Wait until a run after the current one is ready."""
        async with self._changed:
            current = self.generation
            await self._changed.wait_for(
                lambda: self.generation > current and self.state == UiState.ready
            )

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._supervise())

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), _STOP_GRACE_SECONDS)
            except TimeoutError:
                self._log.warning("Did not exit after SIGTERM; killing it")
                proc.kill()
                await proc.wait()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._set_state(UiState.stopped)

    async def _supervise(self) -> None:
        backoff = 1.0
        while not self._stopping:
            started = time.monotonic()
            await self._run_once()
            if self._stopping:
                return
            if time.monotonic() - started >= _STABLE_SECONDS:
                backoff = 1.0
            await self._set_state(UiState.failed)
            self._log.warning("Exited; restarting in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    async def _run_once(self) -> None:
        await self._set_state(UiState.starting)
        self.port = _free_port()
        command = ui_command(self.name, self.entry, self.port)
        env = {**os.environ, **self.entry.env, "WOODGLUE_URL": self.woodglue_url}
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=self.entry.cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            self._log.error("Could not start %s: %s", command[0], exc)
            return
        self._proc = proc
        self.pid = proc.pid
        self._log.info("Started pid %d on port %d", proc.pid, self.port)
        pump = asyncio.get_running_loop().create_task(self._pump_output(proc))
        probe = asyncio.get_running_loop().create_task(self._probe(proc))
        returncode = await proc.wait()
        probe.cancel()
        await asyncio.gather(pump, probe, return_exceptions=True)
        if not self._stopping:
            self._log.warning("pid %d exited with status %s", proc.pid, returncode)

    async def _pump_output(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        async for line in proc.stdout:
            self._log.info("%s", line.decode(errors="replace").rstrip())

    async def _probe(self, proc: asyncio.subprocess.Process) -> None:
        url = f"http://127.0.0.1:{self.port}{ready_path(self.name, self.entry)}"
        client = tornado.httpclient.AsyncHTTPClient()
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while proc.returncode is None:
            try:
                resp = await client.fetch(
                    url, raise_error=False, follow_redirects=False, request_timeout=2
                )
                if resp.code < 500:
                    await self._set_state(UiState.ready)
                    return
            except OSError:
                pass
            if time.monotonic() > deadline:
                self._log.error("Not ready after %.0fs; stopping it", _STARTUP_TIMEOUT_SECONDS)
                proc.terminate()
                return
            await asyncio.sleep(0.2)


class UiProcesses:
    """The supervised processes and upstream URLs of all Process-backed UIs."""

    _processes: dict[str, UiProcess]
    _upstreams: dict[str, str]

    def __init__(self, uis: Mapping[str, UiEntry], woodglue_url: str) -> None:
        self._processes = {
            name: UiProcess(name, entry, woodglue_url)
            for name, entry in uis.items()
            if entry.is_supervised
        }
        self._upstreams = {
            name: entry.upstream.rstrip("/")
            for name, entry in uis.items()
            if entry.upstream is not None
        }

    def get(self, name: str) -> UiProcess:
        return self._processes[name]

    def upstream(self, name: str) -> str | None:
        """Where to proxy a UI's requests now, or `None` while its process is not ready."""
        if name in self._upstreams:
            return self._upstreams[name]
        process = self._processes.get(name)
        return process.upstream if process else None

    def state(self, name: str) -> UiState | None:
        process = self._processes.get(name)
        return process.state if process else None

    def has_processes(self) -> bool:
        return bool(self._processes)

    async def start_all(self) -> None:
        for process in self._processes.values():
            process.start()

    async def stop_all(self) -> None:
        await asyncio.gather(*(p.stop() for p in self._processes.values()))
