"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  deleteTvStrategy,
  pauseTvStrategy,
  TvStrategy,
  TvStrategyForm,
  updateTvStrategy,
} from "@/lib/trading-view/data";
import TvParamsForm from "@/components/trading-view/TvParamsForm";

// Ações por linha da tabela de Estratégias (TV): editar params (modal reutilizando
// TvParamsForm → POST /config versionado), pausar (POST /pause) e excluir (modal
// destrutivo com confirmação → POST /delete). Excluir apaga SÓ dados do módulo TV
// (fills/orders preservados); o gateway recusa estratégia ativa ou com posição
// aberta. Após qualquer mutação, router.refresh() recarrega o server component.

// Achata o config_snapshot (aninhado: sizing/risk_rules/exit_rules) no shape plano
// que o TvParamsForm/endpoint /config esperam.
function flattenConfig(s: TvStrategy): TvStrategyForm {
  let cfg: Record<string, any> = {};
  try {
    cfg = s.config_snapshot ? JSON.parse(s.config_snapshot) : {};
  } catch {
    cfg = {};
  }
  const sizing = cfg.sizing ?? {};
  const risk = cfg.risk_rules ?? {};
  const exit = cfg.exit_rules ?? {};
  return {
    strategy_id: s.strategy_id,
    name: s.name ?? s.strategy_id,
    environment: s.environment,
    symbols_allowed: Array.isArray(cfg.symbols_allowed) ? cfg.symbols_allowed : [],
    timeframes_allowed: Array.isArray(cfg.timeframes_allowed) ? cfg.timeframes_allowed : [],
    allocation_usd: Number(sizing.allocation_usd ?? 0),
    sizing_method: sizing.method ?? "fixed_fractional",
    risk_per_trade_pct: Number(sizing.risk_per_trade_pct ?? 0.75),
    min_trade_usd: Number(sizing.min_trade_usd ?? 12),
    max_position_usd: Number(sizing.max_position_usd ?? 200),
    max_leverage: Number(risk.max_leverage ?? 3),
    max_trades_per_day: Number(risk.max_trades_per_day ?? 5),
    max_daily_loss_usd: Number(risk.max_daily_loss_usd ?? 100),
    cooldown_minutes_after_loss: Number(risk.cooldown_minutes_after_loss ?? 30),
    stop_loss_pct: Number(exit.stop_loss_pct ?? 1.2),
    take_profit_pct: Number(exit.take_profit_pct ?? 2.4),
  };
}

const REASON_LABEL: Record<string, string> = {
  ativa_pause_antes: "Estratégia ativa — pause antes de excluir.",
  posicao_aberta: "Há posição aberta no ambiente — zere antes de excluir.",
  gateway_indisponivel: "Gateway indisponível.",
};
function reasonText(r?: string): string {
  return (r && REASON_LABEL[r]) || r || "Falha na operação.";
}

export default function StrategyRowActions({ strategy }: { strategy: TvStrategy }) {
  const router = useRouter();
  const [mode, setMode] = useState<null | "edit" | "delete">(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<TvStrategyForm>(() => flattenConfig(strategy));
  const [confirmDelete, setConfirmDelete] = useState(false);

  const isActive = strategy.status === "active";
  const isPaused = strategy.status === "paused" || strategy.status === "auto_paused";

  function openEdit() {
    setForm(flattenConfig(strategy));
    setError(null);
    setMode("edit");
  }

  async function onPause() {
    if (busy || isPaused) return;
    setBusy(true);
    const res = await pauseTvStrategy(strategy.strategy_id);
    setBusy(false);
    if (!res.ok) {
      setError(reasonText(res.reason));
      return;
    }
    router.refresh();
  }

  async function onSaveEdit() {
    setBusy(true);
    setError(null);
    const patch: Record<string, unknown> = {
      sizing_method: form.sizing_method,
      allocation_usd: form.allocation_usd,
      risk_per_trade_pct: form.risk_per_trade_pct,
      min_trade_usd: form.min_trade_usd,
      max_position_usd: form.max_position_usd,
      max_leverage: form.max_leverage,
      max_trades_per_day: form.max_trades_per_day,
      max_daily_loss_usd: form.max_daily_loss_usd,
      cooldown_minutes_after_loss: form.cooldown_minutes_after_loss,
      stop_loss_pct: form.stop_loss_pct,
      take_profit_pct: form.take_profit_pct,
    };
    const res = await updateTvStrategy(strategy.strategy_id, patch);
    setBusy(false);
    if (!res.ok) {
      setError(reasonText(res.reason));
      return;
    }
    setMode(null);
    router.refresh();
  }

  async function onConfirmDelete() {
    if (!confirmDelete || busy) return;
    setBusy(true);
    setError(null);
    const res = await deleteTvStrategy(strategy.strategy_id);
    setBusy(false);
    if (!res.ok) {
      setError(reasonText(res.reason));
      return;
    }
    setMode(null);
    router.refresh();
  }

  return (
    <div className="row-actions" onClick={(e) => e.stopPropagation()}>
      <button
        className="icon-btn"
        title="Editar parâmetros"
        aria-label="Editar parâmetros"
        onClick={openEdit}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 20h9" />
          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
        </svg>
      </button>
      <button
        className="icon-btn"
        title={isPaused ? "Já pausada" : "Pausar"}
        aria-label="Pausar"
        onClick={onPause}
        disabled={isPaused || busy}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="6" y="4" width="4" height="16" />
          <rect x="14" y="4" width="4" height="16" />
        </svg>
      </button>
      <button
        className="icon-btn icon-btn-danger"
        title="Excluir"
        aria-label="Excluir"
        onClick={() => {
          setConfirmDelete(false);
          setError(null);
          setMode("delete");
        }}
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
          strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 6h18" />
          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
          <path d="M10 11v6M14 11v6" />
        </svg>
      </button>

      {mode === "edit" && (
        <div className="modal-scrim" onClick={busy ? undefined : () => setMode(null)}>
          <div className="modal" role="dialog" aria-modal="true"
            aria-label={`Editar ${form.name}`} onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="eyebrow">Editar parâmetros · {strategy.environment}</div>
                <h2>{form.name}</h2>
                <div className="sub addr">{strategy.strategy_id}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setMode(null)} disabled={busy}>
                Fechar
              </button>
            </div>
            <TvParamsForm value={form} onChange={(p) => setForm((f) => ({ ...f, ...p }))} />
            {error && <div className="status-error modal-err">{error}</div>}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setMode(null)} disabled={busy}>
                Cancelar
              </button>
              <button className="btn btn-go" onClick={onSaveEdit} disabled={busy}>
                {busy ? "Salvando…" : "Salvar (nova versão)"}
              </button>
            </div>
          </div>
        </div>
      )}

      {mode === "delete" && (
        <div className="modal-scrim" onClick={busy ? undefined : () => setMode(null)}>
          <div className="modal" role="dialog" aria-modal="true"
            aria-label={`Excluir ${strategy.name ?? strategy.strategy_id}`}
            onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div>
                <div className="eyebrow">Excluir estratégia · {strategy.environment}</div>
                <h2>{strategy.name ?? strategy.strategy_id}</h2>
                <div className="sub addr">{strategy.strategy_id}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => setMode(null)} disabled={busy}>
                Fechar
              </button>
            </div>
            <div className="modal-section">
              <p className="modal-note">
                Isto apaga <strong>permanentemente</strong> todos os dados do módulo TV desta
                estratégia: sinais, decisões, incidentes, fila, versões e a própria linha da
                estratégia. Os registros de execução (<strong>fills e orders</strong>) são
                <strong> preservados</strong> para o ledger e a reconciliação.
              </p>
              {isActive && (
                <div className="status-error modal-err">
                  Estratégia ativa — pause antes de excluir.
                </div>
              )}
              <label className="confirm-check">
                <input
                  type="checkbox"
                  checked={confirmDelete}
                  onChange={(e) => setConfirmDelete(e.target.checked)}
                  disabled={busy}
                />
                <span>Confirmo a exclusão dos dados TV desta estratégia</span>
              </label>
            </div>
            {error && <div className="status-error modal-err">{error}</div>}
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setMode(null)} disabled={busy}>
                Cancelar
              </button>
              <button
                className="btn btn-danger"
                onClick={onConfirmDelete}
                disabled={!confirmDelete || busy}
              >
                {busy ? "Excluindo…" : "Excluir estratégia"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
