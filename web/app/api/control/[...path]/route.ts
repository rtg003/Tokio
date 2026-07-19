import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

// Server-side proxy to the gateway control API. The gateway lives ONLY on the
// internal compose network; the web is its single authenticated client. The
// browser never sees GATEWAY_CONTROL_TOKEN. Status changes still go through the
// gateway, which refuses MAINNET unless credentials are configured.
const ALLOWED_GET = new Set([
  "health",
  "ledger",
  "positions",
  "balance",
  "traders",
  "hl/agents",
]);
const ALLOWED_POST_PATTERNS = [
  /^strategy\/[\w-]+\/(pause|activate)$/,
  // trader status/config: status changes are explicit authenticated human acts
  /^trader\/0x[0-9a-fA-F]{40}\/(status|config)$/,
  // close_positions: preview (execute=false) + fechamento reduce_only
  // (execute=true) das posições do trader — ato humano autenticado.
  /^trader\/0x[0-9a-fA-F]{40}\/close_positions$/,
  // fechar UMA posição (símbolo) via reduce_only market — ato humano
  // autenticado (com confirmação na UI).
  /^position\/close$/,
  // rótulo amigável da wallet no combo do topo (upsert/remove) — ato humano.
  /^wallet\/0x[0-9a-fA-F]{40}\/label$/,
  // cancelar UMA ordem em aberto via ícone da tabela — ato humano autenticado
  // (com confirmação na UI).
  /^order\/cancel$/,
  // reexecutar UMA ordem recusada (rejected/error) a preço de mercado — ato
  // humano autenticado (preview + confirmação na UI).
  /^order\/reexecute$/,
  // HL agent wallets: prepare/activate provisioning + revoke (gateway gates
  // the control token; MAINNET still needs credentials configured server-side)
  /^hl\/agents\/(prepare|activate)$/,
  /^hl\/agents\/(testnet|mainnet)\/revoke$/,
  // TV-Executor: criar estratégia (nasce draft) e ativar (draft→active).
  // Ato humano autenticado; gateway ainda impõe mainnet só com credenciais.
  /^tv\/strategies$/,
  /^tv\/strategies\/[a-z0-9_]{3,48}\/activate$/,
  // TV-Executor: pausar, editar config (versionada) e excluir (cascade só do
  // módulo TV; preserva fills/orders; gateway recusa active/posição aberta).
  /^tv\/strategies\/[a-z0-9_]{3,48}\/(pause|config|delete)$/,
  // Sugestões manuais: analisar wallets pelo pipeline de discovery (analyze, sem
  // gravar) e força-salvar as selecionadas como SUGERIDO/origin="usuário".
  /^suggestions\/(analyze|save)$/,
  // UPDATE-0059 (backfill): reclassifica linhas legadas (metrics_confidence NULL)
  // pelo pipeline individual, preservando status/copy_pinned. Ato humano.
  /^discovery\/reclassify$/,
  // UPDATE-0061: reset do circuit breaker (por wallet+ambiente ou global) e
  // cleanup one-shot de fantasmas no ledger — atos humanos autenticados.
  /^circuit-breaker\/reset$/,
  /^ledger\/cleanup$/,
];

function gatewayBase(): string {
  const host = process.env.GATEWAY_HOST ?? "gateway";
  const port = process.env.GATEWAY_PORT ?? "8700";
  return `http://${host}:${port}`;
}

async function requireSession(): Promise<boolean> {
  if (!authConfigured()) return false;
  const cookieStore = await cookies();
  return verifySession(cookieStore.get(SESSION_COOKIE)?.value);
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  if (!(await requireSession())) {
    return NextResponse.json({ ok: false, reason: "unauthenticated" }, { status: 401 });
  }
  const { path } = await params;
  const joined = path.join("/");
  if (!ALLOWED_GET.has(joined)) {
    return NextResponse.json({ ok: false, reason: "not_allowed" }, { status: 403 });
  }
  try {
    const r = await fetch(`${gatewayBase()}/${joined}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "gateway_unreachable" }, { status: 502 });
  }
}

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  if (!(await requireSession())) {
    return NextResponse.json({ ok: false, reason: "unauthenticated" }, { status: 401 });
  }
  const { path } = await params;
  const joined = path.join("/");
  if (!ALLOWED_POST_PATTERNS.some((p) => p.test(joined))) {
    return NextResponse.json({ ok: false, reason: "not_allowed" }, { status: 403 });
  }
  try {
    const search = req.nextUrl.search ?? "";
    const body = await req.text();
    // POSTs de controle podem encadear várias chamadas externas (ex.:
    // hl/agents/activate submete o approveAgent à HL, lê extra_agents e
    // recarrega o adapter). 5s cortava o fluxo no meio; 30s cobre o pior caso.
    // suggestions/* roda o pipeline de discovery para até 10 wallets frias
    // (~8-10s cada no throttle da venue) → 120s p/ não cortar no meio.
    // discovery/reclassify reprocessa 1..N wallets legadas pelo mesmo pipeline.
    const longRun = joined.startsWith("suggestions/") || joined === "discovery/reclassify";
    const timeoutMs = longRun ? 120000 : 30000;
    const r = await fetch(`${gatewayBase()}/control/${joined}${search}`, {
      method: "POST",
      headers: {
        "X-Control-Token": process.env.GATEWAY_CONTROL_TOKEN ?? "",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body } : {}),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "gateway_unreachable" }, { status: 502 });
  }
}
