import { NextResponse, type NextRequest } from "next/server";
import { authConfigured, SESSION_COOKIE, verifySession } from "@/lib/auth";

// Behind the reverse proxy the internal origin is 127.0.0.1:3002; redirects
// must target the PUBLIC origin from the X-Forwarded-* headers set by Caddy.
function publicUrl(request: NextRequest, path: string): URL {
  const host =
    request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  const proto = request.headers.get("x-forwarded-proto") ?? "https";
  if (host) return new URL(path, `${proto}://${host}`);
  return new URL(path, request.url);
}

// Session required on ALL routes except /login and static assets.
export async function middleware(request: NextRequest) {
  const isLogin = request.nextUrl.pathname === "/login";
  const isLoginApi = request.nextUrl.pathname === "/api/login";
  if (!authConfigured()) {
    if (!isLogin && !isLoginApi) {
      return NextResponse.redirect(publicUrl(request, "/login"));
    }
    return NextResponse.next({ request });
  }

  const ok = await verifySession(request.cookies.get(SESSION_COOKIE)?.value);
  if (!ok && !isLogin && !isLoginApi) {
    return NextResponse.redirect(publicUrl(request, "/login"));
  }
  if (ok && isLogin) {
    return NextResponse.redirect(publicUrl(request, "/"));
  }
  return NextResponse.next({ request });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico)$).*)"],
};
