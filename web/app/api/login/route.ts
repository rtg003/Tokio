import { NextRequest, NextResponse } from "next/server";
import {
  authConfigured,
  passwordMatches,
  SESSION_COOKIE,
  sessionCookieOptions,
  signSession,
} from "@/lib/auth";

export async function POST(req: NextRequest) {
  if (!authConfigured()) {
    return NextResponse.json({ ok: false, reason: "auth_not_configured" }, { status: 503 });
  }
  let password = "";
  try {
    const body = await req.json();
    password = String(body.password ?? "");
  } catch {
    return NextResponse.json({ ok: false, reason: "invalid_payload" }, { status: 400 });
  }
  if (!passwordMatches(password)) {
    return NextResponse.json({ ok: false, reason: "invalid_credentials" }, { status: 401 });
  }
  const token = await signSession();
  if (!token) {
    return NextResponse.json({ ok: false, reason: "auth_not_configured" }, { status: 503 });
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set(SESSION_COOKIE, token, sessionCookieOptions);
  return response;
}
