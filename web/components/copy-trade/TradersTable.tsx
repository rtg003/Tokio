import { fmtDateTime, fmtNum, fmtSigned, pnlClass, shortAddr } from "@/lib/format";
import { Trader } from "@/lib/copy-trade/data";
import StatusSelect from "@/components/copy-trade/StatusSelect";

const COLUMN_TIPS: Record<string, string> = {
  "#": "Posição atual na lista filtrada.",
  Trader: "Nome e endereço da carteira analisada. Verde = copiando; amarelo = salvo.",
  Score: "Nota composta 0-100 do funil. Use como leitura rápida; a cópia simulada pesa mais.",
  Coorte: "Grupo operacional do trader conforme perfil de holding, risco e comportamento.",
  "PnL 30d": "Lucro ou prejuízo do trader nos últimos 30 dias, em USDC.",
  "Win rate": "Percentual de trades vencedores. Deve ser interpretado junto com PF e DD.",
  PF: "Profit factor: lucro bruto dividido por perda bruta. Acima de 1 indica edge.",
  "Max DD": "Maior drawdown observado. Mede o quanto o trader já afundou no período.",
  Status: "Ação operacional imediata. SALVO observa; TESTNET copia em testnet; MAINNET usa dinheiro real.",
  "Trades 30d": "Número de trades fechados nos últimos 30 dias. Pouca amostra reduz confiança.",
  "Hold méd.": "Tempo médio de posição. Ajuda a filtrar scalpers que não sobrevivem à latência.",
  "TWRR 30d": "Retorno ponderado pelo tempo nos últimos 30 dias.",
  "Alav. méd.": "Alavancagem média histórica.",
  "Alav. atual": "Maior alavancagem em posições abertas no scan.",
  "Margem disp.": "Percentual de margem livre. Baixo valor aumenta risco de liquidação.",
  "Cópia sim.": "PnL líquido simulado copiando com $1k, taxas, slippage e teto 3x.",
  "Cobertura": "Dias cobertos pelo histórico analisado. Histórico curto é menos confiável.",
  "Metades A": "Resultado simulado nas duas metades da janela; ambas positivas indicam persistência.",
  Equity: "Equity do trader. Traders grandes demais podem não espelhar bem com banca pequena.",
  Ativos: "Principais ativos operados. Ajuda a avaliar concentração e compatibilidade.",
  "Últ. atividade": "Último trade observado. Inatividade reduz copiabilidade.",
  Sizing: "Modo e valor de espelhamento configurados para o trader.",
  "Dist. liq.": "Menor distância até liquidação nas posições abertas.",
  Origem: "Fonte do candidato: discovery, manual, Hermes, Copin ou HyperX.",
  Lógica: "Versão da régua de discovery que produziu as métricas.",
  Janelas: "Quantidade de janelas positivas. Ajuda a separar consistência de sorte pontual.",
};

function Th({
  label,
  className,
}: {
  label: string;
  className?: string;
}) {
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

function traderNameClass(status: string): string {
  // Só o NOME do trader recebe cor; o endereço fica branco
  if (status === "SALVO") return "trader-watch";
  if (status === "TESTNET" || status === "MAINNET") return "trader-copying";
  return "";
}

function parseTopAssets(value: unknown): string[] {
  try {
    return Array.isArray(value) ? value : JSON.parse(String(value ?? "[]"));
  } catch {
    return [];
  }
}

export default function TradersTable({
  traders,
  expanded,
  toggleHref,
}: {
  traders: Trader[] | null;
  expanded: boolean;
  toggleHref: string;
}) {
  const rows = traders ?? [];
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Traders</h2>
        <a className="btn btn-ghost btn-sm" href={toggleHref}>
          {expanded ? "Colunas núcleo" : "Modo expandido"}
        </a>
      </div>
      <div className="tablewrap tablewrap-traders">
        {rows.length === 0 ? (
          <div className="empty">
            nenhum trader na tabela — rode o discovery (os candidatos aprovados entram aqui;
            YAMLs não existem mais)
          </div>
        ) : (
          <table className="traders-table">
            <thead>
              <tr>
                <Th label="#" className="num" />
                <Th label="Trader" />
                <Th label="Score" />
                <Th label="Coorte" />
                <Th label="PnL 30d" className="num" />
                <Th label="Win rate" className="num" />
                <Th label="PF" className="num" />
                <Th label="Max DD" className="num" />
                <Th label="Status" />
                <Th label="Trades 30d" className="num" />
                <Th label="Hold méd." className="num" />
                <Th label="TWRR 30d" className="num" />
                <Th label="Alav. méd." className="num" />
                <Th label="Alav. atual" className="num" />
                <Th label="Margem disp." className="num" />
                <Th label="Cópia sim." className="num" />
                <Th label="Cobertura" className="num" />
                <Th label="Metades A" className="num" />
                <Th label="Equity" className="num" />
                <Th label="Janelas" />
                <Th label="Ativos" />
                <Th label="Últ. atividade" />
                <Th label="Sizing" />
                <Th label="Dist. liq." className="num" />
                <Th label="Origem" />
                <Th label="Lógica" className="num" />
              </tr>
            </thead>
            <tbody>
              {rows.map((t, i) => {
                const topAssets = parseTopAssets(t.top_assets);
                const score = Math.max(0, Math.min(100, Number(t.score ?? 0)));
                const nameClass = traderNameClass(t.status);
                return (
                  <tr key={t.address}>
                    <td className="num">{i + 1}</td>
                    <td>
                      <span className={`trader-name ${nameClass}`}>{t.name ?? shortAddr(t.address)}</span>
                      <span className="sub addr">{shortAddr(t.address)}</span>
                    </td>
                    <td>
                      <span
                        className="scorebar scorebar-compact"
                        title={t.score === null || t.score === undefined ? "sem score" : `${fmtNum(t.score, 1)}`}
                      >
                        <i style={{ width: `${score}%` }} />
                      </span>
                    </td>
                    <td>{t.cohort ?? "—"}</td>
                    <td className={`num ${pnlClass(t.pnl_30d)}`}>
                      {t.pnl_30d === null || t.pnl_30d === undefined ? "—" : `$${fmtSigned(t.pnl_30d, 2)}`}
                    </td>
                    <td className="num">
                      {t.win_rate === null || t.win_rate === undefined
                        ? "—"
                        : `${fmtNum(t.win_rate * 100, 0)}%`}
                    </td>
                    <td className="num">
                      {t.profit_factor === null || t.profit_factor === undefined
                        ? "—"
                        : fmtNum(t.profit_factor, 2)}
                    </td>
                    <td className="num">
                      {t.max_drawdown === null || t.max_drawdown === undefined
                        ? "—"
                        : `−${fmtNum(t.max_drawdown, 1)}%`}
                    </td>
                    <td>
                      <StatusSelect address={t.address} status={t.status} />
                    </td>
                    <td className="num">{t.n_trades_30d ?? "—"}</td>
                    <td className="num">
                      {t.avg_holding_hours === null || t.avg_holding_hours === undefined
                        ? "—"
                        : `${fmtNum(t.avg_holding_hours, 1)}h`}
                    </td>
                    <td className={`num ${pnlClass(t.twrr_30d)}`}>
                      {t.twrr_30d === null || t.twrr_30d === undefined
                        ? "—"
                        : `${fmtNum(t.twrr_30d, 1)}%`}
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
                    <td className={`num ${pnlClass(t.sim_net_pnl_usd)}`}>
                      {t.sim_net_pnl_usd === null || t.sim_net_pnl_usd === undefined
                        ? "—"
                        : `$${fmtSigned(t.sim_net_pnl_usd, 2)}`}
                    </td>
                    <td className="num">
                      {t.coverage_days === null || t.coverage_days === undefined
                        ? "—"
                        : `${fmtNum(t.coverage_days, 0)}d`}
                    </td>
                    <td className="num">
                      {t.sim_half_new_net === null || t.sim_half_new_net === undefined
                        ? "—"
                        : `${t.sim_half_old_net === null || t.sim_half_old_net === undefined ? "n/d" : `$${fmtSigned(t.sim_half_old_net, 0)}`} / $${fmtSigned(t.sim_half_new_net, 0)}`}
                    </td>
                    <td className="num">
                      {t.equity === null || t.equity === undefined ? "—" : fmtNum(t.equity, 0)}
                    </td>
                    <td>{t.windows_positive ?? "—"}</td>
                    <td className="addr">{topAssets.length ? topAssets.join(" ") : "—"}</td>
                    <td className="addr">{fmtDateTime(t.last_activity)}</td>
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
