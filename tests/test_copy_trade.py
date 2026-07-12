"""Phase 2 acceptance: target fills mirrored through the gateway with fixed and
percent sizing; drift check and latency logged; ledger attributes via cloid.
Fonte de traders: tabela `traders` (ADR 0008)."""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from engine.core.config import Settings
from engine.core.db import Database
from engine.strategies.copy_trade.executor import CopyTradeExecutor, TraderConfig

TARGET = "0x00000000000000000000000000000000000000aa"


class FakeWatcher:
    def __init__(self) -> None:
        self.subs: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        # address -> {symbol: signed size} — the trader's real clearinghouse
        # position, the reconcile anchor (WS-independent).
        self.positions: dict[str, dict[str, float]] = {}

    def subscribe(self, address: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self.subs.setdefault(address, []).append(callback)

    def emit(self, address: str, fill: dict[str, Any]) -> None:
        for cb in self.subs.get(address, []):
            cb(fill)

    def target_positions(self, address: str) -> dict[str, float]:
        return dict(self.positions.get(address, {}))


class RecordingGateway:
    """Records intents; simulates ledger/positions endpoints for drift + reconcile."""

    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []
        self.ledger_response: dict[str, Any] = {}
        self.positions_response: list[dict[str, Any]] = []
        # per-symbol szDecimals; default high so rounding is a no-op unless a test
        # sets it (keeps sizing-focused tests independent of the step grid).
        self.sz_decimals: dict[str, int] = {}
        self.default_sz_decimals = 8
        # per-symbol mid price used by reconcile sizing; 0 => reconcile skips it.
        self.mids: dict[str, float] = {}
        self.default_mid = 0.0

        outer = self

        class _C:
            def get(self, path: str):
                class R:
                    def json(_self) -> dict[str, Any]:
                        return outer.ledger_response
                return R()

        self._client = _C()

    def send_intent(self, **payload: Any) -> dict[str, Any]:
        self.intents.append(payload)
        return {"ok": True, "cloid": "0xtest", "status": "dry_run"}

    def cancel(self, **payload: Any) -> dict[str, Any]:
        return {"ok": True}

    def ledger(self) -> dict[str, Any]:
        return self.ledger_response

    def positions(self, strategy_ids: list[str],
                  network: str | None = None) -> list[dict[str, Any]]:
        return self.positions_response

    def wait_ready(self, attempts: int = 3, delay: float = 2.0) -> bool:
        return True

    def market_meta(self, symbol: str, environment: str | None = None) -> dict[str, Any]:
        sz = self.sz_decimals.get(symbol, self.default_sz_decimals)
        return {"ok": True, "szDecimals": sz, "maxLeverage": 50, "minNotional": 10.0,
                "mid": self.mids.get(symbol, self.default_mid)}


def seed_trader(db: Database, **overrides: Any) -> None:
    row = {
        "address": TARGET, "name": "whale01", "status": "TESTNET",
        "mode": "fixed_usdc", "value": 100.0, "max_leverage": 3.0,
        "blocked_assets": "[]", "dry_run": 0, "thresholds": "{}",
        **{k: (json.dumps(v) if k in ("blocked_assets", "thresholds")
               and not isinstance(v, str) else v) for k, v in overrides.items()},
    }
    db.upsert("traders", row, ("address",))


def make_executor(settings: Settings, db: Database,
                  **overrides: Any) -> tuple[CopyTradeExecutor, FakeWatcher, RecordingGateway]:
    watcher = FakeWatcher()
    gw = RecordingGateway()
    # my_equity_fn/target_equity_fn são do executor, não colunas do trader.
    my_equity_fn = overrides.pop("my_equity_fn", lambda _env=None: 1_000.0)
    target_equity_fn = overrides.pop("target_equity_fn", lambda _a: 100_000.0)
    seed_trader(db, **overrides)
    ex = CopyTradeExecutor(settings=settings, db=db, gateway=gw, watcher=watcher,
                           my_equity_fn=my_equity_fn,
                           target_equity_fn=target_equity_fn,
                           target_positions_fn=watcher.target_positions)
    return ex, watcher, gw


def fill(coin: str, side: str, sz: float, px: float, start_pos: float,
         time_ms: float = 0.0) -> dict[str, Any]:
    return {"coin": coin, "side": side, "sz": str(sz), "px": str(px),
            "startPosition": str(start_pos), "time": time_ms}


def test_registers_operating_trader_as_active_strategy(settings, db) -> None:
    make_executor(settings, db)
    rows = db.query("SELECT module, status FROM strategies WHERE id = 'ct_whale01'")
    assert rows and rows[0]["module"] == "copy_trade" and rows[0]["status"] == "active"


def test_reload_picks_up_new_table_trader(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    other = "0x00000000000000000000000000000000000000bb"
    db.upsert("traders", {"address": other, "name": "novo", "status": "TESTNET",
                          "mode": "fixed_usdc", "value": 50.0, "max_leverage": 2.0,
                          "blocked_assets": "[]", "dry_run": 0, "thresholds": "{}"},
              ("address",))
    ex.reload_traders()   # mudanças via API de controle entram sem restart
    assert other in watcher.subs
    assert "ct_novo" in ex.traders


def test_saved_trader_is_not_mirrored(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    db.execute("UPDATE traders SET status = 'SALVO' WHERE address = ?", (TARGET,))
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))
    assert gw.intents == []


def test_open_from_flat_fixed_usdc(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db)
    watcher.emit(TARGET, fill("BTC", "B", 2.0, 50_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    intent = gw.intents[0]
    # fixed 100 USDC at 50k => 0.002 BTC, regardless of the whale's 2 BTC
    assert intent["size"] == pytest.approx(0.002)
    assert intent["side"] == "buy"
    assert intent["dry_run"] is False
    assert intent["environment"] == "testnet"
    assert intent["strategy_id"] == "ct_whale01"


def test_percent_mode_proportional_to_equity(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db,
                                    mode="percent", value=1.0)
    # whale (100k equity) buys 2 BTC @50k (100k notional) -> us (1k equity):
    # notional = 100k * 1.0 * (1000/100000) = 1000 USD -> 0.02 BTC
    watcher.emit(TARGET, fill("BTC", "B", 2.0, 50_000.0, start_pos=0.0))
    assert gw.intents[0]["size"] == pytest.approx(0.02)


def test_percent_respects_max_leverage_ceiling(settings, db) -> None:
    # Espelha o notional_cap da simulação (metrics.simulate_copy): com equity 1k e
    # max_leverage 3x, o notional máximo por posição é $3.000. Uma posição-baleia
    # cujo notional proporcional estouraria esse teto é dimensionada pra baixo, em
    # vez de copiar a exposição inteira do trader.
    ex, watcher, gw = make_executor(settings, db,
                                    mode="percent", value=1.0, max_leverage=3.0)
    # whale (100k equity) buys 10 BTC @50k (500k notional) -> proporcional seria
    # 500k * 1.0 * (1000/100000) = 5000 USD, acima do teto 1000*3 = 3000.
    # Capado a 3000 -> 3000/50000 = 0.06 BTC (sem teto seria 0.1).
    watcher.emit(TARGET, fill("BTC", "B", 10.0, 50_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(0.06)


def test_teto_respects_real_equity(settings, db) -> None:
    # REGRESSÃO do bug: o teto de alavancagem usava $1.000 fixo (my_equity_fn
    # lia /health, que não tem equity). Aqui o equity real injetado é $10,37 e
    # max_leverage 5 -> teto = $51,85. Uma baleia grande é capada nesse teto, não
    # em 1.000*5 = $5.000.
    ex, watcher, gw = make_executor(settings, db, mode="percent", value=1.0,
                                    max_leverage=5.0,
                                    my_equity_fn=lambda _env=None: 10.37)
    # whale abre 40 BTC @50k (2M notional) -> proporcional 2M*(10.37/100000) =
    # $207,4, acima do teto $51,85. Capado -> 51.85/50000 BTC.
    watcher.emit(TARGET, fill("BTC", "B", 40.0, 50_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx((10.37 * 5.0) / 50_000.0)


def test_my_equity_uses_correct_env(settings, db) -> None:
    # Cada trader opera num ambiente; o equity consultado deve ser o DAQUELE
    # ambiente. Trader em MAINNET -> my_equity_fn é chamado com "mainnet".
    seen: list[str | None] = []
    equities = {"testnet": 999.0, "mainnet": 10.37}

    def my_equity(env: str | None = None) -> float:
        seen.append(env)
        return equities.get(env or "", 1_000.0)

    ex, watcher, gw = make_executor(settings, db, status="MAINNET",
                                    mode="percent", value=1.0, max_leverage=5.0,
                                    my_equity_fn=my_equity)
    watcher.emit(TARGET, fill("BTC", "B", 40.0, 50_000.0, start_pos=0.0))
    assert "mainnet" in seen
    assert len(gw.intents) == 1
    # size capado pelo equity de mainnet ($10,37), não pelo de testnet.
    assert gw.intents[0]["size"] == pytest.approx((10.37 * 5.0) / 50_000.0)


def test_my_equity_zero_holds_position(settings, db) -> None:
    # Cold start / erro do /balance -> my_equity_fn devolve 0.0. O executor NÃO
    # deve abrir nem FECHAR a posição (voltar a $1.000 re-inflaria o teto; zerar
    # o mirror fecharia posições boas). Segura a posição atual.
    ex, watcher, gw = make_executor(settings, db, mode="percent", value=1.0,
                                    my_equity_fn=lambda _env=None: 0.0)
    key = ("ct_whale01", "BTC")
    ex._my_pos[key] = 0.05          # posição já espelhada de antes
    ex._target_pos[key] = 1.0
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=1.0))  # whale soma
    assert gw.intents == []          # nenhuma ordem nova nem fechamento
    assert ex._my_pos[key] == 0.05   # posição mantida
    logs = db.query(
        "SELECT event_type FROM events WHERE event_type = 'decision.no_my_equity'")
    assert logs


def test_partial_reduction_mirrors_proportionally(settings, db) -> None:
    # percent mode still scales with the trader's position (proportional to the
    # equity ratio); a 50% reduction by the trader halves our mirror too.
    ex, watcher, gw = make_executor(settings, db, mode="percent", value=1.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))   # open
    watcher.emit(TARGET, fill("ETH", "A", 5.0, 2_100.0, start_pos=10.0))   # -50%
    assert len(gw.intents) == 2
    open_size = gw.intents[0]["size"]
    reduce = gw.intents[1]
    assert reduce["side"] == "sell"
    assert reduce["reduce_only"] is True
    assert reduce["size"] == pytest.approx(open_size * 0.5)


def test_full_close_mirrors_flat(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))
    watcher.emit(TARGET, fill("ETH", "A", 10.0, 2_050.0, start_pos=10.0))
    close = gw.intents[1]
    assert close["reduce_only"] is True
    assert close["size"] == pytest.approx(gw.intents[0]["size"])
    assert ex._my_pos[("ct_whale01", "ETH")] == 0.0


def test_below_min_notional_skipped_and_logged(settings, db) -> None:
    # percent mode so a tiny trim by the whale yields a tiny (non-zero) delta.
    ex, watcher, gw = make_executor(settings, db, mode="percent", value=1.0)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0))   # 0.01 BTC open ok
    # whale trims 1% -> our delta ~0.0001 BTC (~5 USD) < 10 USD minimum -> skip
    watcher.emit(TARGET, fill("BTC", "A", 0.01, 50_000.0, start_pos=1.0))
    assert len(gw.intents) == 1
    logs = db.query("SELECT event_type FROM events WHERE event_type = 'decision.skipped_min_notional'")
    assert logs


def test_per_trader_min_notional_raises_floor(settings, db) -> None:
    # Notional mínimo per-trader (thresholds.min_notional_usd) só pode SUBIR o
    # piso global — ordens entre $10 (global) e $200 (per-trader) são *skipadas*;
    # acima de $200 passam. Mesma semântica de skip, sem gate novo (INVARIANTE).
    ex, watcher, gw = make_executor(settings, db, mode="percent", value=1.0,
                                    thresholds={"min_notional_usd": 200.0})
    # whale abre 0.2 BTC @50k ($10k) -> nosso notional 10k*(1000/100000) = $100.
    # $100 > global $10 mas < per-trader $200 -> skipado.
    watcher.emit(TARGET, fill("BTC", "B", 0.2, 50_000.0, start_pos=0.0))
    assert gw.intents == []
    logs = db.query(
        "SELECT event_type FROM events WHERE event_type = 'decision.skipped_min_notional'"
    )
    assert logs
    # whale soma +0.6 BTC (total 0.8 @50k) -> nosso alvo 0.8*(1000/100000) =
    # 0.008 BTC ($400 notional) > $200 -> passa.
    watcher.emit(TARGET, fill("BTC", "B", 0.6, 50_000.0, start_pos=0.2))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(0.008)


def test_blocked_asset_skipped(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, blocked_assets=["DOGE"])
    watcher.emit(TARGET, fill("DOGE", "B", 1000.0, 0.5, start_pos=0.0))
    assert gw.intents == []


def test_latency_logged_on_every_mirror(settings, db) -> None:
    import json as _json

    ex, watcher, gw = make_executor(settings, db)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 50_000.0, start_pos=0.0, time_ms=1.0))
    rows = db.query("SELECT payload FROM events WHERE event_type = 'decision.mirrored'")
    assert rows
    # latency lives in the JSONL record; the mirrored decision is in the DB
    log_files = list(settings.logs_dir.glob("runner-copytrade-*.jsonl"))
    assert log_files
    lines = [_json.loads(line) for line in log_files[0].read_text().splitlines()]
    mirrored = [l for l in lines if l["event_type"] == "decision.mirrored"]
    assert mirrored and mirrored[0]["latency_ms"] >= 0


def test_drift_check_alerts_above_tolerance(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=1000.0)
    watcher.emit(TARGET, fill("ETH", "B", 10.0, 2_000.0, start_pos=0.0))
    expected = ex._my_pos[("ct_whale01", "ETH")]
    gw.ledger_response = {"ct_whale01": {"positions": {"ETH": {"size": expected * 0.5}}}}
    drifts = ex.drift_check()
    assert len(drifts) == 1 and drifts[0]["symbol"] == "ETH"
    gw.ledger_response = {"ct_whale01": {"positions": {"ETH": {"size": expected}}}}
    assert ex.drift_check() == []


def test_mirror_size_rounds_to_sz_decimals(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    gw.sz_decimals["ETH"] = 2
    # 100 USDC / 3000 = 0.03333... -> rounds to 0.03 (szDecimals=2)
    watcher.emit(TARGET, fill("ETH", "B", 1.0, 3_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(0.03)


def test_mirror_size_zero_skips_order(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=20.0)
    gw.sz_decimals["HYPE"] = 0
    # 20 USDC / 50 = 0.4 HYPE -> rounds to 0 (step=1) -> no order, logged
    watcher.emit(TARGET, fill("HYPE", "B", 1.0, 50.0, start_pos=0.0))
    assert gw.intents == []
    logs = db.query(
        "SELECT event_type FROM events WHERE event_type = 'decision.skipped_size_too_small'")
    assert logs


def test_mirror_size_btc_3_decimals(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    gw.sz_decimals["BTC"] = 3
    # 100 USDC / 47000 = 0.0021276... -> rounds to 0.002 (szDecimals=3)
    watcher.emit(TARGET, fill("BTC", "B", 1.0, 47_000.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(0.002)


def test_mirror_size_hype_0_decimals(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=69.0)
    gw.sz_decimals["HYPE"] = 0
    # 69 USDC / 100 = 0.69 HYPE -> rounds to 1 (step inteiro), repro do UPDATE-0017
    watcher.emit(TARGET, fill("HYPE", "B", 1.0, 100.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(1.0)


def test_mirror_config_validation() -> None:
    with pytest.raises(Exception):
        TraderConfig(name="x", address="0xabc", mode="yolo")


# -- absolute fixed_usdc semantics (UPDATE-0020) -----------------------------

def test_fixed_usdc_does_not_scale_when_trader_doubles(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    # open: whale long 1 FARTCOIN @ 1.0 -> we hold $100 -> 100 units
    watcher.emit(TARGET, fill("FARTCOIN", "B", 1.0, 1.0, start_pos=0.0))
    assert len(gw.intents) == 1
    assert gw.intents[0]["size"] == pytest.approx(100.0)
    # whale DOUBLES to 2 FARTCOIN -> our $100 exposure is unchanged -> no order
    watcher.emit(TARGET, fill("FARTCOIN", "B", 1.0, 1.0, start_pos=1.0))
    assert len(gw.intents) == 1  # absolute sizing: we did NOT double


def test_fixed_usdc_closes_when_trader_flattens(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    watcher.emit(TARGET, fill("FARTCOIN", "B", 1.0, 1.0, start_pos=0.0))
    watcher.emit(TARGET, fill("FARTCOIN", "A", 1.0, 1.0, start_pos=1.0))  # -> flat
    close = gw.intents[1]
    assert close["side"] == "sell"
    assert close["reduce_only"] is True
    assert close["size"] == pytest.approx(100.0)
    assert ex._my_pos[("ct_whale01", "FARTCOIN")] == 0.0


# -- reconcile: recovers missed fills, per trader -> per strategy -------------

def test_reconcile_recovers_missed_fills_both_symbols(settings, db) -> None:
    """The UPDATE-0020 bug: trader has a real position we never mirrored.
    reconcile converges the strategy to the mirror of EVERY symbol, from the
    trader's real position — independent of any WS event."""
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    # trader's real clearinghouse position (short FARTCOIN, long HYPE)
    watcher.positions[TARGET] = {"FARTCOIN": -416.0, "HYPE": 200.0}
    gw.mids = {"FARTCOIN": 1.0, "HYPE": 20.0}
    # our per-strategy ledger book is empty (18 fills were lost)
    gw.ledger_response = {"ct_whale01": {"positions": {}}}

    corrections = ex.reconcile()
    by_symbol = {i["symbol"]: i for i in gw.intents}
    assert set(by_symbol) == {"FARTCOIN", "HYPE"}
    # FARTCOIN: $100 short @1.0 -> -100 units -> sell 100
    assert by_symbol["FARTCOIN"]["side"] == "sell"
    assert by_symbol["FARTCOIN"]["size"] == pytest.approx(100.0)
    # HYPE: $100 long @20 -> +5 units -> buy 5
    assert by_symbol["HYPE"]["side"] == "buy"
    assert by_symbol["HYPE"]["size"] == pytest.approx(5.0)
    # every correction is tied to THIS strategy_id
    assert all(i["strategy_id"] == "ct_whale01" for i in gw.intents)
    assert len(corrections) == 2


def test_reconcile_is_idempotent_once_ledger_aligned(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    ex.RECONCILE_COOLDOWN_S = 0.0  # isolate ledger-based idempotency from cooldown
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}
    ex.reconcile()
    assert len(gw.intents) == 1
    # the fill lands in the ledger -> a 2nd reconcile must emit NOTHING
    gw.ledger_response = {"ct_whale01": {"positions": {"FARTCOIN": {"size": -100.0}}}}
    ex.reconcile()
    assert len(gw.intents) == 1  # no new intents


def test_reconcile_cooldown_blocks_double_send(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}
    ex.reconcile()
    ex.reconcile()  # ledger not yet updated, but cooldown must prevent a re-send
    assert len(gw.intents) == 1


def test_reconcile_isolated_per_strategy_same_symbol(settings, db) -> None:
    """Two ct_* on the SAME symbol reconcile in separate books (§5.2): each
    strategy is compared only to ITS OWN ledger, never the aggregate."""
    other = "0x00000000000000000000000000000000000000bb"
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    db.upsert("traders", {"address": other, "name": "beta", "status": "TESTNET",
                          "mode": "fixed_usdc", "value": 100.0, "max_leverage": 3.0,
                          "blocked_assets": "[]", "dry_run": 0, "thresholds": "{}"},
              ("address",))
    ex.reload_traders()
    # both traders long 1 BTC -> both want +$100 = 0.002 BTC @ 50k
    watcher.positions[TARGET] = {"BTC": 1.0}
    watcher.positions[other] = {"BTC": 2.0}
    gw.mids = {"BTC": 50_000.0}
    # ct_whale01 already aligned in ITS book; ct_beta's book is empty.
    gw.ledger_response = {
        "ct_whale01": {"positions": {"BTC": {"size": 0.002}}},
        "ct_beta": {"positions": {}},
    }
    ex.reconcile()
    # only ct_beta needs a correction — proves per-strategy isolation (an
    # aggregate view would have seen 0.002 and emitted nothing for either)
    assert len(gw.intents) == 1
    assert gw.intents[0]["strategy_id"] == "ct_beta"
    assert gw.intents[0]["side"] == "buy"
    assert gw.intents[0]["size"] == pytest.approx(0.002)


def test_reconcile_skips_when_no_mid(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {}  # no price -> reconcile must skip, never send a $0-priced order
    gw.ledger_response = {"ct_whale01": {"positions": {}}}
    ex.reconcile()
    assert gw.intents == []


def test_reconcile_ignores_non_operable_trader(settings, db) -> None:
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    db.execute("UPDATE traders SET status = 'SALVO' WHERE address = ?", (TARGET,))
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}
    ex.reconcile()
    assert gw.intents == []


def test_reconcile_optimistic_actual_blocks_short_resend(settings, db) -> None:
    """UPDATE-0023 regression: with the ledger lagging (fill not yet recorded), the
    optimistic `_my_pos` must count as `actual` so we don't re-send a full-size
    correction. Proven on a SHORT — where a naive max(ledger, _my_pos) would fail."""
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    ex.RECONCILE_COOLDOWN_S = 0.0  # isolate the fix from the cooldown
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}  # ledger stays empty
    ex.reconcile()
    assert len(gw.intents) == 1
    assert ex._my_pos[("ct_whale01", "FARTCOIN")] == pytest.approx(-100.0)
    # ledger STILL empty, cooldown disabled — only the optimistic actual can block
    ex.reconcile()
    assert len(gw.intents) == 1  # no duplicate short


def test_reconcile_respects_drift_tolerance(settings, db) -> None:
    """Sub-5% drift is noise (cents) — reconcile must not chase it; >5% corrects."""
    # 2% drift: desired -1000 vs ledger -980 -> skip
    ex, watcher, gw = make_executor(settings, db, value=1_000.0)
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {"FARTCOIN": {"size": -980.0}}}}
    ex.reconcile()
    assert gw.intents == []
    # 10% drift: desired -1000 vs ledger -900 -> one correction
    gw.ledger_response = {"ct_whale01": {"positions": {"FARTCOIN": {"size": -900.0}}}}
    ex.reconcile()
    assert len(gw.intents) == 1
    assert gw.intents[0]["side"] == "sell"
    assert gw.intents[0]["size"] == pytest.approx(100.0)


def test_reconcile_stuck_after_three_attempts(settings, db) -> None:
    """A persistently-rejected symbol must stop after RECONCILE_MAX_ATTEMPTS and log
    `reconcile.stuck` instead of looping forever (the 407-rejections incident)."""
    ex, watcher, gw = make_executor(settings, db, value=100.0)
    ex.RECONCILE_COOLDOWN_S = 0.0  # drive attempts back-to-back
    watcher.positions[TARGET] = {"FARTCOIN": -416.0}
    gw.mids = {"FARTCOIN": 1.0}
    gw.ledger_response = {"ct_whale01": {"positions": {}}}  # never reflects

    def failing(**payload):  # rejected: _my_pos not advanced, drift persists
        gw.intents.append(payload)
        return {"ok": False, "reason": "rejected"}

    gw.send_intent = failing
    for _ in range(5):
        ex.reconcile()
    assert len(gw.intents) == ex.RECONCILE_MAX_ATTEMPTS  # 3 sends, then stuck


def test_gateway_wait_ready_tolerates_early_failures() -> None:
    from engine.strategies.base_runner import GatewayClient

    gw = GatewayClient()
    calls = {"n": 0}

    def flaky_health() -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("gateway not up yet")
        return {"ok": True}

    gw.health = flaky_health  # type: ignore[method-assign]
    assert gw.wait_ready(attempts=3, delay=0.0) is True
    assert calls["n"] == 3
    gw.close()
