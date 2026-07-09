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

export type SessionMethod = "password" | "siwe";

export interface SessionClaims {
  exp: number;
  method: SessionMethod;
  address?: string;
}

// Token: `${payload}.${hmac(payload)}`.
//  - password (legado): payload = `${exp}`  (retrocompatível — nunca quebrar sessões vivas)
//  - siwe:              payload = `${exp}|siwe|${address}`
// O separador interno é "|" para não colidir com o "." que separa payload/assinatura.
function encodePayload(claims: SessionClaims): string {
  if (claims.method === "siwe" && claims.address) {
    return `${claims.exp}|siwe|${claims.address.toLowerCase()}`;
  }
  return String(claims.exp);
}

function decodePayload(payload: string): SessionClaims | null {
  const parts = payload.split("|");
  const exp = Number(parts[0]);
  if (!Number.isFinite(exp)) return null;
  if (parts.length === 1) return { exp, method: "password" };
  if (parts.length === 3 && parts[1] === "siwe") {
    return { exp, method: "siwe", address: parts[2] };
  }
  return null;
}

export async function signSession(
  opts: { method?: SessionMethod; address?: string } = {},
): Promise<string | null> {
  const secret = process.env.DASHBOARD_AUTH_SECRET;
  if (!secret) return null;
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const payload = encodePayload({
    exp,
    method: opts.method ?? "password",
    address: opts.address,
  });
  return `${payload}.${await hmac(secret, payload)}`;
}

async function verifyToken(token: string | undefined | null): Promise<SessionClaims | null> {
  const secret = process.env.DASHBOARD_AUTH_SECRET;
  if (!secret || !token) return null;
  const idx = token.lastIndexOf(".");
  if (idx <= 0) return null;
  const payload = token.slice(0, idx);
  const signature = token.slice(idx + 1);
  if (!payload || !signature) return null;
  const claims = decodePayload(payload);
  if (!claims) return null;
  if (claims.exp < Math.floor(Date.now() / 1000)) return null;
  const expected = await hmac(secret, payload);
  if (!safeEqual(signature, expected)) return null;
  return claims;
}

export async function verifySession(token: string | undefined | null): Promise<boolean> {
  return (await verifyToken(token)) !== null;
}

export async function sessionClaims(token: string | undefined | null): Promise<SessionClaims | null> {
  return verifyToken(token);
}

// SIWE: allowlist de endereços autorizados a logar por carteira.
// `AUTH_ALLOWED_ADDRESSES` = lista separada por vírgula (case-insensitive).
// Vazio/ausente = NENHUM endereço autorizado (fecha por padrão — SIWE inativo
// até o operador declarar explicitamente quem pode entrar).
export function allowedAddresses(): string[] {
  return (process.env.AUTH_ALLOWED_ADDRESSES ?? "")
    .split(",")
    .map((a) => a.trim().toLowerCase())
    .filter((a) => a.length > 0);
}

export function siweConfigured(): boolean {
  return Boolean(process.env.DASHBOARD_AUTH_SECRET) && allowedAddresses().length > 0;
}

export function addressAllowed(address: string): boolean {
  const target = address.trim().toLowerCase();
  return allowedAddresses().some((a) => safeEqual(a, target));
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
