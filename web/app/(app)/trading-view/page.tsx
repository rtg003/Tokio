import { cookies } from "next/headers";
import AutoRefresh from "@/components/AutoRefresh";
import KpiRow from "@/components/trading-view/KpiRow";
import LogsTable from "@/components/trading-view/LogsTable";
import NewStrategyButton from "@/components/trading-view/NewStrategyButton";
import PositionsTable from "@/components/trading-view/PositionsTable";
import StrategiesTable from "@/components/trading-view/StrategiesTable";
import TradesOrdersTable from "@/components/trading-view/TradesOrdersTable";
import TvControls, { StrategyOption } from "@/components/trading-view/TvControls";
import {
  getBalance,
  getFills,
  getFillsSummary,
  getMetrics,
  getOrders,
  getPnlSummary,
  getPositions,
  getTvEvents,
  getTvStrategies,
} from "@/lib/trading-view/data";
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
  "7d": "últimos 7 dias",
  "30d": "últimos 30 dias",
  custom: "período personalizado",
};

export default async function TradingViewDashboard({
  searchParams,
}: {
  searchParams: Promise<{
    period?: string; from?: string; to?: string; strategy?: string;
  }>;
}) {
  const params = await searchParams;
  const period = ["today", "7d", "30d", "custom"].includes(params.period ?? "")
    ? (params.period as string)
    : "30d";

  function spDateString(d: Date): string {
    return d.toLocaleDateString("sv-SE", { timeZone: "America/Sao_Paulo" });
  }
  const todayIso = spDateString(new Date());
  let sinceDay = todayIso;
  let untilDay = todayIso;
  if (period === "7d") {
    sinceDay = spDateString(new Date(Date.now() - 7 * 86400_000));
  } else if (period === "30d") {
    sinceDay = spDateString(new Date(Date.now() - 30 * 86400_000));
  } else if (period === "custom") {
    sinceDay = parseDdMmYy(params.from) ?? sinceDay;
    untilDay = parseDdMmYy(params.to) ?? untilDay;
  }
  const sinceTs = `${sinceDay}T00:00:00-03:00`;
  const untilTs = `${untilDay}T23:59:59-03:00`;

  // Controle GLOBAL (topo): ambiente (testnet|mainnet, nunca "all") + wallet.
  const cookieStore = await cookies();
  const selectedEnv = readEnv(cookieStore);
  const selectedWallet = readWallet(cookieStore);
  const walletFilter = selectedWallet === "all" ? null : selectedWallet;
  const selectedStrategy = params.strategy ?? "all";

  // Estratégias do ambiente ativo (isolamento §5.1: só o módulo TV).
  const strategies = await getTvStrategies(selectedEnv);
  const strategyOptions: StrategyOption[] = [
    { value: "all", label: "Todas Estratégias" },
    ...strategies.map((s) => ({ value: s.strategy_id, label: s.name ?? s.strategy_id })),
  ];
  const activeIds = strategies
    .filter((s) => s.status !== "archived")
    .map((s) => s.strategy_id);
  const scopedIds =
    selectedStrategy === "all"
      ? activeIds
      : activeIds.filter((id) => id === selectedStrategy);
  const displayStrategies =
    selectedStrategy === "all"
      ? strategies
      : strategies.filter((s) => s.strategy_id === selectedStrategy);

  const [balance, metrics, fillsSummary, pnlSummary, orders, fills, positions, events] =
    await Promise.all([
      getBalance(selectedEnv, walletFilter),
      getMetrics(scopedIds, sinceDay, untilDay),
      getFillsSummary(scopedIds, sinceTs, untilTs, selectedEnv),
      getPnlSummary(scopedIds, sinceTs, untilTs, selectedEnv),
      getOrders(scopedIds, sinceTs, untilTs, selectedEnv),
      getFills(scopedIds, sinceTs, untilTs, selectedEnv),
      getPositions(scopedIds, selectedEnv),
      getTvEvents({ limit: 50 }),
    ]);

  return (
    <section>
      <AutoRefresh />
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · trading view</div>
          <h1>Trading View</h1>
        </div>
        <div className="controls">
          <NewStrategyButton equity={balance?.equity_usd ?? null} />
          <TvControls
            period={period}
            from={params.from ?? ""}
            to={params.to ?? ""}
            strategy={selectedStrategy}
            strategies={strategyOptions}
          />
        </div>
      </div>

      <KpiRow
        balance={balance}
        metrics={metrics}
        fillsSummary={fillsSummary}
        pnlSummary={pnlSummary}
        periodLabel={PERIOD_LABEL[period]}
      />
      <StrategiesTable strategies={displayStrategies} />
      <PositionsTable positions={positions} />
      <TradesOrdersTable orders={orders} fills={fills} />
      <LogsTable initial={events} />
    </section>
  );
}
