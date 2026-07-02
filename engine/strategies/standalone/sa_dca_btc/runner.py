"""Standalone template runner — minimal DCA inheriting the full BaseRunner
contract (lifecycle, gateway intents, heartbeat, thresholds, kill switch).

This is a DOCUMENTED TEMPLATE: it stays in dry_run and is never activated.
"""
from __future__ import annotations

import time
from pathlib import Path

import yaml

from engine.strategies.base_runner import BaseRunner

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


class DcaBtcRunner(BaseRunner):
    module = "standalone"

    def __init__(self, **kw: object) -> None:
        config = kw.pop("config", None) or yaml.safe_load(CONFIG_PATH.read_text())
        super().__init__(config["id"], config=config, **kw)  # type: ignore[arg-type]
        self._last_buy = 0.0

    def on_cycle(self) -> None:
        interval_s = float(self.config.get("interval_hours", 24)) * 3600
        now = time.monotonic()
        if self._last_buy and now - self._last_buy < interval_s:
            return
        self._last_buy = now
        result = self.send_intent(
            symbol=self.config["symbol"],
            side="buy",
            notional_usd=float(self.config["notional_usd"]),
            order_type="market",
        )
        self.logger.info("decision.dca_buy", {"result": result},
                         strategy_id=self.strategy_id)


def main() -> None:
    DcaBtcRunner().run_forever()


if __name__ == "__main__":
    main()
