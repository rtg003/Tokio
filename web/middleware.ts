import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

type CookieToSet = { name: string; value: string; options?: CookieOptions };

// Inline (edge middleware can't import server-only helpers freely): a
// malformed URL must fall back to the login/unconfigured flow, never a 500.
function supabaseUrlValid(u: string | undefined): u is string {
  try {
    const p = new URL(u ?? "");
    return p.protocol === "https:" || p.protocol === "http:";
  } catch {
    return false;
  }
}

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
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  let response = NextResponse.next({ request });
  if (!supabaseUrlValid(url) || !anon) {
    // Unconfigured environment (first boot): only the login screen renders,
    // showing the configuration notice.
    if (request.nextUrl.pathname !== "/login") {
      return NextResponse.redirect(publicUrl(request, "/login"));
    }
    return response;
  }

  const supabase = createServerClient(url, anon, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        );
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        );
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  const isLogin = request.nextUrl.pathname === "/login";
  if (!user && !isLogin) {
    return NextResponse.redirect(publicUrl(request, "/login"));
  }
  if (user && isLogin) {
    return NextResponse.redirect(publicUrl(request, "/"));
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico)$).*)"],
};
