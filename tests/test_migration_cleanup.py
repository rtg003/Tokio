"""ADR 0010 — migration 0003 remove fills sem atribuição e preserva os atribuídos."""
from __future__ import annotations

import shutil
from pathlib import Path

from engine.core.db import MIGRATIONS_DIR, Database, utcnow


def test_0003_removes_only_unattributed_fills(tmp_path: Path) -> None:
    # aplica só a 0001, insere dados, depois aplica o resto (0002/0003)
    stage = tmp_path / "migrations"
    stage.mkdir()
    shutil.copy(MIGRATIONS_DIR / "0001_initial.sql", stage)
    db = Database(tmp_path / "t.db")
    db.migrate(stage)

    db.insert("strategies", {"id": "ct_x", "module": "copy_trade", "name": "x",
                             "status": "dry_run"})
    db.insert("fills", {"cloid": None, "strategy_id": None, "symbol": "BTC",
                        "side": "buy", "price": 1.0, "size": 1.0, "fee": 0,
                        "ts": utcnow()})                       # poluição (snapshot)
    db.insert("fills", {"cloid": "0xok", "strategy_id": "ct_x", "symbol": "BTC",
                        "side": "buy", "price": 1.0, "size": 1.0, "fee": 0,
                        "ts": utcnow()})                       # legítimo

    for f in MIGRATIONS_DIR.glob("*.sql"):
        if f.name != "0001_initial.sql":
            shutil.copy(f, stage)
    ran = db.migrate(stage)
    assert any("0003" in v for v in ran)

    fills = db.query("SELECT strategy_id FROM fills")
    assert len(fills) == 1 and fills[0]["strategy_id"] == "ct_x"
