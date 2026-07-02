from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml

import engine.supervisor as sup
from engine.supervisor import ChildSpec, Supervisor, load_specs


class SpyLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, e: str, p: dict | None = None, **kw: object) -> None:
        self.events.append((e, p or {}))

    warning = error = debug = info


def test_load_specs_respects_enabled(tmp_path: Path) -> None:
    cfg = tmp_path / "procs.yaml"
    cfg.write_text(yaml.safe_dump({"processes": [
        {"name": "a", "module": "mod.a"},
        {"name": "b", "module": "mod.b", "enabled": False,
         "env": {"X": 1}},
    ]}))
    specs = load_specs(cfg)
    assert [s.name for s in specs] == ["a", "b"]
    assert specs[0].enabled and not specs[1].enabled
    assert specs[1].env == {"X": "1"}


def test_default_processes_file_is_valid() -> None:
    specs = load_specs()
    names = {s.name for s in specs}
    assert {"gateway", "replicator", "runner-copytrade", "runner-tradingview"} <= names
    gw = next(s for s in specs if s.name == "gateway")
    assert gw.env.get("GATEWAY_BIND") == "127.0.0.1"
    dummy = next(s for s in specs if s.name == "runner-dummy")
    assert dummy.enabled is False


def test_supervisor_restarts_crashed_child_and_stops_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(sup, "BACKOFF_MIN_S", 0.05)
    logger = SpyLogger()
    # child exits immediately -> must be restarted with backoff
    crasher = ChildSpec(name="crasher", module="this_module_does_not_exist")
    s = Supervisor([crasher], logger=logger)  # type: ignore[arg-type]

    t = threading.Thread(target=s.run_forever, kwargs={"poll_interval_s": 0.05})
    t.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if s.children[0].restarts >= 1:
            break
        time.sleep(0.05)
    s.request_stop()
    t.join(timeout=10)
    assert not t.is_alive()
    assert s.children[0].restarts >= 1
    assert any(e == "health.child_exited" for e, _ in logger.events)


def test_supervisor_terminates_long_running_child() -> None:
    logger = SpyLogger()
    sleeper = ChildSpec(name="sleeper", module="http.server",
                        env={"PYTHONUNBUFFERED": "1"})
    s = Supervisor([sleeper], logger=logger)  # type: ignore[arg-type]
    s.start()
    assert s.children[0].proc is not None
    assert s.children[0].proc.poll() is None   # alive
    s.shutdown(timeout_s=10)
    assert s.children[0].proc.poll() is not None  # terminated
