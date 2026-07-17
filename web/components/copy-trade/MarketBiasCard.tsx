import { MarketBias } from "@/lib/copy-trade/data";
import { fmtNum, pnlClass } from "@/lib/format";

// UPDATE-0062 (v15): heatmap de viés de mercado do HyperTracker (posições abertas
// nos últimos 7d). INFORMATIVO — nunca entra no ranking do discovery. Renderiza
// de forma tolerante: o payload do HT pode vir como array de itens ou objeto
// {ativo: valor}. Sem snapshot (sem chave HT / tabela vazia) → não renderiza.

type BiasRow = { asset: string; long: number | null; short: number | null; net: number | null };

function num(v: unknown): number | null {
  const n = typeof v === "string" ? Number(v) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) ? n : null;
}

function pick(obj: Record<string, unknown>, keys: string[]): unknown {
  for (const k of keys) {
    if (obj[k] !== undefined && obj[k] !== null) return obj[k];
  }
  return null;
}

// Normaliza o payload heterogêneo do HT numa lista de linhas por ativo.
function normalize(payload: unknown): BiasRow[] {
  const items: Record<string, unknown>[] = [];
  if (Array.isArray(payload)) {
    for (const it of payload) if (it && typeof it === "object") items.push(it as Record<string, unknown>);
  } else if (payload && typeof payload === "object") {
    const p = payload as Record<string, unknown>;
    // formatos comuns: { items: [...] } | { data: [...] } | { coin: value }
    const inner = pick(p, ["items", "data", "heatmap", "assets"]);
    if (Array.isArray(inner)) {
      for (const it of inner) if (it && typeof it === "object") items.push(it as Record<string, unknown>);
    } else {
      for (const [k, v] of Object.entries(p)) {
        if (v && typeof v === "object") items.push({ coin: k, ...(v as Record<string, unknown>) });
        else items.push({ coin: k, net: v });
      }
    }
  }
  const rows: BiasRow[] = [];
  for (const it of items) {
    const asset = String(pick(it, ["coin", "symbol", "asset", "name", "ticker"]) ?? "—");
    const long = num(pick(it, ["long", "longs", "longShare", "long_notional", "longNotional"]));
    const short = num(pick(it, ["short", "shorts", "shortShare", "short_notional", "shortNotional"]));
    let net = num(pick(it, ["net", "netBias", "net_bias", "bias", "net_bias_pct"]));
    if (net === null && long !== null && short !== null) {
      const gross = long + short;
      net = gross > 0 ? ((long - short) / gross) * 100 : 0;
    }
    rows.push({ asset, long, short, net });
  }
  return rows
    .filter((r) => r.asset !== "—")
    .sort((a, b) => Math.abs(b.net ?? 0) - Math.abs(a.net ?? 0))
    .slice(0, 12);
}

export default function MarketBiasCard({ bias }: { bias: MarketBias }) {
  if (!bias) return null;
  const rows = normalize(bias.payload);
  if (rows.length === 0) return null;
  return (
    <div className="card">
      <div className="cardhead">
        <h2>Viés de mercado</h2>
        <span className="cardnote">
          posições abertas nos últimos 7d · fonte: HyperTracker · informativo (não
          afeta ranking) · snapshot {bias.scan_ts?.slice(0, 16).replace("T", " ")} · v{bias.logic_version}
        </span>
      </div>
      <div className="tablewrap tablewrap-orders">
        <table>
          <thead>
            <tr>
              <th>Ativo</th>
              <th className="num">Long</th>
              <th className="num">Short</th>
              <th className="num">Viés líquido</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.asset}>
                <td>{r.asset}</td>
                <td className="num">{r.long != null ? fmtNum(r.long, 2) : "—"}</td>
                <td className="num">{r.short != null ? fmtNum(r.short, 2) : "—"}</td>
                <td className={`num ${pnlClass(r.net)}`}>
                  {r.net != null ? `${r.net >= 0 ? "+" : ""}${fmtNum(r.net, 1)}%` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
