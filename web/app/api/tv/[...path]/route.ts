import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

// Proxy read-only (server-side) para os GETs do módulo Trading View. O gateway
// vive só na rede interna do compose; a web é seu único cliente autenticado. O
// browser nunca vê o token do gateway. Allowlist estrita: apenas as views
// read-only do módulo TV (isolamento já imposto no próprio endpoint, §5.1).
const ALLOWED_GET = new Set(["tv/strategies", "tv/events"]);

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
  req: NextRequest,
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
    const search = req.nextUrl.search ?? "";
    const r = await fetch(`${gatewayBase()}/api/${joined}${search}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    return NextResponse.json(await r.json(), { status: r.status });
  } catch {
    return NextResponse.json({ ok: false, reason: "gateway_unreachable" }, { status: 502 });
  }
}
