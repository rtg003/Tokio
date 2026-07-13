import { cookies } from "next/headers";
import AutoRefresh from "@/components/AutoRefresh";
import DashboardControls from "@/components/DashboardControls";
import KpiRow from "@/components/copy-trade/KpiRow";
import PositionsTable from "@/components/copy-trade/PositionsTable";
import TradersTable from "@/components/copy-trade/TradersTable";
import TradesOrdersTable from "@/components/copy-trade/TradesOrdersTable";
import {
  getBalance,
  getCopyStrategyIds,
  getFills,
  getFillsSummary,
  getMetrics,
  getOrders,
  getPnlSummary,
  getPositions,
  getTraders,
  traderOptions,
} from "@/lib/copy-trade/data";
import { readEnv, readWallet } from "@/lib/prefs";

export const dynamic = "force-dynamic";

function parseDdMmYy(v: string | undefined): string | null {
  const m = /^(\d{2})\/(\d{2})\/(\d{2})$/.exec(v ?? "");
  if (!m) return null;
  const [, dd, mm, yy] = m;
  const day = Number(dd), month = Number(mm);
  if (day < 1 || day > 31 || month < 1 || month > 12) return null;
  return `20${yy}-${mm}-${dd}`;
}

const PERIOD_LABEL: Record<string, string> = {
  today: "hoje",
  yesterday: "ontem",
  "7d": "últimos 7 dias",
  custom: "período personalizado",
};

export default async function CopyTradeDashboard({
  searchParams,
}: {
  searchParams: Promise<{
    period?: string; from?: string; to?: string; trader?: string; cols?: string;
  }>;
}) {
  const params = await searchParams;
  const expanded = params.cols !== "core"; // padrão: expandido (todas colunas)
  const baseQuery = new URLSearchParams(
    Object.entries(params).filter(([k, v]) => k !== "cols" && v) as [string, string][],
  ).toString();
  const toggleHref = expanded ? `?${baseQuery}${baseQuery ? "&" : ""}cols=core` : `?${baseQuery}`;
  const period = ["today", "yesterday", "7d", "custom"].includes(params.period ?? "")
    ? (params.period as string)
    : "today";

  // Datas no fuso de São Paulo (UTC-3)
  function spDateString(d: Date): string {
    // Formata como YYYY-MM-DD no fuso de SP
    return d.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
  }
  function spToday(): string {
    return spDateString(new Date());
  }
  function spDaysAgo(n: number): string {
    return spDateString(new Date(Date.now() - n * 86400_000));
  }

  const todayIso = spToday();
  let sinceDay = todayIso;
  let untilDay = todayIso;
  if (period === "yesterday") {
    sinceDay = spDaysAgo(1);
    untilDay = spDaysAgo(1);
  } else if (period === "7d") {
    sinceDay = spDaysAgo(7);
  } else if (period === "custom") {
    sinceDay = parseDdMmYy(params.from) ?? sinceDay;
    untilDay = parseDdMmYy(params.to) ?? untilDay;
  }
  // sinceTs/untilTs em SP: 00:00 e 23:59 do dia em SP (UTC-3 = +03:00 sobre UTC)
  const sinceTs = `${sinceDay}T00:00:00-03:00`;
  const untilTs = `${untilDay}T23:59:59-03:00`;

  // Controle GLOBAL (topo): ambiente (testnet|mainnet, nunca "all") + wallet.
  const cookieStore = await cookies();
  const selectedEnv = readEnv(cookieStore);
  const selectedWallet = readWallet(cookieStore);
  const walletFilter = selectedWallet === "all" ? null : selectedWallet;
  const selectedTrader = params.trader ?? "all";

  const traders = await getTraders();
  const allTraders = traders ?? [];
  // Ambiente isolado: mostra os operantes do ambiente ativo (environment ===
  // selectedEnv) + os candidatos sem ambiente (SUGERIDO/SALVO, environment null),
  // para preservar o funil descoberta→promoção. Esconde o outro ambiente.
  const filteredTraders = allTraders.filter((t) => {
    const envOk = t.environment === selectedEnv || t.environment == null;
    const traderOk = selectedTrader === "all" || t.address === selectedTrader;
    return envOk && traderOk;
  });
  const copyStrategyIds = filteredTraders
    .map((t) => t.strategy_id)
    .filter((id): id is string => Boolean(id));
  const ledgerStrategyIds =
    selectedTrader === "all"
      ? (await getCopyStrategyIds()) ?? []
      : allTraders
          .filter((t) => t.address === selectedTrader)
          .map((t) => t.strategy_id)
          .filter((id): id is string => Boolean(id));
  const network = selectedEnv;
  const balance = await getBalance(selectedEnv, walletFilter);
  const [metrics, fillsSummary, pnlSummary, orders, fills, positions] = await Promise.all([
    getMetrics(copyStrategyIds, sinceDay, untilDay),
    getFillsSummary(ledgerStrategyIds, sinceTs, untilTs, network, walletFilter),
    getPnlSummary(ledgerStrategyIds, sinceTs, untilTs, network, walletFilter),
    getOrders(ledgerStrategyIds, sinceTs, untilTs, network, walletFilter),
    getFills(ledgerStrategyIds, sinceTs, untilTs, network, walletFilter),
    getPositions(ledgerStrategyIds, network, walletFilter),
  ]);
  const traderFilterOptions = traderOptions(allTraders);

  return (
    <section>
      <AutoRefresh />
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · copy trade</div>
          <h1>Copy Trade</h1>
        </div>
        <DashboardControls
          period={period}
          from={params.from ?? ""}
          to={params.to ?? ""}
          trader={selectedTrader}
          traders={traderFilterOptions}
        />
      </div>

      <KpiRow
        balance={balance}
        metrics={metrics}
        fillsSummary={fillsSummary}
        pnlSummary={pnlSummary}
        periodLabel={PERIOD_LABEL[period]}
        envFiltered={true}
      />
      <PositionsTable positions={positions} />
      <TradesOrdersTable orders={orders} fills={fills} />
      <TradersTable
        traders={filteredTraders}
        env={selectedEnv}
        expanded={expanded}
        toggleHref={toggleHref}
      />
    </section>
  );
}
