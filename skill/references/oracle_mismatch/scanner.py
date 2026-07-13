#!/usr/bin/env python3
"""Oracle Mismatch — scanner de vigilância (MVP, camada Hermes).

Lê dados PÚBLICOS da Hyperliquid (`POST /info metaAndAssetCtxs`), compara cada par
da watchlist contra uma referência (`hl_peer` = mediana das variações de listings
irmãos numa janela; `cex` = nível spot público) e, quando o descolamento passa do
limiar por par, dispara alerta (Telegram + log JSONL).

Fronteira: NUNCA emite ordem, NUNCA importa `engine/`, NUNCA usa credencial de
trading. Só leitura pública + Telegram opcional. O humano opera a partir do alerta.

Contrato/detecção completos: ./README.md e docs do plano Oracle Mismatch.

Uso:
    python skill/references/oracle_mismatch/scanner.py --once
    python skill/references/oracle_mismatch/scanner.py --once --dry-run
    python skill/references/oracle_mismatch/scanner.py --list-symbols [--filter TERMO]
    python skill/references/oracle_mismatch/scanner.py --reset-state [PAIR_ID]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

# --- Caminhos (robustos a partir do __file__; cron roda com cwd=repo root) ------
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # skill/references/oracle_mismatch -> repo root
WATCHLIST_PATH = HERE / "watchlist.yaml"
STATE_PATH = REPO_ROOT / "state" / "oracle_mismatch_state.json"
LOG_PATH = REPO_ROOT / "logs" / "oracle_mismatch.jsonl"
ENV_PATH = REPO_ROOT / ".env"

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
HTTP_TIMEOUT = 5.0
SAMPLE_RING = 20          # N amostras por símbolo no estado
WINDOW_TOL_S = 90         # tolerância p/ achar a amostra na borda da janela
DATA_STALE_S = 120        # dado do provider mais velho que isso = inválido
STILL_OPEN_S = 30 * 60    # lembrete único p/ evento aberto > 30 min
CLOSE_CYCLES = 2          # ciclos abaixo da histerese p/ fechar
HYSTERESIS = 0.7          # fração do threshold p/ manter evento aberto

STATE_VERSION = 2


# --- Log ----------------------------------------------------------------------
def log(event: str, level: str = "info", **fields: Any) -> None:
    rec = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:  # log nunca derruba o ciclo
        print(f"[log_write_failed] {exc}", file=sys.stderr)
    print(line)


# --- .env loader (só p/ Telegram; nunca loga valores) -------------------------
def load_env_secret(name: str) -> str:
    val = os.environ.get(name, "")
    if val:
        return val
    if ENV_PATH.exists():
        try:
            for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
                s = raw.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
        except Exception:
            return ""
    return ""


def send_telegram(text: str, dry_run: bool) -> bool:
    if dry_run:
        log("telegram_dry_run", text=text[:200])
        return False
    token = load_env_secret("TELEGRAM_BOT_TOKEN")
    chat_id = load_env_secret("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        log("skipped_unconfigured", level="warning", text=text[:200])
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # notificação nunca derruba o ciclo
        log("telegram_failed", level="warning", error=str(exc)[:200])
        return False


# --- Estado persistido --------------------------------------------------------
def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": STATE_VERSION, "symbols": {}, "alerts": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "symbols" not in data:
            raise ValueError("estrutura inesperada")
        data.setdefault("version", STATE_VERSION)
        data.setdefault("symbols", {})
        data.setdefault("alerts", {})
        return data
    except Exception as exc:
        corrupt = STATE_PATH.with_suffix(f".corrupt-{int(time.time())}")
        try:
            STATE_PATH.replace(corrupt)
        except Exception:
            pass
        log("state_reset", level="warning", reason=str(exc)[:200], moved_to=corrupt.name)
        return {"version": STATE_VERSION, "symbols": {}, "alerts": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATE_PATH)  # atômico


def append_sample(state: dict[str, Any], symbol: str, ts: float, price: float) -> None:
    sym = state["symbols"].setdefault(symbol, {"samples": []})
    sym["samples"].append([round(ts, 3), price])
    if len(sym["samples"]) > SAMPLE_RING:
        sym["samples"] = sym["samples"][-SAMPLE_RING:]


def pct_change_over_window(
    state: dict[str, Any], symbol: str, now: float, window_s: int
) -> float | None:
    """Δ% do símbolo entre agora e a borda da janela (amostra mais próxima de
    now-window_s dentro da tolerância). None = sem baseline válido (warm-up)."""
    samples = state["symbols"].get(symbol, {}).get("samples", [])
    if len(samples) < 2:
        return None
    target = now - window_s
    best = min(samples, key=lambda s: abs(s[0] - target))
    if abs(best[0] - target) > WINDOW_TOL_S:
        return None
    base_price = best[1]
    cur_price = samples[-1][1]
    if base_price <= 0:
        return None
    return (cur_price / base_price - 1.0) * 100.0


# --- Hyperliquid --------------------------------------------------------------
def fetch_hl_meta_ctxs(dex: str | None = None) -> tuple[list[dict], list[dict]]:
    """meta+ctxs de um perp meta. `dex=None` → meta padrão (cripto); `dex="xyz"`
    → perp DEX de builder (HIP-3, símbolos namespaced `xyz:AAPL`, `xyz:SPCX`…)."""
    payload: dict[str, Any] = {"type": "metaAndAssetCtxs"}
    if dex:
        payload["dex"] = dex
    resp = httpx.post(HL_INFO_URL, json=payload, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    meta, ctxs = data[0], data[1]
    universe = meta.get("universe", [])
    return universe, ctxs


def fetch_builder_dexs() -> list[str]:
    """Nomes dos perp DEXs de builder (HIP-3). O default (index 0) vem null."""
    try:
        resp = httpx.post(HL_INFO_URL, json={"type": "perpDexs"}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        out = []
        for entry in resp.json():
            if isinstance(entry, dict) and entry.get("name"):
                out.append(entry["name"])
        return out
    except Exception as exc:
        log("perp_dexs_failed", level="warning", error=str(exc)[:200])
        return []


def hl_price_map(universe: list[dict], ctxs: list[dict]) -> dict[str, dict[str, float]]:
    """name -> {oracle, mark, mid} (float). Índice de ctxs alinha com universe."""
    out: dict[str, dict[str, float]] = {}
    for i, asset in enumerate(universe):
        name = asset.get("name")
        if name is None or i >= len(ctxs):
            continue
        ctx = ctxs[i]
        def _f(key: str) -> float:
            try:
                return float(ctx.get(key))
            except (TypeError, ValueError):
                return 0.0
        out[name] = {
            "oracle": _f("oraclePx"),
            "mark": _f("markPx"),
            "mid": _f("midPx"),
        }
    return out


def fetch_all_hl_prices() -> dict[str, dict[str, float]]:
    """Price map unificado: meta padrão (cripto) + cada perp DEX de builder
    (HIP-3, chaves já namespaced tipo `xyz:SPCX`). Uma chamada por dex; falha de
    um dex loga `dex_fetch_failed` e segue com os demais — nunca derruba o ciclo.
    """
    universe, ctxs = fetch_hl_meta_ctxs()  # default (cripto) — deixa exceção subir
    prices = hl_price_map(universe, ctxs)
    for dex in fetch_builder_dexs():
        try:
            duni, dctxs = fetch_hl_meta_ctxs(dex=dex)
            prices.update(hl_price_map(duni, dctxs))
        except Exception as exc:
            log("dex_fetch_failed", level="warning", dex=dex, error=str(exc)[:200])
    return prices


def hl_price(prices: dict[str, dict[str, float]], symbol: str, price_type: str) -> float | None:
    entry = prices.get(symbol)
    if not entry:
        return None
    val = entry.get(price_type, 0.0)
    return val if val > 0 else None


# --- CEX providers (REST público, sem key) ------------------------------------
def fetch_cex_price(venue: str, symbol: str) -> float | None:
    try:
        if venue == "binance":
            r = httpx.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol}, timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            return float(r.json()["price"])
        if venue == "coinbase":
            r = httpx.get(
                f"https://api.coinbase.com/v2/prices/{symbol}/spot", timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            return float(r.json()["data"]["amount"])
        if venue == "bybit":
            r = httpx.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "spot", "symbol": symbol}, timeout=HTTP_TIMEOUT,
            )
            r.raise_for_status()
            return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception as exc:
        log("sample_invalid", level="warning", ref_kind="cex", venue=venue,
            symbol=symbol, reason=str(exc)[:160])
        return None
    log("sample_invalid", level="warning", ref_kind="cex", reason=f"venue desconhecido: {venue}")
    return None


# --- Debounce / histerese -----------------------------------------------------
def handle_signal(
    state: dict[str, Any], pair_id: str, fired: bool, metric: float,
    threshold: float, now: float, msg_builder, dry_run: bool,
) -> None:
    alerts = state["alerts"]
    open_alert = alerts.get(pair_id)
    hyst = threshold * HYSTERESIS
    above_hyst = abs(metric) > hyst

    if open_alert is None:
        if fired:
            alerts[pair_id] = {
                "first_ts": now, "last_ts": now, "diff_pct_max": metric,
                "below_count": 0, "still_open_notified": False,
            }
            sent = send_telegram(msg_builder("ABERTO"), dry_run)
            log("alert_open", pair_id=pair_id, metric=round(metric, 3),
                threshold=threshold, notified=sent)
        return

    # já existe evento aberto
    open_alert["last_ts"] = now
    if abs(metric) > abs(open_alert["diff_pct_max"]):
        open_alert["diff_pct_max"] = metric

    if above_hyst:
        open_alert["below_count"] = 0
        duration = now - open_alert["first_ts"]
        if duration > STILL_OPEN_S and not open_alert["still_open_notified"]:
            open_alert["still_open_notified"] = True
            sent = send_telegram(msg_builder("AINDA ABERTO"), dry_run)
            log("alert_still_open", pair_id=pair_id, metric=round(metric, 3),
                duration_s=round(duration), notified=sent)
        else:
            log("alert_ongoing", pair_id=pair_id, metric=round(metric, 3),
                duration_s=round(duration))
    else:
        open_alert["below_count"] += 1
        if open_alert["below_count"] >= CLOSE_CYCLES:
            duration = now - open_alert["first_ts"]
            log("alert_closed", pair_id=pair_id,
                diff_pct_max=round(open_alert["diff_pct_max"], 3),
                duration_s=round(duration))
            alerts.pop(pair_id, None)
        else:
            log("alert_cooling", pair_id=pair_id, metric=round(metric, 3),
                below_count=open_alert["below_count"])


# --- Avaliação por par --------------------------------------------------------
def eval_pair(
    state: dict[str, Any], pair: dict[str, Any], prices: dict[str, dict[str, float]],
    now: float, defaults: dict[str, Any], dry_run: bool,
) -> None:
    pair_id = pair.get("pair_id", "?")
    symbol = pair.get("hl_symbol")
    price_type = pair.get("price_type", "oracle")
    ref_kind = pair.get("ref_kind")
    threshold = float(pair.get("threshold_pct", defaults.get("threshold_pct", 10.0)))
    window_s = int(pair.get("window_s", defaults.get("window_s", 600)))

    px_hl = hl_price(prices, symbol, price_type)
    if px_hl is None:
        log("sample_invalid", level="warning", pair_id=pair_id, hl_symbol=symbol,
            reason="símbolo ausente no meta ou preço<=0")
        return
    append_sample(state, symbol, now, px_hl)

    if ref_kind == "cex":
        ref = pair.get("ref", {})
        px_cex = fetch_cex_price(ref.get("venue", ""), ref.get("symbol", ""))
        if px_cex is None or px_cex <= 0:
            return  # sample_invalid já logado
        diff_pct = (px_hl / px_cex - 1.0) * 100.0
        fired = abs(diff_pct) > threshold
        log("sample", pair_id=pair_id, ref_kind="cex", px_hl=px_hl, px_ref=px_cex,
            diff_pct=round(diff_pct, 3), threshold=threshold, fired=fired)

        def _msg(state_lbl: str) -> str:
            return (f"*Oracle Mismatch* [{state_lbl}] `{pair_id}`\n"
                    f"HL {price_type}: {px_hl:g} | {ref.get('venue')} {ref.get('symbol')}: {px_cex:g}\n"
                    f"diff: *{diff_pct:+.2f}%* (limiar {threshold:g}%)")

        handle_signal(state, pair_id, fired, diff_pct, threshold, now, _msg, dry_run)
        return

    if ref_kind == "hl_peer":
        peers = pair.get("peer_group", []) or []
        # amostra os peers no mesmo ciclo
        for p in peers:
            p_px = hl_price(prices, p, price_type)
            if p_px is not None:
                append_sample(state, p, now, p_px)
        pair_delta = pct_change_over_window(state, symbol, now, window_s)
        if pair_delta is None:
            log("warmup", pair_id=pair_id, hl_symbol=symbol,
                reason="sem baseline na janela")
            return
        peer_deltas = []
        for p in peers:
            d = pct_change_over_window(state, p, now, window_s)
            if d is not None:
                peer_deltas.append(d)
        if len(peer_deltas) < 2:
            log("sample_invalid", level="warning", pair_id=pair_id,
                reason=f"peers válidos na janela < 2 ({len(peer_deltas)})")
            return
        peer_median = statistics.median(peer_deltas)
        cond_pair = abs(pair_delta) > threshold
        cond_peers_flat = abs(peer_median) < threshold / 3.0
        fired = cond_pair and cond_peers_flat
        log("sample", pair_id=pair_id, ref_kind="hl_peer", px_hl=px_hl,
            pair_delta_pct=round(pair_delta, 3),
            peer_median_delta_pct=round(peer_median, 3),
            n_peers=len(peer_deltas), threshold=threshold,
            window_s=window_s, fired=fired)

        def _msg(state_lbl: str) -> str:
            return (f"*Oracle Mismatch* [{state_lbl}] `{pair_id}` ({symbol})\n"
                    f"Δ% par: *{pair_delta:+.2f}%* | Δ% peers (mediana): {peer_median:+.2f}%\n"
                    f"janela {window_s}s, {len(peer_deltas)} peers | limiar {threshold:g}%")

        handle_signal(state, pair_id, fired, pair_delta, threshold, now, _msg, dry_run)
        return

    log("sample_invalid", level="warning", pair_id=pair_id,
        reason=f"ref_kind desconhecido: {ref_kind}")


# --- Watchlist ----------------------------------------------------------------
def load_watchlist() -> dict[str, Any]:
    if not WATCHLIST_PATH.exists():
        log("watchlist_missing", level="error", path=str(WATCHLIST_PATH))
        return {"defaults": {}, "pairs": []}
    data = yaml.safe_load(WATCHLIST_PATH.read_text(encoding="utf-8")) or {}
    data.setdefault("defaults", {})
    data.setdefault("pairs", [])
    return data


# --- Comandos -----------------------------------------------------------------
def cmd_list_symbols(term: str | None) -> int:
    prices = fetch_all_hl_prices()  # inclui HIP-3 (xyz:*)
    rows = []
    for name, px in prices.items():
        if term and term.lower() not in name.lower():
            continue
        rows.append((name, px["oracle"], px["mark"], px["mid"]))
    rows.sort(key=lambda r: r[0].lower())
    print(f"# {len(rows)} símbolos" + (f" (filtro '{term}')" if term else "")
          + f" de {len(prices)} no universe da mainnet")
    for name, o, m, mid in rows:
        print(f"{name:<24} oracle={o:<14g} mark={m:<14g} mid={mid:g}")
    return 0


def cmd_reset_state(pair_id: str | None) -> int:
    if not STATE_PATH.exists():
        print("sem estado para resetar")
        return 0
    if pair_id is None:
        STATE_PATH.unlink()
        log("state_reset", reason="reset manual (total)")
        print("estado resetado (total)")
        return 0
    state = load_state()
    removed = state["alerts"].pop(pair_id, None)
    save_state(state)
    log("state_reset", reason="reset manual (par)", pair_id=pair_id,
        had_open_alert=removed is not None)
    print(f"alerta aberto de '{pair_id}' resetado (amostras preservadas)")
    return 0


def run_once(dry_run: bool) -> int:
    now = time.time()
    wl = load_watchlist()
    defaults = wl.get("defaults", {})
    pairs = [p for p in wl.get("pairs", []) if p.get("enabled", True)]
    if not pairs:
        log("no_enabled_pairs", level="warning")
        return 0
    try:
        prices = fetch_all_hl_prices()  # meta padrão + builder dexs (HIP-3)
    except Exception as exc:
        log("hl_fetch_failed", level="error", error=str(exc)[:200])
        return 1
    state = load_state()
    for pair in pairs:
        try:
            eval_pair(state, pair, prices, now, defaults, dry_run)
        except Exception as exc:  # um par ruim não derruba os outros
            log("pair_error", level="error", pair_id=pair.get("pair_id", "?"),
                error=str(exc)[:200])
    save_state(state)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Oracle Mismatch scanner (MVP, Hermes)")
    ap.add_argument("--once", action="store_true", help="roda um ciclo e sai (uso no cron)")
    ap.add_argument("--dry-run", action="store_true", help="calcula/loga, não envia Telegram")
    ap.add_argument("--list-symbols", action="store_true", help="lista o universe da mainnet")
    ap.add_argument("--filter", default=None, help="filtro de substring p/ --list-symbols")
    ap.add_argument("--reset-state", nargs="?", const="__ALL__", default=None,
                    help="reseta estado (opcional: só o par informado)")
    args = ap.parse_args(argv)

    if args.list_symbols:
        return cmd_list_symbols(args.filter)
    if args.reset_state is not None:
        return cmd_reset_state(None if args.reset_state == "__ALL__" else args.reset_state)
    if args.once:
        return run_once(dry_run=args.dry_run)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
