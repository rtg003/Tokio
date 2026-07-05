import { fmtNum, fmtTime, shortAddr, statusChip } from "@/lib/format";
import { Order } from "@/lib/copy-trade/data";

export default function OrdersTable({ orders }: { orders: Order[] | null }) {
  const rows = orders ?? [];
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Ordens</h2>
        <span className="cardnote">ciclo completo · atribuição por cloid · fonte: tabela orders</span>
      </div>
      <div className="tablewrap tablewrap-orders">
        {rows.length === 0 ? (
          <div className="empty">nenhuma ordem de copy trade no período</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Hora</th>
                <th>Estratégia</th>
                <th>Par</th>
                <th>Lado</th>
                <th>Tipo</th>
                <th className="num">Qtd</th>
                <th className="num">Preço</th>
                <th>Status</th>
                <th className="num">Latência</th>
                <th>cloid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.cloid}>
                  <td>{fmtTime(o.created_at)}</td>
                  <td>{o.strategy_id}</td>
                  <td>{o.symbol}</td>
                  <td>
                    <span className={`side ${o.side === "buy" ? "long" : "short"}`}>
                      {o.side === "buy" ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td>{String(o.type).toUpperCase()}</td>
                  <td className="num">{fmtNum(o.size, 4)}</td>
                  <td className="num">{o.price ? fmtNum(o.price) : "MKT"}</td>
                  <td>
                    <span className={`chip ${statusChip[o.status] ?? "dry"}`}>
                      {o.status.toUpperCase()}
                    </span>
                    {o.reject_reason && <span className="sub">{o.reject_reason}</span>}
                  </td>
                  <td className="num">{o.latency_ms ? `${Math.round(o.latency_ms)}ms` : "—"}</td>
                  <td className="addr">{shortAddr(o.cloid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
