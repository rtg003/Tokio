import AutoRefresh from "@/components/AutoRefresh";
import DashboardControls from "@/components/DashboardControls";
import FillsTable from "@/components/copy-trade/FillsTable";
import KpiRow from "@/components/copy-trade/KpiRow";
import OrdersTable from "@/components/copy-trade/OrdersTable";
import TradersTable from "@/components/copy-trade/TradersTable";
import {
  accountOptions,
  getBalance,
  getCopyStrategyIds,
  getExchanges,
  getFills,
  getMetrics,
  getOrders,
  getTraders,
} from "@/lib/copy-trade/data";

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

export default async function CopyTradeDashboard({
  searchParams,
}: {
  searchParams: Promise<{
    period?: string; from?: string; to?: string; account?: string; cols?: string;
  }>;
}) {
  const params = await searchParams;
  const expanded = params.cols === "all";
  const baseQuery = new URLSearchParams(
    Object.entries(params).filter(([k, v]) => k !== "cols" && v) as [string, string][],
  ).toString();
  const toggleHref = expanded ? `?${baseQuery}` : `?${baseQuery}${baseQuery ? "&" : ""}cols=all`;
  const period = ["today", "7d", "30d", "custom"].includes(params.period ?? "")
    ? (params.period as string)
    : "30d";

  const todayIso = new Date().toISOString().slice(0, 10);
  let sinceDay = todayIso;
  let untilDay = todayIso;
  if (period === "7d") {
    sinceDay = new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10);
  } else if (period === "30d") {
    sinceDay = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10);
  } else if (period === "custom") {
    sinceDay = parseDdMmYy(params.from) ?? sinceDay;
    untilDay = parseDdMmYy(params.to) ?? untilDay;
  }
  const sinceTs = `${sinceDay}T00:00:00Z`;
  const untilTs = `${untilDay}T23:59:59Z`;

  const [balance, exchanges, ctIds, traders] = await Promise.all([
    getBalance(),
    getExchanges(),
    getCopyStrategyIds(),
    getTraders(),
  ]);
  const copyStrategyIds = ctIds ?? [];
  const [metrics, orders, fills] = await Promise.all([
    getMetrics(copyStrategyIds, sinceDay, untilDay),
    getOrders(copyStrategyIds, sinceTs, untilTs),
    getFills(copyStrategyIds, sinceTs, untilTs),
  ]);
  const accounts = accountOptions(exchanges);
  const account = params.account ?? accounts[0].value;

  return (
    <section>
      <AutoRefresh />
      <div className="pagehead">
        <div>
          <div className="eyebrow">Estratégias · copy trade</div>
          <h1>Copy Trade</h1>
        </div>
        <DashboardControls
          accounts={accounts}
          account={account}
          period={period}
          from={params.from ?? ""}
          to={params.to ?? ""}
        />
      </div>

      <KpiRow balance={balance} metrics={metrics} periodLabel={PERIOD_LABEL[period]} />
      <TradersTable traders={traders} expanded={expanded} toggleHref={toggleHref} />
      <OrdersTable orders={orders} />
      <FillsTable fills={fills} />
    </section>
  );
}
