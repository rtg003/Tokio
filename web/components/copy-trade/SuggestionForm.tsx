"use client";

import { useState } from "react";
import {
  analyzeSuggestions,
  type AnalyzeResponse,
} from "@/lib/copy-trade/data";
import SuggestionResults from "@/components/copy-trade/SuggestionResults";

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
const MAX_ADDRESSES = 10;

// Extrai endereços da textarea (separados por vírgula, espaço ou quebra de
// linha), remove duplicados preservando a ordem.
function parseAddresses(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const tok of raw.split(/[\s,;]+/)) {
    const a = tok.trim().toLowerCase();
    if (!a) continue;
    if (!seen.has(a)) {
      seen.add(a);
      out.push(a);
    }
  }
  return out;
}

export default function SuggestionForm() {
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);

  const parsed = parseAddresses(raw);
  const invalid = parsed.filter((a) => !ADDR_RE.test(a));
  const tooMany = parsed.length > MAX_ADDRESSES;
  const canAnalyze =
    !busy && parsed.length >= 1 && invalid.length === 0 && !tooMany;

  async function onAnalyze() {
    if (!canAnalyze) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const res = await analyzeSuggestions(parsed);
    setBusy(false);
    if (!res.ok) {
      setError(res.reason ?? "erro_analise");
      return;
    }
    setResult(res);
  }

  return (
    <>
      <div className="card">
        <div className="cardhead">
          <h2>Analisar wallets</h2>
        </div>
        <div style={{ padding: "16px 18px" }}>
          <p className="empty" style={{ padding: "0 0 12px" }}>
            Cole de 1 a {MAX_ADDRESSES} endereços (0x…), separados por vírgula,
            espaço ou linha. A análise roda o pipeline de discovery completo e
            NÃO grava nada — você escolhe o que salvar no passo seguinte.
          </p>
          <textarea
            className="input"
            style={{ width: "100%", maxWidth: "none", minHeight: 96,
              resize: "vertical", fontFamily: "var(--mono)" }}
            placeholder="0x1234…abcd, 0x5678…ef01"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            disabled={busy}
          />
          <div style={{ display: "flex", alignItems: "center", gap: 14,
            marginTop: 12, flexWrap: "wrap" }}>
            <button
              type="button"
              className="btn btn-amber"
              disabled={!canAnalyze}
              onClick={onAnalyze}
            >
              {busy ? "Analisando…" : "Analisar"}
            </button>
            <span className="empty" style={{ padding: 0 }}>
              {parsed.length} endereço(s)
              {invalid.length > 0 && (
                <span className="neg"> · {invalid.length} inválido(s)</span>
              )}
              {tooMany && (
                <span className="neg"> · máximo {MAX_ADDRESSES}</span>
              )}
            </span>
          </div>
          {busy && (
            <p className="empty" style={{ padding: "12px 0 0" }}>
              Consultando a venue para cada wallet — pode levar até ~2 min.
            </p>
          )}
          {error && (
            <p className="empty neg" style={{ padding: "12px 0 0" }}>
              Falha na análise: {error}
            </p>
          )}
        </div>
      </div>

      {result && <SuggestionResults result={result} />}
    </>
  );
}
