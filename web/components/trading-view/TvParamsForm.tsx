"use client";

import { fmtNum } from "@/lib/format";
import { TvStrategyForm } from "@/lib/trading-view/data";

// Form §5 (risco + execução), reutilizável no wizard e na edição. Controlado:
// recebe o estado e um setter parcial. O sizing é sempre calculado no servidor;
// o preview aqui é só informativo (mesma fórmula do fixed_fractional).
type Props = {
  value: TvStrategyForm;
  onChange: (patch: Partial<TvStrategyForm>) => void;
  equity?: number | null;
};

function num(v: number | undefined, d: number): number {
  return typeof v === "number" && !Number.isNaN(v) ? v : d;
}

// size ≈ risco% × alocação / distância_do_stop, limitado por posição máxima.
export function previewNotional(f: TvStrategyForm): number {
  const alloc = num(f.allocation_usd, 0);
  const risk = num(f.risk_per_trade_pct, 0.75) / 100;
  const stop = num(f.stop_loss_pct, 1.2) / 100;
  if (alloc <= 0 || stop <= 0) return 0;
  return Math.min((risk * alloc) / stop, num(f.max_position_usd, 200));
}

export default function TvParamsForm({ value, onChange, equity }: Props) {
  const overAlloc =
    equity != null && equity > 0 && num(value.allocation_usd, 0) > equity;
  const preview = previewNotional(value);

  const field = (
    label: string,
    key: keyof TvStrategyForm,
    hint: string,
    step = "any",
  ) => (
    <div className="field">
      <label className="field-lab">{label}</label>
      <input
        className="input"
        type="number"
        step={step}
        value={(value[key] as number) ?? ""}
        onChange={(e) => onChange({ [key]: Number(e.target.value) } as Partial<TvStrategyForm>)}
      />
      <span className="field-hint">{hint}</span>
    </div>
  );

  return (
    <div>
      <div className="modal-grid">
        <div className="modal-col">
          <div className="field">
            <label className="field-lab">Alocação de capital (USD)</label>
            <input
              className="input"
              type="number"
              step="any"
              value={value.allocation_usd ?? ""}
              onChange={(e) => onChange({ allocation_usd: Number(e.target.value) })}
            />
            <span className={`field-hint ${overAlloc ? "neg" : ""}`}>
              {overAlloc
                ? `acima do equity da wallet ($${fmtNum(equity ?? 0)})`
                : "base do sizing · soma das alocações ≤ equity da wallet"}
            </span>
          </div>
          <div className="field">
            <label className="field-lab">Método de sizing</label>
            <select
              className="select"
              value={value.sizing_method ?? "fixed_fractional"}
              onChange={(e) =>
                onChange({ sizing_method: e.target.value as TvStrategyForm["sizing_method"] })
              }
            >
              <option value="fixed_fractional">fixed_fractional</option>
              <option value="quarter_kelly">quarter_kelly (≥ 50 trades)</option>
            </select>
            <span className="field-hint">Kelly recalculado pelo sistema, nunca em tempo real</span>
          </div>
          {field("Risco por trade (%)", "risk_per_trade_pct", "usado no fixed_fractional")}
          {field("Trade mínimo (USD)", "min_trade_usd", "abaixo ⇒ SIZE_BELOW_MINIMUM")}
          {field("Posição máxima (USD)", "max_position_usd", "cap absoluto — nem Kelly ultrapassa")}
        </div>
        <div className="modal-col">
          {field("Alavancagem máxima", "max_leverage", "cap")}
          {field("Trades/dia (máx.)", "max_trades_per_day", "limite diário", "1")}
          {field("Perda diária máx. (USD)", "max_daily_loss_usd", "do ledger do módulo no ambiente")}
          {field("Cooldown pós-perda (min)", "cooldown_minutes_after_loss", "limite", "1")}
          {field("Stop loss (%)", "stop_loss_pct", "obrigatório — entrada sem stop = fechar + incidente")}
          {field("Take profit (%)", "take_profit_pct", "por estratégia")}
        </div>
      </div>
      <div className="modal-note">
        Preview do sizing: com alocação ${fmtNum(num(value.allocation_usd, 0))} e stop a{" "}
        {fmtNum(num(value.stop_loss_pct, 1.2), 2)}%, um sinal abre ≈{" "}
        <strong>${fmtNum(preview)}</strong> (cálculo final é no servidor).
      </div>
    </div>
  );
}
