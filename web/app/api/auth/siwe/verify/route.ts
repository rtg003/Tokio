import { NextRequest, NextResponse } from "next/server";
import { parseSiweMessage } from "viem/siwe";
import { verifyMessage } from "viem";
import {
  addressAllowed,
  SESSION_COOKIE,
  sessionCookieOptions,
  signSession,
  siweConfigured,
} from "@/lib/auth";
import { consumeNonce } from "@/lib/siwe-nonce";

export const dynamic = "force-dynamic";

// Verifica um login SIWE (EIP-4361):
//  1. parseia a mensagem assinada e extrai address/nonce/domain;
//  2. consome o nonce (uso único → anti-replay);
//  3. valida domain contra o host da requisição (anti-phishing);
//  4. confere o endereço na allowlist (AUTH_ALLOWED_ADDRESSES);
//  5. verifica a assinatura EIP-191 recuperando o signatário;
//  6. emite o MESMO cookie `tokio_session` da senha, marcado method=siwe.
// Nada aqui toca em chaves/keyring nem no caminho de ordem do gateway.
export async function POST(req: NextRequest) {
  if (!siweConfigured()) {
    return NextResponse.json({ ok: false, reason: "siwe_not_configured" }, { status: 503 });
  }

  let message = "";
  let signature = "";
  try {
    const body = await req.json();
    message = String(body.message ?? "");
    signature = String(body.signature ?? "");
  } catch {
    return NextResponse.json({ ok: false, reason: "invalid_payload" }, { status: 400 });
  }
  if (!message || !signature) {
    return NextResponse.json({ ok: false, reason: "invalid_payload" }, { status: 400 });
  }

  const fields = parseSiweMessage(message);
  const address = fields.address;
  const nonce = fields.nonce;
  if (!address || !nonce) {
    return NextResponse.json({ ok: false, reason: "invalid_message" }, { status: 400 });
  }

  // Nonce de uso único: se já foi consumido/expirou, rejeita (anti-replay).
  if (!consumeNonce(nonce)) {
    return NextResponse.json({ ok: false, reason: "nonce_invalid" }, { status: 401 });
  }

  // Domain binding: a mensagem tem de ser para ESTE host (anti-phishing).
  const host = req.headers.get("host") ?? "";
  if (fields.domain && host && fields.domain !== host) {
    return NextResponse.json({ ok: false, reason: "domain_mismatch" }, { status: 401 });
  }

  // Allowlist ANTES de verificar a assinatura — só endereços declarados entram.
  if (!addressAllowed(address)) {
    return NextResponse.json({ ok: false, reason: "address_not_allowed" }, { status: 403 });
  }

  let valid = false;
  try {
    valid = await verifyMessage({
      address: address as `0x${string}`,
      message,
      signature: signature as `0x${string}`,
    });
  } catch {
    valid = false;
  }
  if (!valid) {
    return NextResponse.json({ ok: false, reason: "bad_signature" }, { status: 401 });
  }

  const token = await signSession({ method: "siwe", address });
  if (!token) {
    return NextResponse.json({ ok: false, reason: "auth_not_configured" }, { status: 503 });
  }

  // Auditoria leve (P1): a trilha formal em `hl_auth_audit` chega no P2 com a
  // migration 0014. Por ora registramos o login por carteira no log do servidor.
  console.info(`[siwe] login ok address=${address.toLowerCase()}`);

  const response = NextResponse.json({ ok: true, address: address.toLowerCase() });
  response.cookies.set(SESSION_COOKIE, token, sessionCookieOptions);
  return response;
}
