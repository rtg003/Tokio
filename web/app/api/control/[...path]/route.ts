import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

// Server-side proxy to the gateway control API. The gateway lives ONLY on the
// internal compose network; the web is its single authenticated client. The
// browser never sees GATEWAY_CONTROL_TOKEN. Status changes still go through the
// gateway, which refuses MAINNET unless credentials are configured.
const ALLOWED_GET = new Set(["health", "ledger", "positions", "balance", "traders"]);
const ALLOWED_POST_PATTERNS = [
  /^strategy\/[\w-]+\/(pause|activate)$/,
  // trader status/config: status changes are explicit authenticated human acts
  /^trader\/0x[0-9a-fA-F]{40}\/(status|config)$/,
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
    const r = await fetch(`${gatewayBase()}/control/${joined}${search}`, {
      method: "POST",
      headers: {
        "X-Control-Token": process.env.GATEWAY_CONTROL_TOKEN ?? "",
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      ...(body ? { body } : {}),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "gateway_unreachable" }, { status: 502 });
  }
}
