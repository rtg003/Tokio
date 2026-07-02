"""Dummy runner — Phase 1 acceptance vehicle.

Exercises the full path runner -> gateway -> adapter on a schedule so the
complete order lifecycle shows up in the logs. Dry-run by default; only ever
activated manually for the testnet acceptance test.
"""
from __future__ import annotations

import time

from engine.strategies.base_runner import BaseRunner


class DummyRunner(BaseRunner):
    module = "dummy"

    def __init__(self, strategy_id: str = "dm_pulse", **kw: object) -> None:
        config = kw.pop("config", None) or {
            "name": "dummy pulse",
            "symbol": "BTC",
            "notional_usd": 12.0,
            "interval_s": 300,
            "cycle_interval_s": 1.0,
            "initial_status": "dry_run",
        }
        super().__init__(strategy_id, config=config, **kw)  # type: ignore[arg-type]
        self._last_pulse = 0.0
        self._side = "buy"

    def on_cycle(self) -> None:
        now = time.monotonic()
        if now - self._last_pulse < float(self.config.get("interval_s", 300)):
            return
        self._last_pulse = now
        result = self.send_intent(
            symbol=self.config["symbol"],
            side=self._side,
            notional_usd=float(self.config["notional_usd"]),
            order_type="market",
        )
        self.logger.info("decision.pulse", {"side": self._side, "result": result},
                         strategy_id=self.strategy_id)
        self._side = "sell" if self._side == "buy" else "buy"


def main() -> None:
    DummyRunner().run_forever()


if __name__ == "__main__":
    main()
