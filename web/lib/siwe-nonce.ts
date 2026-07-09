import { randomBytes } from "crypto";

// Armazenamento de nonces SIWE — server-side, uso único, TTL curto.
//
// Deployment da Tokio é single-process (Next standalone numa VPS), então um
// Map em memória basta e evita mais uma dependência/estado no SQLite. Se algum
// dia rodar multi-worker, migrar para o SQLite via gateway. O nonce NUNCA sai
// do servidor exceto no challenge; a verificação o consome (uso único) para
// impedir replay de uma assinatura capturada.

const NONCE_TTL_MS = 5 * 60 * 1000; // 5 min

interface NonceEntry {
  expiresAt: number;
}

const store = new Map<string, NonceEntry>();

function sweep(now: number): void {
  for (const [nonce, entry] of store) {
    if (entry.expiresAt <= now) store.delete(nonce);
  }
}

export function issueNonce(): string {
  const now = Date.now();
  sweep(now);
  // viem exige nonce alfanumérico >= 8 chars; hex de 16 bytes satisfaz.
  const nonce = randomBytes(16).toString("hex");
  store.set(nonce, { expiresAt: now + NONCE_TTL_MS });
  return nonce;
}

// Consome o nonce: retorna true só se existia e não expirou. Uso único —
// remove independentemente do resultado para não permitir retry.
export function consumeNonce(nonce: string | undefined | null): boolean {
  if (!nonce) return false;
  const entry = store.get(nonce);
  store.delete(nonce);
  if (!entry) return false;
  return entry.expiresAt > Date.now();
}
