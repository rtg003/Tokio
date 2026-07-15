import { fmtDateTime, fmtNotional, fmtNum, fmtSigned, pnlClass, statusChip } from "@/lib/format";
import { Fill, Order, Trader } from "@/lib/copy-trade/data";
import CancelOrderButton from "./CancelOrderButton";

type Row = {
  kind: "ORDEM" | "TRADE";
  key: string;
  time: string;
  symbol: string;
  side: string;
  size: number;
  price?: number | null;
  leverage?: number | null;
  fee?: number | null;
  pnl?: number | null;
  status?: string | null;
  reject?: string | null;
  latency?: number | null;
  cloid: string;
  strategyId?: string | null;
  master?: string | null;
  network?: string | null;
};

const ts = (v: string | undefined | null) => (v ? new Date(v).getTime() : 0);
// Rótulo curto de carteira: 6 primeiros caracteres (shortAddr corta 6+4).
const short6 = (a: string | null | undefined) => (a ? a.slice(0, 6) : null);
// Margem = notional / alavancagem. "—" quando falta preço ou alav. (ordens
// gravadas antes da migration 0022 ficam com leverage NULL).
const marginOf = (r: Row): number | null => {
  if (!r.price || !r.leverage || r.leverage <= 0) return null;
  return (r.price * r.size) / r.leverage;
};

export default function TradesOrdersTable({
  orders,
  fills,
  traders,
}: {
  orders: Order[] | null;
  fills: Fill[] | null;
  traders: Trader[] | null;
}) {
  const traderMap = new Map<string, Trader>();
  for (const t of traders ?? []) {
    if (t.strategy_id) traderMap.set(t.strategy_id, t);
  }
  // Trader copiado (via strategy_id); fallback: carteira executora (master).
  function traderLabel(row: Row): string {
    const t = row.strategyId ? traderMap.get(row.strategyId) : undefined;
    return t?.name ?? short6(t?.address) ?? short6(row.master) ?? "—";
  }

  const openOrders: Row[] = (orders ?? [])
    .filter((o) => o.status !== "filled" && o.status !== "closed" && o.status !== "cancelled")
    .sort((a, b) => ts(b.created_at) - ts(a.created_at))
    .map((o) => ({
      kind: "ORDEM",
      key: `o-${o.cloid}`,
      time: o.created_at,
      symbol: o.symbol,
      side: o.side,
      size: o.size,
      price: o.price,
      leverage: o.leverage,
      status: o.status,
      reject: o.reject_reason,
      latency: o.latency_ms,
      cloid: o.cloid,
      strategyId: o.strategy_id,
      master: o.master_address,
      network: o.network,
    }));

  const tradeRows: Row[] = (fills ?? [])
    .slice()
    .sort((a, b) => ts(b.ts) - ts(a.ts))
    .map((f, i) => ({
      kind: "TRADE",
      key: `f-${f.cloid}-${i}`,
      time: f.ts,
      symbol: f.symbol,
      side: f.side,
      size: f.size,
      price: f.price,
      leverage: f.leverage,
      fee: f.fee,
      pnl: f.realized_pnl,
      cloid: f.cloid,
      strategyId: f.strategy_id,
      master: f.master_address,
    }));

  // ordens em aberto no topo, trades executados abaixo
  const rows = [...openOrders, ...tradeRows];

  return (
    <div className="card">
      <div className="cardhead">
        <h2>Trades e Ordens em Aberto</h2>
        <span className="cardnote">
          ordens em aberto (topo) + fills executados · atribuição por cloid · fonte: tabelas orders/fills
        </span>
      </div>
      <div className="tablewrap tablewrap-fills">
        {rows.length === 0 ? (
          <div className="empty">nenhuma ordem em aberto nem trade no período</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Trader</th>
                <th>Tipo</th>
                <th>Hora</th>
                <th>Par</th>
                <th>Lado</th>
                <th className="num">Qtd</th>
                <th className="num">Preço</th>
                <th className="num">Margem</th>
                <th className="num">Alav.</th>
                <th className="num">Valor</th>
                <th className="num">Fee</th>
                <th className="num">PnL líquido</th>
                <th>Status</th>
                <th className="num">Latência</th>
                <th className="num" aria-label="Cancelar"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td className="addr">{traderLabel(r)}</td>
                  <td>
                    <span className={`chip ${r.kind === "ORDEM" ? "ack" : "filled"}`}>{r.kind}</span>
                  </td>
                  <td>{fmtDateTime(r.time)}</td>
                  <td>{r.symbol}</td>
                  <td>
                    <span className={`side ${r.side === "buy" ? "long" : "short"}`}>
                      {r.side === "buy" ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="num">{fmtNum(r.size, 4)}</td>
                  <td className="num">{r.price ? `$${fmtNum(r.price)}` : "MKT"}</td>
                  <td className="num">
                    {marginOf(r) === null ? "—" : `$${fmtNum(marginOf(r) as number, 2)}`}
                  </td>
                  <td className="num">{r.leverage ? `${fmtNum(r.leverage, 1)}×` : "—"}</td>
                  <td className="num">${fmtNotional(r.size, r.price)}</td>
                  <td className="num">{r.fee != null ? `$${fmtNum(r.fee, 4)}` : "—"}</td>
                  <td className={`num ${r.kind === "TRADE" ? pnlClass(r.pnl) : ""}`}>
                    {r.pnl === null || r.pnl === undefined ? "—" : `$${fmtSigned(r.pnl)}`}
                  </td>
                  <td>
                    {r.kind === "ORDEM" ? (
                      <>
                        <span className={`chip ${statusChip[r.status ?? ""] ?? "dry"}`}>
                          {(r.status ?? "").toUpperCase()}
                        </span>
                        {r.reject && <span className="sub">{r.reject}</span>}
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num">
                    {r.latency ? `${Math.round(r.latency)}ms` : "—"}
                  </td>
                  <td className="num pos-close-cell">
                    {r.kind === "ORDEM" ? (
                      <CancelOrderButton
                        strategyId={r.strategyId}
                        symbol={r.symbol}
                        cloid={r.cloid}
                        env={r.network}
                      />
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
