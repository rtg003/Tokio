from __future__ import annotations

import os
from pathlib import Path

import pytest

from engine.core.config import Settings
from engine.core.db import Database
from engine.core.logger import EventLogger
from engine.exchanges.paper import PaperAdapter
from engine.gateway.server import GatewayState, build_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.paths.data_dir = str(tmp_path / "data")
    s.paths.logs_dir = str(tmp_path / "logs")
    s.paths.kill_file = str(tmp_path / "KILL")
    return s


@pytest.fixture()
def db(settings: Settings) -> Database:
    d = Database(settings.sqlite_path)
    d.migrate()
    yield d
    d.close()


@pytest.fixture()
def paper() -> PaperAdapter:
    return PaperAdapter(prices={"BTC": 100_000.0, "ETH": 4_000.0})


@pytest.fixture()
def gateway_state(settings: Settings, db: Database, paper: PaperAdapter) -> GatewayState:
    logger = EventLogger("gateway-test", settings.logs_dir, db=db)
    return GatewayState(settings, paper, db, logger=logger)


@pytest.fixture()
def client(gateway_state: GatewayState):
    from fastapi.testclient import TestClient

    os.environ.setdefault("GATEWAY_CONTROL_TOKEN", "test-token")
    with TestClient(build_app(gateway_state)) as c:
        yield c


def register_strategy(db: Database, strategy_id: str, *, module: str = "dummy",
                      status: str = "active") -> None:
    db.upsert("strategies", {
        "id": strategy_id, "module": module, "name": strategy_id, "status": status,
    }, ("id",))
