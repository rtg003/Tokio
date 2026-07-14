import { fmtDateTime, fmtNotional, fmtNum, fmtSigned, pnlClass, shortAddr, statusChip } from "@/lib/format";
import { Fill, Order } from "@/lib/trading-view/data";

type Row = {
  kind: "ORDEM" | "TRADE";
  key: string;
  time: string;
  symbol: string;
  side: string;
  size: number;
  price?: number | null;
  fee?: number | null;
  pnl?: number | null;
  status?: string | null;
  reject?: string | null;
  latency?: number | null;
  cloid: string;
  master?: string | null;
};

const ts = (v: string | undefined | null) => (v ? new Date(v).getTime() : 0);
// Rótulo curto de carteira: 6 primeiros caracteres (shortAddr corta 6+4).
const short6 = (a: string | null | undefined) => (a ? a.slice(0, 6) : "—");

export default function TradesOrdersTable({
  orders,
  fills,
}: {
  orders: Order[];
  fills: Fill[];
}) {
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
      status: o.status,
      reject: o.reject_reason,
      latency: o.latency_ms,
      cloid: o.cloid,
      master: o.master_address,
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
      fee: f.fee,
      pnl: f.realized_pnl,
      cloid: f.cloid,
      master: f.master_address,
    }));

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
                <th className="num">Valor</th>
                <th className="num">Fee</th>
                <th className="num">PnL líquido</th>
                <th>Status</th>
                <th className="num">Latência</th>
                <th>cloid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td className="addr">{short6(r.master)}</td>
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
                  <td className="num">{r.latency ? `${Math.round(r.latency)}ms` : "—"}</td>
                  <td className="addr">{shortAddr(r.cloid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
