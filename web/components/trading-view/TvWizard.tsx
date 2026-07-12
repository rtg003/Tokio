"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  activateTvStrategy,
  createTvStrategy,
  CreateResult,
  getHandshake,
  TvEnv,
  TvStrategyForm,
} from "@/lib/trading-view/data";
import TvParamsForm, { previewNotional } from "@/components/trading-view/TvParamsForm";

// Wizard §4 (4 passos) com handshake fim-a-fim: a estratégia nasce 'draft', o
// sinal de teste chega BLOCKED·STRATEGY_DISABLED (risco zero) e só então
// "Concluir" ativa na testnet. Base visual = CopyConfigModal (.modal-*).
const DEFAULTS: TvStrategyForm = {
  strategy_id: "",
  name: "",
  environment: "testnet",
  symbols_allowed: ["BTC"],
  timeframes_allowed: ["4h"],
  allocation_usd: 1000,
  sizing_method: "fixed_fractional",
  risk_per_trade_pct: 0.75,
  min_trade_usd: 12,
  max_position_usd: 200,
  max_leverage: 3,
  max_trades_per_day: 5,
  max_daily_loss_usd: 100,
  cooldown_minutes_after_loss: 30,
  stop_loss_pct: 1.2,
  take_profit_pct: 2.4,
};

const STEPS = ["Identidade", "Risco e execução", "Conexão TradingView", "Handshake"];

function slugify(v: string): string {
  return v.toLowerCase().replace(/[^a-z0-9_]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48);
}

export default function TvWizard({
  equity,
  onClose,
}: {
  equity?: number | null;
  onClose: () => void;
}) {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<TvStrategyForm>(DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [created, setCreated] = useState<CreateResult | null>(null);
  const [received, setReceived] = useState<{ block_code?: string | null } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const patch = (p: Partial<TvStrategyForm>) => setForm((f) => ({ ...f, ...p }));

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPoll(), [stopPoll]);

  // passo 4: começa a pollar o handshake assim que a estratégia foi criada.
  useEffect(() => {
    if (step !== 3 || !created?.strategy_id) return;
    let alive = true;
    const tick = async () => {
      const hs = await getHandshake(created.strategy_id!);
      if (alive && hs.received && hs.signal) {
        setReceived({ block_code: hs.signal.block_code });
        stopPoll();
      }
    };
    void tick();
    pollRef.current = setInterval(tick, 4000);
    return () => {
      alive = false;
      stopPoll();
    };
  }, [step, created, stopPoll]);

  const identityValid =
    /^[a-z0-9_]{3,48}$/.test(form.strategy_id) &&
    form.name.trim().length > 0 &&
    form.symbols_allowed.length > 0;
  const riskValid =
    form.allocation_usd > 0 &&
    (equity == null || form.allocation_usd <= equity) &&
    (form.stop_loss_pct ?? 0) > 0 &&
    previewNotional(form) > 0;

  async function doCreate() {
    setBusy(true);
    setErr(null);
    const res = await createTvStrategy(form);
    setBusy(false);
    if (!res.ok) {
      setErr(res.reason ?? "erro_criacao");
      return;
    }
    setCreated(res);
    setStep(3);
  }

  async function doActivate() {
    if (!created?.strategy_id) return;
    setBusy(true);
    setErr(null);
    const res = await activateTvStrategy(created.strategy_id);
    setBusy(false);
    if (!res.ok) {
      setErr(res.reason ?? "erro_ativacao");
      return;
    }
    router.refresh();
    onClose();
  }

  function copy(kind: string, text: string) {
    void navigator.clipboard?.writeText(text);
    setCopied(kind);
    setTimeout(() => setCopied((c) => (c === kind ? null : c)), 1500);
  }

  return (
    <div className="modal-scrim" onClick={busy ? undefined : onClose}>
      <div className="modal" role="dialog" aria-modal onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="sub">nova estratégia · trading view</div>
            <h2>{STEPS[step]}</h2>
          </div>
          <div className="sub">
            passo {step + 1} de {STEPS.length}
          </div>
        </div>

        {step === 0 && (
          <section className="modal-section">
            <div className="modal-grid">
              <div className="modal-col">
                <div className="field">
                  <label className="field-lab">Nome</label>
                  <input
                    className="input"
                    value={form.name}
                    onChange={(e) => {
                      const name = e.target.value;
                      patch({
                        name,
                        strategy_id: form.strategy_id || `tv_${slugify(name)}`,
                      });
                    }}
                  />
                </div>
                <div className="field">
                  <label className="field-lab">strategy_id (slug)</label>
                  <input
                    className="input"
                    value={form.strategy_id}
                    onChange={(e) => patch({ strategy_id: slugify(e.target.value) })}
                  />
                  <span className="field-hint">a–z, 0–9, _ · 3–48 chars</span>
                </div>
                <div className="field">
                  <label className="field-lab">Ambiente inicial</label>
                  <select
                    className="select"
                    value={form.environment}
                    onChange={(e) => patch({ environment: e.target.value as TvEnv })}
                  >
                    <option value="testnet">TESTNET</option>
                    <option value="mainnet">MAINNET</option>
                  </select>
                </div>
              </div>
              <div className="modal-col">
                <div className="field">
                  <label className="field-lab">Símbolos (vírgula)</label>
                  <input
                    className="input"
                    value={form.symbols_allowed.join(",")}
                    onChange={(e) =>
                      patch({
                        symbols_allowed: e.target.value
                          .split(",")
                          .map((s) => s.trim().toUpperCase())
                          .filter(Boolean),
                      })
                    }
                  />
                </div>
                <div className="field">
                  <label className="field-lab">Timeframes (vírgula)</label>
                  <input
                    className="input"
                    value={form.timeframes_allowed.join(",")}
                    onChange={(e) =>
                      patch({
                        timeframes_allowed: e.target.value
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      })
                    }
                  />
                </div>
              </div>
            </div>
          </section>
        )}

        {step === 1 && (
          <section className="modal-section">
            <TvParamsForm value={form} onChange={patch} equity={equity} />
          </section>
        )}

        {step === 2 && (
          <section className="modal-section">
            <div className="modal-note">
              Grave a estratégia para gerar a URL e o segredo. Nada é digitado à mão: copie o
              webhook e o JSON do alerta no próximo passo (após criar).
            </div>
            <ul className="field-hint">
              <li>Alerta no Pine · Once Per Bar Close para indicador</li>
              <li>Plano TradingView pago + 2FA</li>
            </ul>
          </section>
        )}

        {step === 3 && created && (
          <section className="modal-section">
            <div className="field">
              <label className="field-lab">Webhook URL</label>
              <div className="controls">
                <input className="input" readOnly value={created.webhook_url ?? ""} />
                <button className="btn" onClick={() => copy("url", created.webhook_url ?? "")}>
                  {copied === "url" ? "copiado" : "copiar"}
                </button>
              </div>
            </div>
            <div className="field">
              <label className="field-lab">Mensagem JSON do alerta</label>
              <div className="controls">
                <textarea
                  className="input"
                  readOnly
                  rows={7}
                  value={JSON.stringify(created.alert_json, null, 2)}
                />
                <button
                  className="btn"
                  onClick={() => copy("json", JSON.stringify(created.alert_json, null, 2))}
                >
                  {copied === "json" ? "copiado" : "copiar"}
                </button>
              </div>
              <span className="field-hint">
                strategy_id + secret embutidos · o segredo só aparece agora
              </span>
            </div>
            <div className="modal-progress">
              {received
                ? `Sinal de teste recebido · ${received.block_code ?? "BLOCKED"} — pipeline provado com risco zero.`
                : "Aguardando primeiro sinal… (a estratégia está draft; o teste chega BLOCKED·STRATEGY_DISABLED)"}
            </div>
          </section>
        )}

        {err && <div className="modal-err neg">Erro: {err}</div>}

        <div className="modal-actions">
          {step > 0 && step < 3 && (
            <button className="btn" disabled={busy} onClick={() => setStep((s) => s - 1)}>
              Voltar
            </button>
          )}
          {step === 0 && (
            <button className="btn" disabled={!identityValid} onClick={() => setStep(1)}>
              Continuar
            </button>
          )}
          {step === 1 && (
            <button className="btn" disabled={!riskValid} onClick={() => setStep(2)}>
              Continuar
            </button>
          )}
          {step === 2 && (
            <button className="btn" disabled={busy} onClick={doCreate}>
              {busy ? "criando…" : "Criar e gerar webhook"}
            </button>
          )}
          {step === 3 && (
            <button className="btn" disabled={busy || !received} onClick={doActivate}>
              {busy ? "ativando…" : "Concluir · ativar na testnet"}
            </button>
          )}
          <button className="btn ghost" disabled={busy} onClick={onClose}>
            Fechar
          </button>
        </div>
      </div>
    </div>
  );
}
