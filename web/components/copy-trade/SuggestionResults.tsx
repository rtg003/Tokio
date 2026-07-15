"use client";

import { useMemo, useState } from "react";
import {
  saveSuggestions,
  type AnalyzeResponse,
  type SaveResponse,
  type SuggestionReport,
} from "@/lib/copy-trade/data";

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

// Wallet inválida não é salvável; qualquer outra pode ser força-salva.
function isInvalid(r: SuggestionReport): boolean {
  return r.reject_reasons.includes("endereco_invalido");
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

  async function onSave() {
    const addrs = [...selected];
    if (busy || addrs.length === 0) return;
    const forcing = rows.filter(
      (r) => selected.has(r.address) && !r.passes_filters,
    ).length;
    const msg =
      `Salvar ${addrs.length} wallet(s) como SUGERIDO (origin="usuário")?` +
      (forcing > 0
        ? `\n\n${forcing} delas REPROVAM filtros automáticos e serão salvas ` +
          `mesmo assim (força-salvar).`
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
              <th>Wallet</th>
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
              return (
                <tr key={r.address}>
                  <td style={{ width: "1%" }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={invalid || busy}
                      onChange={() => toggle(r.address)}
                      aria-label={`Selecionar ${r.address}`}
                    />
                  </td>
                  <td title={r.address}>
                    {r.name ? `${r.name} · ` : ""}
                    {short(r.address)}
                  </td>
                  <td className="num">{num(r.score, 1)}</td>
                  <td>{r.cohort ?? "—"}</td>
                  <td className="num">{usd(r.metrics.sim_stage4_net_usd)}</td>
                  <td className="num">{num(r.metrics.twrr_30d, 1, "%")}</td>
                  <td className="num">{num(r.metrics.profit_factor, 2)}</td>
                  <td className="num">{num(r.metrics.max_drawdown, 1, "%")}</td>
                  <td className="num">{r.metrics.n_trades_30d ?? "—"}</td>
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
          salvas mesmo assim.
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
