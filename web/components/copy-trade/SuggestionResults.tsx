"use client";

import { Fragment, useMemo, useState } from "react";
import {
  saveSuggestions,
  type AnalyzeResponse,
  type SaveResponse,
  type SuggestionReport,
} from "@/lib/copy-trade/data";
import ConfidenceBadge, {
  TRUNCATION_TIP,
  ageSource,
  isComplete,
} from "@/components/copy-trade/ConfidenceBadge";

function short(addr: string): string {
  return addr.length > 12 ? `${addr.slice(0, 6)}…${addr.slice(-4)}` : addr;
}

function num(v: number | null | undefined, digits = 1, suffix = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

function usd(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `US$ ${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

// Célula de métrica LONGITUDINAL (30/60d): quando a confiança não é COMPLETA, o
// valor não pode ser exibido como exato — prefixa "~" e sinaliza aproximação.
function Longitudinal({
  text,
  complete,
}: {
  text: string;
  complete: boolean;
}) {
  if (text === "—" || complete) return <>{text}</>;
  return (
    <span
      style={{ opacity: 0.7, fontStyle: "italic" }}
      title="Confiança não é COMPLETA — valor longitudinal APROXIMADO (amostra recente)."
    >
      ~{text}
    </span>
  );
}

// UPDATE-0059: célula de SIM NET honesta. Quando a confiança é COMPLETA usamos a
// sim LONGITUDINAL (sim_stage4_net_usd). Quando não é, a longitudinal vem nula
// (inv. 0056) — então mostramos a simulação AMOSTRAL "~$X (Yd)" em vez de "—".
function SimNetCell({ r, complete }: { r: SuggestionReport; complete: boolean }) {
  if (complete && r.metrics.sim_stage4_net_usd != null) {
    return <>{usd(r.metrics.sim_stage4_net_usd)}</>;
  }
  const s = r.sample_metrics;
  if (s && s.sim_net_usd != null && s.window_days != null) {
    const proj =
      s.net_per_day != null
        ? ` · projeção ~${usd(s.net_per_day * 30)}/30d`
        : "";
    return (
      <span
        style={{ opacity: 0.7, fontStyle: "italic" }}
        title={
          `Simulação sobre a amostra REALMENTE coberta (${s.window_days.toFixed(1)}d), ` +
          `não a janela de 30/60d — as sim_* longitudinais ficam nulas quando a ` +
          `confiança não é COMPLETA (amostra, não medição).${proj}`
        }
      >
        ~{usd(s.sim_net_usd)} ({num(s.window_days, 1, "d")})
      </span>
    );
  }
  return <>—</>;
}

// Wallet inválida não é salvável; qualquer outra pode ser força-salva.
function isInvalid(r: SuggestionReport): boolean {
  return r.reject_reasons.includes("endereco_invalido");
}

function DetailPanel({ r }: { r: SuggestionReport }) {
  const ht = r.hypertracker;
  const source = ageSource({
    htEarliestMs: ht?.earliest_activity_ms,
    walletAgeDays: r.wallet_age_days,
  });
  const truncated = r.fills_complete === false;
  return (
    <div className="sug-detail">
      <div className="sug-detail-grid">
        {/* Idade × amostra (conceitos SEPARADOS — Fase 1) */}
        <section>
          <h4>Idade & amostra</h4>
          <dl>
            <div>
              <dt>Idade real da wallet</dt>
              <dd>{num(r.wallet_age_days, 0, " dias")}</dd>
            </div>
            <div>
              <dt>Fonte da idade</dt>
              <dd>{source}</dd>
            </div>
            <div>
              <dt title={TRUNCATION_TIP}>
                Amostra de fills {truncated ? "⚠️" : ""}
              </dt>
              <dd title={truncated ? TRUNCATION_TIP : undefined}>
                {num(r.fills_sample_days, 1, " dias")} ·{" "}
                {r.fills_sample_count ?? "—"} fills
                {truncated ? " (truncada)" : ""}
              </dd>
            </div>
            <div>
              <dt>Confiança</dt>
              <dd>
                <ConfidenceBadge confidence={r.metrics_confidence} />
              </dd>
            </div>
          </dl>
        </section>

        {/* HyperTracker (agregado) × Hyperliquid (trading) — nunca se misturam */}
        <section>
          <h4>HyperTracker (agregado)</h4>
          <dl>
            <div>
              <dt>Equity total</dt>
              <dd>{usd(ht?.total_equity)}</dd>
            </div>
            <div>
              <dt>Perp PnL</dt>
              <dd>{usd(ht?.perp_pnl)}</dd>
            </div>
            <div>
              <dt>Exposição</dt>
              <dd>{num(ht?.exposure_ratio, 2, "x")}</dd>
            </div>
          </dl>
        </section>
        <section>
          <h4>Hyperliquid (trading)</h4>
          <dl>
            <div>
              <dt>Equity</dt>
              <dd>{num(r.metrics.equity, 0)}</dd>
            </div>
            <div>
              <dt>PnL 30d</dt>
              <dd>{usd(r.metrics.pnl_30d)}</dd>
            </div>
            <div>
              <dt>Win rate 30d</dt>
              <dd>
                <Longitudinal
                  text={num(
                    r.metrics.win_rate_30d != null
                      ? r.metrics.win_rate_30d * 100
                      : null,
                    0,
                    "%",
                  )}
                  complete={isComplete(r.metrics_confidence)}
                />
              </dd>
            </div>
          </dl>
        </section>
      </div>

      {/* UPDATE-0059: simulação AMOSTRAL — só faz sentido quando a longitudinal
          está indeterminada (confiança ≠ complete). É medição sobre o span
          coberto + projeção /30d EXPLICITAMENTE informativa (nunca é filtro). */}
      {!isComplete(r.metrics_confidence) &&
        r.sample_metrics &&
        r.sample_metrics.window_days != null && (
          <div className="sug-detail-block">
            <h4>
              Simulação amostral{" "}
              <span className="empty" style={{ padding: 0 }}>
                (sobre o span coberto — projeção, não medição de 30/60d)
              </span>
            </h4>
            <dl className="sug-detail-sample">
              <div>
                <dt>SIM net (amostra)</dt>
                <dd>
                  {usd(r.sample_metrics.sim_net_usd)} em{" "}
                  {num(r.sample_metrics.window_days, 1, "d")}
                </dd>
              </div>
              <div>
                <dt>Projeção /30d (informativa)</dt>
                <dd>
                  {r.sample_metrics.net_per_day != null
                    ? `~${usd(r.sample_metrics.net_per_day * 30)}/30d se o ritmo se mantiver`
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Expectância (amostra)</dt>
                <dd>{usd(r.sample_metrics.expectancy_usd)}</dd>
              </div>
              <div>
                <dt>Max DD (amostra)</dt>
                <dd>{num(r.sample_metrics.max_dd_pct, 1, "%")}</dd>
              </div>
            </dl>
          </div>
        )}

      {/* Filtros INDETERMINADOS ≠ reprovações (Fase 1) */}
      {r.indeterminate_reasons && r.indeterminate_reasons.length > 0 && (
        <div className="sug-detail-block">
          <h4>
            Filtros indeterminados{" "}
            <span className="empty" style={{ padding: 0 }}>
              (não reprovam — a amostra não cobre a janela para julgar)
            </span>
          </h4>
          <ul>
            {r.indeterminate_reasons.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      {r.reject_reasons.length > 0 && (
        <div className="sug-detail-block">
          <h4>Reprovações de filtro</h4>
          <ul>
            {r.reject_reasons.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      {r.metrics_warnings && r.metrics_warnings.length > 0 && (
        <div className="sug-detail-block">
          <h4>Avisos</h4>
          <ul>
            {r.metrics_warnings.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function SuggestionResults({
  result,
}: {
  result: AnalyzeResponse;
}) {
  const rows = result.results;
  const [selected, setSelected] = useState<Set<string>>(
    () =>
      new Set(
        rows.filter((r) => r.passes_filters && !isInvalid(r)).map((r) => r.address),
      ),
  );
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState(false);
  const [saveResult, setSaveResult] = useState<SaveResponse | null>(null);

  const selectableCount = useMemo(
    () => rows.filter((r) => !isInvalid(r)).length,
    [rows],
  );

  function toggle(addr: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(addr)) next.delete(addr);
      else next.add(addr);
      return next;
    });
  }

  function toggleExpand(addr: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(addr)) next.delete(addr);
      else next.add(addr);
      return next;
    });
  }

  async function onSave() {
    const addrs = [...selected];
    if (busy || addrs.length === 0) return;
    const forcing = rows.filter(
      (r) => selected.has(r.address) && !r.passes_filters,
    ).length;
    const sampled = rows.filter(
      (r) => selected.has(r.address) && !isComplete(r.metrics_confidence),
    ).length;
    const msg =
      `Salvar ${addrs.length} wallet(s) como SUGERIDO (origin="usuário")?` +
      (forcing > 0
        ? `\n\n${forcing} delas REPROVAM filtros automáticos e serão salvas ` +
          `mesmo assim (força-salvar).`
        : "") +
      (sampled > 0
        ? `\n\n${sampled} delas têm confiança < COMPLETA (amostra recente/` +
          `insuficiente) — as métricas longitudinais são aproximadas. A guarda ` +
          `anti-sobrescrita preserva métricas COMPLETAS já persistidas.`
        : "");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setSaveResult(null);
    const res = await saveSuggestions(addrs);
    setBusy(false);
    setSaveResult(res);
  }

  return (
    <div className="card">
      <div className="cardhead">
        <h2>Resultado da análise</h2>
        <span className="empty" style={{ padding: 0 }}>
          {result.summary.passa_filtros} passam · {result.summary.reprova_filtros}{" "}
          reprovam · {result.summary.total} total
        </span>
      </div>

      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th style={{ width: "1%" }} aria-label="Selecionar" />
              <th style={{ width: "1%" }} aria-label="Detalhes" />
              <th>Wallet</th>
              <th>Confiança</th>
              <th className="num" title="Idade real da wallet (não é o span da amostra).">
                Idade
              </th>
              <th className="num">Score</th>
              <th>Coorte</th>
              <th className="num">Sim net</th>
              <th className="num">TWRR 30d</th>
              <th className="num">PF</th>
              <th className="num">Max DD</th>
              <th className="num">Trades 30d</th>
              <th>Situação</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const invalid = isInvalid(r);
              const checked = selected.has(r.address);
              const open = expanded.has(r.address);
              const complete = isComplete(r.metrics_confidence);
              return (
                <Fragment key={r.address}>
                  <tr>
                    <td style={{ width: "1%" }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={invalid || busy}
                        onChange={() => toggle(r.address)}
                        aria-label={`Selecionar ${r.address}`}
                      />
                    </td>
                    <td style={{ width: "1%" }}>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => toggleExpand(r.address)}
                        aria-expanded={open}
                        aria-label={`Detalhes de ${r.address}`}
                        title="Detalhes: idade, amostra, HyperTracker × Hyperliquid, indeterminados"
                      >
                        {open ? "▾" : "▸"}
                      </button>
                    </td>
                    <td title={r.address}>
                      {r.name ? `${r.name} · ` : ""}
                      {short(r.address)}
                    </td>
                    <td>
                      <ConfidenceBadge confidence={r.metrics_confidence} />
                    </td>
                    <td
                      className="num"
                      title={`Idade real da wallet · fonte: ${ageSource({
                        htEarliestMs: r.hypertracker?.earliest_activity_ms,
                        walletAgeDays: r.wallet_age_days,
                      })}`}
                    >
                      {num(r.wallet_age_days, 0, "d")}
                    </td>
                    <td className="num">{num(r.score, 1)}</td>
                    <td>{r.cohort ?? "—"}</td>
                    <td className="num">
                      <SimNetCell r={r} complete={complete} />
                    </td>
                    <td
                      className="num"
                      title="TWRR 30d vem do portfolio (série completa) — medição, não amostra."
                    >
                      {num(r.metrics.twrr_30d, 1, "%")}
                    </td>
                    <td className="num">
                      <Longitudinal
                        text={num(r.metrics.profit_factor, 2)}
                        complete={complete}
                      />
                    </td>
                    <td
                      className="num"
                      title="Max DD vem do portfolio (série completa) — medição, não amostra."
                    >
                      {num(r.metrics.max_drawdown, 1, "%")}
                    </td>
                    <td className="num">
                      <Longitudinal
                        text={r.metrics.n_trades_30d != null ? String(r.metrics.n_trades_30d) : "—"}
                        complete={complete}
                      />
                    </td>
                    <td>
                      {invalid ? (
                        <span className="chip rej" title="Endereço inválido">
                          inválido
                        </span>
                      ) : r.passes_filters ? (
                        <span className="chip filled">passa filtros</span>
                      ) : (
                        <span
                          className="chip ack"
                          title={r.reject_reasons.join("\n")}
                        >
                          reprova ({r.reject_reasons.length})
                        </span>
                      )}
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={13} style={{ padding: 0 }}>
                        <DetailPanel r={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 14,
        padding: "14px 18px", flexWrap: "wrap" }}>
        <button
          type="button"
          className="btn btn-amber"
          disabled={busy || selected.size === 0}
          onClick={onSave}
        >
          {busy ? "Salvando…" : `Salvar selecionadas (${selected.size})`}
        </button>
        <span className="empty" style={{ padding: 0 }}>
          {selectableCount} salvável(is). Wallets que reprovam filtros podem ser
          salvas mesmo assim. Ao salvar, a confiança é persistida e métricas
          COMPLETAS já gravadas são preservadas.
        </span>
      </div>

      {saveResult && (
        <div style={{ padding: "0 18px 16px" }}>
          {saveResult.ok ? (
            <p className="empty pos" style={{ padding: 0 }}>
              {saveResult.summary.salvos} salva(s) como SUGERIDO
              {saveResult.summary.ignorados > 0 &&
                ` · ${saveResult.summary.ignorados} ignorada(s)`}
              . Elas já aparecem na tabela de traders do Copy Trade.
            </p>
          ) : (
            <p className="empty neg" style={{ padding: 0 }}>
              Falha ao salvar: {saveResult.reason ?? "erro"}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
