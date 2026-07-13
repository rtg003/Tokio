#!/usr/bin/env python3
"""Oracle Mismatch — detector de novos listings no builder dex (HIP-3).

Roda 1x/dia (cron), busca o universe atual do builder dex `xyz:` (via perpDexs),
compara contra o último snapshot, e avisa no Telegram + log quando um símbolo
NOVO aparece. Também avisa quando um símbolo SUMIU (delisting — relevante porque
pode indicar problema ou mudança de oráculo).

Fronteira: só leitura pública + alerta. NUNCA emite ordem. NUNCA importa engine/.

Uso:
    python skill/references/oracle_mismatch/listing_watch.py
    python skill/references/oracle_mismatch/listing_watch.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Reaproveita as funções de fetch do scanner
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

from scanner import (  # noqa: E402
    fetch_all_hl_prices,
    load_env_secret,
    log,
    send_telegram,
    ENV_PATH,
    LOG_PATH,
)

SNAPSHOT_PATH = REPO_ROOT / "state" / "oracle_listings_snapshot.json"

# Só vigiamos o builder dex xyz: (HIP-3 equities/pré-IPO — onde misconfig acontece)
BUILDER_PREFIX = "xyz:"


def load_snapshot() -> dict[str, float]:
    """Símbolos vistos na última execução → {symbol: price}."""
    if not SNAPSHOT_PATH.exists():
        return {}
    try:
        data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_snapshot(symbols: dict[str, float]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(symbols, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(tmp, SNAPSHOT_PATH)


def run(dry_run: bool) -> int:
    now = time.time()

    # Busca todos os preços (meta padrão + builder dexs)
    try:
        prices = fetch_all_hl_prices()
    except Exception as exc:
        log("listing_watch_failed", level="error", error=str(exc)[:200])
        return 1

    # Filtra só builder dex (xyz:*)
    current = {
        name: px["oracle"]
        for name, px in prices.items()
        if name.startswith(BUILDER_PREFIX) and px.get("oracle", 0) > 0
    }

    previous = load_snapshot()

    new_listings = sorted(set(current) - set(previous))
    removed = sorted(set(previous) - set(current))

    # Sempre loga o ciclo (mesmo sem mudanças, para auditoria)
    log(
        "listing_watch",
        total_symbols=len(current),
        new_count=len(new_listings),
        removed_count=len(removed),
        new_symbols=new_listings[:20],
        removed_symbols=removed[:20],
    )

    # Salva snapshot (mesmo sem mudanças — atualiza preços/prevenção de drift)
    save_snapshot(current)

    if not new_listings and not removed:
        # Silencioso — nada para reportar
        print(f"listing_watch: {len(current)} símbolos, nenhuma mudança.")
        return 0

    # Constrói mensagem
    lines = [f"*Oracle Mismatch — Listing Watch*"]
    if new_listings:
        lines.append(f"\n🆕 *{len(new_listings)} novo(s) listing(s):*")
        for s in new_listings[:15]:
            px = current.get(s, 0)
            lines.append(f"  `{s}` (oracle: {px:g})")
        if len(new_listings) > 15:
            lines.append(f"  _...e mais {len(new_listings) - 15}_")
    if removed:
        lines.append(f"\n❌ *{len(removed)} listing(s) removido(s):*")
        for s in removed[:15]:
            lines.append(f"  `{s}`")
        if len(removed) > 15:
            lines.append(f"  _...e mais {len(removed) - 15}_")

    lines.append(f"\n_Total no universe `xyz:`: {len(current)}_")
    lines.append(f"_Revise `watchlist.yaml` se quiser vigiar algum novo._")

    msg = "\n".join(lines)
    sent = send_telegram(msg, dry_run)
    log(
        "listing_watch_alert",
        new_listings=new_listings,
        removed=removed,
        notified=sent,
    )
    print(msg)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Oracle Mismatch — listing watch")
    ap.add_argument("--dry-run", action="store_true", help="não envia Telegram")
    args = ap.parse_args(argv)
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
