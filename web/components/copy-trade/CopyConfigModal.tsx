"use client";

import { useMemo, useState } from "react";
import { fmtNum } from "@/lib/format";
import { TraderExecConfig } from "@/lib/copy-trade/data";

const MIN_NOTIONAL_USD = 10; // mínimo global da Hyperliquid (settings.risk)

type Props = {
  address: string;
  name: string;
  targetEnv: "testnet" | "mainnet";
  currentConfig?: {
    mode?: string;
    value?: number;
    max_leverage?: number;
    blocked_assets?: string[] | string;
  };
  equity?: number | null;
  busy?: boolean;
  error?: string | null;
  onClose: () => void;
  onConfirm: (config: TraderExecConfig) => void;
};

function initialBlocked(raw: string[] | string | undefined): string {
  if (!raw) return "";
  if (Array.isArray(raw)) return raw.join(",");
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.join(",") : "";
  } catch {
    return "";
  }
}

export default function CopyConfigModal({
  address,
  name,
  targetEnv,
  currentConfig,
  equity,
  busy = false,
  error = null,
  onClose,
  onConfirm,
}: Props) {
  const isMainnet = targetEnv === "mainnet";
  const [mode, setMode] = useState<"percent" | "fixed_usdc">(
    currentConfig?.mode === "fixed_usdc" ? "fixed_usdc" : "percent",
  );
  const [fraction, setFraction] = useState<number>(
    currentConfig?.mode === "percent" && currentConfig?.value
      ? currentConfig.value
      : 1.0,
  );
  const [fixedValue, setFixedValue] = useState<number>(
    currentConfig?.mode === "fixed_usdc" && currentConfig?.value
      ? currentConfig.value
      : 50,
  );
  const [maxLeverage, setMaxLeverage] = useState<number>(
    currentConfig?.max_leverage ?? 3,
  );
  const [blocked, setBlocked] = useState<string>(
    initialBlocked(currentConfig?.blocked_assets),
  );
  const [confirmed, setConfirmed] = useState(false);

  const riskMax = useMemo(() => {
    if (equity === null || equity === undefined) return null;
    return equity * maxLeverage;
  }, [equity, maxLeverage]);

  const canActivate = !busy && (!isMainnet || confirmed);

  function submit() {
    if (!canActivate) return;
    const blocked_assets = blocked
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    onConfirm({
      mode,
      value: mode === "percent" ? fraction : fixedValue,
      max_leverage: maxLeverage,
      blocked_assets,
    });
  }

  return (
    <div className="modal-scrim" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Configurar cópia de ${name}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <div className="eyebrow">
              Ativar cópia · {targetEnv === "mainnet" ? "MAINNET" : "TESTNET"}
            </div>
            <h2>{name}</h2>
            <div className="sub addr">{address}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            Fechar
          </button>
        </div>

        <div className="modal-grid">
          <div className="modal-col">
            <label className="field">
              <span className="field-lab">Modo de sizing</span>
              <select
                className="select"
                value={mode}
                onChange={(e) => setMode(e.target.value as "percent" | "fixed_usdc")}
                disabled={busy}
              >
                <option value="percent">Percentual (proporcional ao trader)</option>
                <option value="fixed_usdc">Valor fixo (USDC por posição)</option>
              </select>
            </label>

            {mode === "percent" ? (
              <label className="field">
                <span className="field-lab">Fração da proporção</span>
                <input
                  className="input"
                  type="number"
                  min={0.01}
                  max={1.0}
                  step={0.01}
                  value={fraction}
                  onChange={(e) => setFraction(Number(e.target.value))}
                  disabled={busy}
                />
                <span className="field-hint">0,01 – 1,00 (1,0 = proporção cheia)</span>
              </label>
            ) : (
              <label className="field">
                <span className="field-lab">Valor fixo (USDC)</span>
                <input
                  className="input"
                  type="number"
                  min={10}
                  max={1000}
                  step={1}
                  value={fixedValue}
                  onChange={(e) => setFixedValue(Number(e.target.value))}
                  disabled={busy}
                />
                <span className="field-hint">$10 – $1.000 por posição</span>
              </label>
            )}

            <label className="field">
              <span className="field-lab">Alavancagem máxima</span>
              <input
                className="input"
                type="number"
                min={1}
                max={10}
                step={1}
                value={maxLeverage}
                onChange={(e) => setMaxLeverage(Number(e.target.value))}
                disabled={busy}
              />
              <span className="field-hint">1x – 10x (teto de notional por posição)</span>
            </label>

            <label className="field">
              <span className="field-lab">Notional mínimo</span>
              <input className="input" type="text" value={`$${MIN_NOTIONAL_USD}`} readOnly disabled />
              <span className="field-hint">mínimo Hyperliquid (global)</span>
            </label>

            <label className="field">
              <span className="field-lab">Ativos bloqueados</span>
              <input
                className="input"
                type="text"
                placeholder="CASHCAT,SHITCOIN"
                value={blocked}
                onChange={(e) => setBlocked(e.target.value)}
                disabled={busy}
              />
              <span className="field-hint">separados por vírgula</span>
            </label>
          </div>

          <div className="modal-col">
            <div className="risk-card">
              <div className="risk-lab">Resumo de risco</div>
              <div className="risk-formula">
                {equity === null || equity === undefined ? (
                  <>Equity indisponível × {maxLeverage}x</>
                ) : (
                  <>
                    Equity ${fmtNum(equity, 0)} × {maxLeverage}x ={" "}
                    <strong>${fmtNum(riskMax ?? 0, 0)}</strong> máx por posição
                  </>
                )}
              </div>
              <div className="risk-sub">
                Cada posição é dimensionada abaixo desse teto — espelha o notional_cap
                da simulação.
              </div>
            </div>

            {isMainnet && (
              <label className="confirm-check">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  disabled={busy}
                />
                <span>Confirmo operação com dinheiro real</span>
              </label>
            )}

            {error && <div className="status-error">{error}</div>}

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
                Cancelar
              </button>
              <button
                className={`btn ${isMainnet ? "btn-danger" : "btn-go"}`}
                onClick={submit}
                disabled={!canActivate}
              >
                {busy ? "Ativando…" : "Ativar cópia"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
