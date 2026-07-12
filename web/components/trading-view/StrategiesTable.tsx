import { fmtDateTime, statusChip } from "@/lib/format";
import { TvStrategy } from "@/lib/trading-view/data";

function parseSymbols(snapshot?: string | null): string {
  if (!snapshot) return "—";
  try {
    const cfg = JSON.parse(snapshot);
    const syms: unknown = cfg.symbols_allowed;
    if (Array.isArray(syms) && syms.length) return syms.join(", ");
  } catch {
    /* snapshot malformado ⇒ traço */
  }
  return "—";
}

function parseTimeframes(snapshot?: string | null): string {
  if (!snapshot) return "—";
  try {
    const cfg = JSON.parse(snapshot);
    const tfs: unknown = cfg.timeframes_allowed;
    if (Array.isArray(tfs) && tfs.length) return tfs.join(", ");
  } catch {
    /* idem */
  }
  return "—";
}

export default function StrategiesTable({ strategies }: { strategies: TvStrategy[] }) {
  const rows = (strategies ?? []).filter((s) => s.status !== "archived");
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Estratégias</h2>
        <span className="cardnote">
          estratégias TradingView do ambiente ativo · fonte: view tv_strategies
        </span>
      </div>
      <div className="tablewrap">
        {rows.length === 0 ? (
          <div className="empty">nenhuma estratégia neste ambiente</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Estratégia</th>
                <th>Status</th>
                <th>Ambiente</th>
                <th>Símbolos</th>
                <th>Timeframes</th>
                <th className="num">Versão</th>
                <th>Criada</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.strategy_id}>
                  <td>{s.name ?? s.strategy_id}</td>
                  <td>
                    <span className={`chip ${statusChip[s.status] ?? "dry"}`}>
                      {s.status.toUpperCase()}
                    </span>
                  </td>
                  <td>{s.environment}</td>
                  <td>{parseSymbols(s.config_snapshot)}</td>
                  <td>{parseTimeframes(s.config_snapshot)}</td>
                  <td className="num">v{s.version}</td>
                  <td>{s.created_at ? fmtDateTime(s.created_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
