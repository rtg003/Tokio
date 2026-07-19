import { fmtDateTime, fmtNotional, fmtNum, shortAddr, statusChip } from "@/lib/format";
import { Order } from "@/lib/copy-trade/data";

export default function OrdersTable({ orders }: { orders: Order[] | null }) {
  // Filtrar só ordens em aberto (não filled/closed)
  const allRows = orders ?? [];
  const rows = allRows
    .filter((o) => o.status !== "filled" && o.status !== "closed" && o.status !== "cancelled")
    .sort((a, b) => {
      // Ordem decrescente por created_at
      const da = a.created_at ? new Date(a.created_at).getTime() : 0;
      const db = b.created_at ? new Date(b.created_at).getTime() : 0;
      return db - da;
    });
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Ordens</h2>
        <span className="cardnote">ordens em aberto · atribuição por cloid · fonte: tabela orders</span>
      </div>
      <div className="tablewrap tablewrap-orders">
        {rows.length === 0 ? (
          <div className="empty">nenhuma ordem em aberto</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Hora</th>
                <th>Par</th>
                <th>Lado</th>
                <th className="num">Qtd</th>
                <th className="num">Preço</th>
                <th className="num">Valor</th>
                <th>Tipo</th>
                <th>Status</th>
                <th className="num">Latência</th>
                <th>cloid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.cloid}>
                  <td>{fmtDateTime(o.created_at)}</td>
                  <td>{o.symbol}</td>
                  <td>
                    <span className={`side ${o.side === "buy" ? "long" : "short"}`}>
                      {o.side === "buy" ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="num">{fmtNum(o.size, 4)}</td>
                  <td className="num">{o.price ? fmtNum(o.price) : "MKT"}</td>
                  <td className="num">{fmtNotional(o.size, o.price)}</td>
                  <td>{String(o.type).toUpperCase()}</td>
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
