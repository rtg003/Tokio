"use client";

import { useEffect, useMemo, useState } from "react";
import { fmtNum, fmtSigned, pnlClass } from "@/lib/format";
import {
  ClosePosition,
  getTraderOpenPositions,
  TraderExecConfig,
} from "@/lib/copy-trade/data";

const MIN_NOTIONAL_USD = 10; // mínimo global da Hyperliquid (settings.risk)
const CLOSE_FEE_RATE = 0.00045; // 0,045% taxa estimada de fechamento (taker)

// Estatísticas reais do trader (linha de /api/traders) usadas para sugerir
// fração e alavancagem sensatas por trader.
export type TraderStats = {
  equity?: number | null;
  avg_leverage?: number | null;
  max_current_leverage?: number | null;
  sim_max_dd_pct?: number | null;
};

type Props = {
  address: string;
  name: string;
  targetEnv: "testnet" | "mainnet";
  currentEnv: "testnet" | "mainnet" | null;
  currentConfig?: {
    mode?: string;
    value?: number;
    max_leverage?: number;
    blocked_assets?: string[] | string;
    thresholds?: Record<string, number> | string;
    copy_existing_positions?: boolean | number;
  };
  stats?: TraderStats;
  equity?: number | null;
  busy?: boolean;
  error?: string | null;
  progress?: string | null;
  onClose: () => void;
  onConfirm: (config: TraderExecConfig, closePositions: boolean) => void;
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

function parseThresholds(raw: Record<string, number> | string | undefined): Record<string, number> {
  if (!raw) return {};
  if (typeof raw === "object") return raw;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

// Alavancagem sugerida: acompanha a alavancagem que o trader realmente usa
// (posição aberta agora → média histórica → 5), travada em [1, 10].
// UPDATE-0078: padrão de fallback 3→5; máximo permitido continua 10.
function suggestLeverage(s?: TraderStats): number {
  const lev = s?.max_current_leverage ?? s?.avg_leverage ?? 5;
  return Math.min(10, Math.max(1, Math.round(lev || 5)));
}

// Fração sugerida: reduz a proporção quando a cópia simulada foi muito volátil,
// mirando ~25% de drawdown. Sem sim_max_dd_pct → 1,0 (proporção cheia).
function suggestFraction(s?: TraderStats): number {
  const dd = s?.sim_max_dd_pct;
  if (dd == null || dd <= 0) return 1.0;
  const f = Math.min(1.0, Math.max(0.1, 0.25 / (dd / 100)));
  return Math.round(f * 100) / 100;
}

function initialMinNotional(cfg: Props["currentConfig"]): number {
  const v = parseThresholds(cfg?.thresholds).min_notional_usd;
  return typeof v === "number" && v >= MIN_NOTIONAL_USD ? v : MIN_NOTIONAL_USD;
}

function netClose(p: ClosePosition): number {
  const notional =
    p.position_value != null
      ? Math.abs(p.position_value)
      : Math.abs((p.size ?? 0) * (p.entry_price ?? 0));
  const fee = notional * CLOSE_FEE_RATE;
  return (p.unrealized_pnl ?? 0) - fee;
}

export default function CopyConfigModal({
  address,
  name,
  targetEnv,
  currentEnv,
  currentConfig,
  stats,
  equity,
  busy = false,
  error = null,
  progress = null,
  onClose,
  onConfirm,
}: Props) {
  const isMainnet = targetEnv === "mainnet";

  // Sugestões calculadas a partir da realidade do trader.
  const sugFraction = suggestFraction(stats);
  const sugLeverage = suggestLeverage(stats);

  // -- Seção A: posições abertas no ambiente antigo -------------------------
  const [loadingPos, setLoadingPos] = useState(currentEnv !== null);
  const [positions, setPositions] = useState<ClosePosition[]>([]);
  const [closeConfirmed, setCloseConfirmed] = useState(false);

  useEffect(() => {
    let alive = true;
    if (currentEnv === null) {
      setLoadingPos(false);
      return;
    }
    getTraderOpenPositions(address, currentEnv).then((res) => {
      if (!alive) return;
      setPositions(res.positions.filter((p) => Math.abs(p.size ?? 0) > 0));
      setLoadingPos(false);
    });
    return () => {
      alive = false;
    };
  }, [address, currentEnv]);

  const hasPositions = positions.length > 0;
  const totalNet = useMemo(
    () => positions.reduce((s, p) => s + netClose(p), 0),
    [positions],
  );

  // -- Seção B: configuração de sizing --------------------------------------
  // Modo padrão = percentual. Só uma config percent já salva é respeitada; os
  // defaults de seed (fixed_usdc/3x) dão lugar às sugestões por trader.
  const savedPercent = currentConfig?.mode === "percent";
  const [mode, setMode] = useState<"percent" | "fixed_usdc">("percent");
  const [fraction, setFraction] = useState<number>(
    savedPercent && currentConfig?.value ? currentConfig.value : sugFraction,
  );
  const [fixedValue, setFixedValue] = useState<number>(
    currentConfig?.mode === "fixed_usdc" && currentConfig?.value ? currentConfig.value : 50,
  );
  const [maxLeverage, setMaxLeverage] = useState<number>(
    savedPercent && currentConfig?.max_leverage ? currentConfig.max_leverage : sugLeverage,
  );
  const [minNotional, setMinNotional] = useState<number>(initialMinNotional(currentConfig));
  const [blocked, setBlocked] = useState<string>(initialBlocked(currentConfig?.blocked_assets));
  // UPDATE-0084: copiar (ou não) as posições JÁ ABERTAS do trader na ativação.
  // Default marcado (comportamento atual); só false quando salvo explicitamente.
  const [copyExisting, setCopyExisting] = useState<boolean>(
    currentConfig?.copy_existing_positions === undefined
      ? true
      : Boolean(currentConfig.copy_existing_positions),
  );
  const [confirmedReal, setConfirmedReal] = useState(false);

  const riskMax = useMemo(() => {
    if (equity === null || equity === undefined) return null;
    return equity * maxLeverage;
  }, [equity, maxLeverage]);

  const canActivate =
    !busy &&
    (!isMainnet || confirmedReal) &&
    (!hasPositions || closeConfirmed);

  function submit() {
    if (!canActivate) return;
    const blocked_assets = blocked
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    onConfirm(
      {
        mode,
        value: mode === "percent" ? fraction : fixedValue,
        max_leverage: maxLeverage,
        blocked_assets,
        thresholds: {
          ...parseThresholds(currentConfig?.thresholds),
          min_notional_usd: minNotional,
        },
        copy_existing_positions: copyExisting,
      },
      hasPositions,
    );
  }

  return (
    <div className="modal-scrim" onClick={busy ? undefined : onClose}>
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
              Ativar cópia · {isMainnet ? "MAINNET" : "TESTNET"}
            </div>
            <h2>{name}</h2>
            <div className="sub addr">{address}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={busy}>
            Fechar
          </button>
        </div>

        {/* ---- Seção A: posições abertas (só se houver) ---- */}
        {loadingPos && <div className="modal-note">Carregando posições…</div>}
        {!loadingPos && hasPositions && (
          <section className="modal-section">
            <div className="section-lab">
              Posições abertas em {currentEnv} — serão fechadas
            </div>
            <table className="postable">
              <thead>
                <tr>
                  <th>Ativo</th>
                  <th>Lado</th>
                  <th className="num">Size</th>
                  <th className="num">Entry</th>
                  <th className="num">PnL n/r</th>
                  <th className="num">Líq. est.</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => {
                  const nc = netClose(p);
                  return (
                    <tr key={p.symbol}>
                      <td>{p.symbol}</td>
                      <td>{(p.size ?? 0) > 0 ? "LONG" : "SHORT"}</td>
                      <td className="num">{fmtNum(Math.abs(p.size ?? 0), 4)}</td>
                      <td className="num">{fmtNum(p.entry_price, 2)}</td>
                      <td className={`num ${pnlClass(p.unrealized_pnl)}`}>
                        {fmtSigned(p.unrealized_pnl)}
                      </td>
                      <td className={`num ${pnlClass(nc)}`}>{fmtSigned(nc)}</td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={5}>Total líquido estimado</td>
                  <td className={`num ${pnlClass(totalNet)}`}>{fmtSigned(totalNet)}</td>
                </tr>
              </tfoot>
            </table>
            <div className={`close-warn ${totalNet < 0 ? "neg" : "pos"}`}>
              {totalNet < 0
                ? `⚠️ Fechamento resultará em perda de ${fmtSigned(totalNet)}`
                : `✅ Fechamento com lucro de ${fmtSigned(totalNet)}`}
            </div>
            <label className="confirm-check">
              <input
                type="checkbox"
                checked={closeConfirmed}
                onChange={(e) => setCloseConfirmed(e.target.checked)}
                disabled={busy}
              />
              <span>
                Confirmo o fechamento das {positions.length} posições em {currentEnv}
              </span>
            </label>
          </section>
        )}

        {/* ---- Seção B: configuração ---- */}
        <section className="modal-section">
          <div className="section-lab">Configuração de sizing</div>
          <div className="modal-grid">
            <div className="modal-col">
              <label className="field">
                <span className="field-lab">Modo</span>
                <select
                  className="select"
                  value={mode}
                  onChange={(e) => setMode(e.target.value as "percent" | "fixed_usdc")}
                  disabled={busy}
                >
                  <option value="percent">Percentual</option>
                  <option value="fixed_usdc">Valor fixo (USDC)</option>
                </select>
              </label>

              {mode === "percent" ? (
                <label className="field">
                  <span className="field-lab">Fração</span>
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
                  <span className="field-lab">Valor fixo</span>
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
                <span className="field-hint">1x – 10x</span>
              </label>

              <div className="suggest-hint">
                sugerido para este trader: {fmtNum(sugFraction, 2)}× / {sugLeverage}x
              </div>
            </div>

            <div className="modal-col">
              <label className="field">
                <span className="field-lab">Notional mínimo</span>
                <input
                  className="input"
                  type="number"
                  min={MIN_NOTIONAL_USD}
                  step={1}
                  value={minNotional}
                  onChange={(e) => setMinNotional(Number(e.target.value))}
                  disabled={busy}
                />
                <span className="field-hint">≥ ${MIN_NOTIONAL_USD} (mínimo Hyperliquid)</span>
              </label>

              <label className="field">
                <span className="field-lab">Ativos bloqueados</span>
                <input
                  className="input"
                  type="text"
                  placeholder="Separados por vírgula"
                  value={blocked}
                  onChange={(e) => setBlocked(e.target.value)}
                  disabled={busy}
                />
                <span className="field-hint">ex.: CASHCAT, SHITCOIN</span>
              </label>

              <label className="confirm-check">
                <input
                  type="checkbox"
                  checked={copyExisting}
                  onChange={(e) => setCopyExisting(e.target.checked)}
                  disabled={busy}
                />
                <span>
                  Copiar posições já abertas do trader
                  <span className="field-hint">
                    {copyExisting
                      ? "espelha o que o trader já tem aberto ao ativar"
                      : "começa do zero — copia só as próximas operações"}
                  </span>
                </span>
              </label>
            </div>
          </div>
        </section>

        {/* ---- Seção C: resumo + confirmação ---- */}
        <section className="modal-section">
          <div className="risk-card">
            {equity === null || equity === undefined ? (
              <>Equity indisponível × {maxLeverage}x</>
            ) : (
              <>
                Equity ${fmtNum(equity, 0)} × {maxLeverage}x ={" "}
                <strong>${fmtNum(riskMax ?? 0, 0)}</strong> máx por posição
              </>
            )}
            {maxLeverage > 5 && <span className="risk-flag"> ⚠ Exposição elevada</span>}
          </div>

          {isMainnet && (
            <label className="switch-field">
              <span className="switch">
                <input
                  type="checkbox"
                  checked={confirmedReal}
                  onChange={(e) => setConfirmedReal(e.target.checked)}
                  disabled={busy}
                />
                <span className="switch-track" />
                <span className="switch-thumb" />
              </span>
              <span>Confirmo operação com dinheiro real</span>
            </label>
          )}
        </section>

        {progress && <div className="modal-progress">{progress}</div>}
        {error && <div className="status-error modal-err">{error}</div>}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancelar
          </button>
          <button
            className={`btn ${
              hasPositions ? "btn-amber" : isMainnet ? "btn-danger" : "btn-go"
            }`}
            onClick={submit}
            disabled={!canActivate}
          >
            {busy
              ? "Processando…"
              : hasPositions
                ? "Fechar e ativar"
                : "Ativar cópia"}
          </button>
        </div>
      </div>
    </div>
  );
}
