"""Engine supervisor — production entrypoint for the shared VPS (ADR 0007).

One systemd unit (`tokio-engine.service`) runs this supervisor, which keeps
the per-process isolation the architecture requires: gateway, replicator and
each strategy runner remain SEPARATE OS processes, restarted individually
with exponential backoff. A crash in one child never touches the others.

Process list comes from `deploy/engine-processes.yaml`. SIGTERM/SIGINT are
propagated to all children (clean systemd stop). Docker Compose remains the
dev environment; on the shared VPS, Docker is not used (the docker group
would be root-equivalent and break the isolation rules of the box).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine.core.config import get_settings
from engine.core.logger import EventLogger

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSES_FILE = REPO_ROOT / "deploy" / "engine-processes.yaml"

BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 60.0
STABLE_RESET_S = 60.0   # a child alive this long resets its backoff


@dataclass
class ChildSpec:
    name: str
    module: str                 # python -m <module>
    enabled: bool = True
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class ChildState:
    spec: ChildSpec
    proc: subprocess.Popen | None = None
    backoff_s: float = BACKOFF_MIN_S
    started_at: float = 0.0
    restarts: int = 0


def load_specs(path: Path = DEFAULT_PROCESSES_FILE) -> list[ChildSpec]:
    raw = yaml.safe_load(path.read_text()) or {}
    specs: list[ChildSpec] = []
    for item in raw.get("processes", []):
        specs.append(ChildSpec(
            name=item["name"],
            module=item["module"],
            enabled=bool(item.get("enabled", True)),
            env={k: str(v) for k, v in (item.get("env") or {}).items()},
        ))
    return specs


class Supervisor:
    def __init__(self, specs: list[ChildSpec], logger: EventLogger | None = None) -> None:
        settings = get_settings()
        self.logger = logger or EventLogger("supervisor", settings.logs_dir)
        self.children = [ChildState(s) for s in specs if s.enabled]
        self._stop = threading.Event()

    def _spawn(self, child: ChildState) -> None:
        env = {**os.environ, **child.spec.env}
        child.proc = subprocess.Popen(
            [sys.executable, "-m", child.spec.module],
            cwd=str(REPO_ROOT),
            env=env,
        )
        child.started_at = time.monotonic()
        self.logger.info("health.child_started", {
            "name": child.spec.name, "pid": child.proc.pid,
            "restarts": child.restarts,
        })

    def _reap_and_restart(self) -> None:
        for child in self.children:
            proc = child.proc
            if proc is None or proc.poll() is None:
                if proc is not None and time.monotonic() - child.started_at >= STABLE_RESET_S:
                    child.backoff_s = BACKOFF_MIN_S
                continue
            uptime = time.monotonic() - child.started_at
            self.logger.error("health.child_exited", {
                "name": child.spec.name, "exit_code": proc.returncode,
                "uptime_s": round(uptime, 1), "backoff_s": child.backoff_s,
            })
            child.proc = None
            if self._stop.is_set():
                continue
            # backoff outside the loop thread would complicate shutdown; a
            # short blocking sleep here is fine (children are independent).
            self._stop.wait(child.backoff_s)
            child.backoff_s = min(child.backoff_s * 2, BACKOFF_MAX_S)
            child.restarts += 1
            if not self._stop.is_set():
                self._spawn(child)

    def start(self) -> None:
        for child in self.children:
            self._spawn(child)

    def run_forever(self, poll_interval_s: float = 1.0) -> None:
        self.start()
        self.logger.info("health.supervisor_start",
                         {"children": [c.spec.name for c in self.children]})
        while not self._stop.is_set():
            self._reap_and_restart()
            self._stop.wait(poll_interval_s)
        self.shutdown()

    def shutdown(self, timeout_s: float = 15.0) -> None:
        self._stop.set()
        for child in self.children:
            if child.proc and child.proc.poll() is None:
                child.proc.terminate()
        deadline = time.monotonic() + timeout_s
        for child in self.children:
            if child.proc is None:
                continue
            remaining = max(0.1, deadline - time.monotonic())
            try:
                child.proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.proc.kill()
                self.logger.warning("health.child_killed", {"name": child.spec.name})
        self.logger.info("health.supervisor_stop", {})

    def request_stop(self) -> None:
        self._stop.set()


def main() -> None:
    supervisor = Supervisor(load_specs())

    def _handle(signum: int, _frame: object) -> None:
        supervisor.request_stop()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    supervisor.run_forever()


if __name__ == "__main__":
    main()
