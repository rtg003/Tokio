import { NextResponse } from "next/server";
import { siweConfigured } from "@/lib/auth";
import { issueNonce } from "@/lib/siwe-nonce";

export const dynamic = "force-dynamic";

// Emite um nonce SIWE de uso único (TTL 5 min). O cliente inclui esse nonce na
// mensagem EIP-4361 que a MetaMask assina; a rota /verify o consome. Sem SIWE
// configurado (allowlist vazia) a rota fica fechada — não vaza nonce nem
// sinaliza que o login por carteira existe.
export async function GET() {
  if (!siweConfigured()) {
    return NextResponse.json({ ok: false, reason: "siwe_not_configured" }, { status: 503 });
  }
  const nonce = issueNonce();
  return NextResponse.json({ ok: true, nonce });
}
