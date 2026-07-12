"use client";

import { Fragment, useState } from "react";
import { fmtDateTime } from "@/lib/format";
import { TvEvent } from "@/lib/trading-view/data";

// Logs unificados do módulo (view tv_events): SIGNAL | INCIDENT | HERMES | USER
// | SYSTEM. Minimalista e expansível (clique abre o detail JSON), paginado por
// cursor ?before=<ts> via o proxy read-only /api/tv/events.
const PAGE = 50;

const KINDS = ["", "SIGNAL", "INCIDENT", "HERMES", "USER", "SYSTEM"] as const;

function sevClass(sev: string): string {
  if (sev === "pos") return "live";
  if (sev === "neg" || sev === "critical" || sev === "error") return "rej";
  if (sev === "amber" || sev === "warning") return "ack";
  if (sev === "faint") return "dry";
  return "";
}

export default function LogsTable({ initial }: { initial: TvEvent[] }) {
  const [rows, setRows] = useState<TvEvent[]>(initial);
  const [kind, setKind] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [exhausted, setExhausted] = useState(initial.length < PAGE);
  const [open, setOpen] = useState<string | null>(null);

  async function fetchPage(k: string, before?: string) {
    const q = new URLSearchParams({ limit: String(PAGE) });
    if (k) q.set("kind", k);
    if (before) q.set("before", before);
    const res = await fetch(`/api/tv/events?${q.toString()}`, { cache: "no-store" });
    const data = (await res.json().catch(() => [])) as TvEvent[];
    return Array.isArray(data) ? data : [];
  }

  async function changeKind(k: string) {
    setKind(k);
    setLoading(true);
    const data = await fetchPage(k);
    setRows(data);
    setExhausted(data.length < PAGE);
    setLoading(false);
  }

  async function loadMore() {
    if (rows.length === 0) return;
    setLoading(true);
    const data = await fetchPage(kind, rows[rows.length - 1].ts);
    setRows((prev) => [...prev, ...data]);
    setExhausted(data.length < PAGE);
    setLoading(false);
  }

  return (
    <div className="card">
      <div className="cardhead">
        <h2>Logs</h2>
        <span className="cardnote">
          eventos unificados do módulo (sinais, incidentes, alterações) · fonte: view tv_events
        </span>
      </div>
      <div className="controls" style={{ marginBottom: 8 }}>
        <select
          className="select"
          aria-label="Tipo de evento"
          value={kind}
          onChange={(e) => changeKind(e.target.value)}
        >
          {KINDS.map((k) => (
            <option key={k || "all"} value={k}>
              {k || "Todos os tipos"}
            </option>
          ))}
        </select>
      </div>
      <div className="tablewrap">
        {rows.length === 0 ? (
          <div className="empty">nenhum evento</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Hora</th>
                <th>Tipo</th>
                <th>Resumo</th>
                <th>ref</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e, i) => {
                const id = `${e.ts}-${e.kind}-${e.ref_id ?? i}`;
                const isOpen = open === id;
                return (
                  <Fragment key={id}>
                    <tr
                      onClick={() => setOpen(isOpen ? null : id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>{fmtDateTime(e.ts)}</td>
                      <td>
                        <span className={`chip ${sevClass(e.severity)}`}>{e.kind}</span>
                      </td>
                      <td>{e.summary}</td>
                      <td className="addr">{e.ref_id ?? "—"}</td>
                    </tr>
                    {isOpen && e.detail && (
                      <tr>
                        <td colSpan={4}>
                          <pre className="logdetail">{e.detail}</pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      {!exhausted && (
        <div className="controls" style={{ marginTop: 8 }}>
          <button className="btn" onClick={loadMore} disabled={loading}>
            {loading ? "carregando…" : "carregar mais"}
          </button>
        </div>
      )}
    </div>
  );
}
