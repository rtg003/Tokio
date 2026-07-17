"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { fmtDateTime, fmtNum, fmtSigned, pnlClass, shortAddr } from "@/lib/format";
import { Trader, reclassify } from "@/lib/copy-trade/data";
import StatusSelect from "@/components/copy-trade/StatusSelect";
import ConfidenceBadge, {
  ageSource,
  isComplete,
  isLegacy,
} from "@/components/copy-trade/ConfidenceBadge";

// Métrica LONGITUDINAL FILLS-DERIVED (WR/PF/sim/trades): quando a confiança
// persistida não é COMPLETA — ou é LEGADA (não reavaliada) — o valor não pode
// ser exibido como exato; sinaliza aproximação (UPDATE-0058/0059). NB: PnL 30d/
// TWRR/Max DD vêm do portfolio (não-truncado) e NÃO passam por aqui (Parte A).
function Lon({
  children,
  complete,
  legacy = false,
}: {
  children: React.ReactNode;
  complete: boolean;
  legacy?: boolean;
}) {
  if ((complete && !legacy) || children === "—") return <>{children}</>;
  return (
    <span
      style={{ opacity: 0.7, fontStyle: "italic" }}
      title={
        legacy
          ? "Linha LEGADA (não reavaliada) — o valor longitudinal pode ser amostra truncada. Clique em Reanalisar para reclassificar."
          : "Confiança não é COMPLETA — valor longitudinal APROXIMADO (amostra recente)."
      }
    >
      ~{children}
    </span>
  );
}

// UPDATE-0059 (backfill): botão por linha p/ reclassificar uma wallet legada
// (metrics_confidence NULL) pelo pipeline individual, PRESERVANDO status/config.
// Otimista: em sucesso, refaz o fetch do server component (router.refresh()).
function ReclassifyButton({ address }: { address: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  async function run() {
    if (busy) return;
    setBusy(true);
    setErr(false);
    const res = await reclassify([address]);
    setBusy(false);
    if (res.ok && res.reclassified > 0) router.refresh();
    else setErr(true);
  }
  return (
    <button
      type="button"
      className="btn btn-ghost btn-sm"
      disabled={busy}
      onClick={run}
      title="Reprocessa esta wallet pelo pipeline de discovery e classifica a confiança, preservando status e configuração."
      style={{ marginTop: 4 }}
    >
      {busy ? "reanalisando…" : err ? "falhou ↻" : "Reanalisar"}
    </button>
  );
}

const COLUMN_TIPS: Record<string, string> = {
  "#": "Posição atual na lista filtrada (ordenada por cópia simulada líquida).",
  "SIM NET": "PnL líquido simulado copiando com taxas, slippage e teto 3x. Métrica decisiva do ranking.",
  Trader: "Nome e endereço da carteira analisada. Verde = copiando; amarelo = salvo.",
  Score: "Nota composta 0-100 do funil (informativa). A ordenação usa a cópia simulada líquida.",
  Coorte: "Grupo operacional do trader conforme perfil de holding, risco e comportamento.",
  "PnL 30d": "Lucro ou prejuízo do trader nos últimos 30 dias, em USDC.",
  "Win rate": "Percentual de trades vencedores. Deve ser interpretado junto com PF e DD.",
  PF: "Profit factor: lucro bruto dividido por perda bruta. Acima de 1 indica edge.",
  "Max DD": "Maior drawdown observado. Mede o quanto o trader já afundou no período.",
  Status: "Ação operacional imediata. SALVO observa; TESTNET copia em testnet; MAINNET usa dinheiro real.",
  "Trades 30d": "Número de trades fechados nos últimos 30 dias. Pouca amostra reduz confiança.",
  "Hold méd.": "Tempo médio de posição. Ajuda a filtrar scalpers que não sobrevivem à latência.",
  "SIM EXP": "Expectância líquida por trade na cópia simulada (net / trade fechado).",
  "SIM DD": "Drawdown máximo da curva de equity da cópia simulada.",
  "TWRR 30d": "Retorno ponderado pelo tempo nos últimos 30 dias.",
  "Alav. méd.": "Alavancagem média histórica.",
  "Alav. atual": "Maior alavancagem em posições abertas no scan.",
  "Margem disp.": "Percentual de margem livre. Baixo valor aumenta risco de liquidação.",
  "Metades A": "Resultado simulado nas duas metades da janela; ambas positivas indicam persistência.",
  Equity: "Equity do trader. Traders grandes demais podem não espelhar bem com banca pequena.",
  Ativos: "Principais ativos operados. Ajuda a avaliar concentração e compatibilidade.",
  "Últ. atividade": "Último trade observado. Inatividade reduz copiabilidade.",
  Confiança: "Qualidade da amostra que produziu as métricas: DADOS COMPLETOS (janela coberta), AMOSTRA RECENTE (trader hiperativo/histórico truncado — métricas longitudinais aproximadas) ou INSUFICIENTE.",
  Idade: "Idade real da wallet (não é o span da amostra). Fonte: HyperTracker (earliestActivityAt) ou portfolio.allTime da Hyperliquid.",
  Sizing: "Modo e valor de espelhamento configurados para o trader.",
  "Dist. liq.": "Menor distância até liquidação nas posições abertas.",
  Origem: "Fonte do candidato: discovery, manual, Hermes, Copin ou HyperX.",
  Lógica: "Versão da régua de discovery que produziu as métricas.",
  Janelas: "Quantidade de janelas positivas. Ajuda a separar consistência de sorte pontual.",
};

function parseTopAssets(value: unknown): string[] {
  try {
    return Array.isArray(value) ? value : JSON.parse(String(value ?? "[]"));
  } catch {
    return [];
  }
}

// Acessores de ordenação por rótulo de coluna. `numeric` decide o comparador.
// A coluna "#" (rank) e "Sizing" não são ordenáveis (ausentes deste mapa).
type Accessor = { get: (t: Trader) => number | string | null | undefined; numeric: boolean };
const ACCESSORS: Record<string, Accessor> = {
  "SIM NET": { get: (t) => t.sim_net_pnl_usd, numeric: true },
  Trader: { get: (t) => t.name ?? t.address, numeric: false },
  Score: { get: (t) => t.score, numeric: true },
  Coorte: { get: (t) => t.cohort, numeric: false },
  "Win rate": { get: (t) => t.win_rate, numeric: true },
  PF: { get: (t) => t.profit_factor, numeric: true },
  "TWRR 30d": { get: (t) => t.twrr_30d, numeric: true },
  "PnL 30d": { get: (t) => t.pnl_30d, numeric: true },
  "Max DD": { get: (t) => t.max_drawdown, numeric: true },
  Status: { get: (t) => t.status, numeric: false },
  "Trades 30d": { get: (t) => t.n_trades_30d, numeric: true },
  "Hold méd.": { get: (t) => t.avg_holding_hours, numeric: true },
  "SIM EXP": { get: (t) => t.sim_expectancy_usd, numeric: true },
  "SIM DD": { get: (t) => t.sim_max_dd_pct, numeric: true },
  "Alav. méd.": { get: (t) => t.avg_leverage, numeric: true },
  "Alav. atual": { get: (t) => t.max_current_leverage, numeric: true },
  "Margem disp.": { get: (t) => t.available_margin_pct, numeric: true },
  "Metades A": { get: (t) => t.sim_half_new_net, numeric: true },
  Equity: { get: (t) => t.equity, numeric: true },
  Janelas: { get: (t) => t.windows_positive, numeric: true },
  Ativos: { get: (t) => parseTopAssets(t.top_assets).join(" "), numeric: false },
  "Últ. atividade": { get: (t) => t.last_activity, numeric: false },
  Idade: { get: (t) => t.wallet_age_days, numeric: true },
  "Dist. liq.": { get: (t) => t.liq_distance, numeric: true },
  Origem: { get: (t) => t.origin, numeric: false },
  Lógica: { get: (t) => t.logic_version, numeric: true },
};

type SortDir = "asc" | "desc";

function SortIcon({ dir }: { dir: SortDir | null }) {
  // Ícone flat (chevron via stroke). Estado inativo ocupa o mesmo espaço, sem seta.
  if (dir === null) {
    return <svg className="th-sort-ico th-sort-ico-empty" viewBox="0 0 10 10" aria-hidden />;
  }
  const d = dir === "asc" ? "M2 6.5 L5 3.5 L8 6.5" : "M2 3.5 L5 6.5 L8 3.5";
  return (
    <svg className="th-sort-ico" viewBox="0 0 10 10" aria-hidden>
      <path d={d} fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Th({
  label,
  className,
  sortKey,
  sortDir,
  onSort,
}: {
  label: string;
  className?: string;
  sortKey: string;
  sortDir: SortDir;
  onSort: (label: string) => void;
}) {
  const sortable = label in ACCESSORS;
  const active = sortable && label === sortKey;
  if (!sortable) {
    return (
      <th
        className={`${className ?? ""} th-tip`}
        data-tip={COLUMN_TIPS[label] ?? label}
        title={COLUMN_TIPS[label] ?? label}
      >
        {label}
      </th>
    );
  }
  return (
    <th
      className={`${className ?? ""} th-tip th-sort`}
      data-tip={COLUMN_TIPS[label] ?? label}
      title={COLUMN_TIPS[label] ?? label}
      onClick={() => onSort(label)}
      aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
    >
      {label}
      <SortIcon dir={active ? sortDir : null} />
    </th>
  );
}

function traderNameClass(status: string): string {
  // Só o NOME do trader recebe cor; o endereço fica branco
  if (status === "SALVO") return "trader-watch";
  if (status === "TESTNET" || status === "MAINNET") return "trader-copying";
  return "";
}

// Endereço abreviado clicável: copia o endereço COMPLETO para a área de
// transferência (ação local, não envia dados). Feedback transitório ~1,2s.
function CopyAddr({ address }: { address: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard indisponível — silencioso */
    }
  }
  return (
    <span
      className={`sub addr addr-copy ${copied ? "addr-copied" : ""}`}
      role="button"
      tabIndex={0}
      title="clique para copiar o endereço completo"
      onClick={copy}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          copy();
        }
      }}
    >
      {copied ? "copiado ✓" : shortAddr(address)}
    </span>
  );
}

// Badge "AUTO-PAUSADA": a STRATEGY do trader foi auto-pausada (breach de
// threshold ou circuit breaker global). Enquanto auto_paused o runner descarta
// os fills do alvo (nenhuma cópia sai). Status da strategy ≠ status operacional
// do trader — por isso o badge é separado da coluna Status.
function AutoPausedBadge({ status }: { status?: string | null }) {
  if (status !== "auto_paused") return null;
  return (
    <span
      title="Strategy AUTO-PAUSADA: nenhuma cópia sai enquanto durar. Causas: breach de threshold (min PnL/win rate) ou circuit breaker global (perda diária). Reative no combobox de Status ou aguarde o auto-resume (se configurado)."
      style={{
        marginLeft: 6,
        fontSize: 10,
        fontWeight: 700,
        letterSpacing: 0.3,
        padding: "1px 5px",
        borderRadius: 4,
        color: "#c1121f",
        border: "1px solid #c1121f",
        whiteSpace: "nowrap",
      }}
    >
      AUTO-PAUSADA
    </span>
  );
}

export default function TradersTable({
  traders,
  env,
  expanded,
  toggleHref,
}: {
  traders: Trader[] | null;
  env: "testnet" | "mainnet";
  expanded: boolean;
  toggleHref: string;
}) {
  const rows = useMemo(() => traders ?? [], [traders]);
  const [sortKey, setSortKey] = useState("SIM NET");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function handleSort(label: string) {
    if (label === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(label);
      setSortDir("desc");
    }
  }

  const sortedRows = useMemo(() => {
    const acc = ACCESSORS[sortKey];
    if (!acc) return rows;
    const isNil = (v: unknown) =>
      v === null || v === undefined || (acc.numeric && Number.isNaN(Number(v)));
    return [...rows].sort((a, b) => {
      const va = acc.get(a);
      const vb = acc.get(b);
      const na = isNil(va);
      const nb = isNil(vb);
      // null/undefined sempre por último, em qualquer direção
      if (na && nb) return 0;
      if (na) return 1;
      if (nb) return -1;
      const cmp = acc.numeric
        ? Number(va) - Number(vb)
        : String(va).localeCompare(String(vb), "pt-BR");
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [rows, sortKey, sortDir]);

  return (
    <div className="card">
      <div className="cardhead">
        <h2>Traders</h2>
        <a className="btn btn-ghost btn-sm" href={toggleHref}>
          {expanded ? "Colunas núcleo" : "Modo expandido"}
        </a>
      </div>
      <div className="tablewrap tablewrap-traders">
        {sortedRows.length === 0 ? (
          <div className="empty">
            nenhum trader na tabela — rode o discovery (os candidatos aprovados entram aqui;
            YAMLs não existem mais)
          </div>
        ) : (
          <table className="traders-table">
            <thead>
              <tr>
                <Th label="#" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="SIM NET" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Trader" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Score" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Coorte" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Win rate" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="PF" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="PnL 30d" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Max DD" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Trades 30d" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Hold méd." className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Ativos" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Últ. atividade" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Confiança" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Idade" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Status" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="TWRR 30d" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="SIM EXP" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="SIM DD" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Alav. méd." className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Alav. atual" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Margem disp." className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Metades A" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Equity" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Janelas" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Sizing" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Dist. liq." className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Origem" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <Th label="Lógica" className="num" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((t, i) => {
                const topAssets = parseTopAssets(t.top_assets);
                const score = Math.max(0, Math.min(100, Number(t.score ?? 0)));
                const nameClass = traderNameClass(t.status);
                const complete = isComplete(t.metrics_confidence);
                const legacy = isLegacy(t.metrics_confidence);
                return (
                  <tr key={t.address}>
                    <td className="num">{i + 1}</td>
                    <td className={`num ${pnlClass(t.sim_net_pnl_usd ?? t.sample_sim_net_usd)}`}>
                      {t.sim_net_pnl_usd !== null && t.sim_net_pnl_usd !== undefined ? (
                        <Lon complete={complete} legacy={legacy}>
                          {`$${fmtSigned(t.sim_net_pnl_usd, 2)}`}
                        </Lon>
                      ) : t.sample_sim_net_usd !== null &&
                        t.sample_sim_net_usd !== undefined &&
                        t.sample_sim_window_days ? (
                        <span
                          style={{ opacity: 0.7, fontStyle: "italic" }}
                          title={`Simulação AMOSTRAL sobre o span coberto (${fmtNum(t.sample_sim_window_days, 1)}d) — as sim_* longitudinais ficam nulas quando a confiança não é COMPLETA (amostra, não medição).${t.sample_sim_net_per_day != null ? ` Projeção ~$${fmtSigned(t.sample_sim_net_per_day * 30, 0)}/30d.` : ""}`}
                        >
                          ~${fmtSigned(t.sample_sim_net_usd, 2)} ({fmtNum(t.sample_sim_window_days, 1)}d)
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <span className={`trader-name ${nameClass}`}>{t.name ?? shortAddr(t.address)}</span>
                      <AutoPausedBadge status={t.strategy_status} />
                      <CopyAddr address={t.address} />
                    </td>
                    <td>
                      <span
                        className="scorebar scorebar-compact"
                        title={t.score === null || t.score === undefined ? "sem score" : `${fmtNum(t.score, 1)}`}
                      >
                        <i style={{ width: `${score}%` }} />
                      </span>
                    </td>
                    <td>
                      {t.cohort ? (
                        <a
                          className="cohort-link"
                          href={`https://app.coinmarketman.com/hypertracker/wallet/${t.address}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Abrir ${t.cohort} no Coinmarketman`}
                        >
                          {String(t.cohort).split(" · ")[0]}
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="num">
                      <Lon complete={complete} legacy={legacy}>
                        {t.win_rate === null || t.win_rate === undefined
                          ? "—"
                          : `${fmtNum(t.win_rate * 100, 0)}%`}
                      </Lon>
                    </td>
                    <td className="num">
                      <Lon complete={complete} legacy={legacy}>
                        {t.profit_factor === null || t.profit_factor === undefined
                          ? "—"
                          : fmtNum(t.profit_factor, 2)}
                      </Lon>
                    </td>
                    {/* UPDATE-0059 (Parte A): PnL 30d vem do portfolio (série
                        completa) — medição, não amostra. Nunca marcado com ~. */}
                    <td
                      className={`num ${pnlClass(t.pnl_30d)}`}
                      title="PnL 30d vem do portfolio (série completa) — medição, não amostra."
                    >
                      {t.pnl_30d === null || t.pnl_30d === undefined ? "—" : `$${fmtSigned(t.pnl_30d, 2)}`}
                    </td>
                    {/* UPDATE-0059 (Parte A): Max DD vem do portfolio — medição. */}
                    <td
                      className="num"
                      title="Max DD vem do portfolio (série completa) — medição, não amostra."
                    >
                      {t.max_drawdown === null || t.max_drawdown === undefined
                        ? "—"
                        : `−${fmtNum(t.max_drawdown, 1)}%`}
                    </td>
                    <td className="num">
                      <Lon complete={complete} legacy={legacy}>{t.n_trades_30d ?? "—"}</Lon>
                    </td>
                    <td className="num">
                      {t.avg_holding_hours === null || t.avg_holding_hours === undefined
                        ? "—"
                        : `${fmtNum(t.avg_holding_hours, 1)}h`}
                    </td>
                    <td className="addr">{topAssets.length ? topAssets.join(" ") : "—"}</td>
                    <td className="addr">{fmtDateTime(t.last_activity)}</td>
                    <td>
                      <ConfidenceBadge confidence={t.metrics_confidence} />
                      {legacy && <ReclassifyButton address={t.address} />}
                    </td>
                    <td
                      className="num"
                      title={
                        t.wallet_age_days === null || t.wallet_age_days === undefined
                          ? "idade não disponível"
                          : `idade real da wallet · fonte: ${ageSource({
                              htEarliestMs: t.ht_earliest_activity_ms,
                              walletAgeDays: t.wallet_age_days,
                            })}`
                      }
                    >
                      {t.wallet_age_days === null || t.wallet_age_days === undefined
                        ? "—"
                        : `${fmtNum(t.wallet_age_days, 0)}d`}
                    </td>
                    <td>
                      <StatusSelect
                        address={t.address}
                        status={t.status}
                        env={env}
                        name={t.name ?? undefined}
                        config={{
                          mode: t.mode,
                          value: t.value,
                          max_leverage: t.max_leverage,
                          blocked_assets: t.blocked_assets,
                          thresholds: t.thresholds,
                        }}
                        stats={{
                          equity: t.equity,
                          avg_leverage: t.avg_leverage,
                          max_current_leverage: t.max_current_leverage,
                          sim_max_dd_pct: t.sim_max_dd_pct,
                        }}
                        equity={t.equity}
                      />
                    </td>
                    {/* UPDATE-0059 (Parte A): TWRR 30d vem do portfolio — medição. */}
                    <td
                      className={`num ${pnlClass(t.twrr_30d)}`}
                      title="TWRR 30d vem do portfolio (série completa) — medição, não amostra."
                    >
                      {t.twrr_30d === null || t.twrr_30d === undefined
                        ? "—"
                        : `${fmtNum(t.twrr_30d, 1)}%`}
                    </td>
                    <td className={`num ${pnlClass(t.sim_expectancy_usd)}`}>
                      <Lon complete={complete} legacy={legacy}>
                        {t.sim_expectancy_usd === null || t.sim_expectancy_usd === undefined
                          ? "—"
                          : `$${fmtSigned(t.sim_expectancy_usd, 2)}`}
                      </Lon>
                    </td>
                    <td className="num">
                      <Lon complete={complete} legacy={legacy}>
                        {t.sim_max_dd_pct === null || t.sim_max_dd_pct === undefined
                          ? "—"
                          : `−${fmtNum(t.sim_max_dd_pct, 1)}%`}
                      </Lon>
                    </td>
                    <td className="num">
                      {t.avg_leverage === null || t.avg_leverage === undefined
                        ? "—"
                        : `${fmtNum(t.avg_leverage, 1)}x`}
                    </td>
                    <td className="num">
                      {t.max_current_leverage === null || t.max_current_leverage === undefined
                        ? "—"
                        : `${fmtNum(t.max_current_leverage, 1)}x`}
                    </td>
                    <td className="num">
                      {t.available_margin_pct === null || t.available_margin_pct === undefined
                        ? "—"
                        : `${fmtNum(t.available_margin_pct, 0)}%`}
                    </td>
                    <td className="num">
                      <Lon complete={complete} legacy={legacy}>
                        {t.sim_half_new_net === null || t.sim_half_new_net === undefined
                          ? "—"
                          : `${t.sim_half_old_net === null || t.sim_half_old_net === undefined ? "n/d" : `$${fmtSigned(t.sim_half_old_net, 0)}`} / $${fmtSigned(t.sim_half_new_net, 0)}`}
                      </Lon>
                    </td>
                    <td className="num">
                      {t.equity === null || t.equity === undefined ? "—" : fmtNum(t.equity, 0)}
                    </td>
                    <td>{t.windows_positive ?? "—"}</td>
                    <td>
                      {t.mode === "percent" ? `${t.value}× prop.` : `${fmtNum(t.value, 0)} USDC fixo`}
                    </td>
                    <td className="num">
                      {t.liq_distance === null || t.liq_distance === undefined
                        ? "—"
                        : `${fmtNum(t.liq_distance, 1)}%`}
                    </td>
                    <td>{t.origin}</td>
                    <td className="num">v{t.logic_version}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
