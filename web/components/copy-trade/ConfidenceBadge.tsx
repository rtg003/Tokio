"use client";

// UPDATE-0058 (Fase 3/3): apresentação da confiança das métricas produzida
// pelas Fases 1/2 (UPDATE-0056/0057). NÃO altera o motor de classificação — só
// traduz `metrics_confidence` + campos de amostra/idade/HyperTracker em UI clara.

export type MetricsConfidence = "complete" | "sampled" | "insufficient";

type Meta = { label: string; chip: string; tip: string };

// complete → verde (filled) · sampled → âmbar (ack) · insufficient → vermelho (rej).
export const CONFIDENCE_META: Record<string, Meta> = {
  complete: {
    label: "DADOS COMPLETOS",
    chip: "filled",
    tip: "A amostra de fills cobre a janela longitudinal (30/60d). Métricas e simulações são exatas.",
  },
  sampled: {
    label: "AMOSTRA RECENTE",
    chip: "ack",
    tip: "A amostra cobre só uma fração da janela (trader hiperativo ou histórico truncado). Métricas longitudinais são APROXIMADAS e as simulações ficam INDETERMINADAS.",
  },
  insufficient: {
    label: "INSUFICIENTE",
    chip: "rej",
    tip: "Poucos trades fechados na amostra — não há base para métricas de 30/60d nem para simulações. Filtros longitudinais ficam INDETERMINADOS.",
  },
  // UPDATE-0059: linha LEGADA analisada antes da classificação de confiança
  // (metrics_confidence NULL). Não é um veredito — é "ainda não reavaliado".
  legacy: {
    label: "NÃO REAVALIADO",
    chip: "dry",
    tip: "Analisada antes da classificação de confiança (migração 0024). Não sabemos se as métricas longitudinais são completas ou amostra truncada — clique em Reanalisar para reclassificar preservando status/config.",
  },
};

// Tooltip canônico sobre o truncamento da API em ~2.000 fills.
export const TRUNCATION_TIP =
  "A API da Hyperliquid (userFills) devolve no máximo ~2.000 fills por wallet. " +
  "Para traders hiperativos isso cobre apenas HORAS de atividade — por isso as " +
  "métricas de 30/60d podem ser amostradas, não completas.";

export function isComplete(confidence: string | null | undefined): boolean {
  return (confidence ?? "complete") === "complete";
}

// UPDATE-0059: linha legada (sem classificação de confiança). Só a tabela
// principal tem essas linhas; o relatório de Sugestões sempre traz confidence.
export function isLegacy(confidence: string | null | undefined): boolean {
  return confidence === null || confidence === undefined || confidence === "";
}

// Fonte da idade da wallet, inferida dos campos do relatório: HyperTracker
// (earliestActivityAt) é autoritativa (Fase 2); senão portfolio.allTime (Fase 1);
// senão o fill mais antigo da amostra.
export function ageSource(args: {
  htEarliestMs?: number | null;
  walletAgeDays?: number | null;
}): string {
  if (args.htEarliestMs !== null && args.htEarliestMs !== undefined) {
    return "HyperTracker · earliestActivityAt";
  }
  if (args.walletAgeDays !== null && args.walletAgeDays !== undefined) {
    return "Hyperliquid · portfolio.allTime";
  }
  return "amostra de fills";
}

export default function ConfidenceBadge({
  confidence,
}: {
  confidence: string | null | undefined;
}) {
  const key = isLegacy(confidence) ? "legacy" : (confidence as string);
  const meta = CONFIDENCE_META[key] ?? CONFIDENCE_META.complete;
  return (
    <span className={`chip ${meta.chip}`} title={meta.tip}>
      {meta.label}
    </span>
  );
}
