export const SESSION_COOKIE = "tokio_session";
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;

function encoder(): TextEncoder {
  return new TextEncoder();
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((b) => {
    binary += String.fromCharCode(b);
  });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function hmac(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder().encode(payload));
  return base64Url(new Uint8Array(signature));
}

export function authConfigured(): boolean {
  return Boolean(process.env.DASHBOARD_PASSWORD && process.env.DASHBOARD_AUTH_SECRET);
}

export function safeEqual(a: string, b: string): boolean {
  const max = Math.max(a.length, b.length);
  let diff = a.length ^ b.length;
  for (let i = 0; i < max; i += 1) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

export function passwordMatches(candidate: string): boolean {
  const expected = process.env.DASHBOARD_PASSWORD ?? "";
  return Boolean(expected) && safeEqual(candidate, expected);
}

export async function signSession(): Promise<string | null> {
  const secret = process.env.DASHBOARD_AUTH_SECRET;
  if (!secret) return null;
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const payload = String(exp);
  return `${payload}.${await hmac(secret, payload)}`;
}

export async function verifySession(token: string | undefined | null): Promise<boolean> {
  const secret = process.env.DASHBOARD_AUTH_SECRET;
  if (!secret || !token) return false;
  const [payload, signature] = token.split(".");
  if (!payload || !signature) return false;
  const exp = Number(payload);
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return false;
  const expected = await hmac(secret, payload);
  return safeEqual(signature, expected);
}

export function secureCookieEnabled(): boolean {
  const override = process.env.DASHBOARD_COOKIE_SECURE;
  if (override !== undefined) return override === "true";
  return process.env.NODE_ENV === "production";
}

export const sessionCookieOptions = {
  httpOnly: true,
  sameSite: "lax" as const,
  secure: secureCookieEnabled(),
  path: "/",
  maxAge: SESSION_TTL_SECONDS,
};
