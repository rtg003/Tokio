import { fmtDateTime, fmtNotional, fmtNum, fmtSigned, pnlClass, shortAddr } from "@/lib/format";
import { Fill } from "@/lib/copy-trade/data";

export default function FillsTable({ fills }: { fills: Fill[] | null }) {
  const rows = (fills ?? [])
    .slice()
    .sort((a, b) => {
      const da = a.ts ? new Date(a.ts).getTime() : 0;
      const db = b.ts ? new Date(b.ts).getTime() : 0;
      return db - da;
    });
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Trades</h2>
        <span className="cardnote">fills executados · PnL realizado líquido de fees · fonte: tabela fills</span>
      </div>
      <div className="tablewrap tablewrap-fills">
        {rows.length === 0 ? (
          <div className="empty">nenhum trade de copy trade no período</div>
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
                <th className="num">Fee</th>
                <th className="num">PnL líquido</th>
                <th>cloid</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f, i) => (
                <tr key={`${f.cloid}-${i}`}>
                  <td>{fmtDateTime(f.ts)}</td>
                  <td>{f.symbol}</td>
                  <td>
                    <span className={`side ${f.side === "buy" ? "long" : "short"}`}>
                      {f.side === "buy" ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="num">{fmtNum(f.size, 4)}</td>
                  <td className="num">${fmtNum(f.price)}</td>
                  <td className="num">${fmtNotional(f.size, f.price)}</td>
                  <td className="num">${fmtNum(f.fee, 4)}</td>
                  <td className={`num ${pnlClass(f.realized_pnl)}`}>
                    {f.realized_pnl === null || f.realized_pnl === undefined ? "—" : `$${fmtSigned(f.realized_pnl)}`}
                  </td>
                  <td className="addr">{shortAddr(f.cloid)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
