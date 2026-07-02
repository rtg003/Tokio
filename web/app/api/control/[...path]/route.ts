import { NextRequest, NextResponse } from "next/server";
import { createClient, supabaseConfigured } from "@/lib/supabase/server";

// Server-side proxy to the gateway control API. The gateway lives ONLY on the
// internal compose network; the web is its single authenticated client. The
// browser never sees GATEWAY_CONTROL_TOKEN. The web can never send orders and
// can never switch accounts to mainnet — those routes simply don't exist here.
const ALLOWED_GET = new Set(["health", "ledger", "positions", "balance"]);
const ALLOWED_POST_PATTERNS = [/^control\/strategy\/[\w-]+\/(pause|activate)$/];

function gatewayBase(): string {
  const host = process.env.GATEWAY_HOST ?? "gateway";
  const port = process.env.GATEWAY_PORT ?? "8700";
  return `http://${host}:${port}`;
}

async function requireSession(): Promise<boolean> {
  if (!supabaseConfigured()) return false;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  return Boolean(user);
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
  _req: NextRequest,
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
    const r = await fetch(`${gatewayBase()}/${joined}`, {
      method: "POST",
      headers: { "X-Control-Token": process.env.GATEWAY_CONTROL_TOKEN ?? "" },
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "gateway_unreachable" }, { status: 502 });
  }
}
