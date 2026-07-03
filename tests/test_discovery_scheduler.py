from __future__ import annotations

import threading
import time as _time
from datetime import datetime
from zoneinfo import ZoneInfo

from engine.core.logger import EventLogger
from engine.strategies.copy_trade.discovery_scheduler import (
    DiscoveryScheduler,
    SCAN_TZ,
    next_scan_at,
    traders_table_empty,
)
from engine.strategies.copy_trade.traders_store import upsert_candidate

SP = ZoneInfo("America/Sao_Paulo")


def test_next_scan_at_today_and_tomorrow() -> None:
    before = datetime(2026, 7, 3, 3, 0, tzinfo=SP)     # antes das 05:00
    after = datetime(2026, 7, 3, 6, 0, tzinfo=SP)      # depois das 05:00
    assert next_scan_at(before) == datetime(2026, 7, 3, 5, 0, tzinfo=SP)
    assert next_scan_at(after) == datetime(2026, 7, 4, 5, 0, tzinfo=SP)
    # timezone-aware mesmo com entrada em UTC
    utc_in = datetime(2026, 7, 3, 12, 0, tzinfo=ZoneInfo("UTC"))  # 09:00 SP
    assert next_scan_at(utc_in).tzinfo is SCAN_TZ


def test_bootstrap_scan_runs_only_when_table_empty(settings, db) -> None:
    logger = EventLogger("sched-test", settings.logs_dir, db=db)
    calls: list[str] = []
    sched = DiscoveryScheduler(db, logger, scan_fn=lambda reason: calls.append(reason) or True)

    assert traders_table_empty(db)
    assert sched.bootstrap_if_empty() is True
    assert calls == ["bootstrap_tabela_vazia"]

    upsert_candidate(db, address="0x" + "aa" * 20, score=50.0, logic_version=2)
    assert sched.bootstrap_if_empty() is False        # tabela populada e lógica atual
    assert calls == ["bootstrap_tabela_vazia"]


def test_bootstrap_rescans_when_logic_version_advances(settings, db) -> None:
    """Tabela populada pela v1 + config na v2 → re-scan automático no start."""
    logger = EventLogger("sched-test", settings.logs_dir, db=db)
    calls: list[str] = []
    upsert_candidate(db, address="0x" + "aa" * 20, score=50.0, logic_version=1)
    sched = DiscoveryScheduler(db, logger,
                               scan_fn=lambda reason: calls.append(reason) or True)
    assert sched.bootstrap_if_empty() is True
    assert calls == ["bootstrap_logic_version"]


def test_daily_trigger_fires_when_time_reached(settings, db) -> None:
    logger = EventLogger("sched-test", settings.logs_dir, db=db)
    upsert_candidate(db, address="0x" + "aa" * 20, logic_version=2)  # evita o bootstrap
    calls: list[str] = []
    fake_now = {"t": datetime(2026, 7, 3, 4, 59, 50, tzinfo=SP)}
    sched = DiscoveryScheduler(
        db, logger,
        scan_fn=lambda reason: calls.append(reason) or True,
        now_fn=lambda: fake_now["t"],
    )
    t = threading.Thread(target=sched.run_forever, kwargs={"poll_interval_s": 0.02})
    t.start()
    _time.sleep(0.1)
    assert calls == []                                  # ainda não deu a hora
    fake_now["t"] = datetime(2026, 7, 3, 5, 0, 1, tzinfo=SP)
    deadline = _time.monotonic() + 5
    while not calls and _time.monotonic() < deadline:
        _time.sleep(0.02)
    sched.stop()
    t.join(timeout=5)
    assert calls == ["agendado_diario"]


def test_bootstrap_retries_while_table_empty(settings, db) -> None:
    logger = EventLogger("sched-test", settings.logs_dir, db=db)
    calls: list[str] = []

    def failing_then_ok(reason: str) -> bool:
        calls.append(reason)
        if len(calls) >= 2:   # 2ª tentativa "funciona": popula a tabela
            upsert_candidate(db, address="0x" + "cc" * 20, score=10.0, logic_version=2)
            return True
        return False          # 1ª tentativa falha (ex.: 429)

    sched = DiscoveryScheduler(db, logger, scan_fn=failing_then_ok,
                               now_fn=lambda: datetime(2026, 7, 3, 10, 0, tzinfo=SP))
    t = threading.Thread(target=sched.run_forever,
                         kwargs={"poll_interval_s": 0.02, "bootstrap_retry_s": 0.05})
    t.start()
    deadline = _time.monotonic() + 5
    while len(calls) < 2 and _time.monotonic() < deadline:
        _time.sleep(0.02)
    _time.sleep(0.15)          # janela extra: NÃO pode haver 3ª tentativa
    sched.stop()
    t.join(timeout=5)
    assert calls == ["bootstrap_tabela_vazia", "bootstrap_tabela_vazia"]


def test_scan_failure_is_captured_and_logged(settings, db, monkeypatch) -> None:
    """run_scan nunca vaza exceção: falha vira discovery.scan_failed + False."""
    import engine.strategies.copy_trade.funnel as funnel_mod

    def boom(*a: object, **kw: object) -> None:
        raise RuntimeError("api fora do ar (simulado)")

    monkeypatch.setattr(funnel_mod, "run_scan", boom)
    logger = EventLogger("sched-test", settings.logs_dir, db=db)
    from engine.strategies.copy_trade.discovery_scheduler import run_scan

    ok = run_scan(db, logger, reason="teste")   # não pode levantar exceção
    assert ok is False
    events = db.query(
        "SELECT event_type FROM events WHERE event_type LIKE 'discovery.%' ORDER BY id")
    types = [e["event_type"] for e in events]
    assert "discovery.scan_started" in types and "discovery.scan_failed" in types
