// Preferências GLOBAIS de UI (wallet + ambiente) persistidas em cookies simples
// (não-httpOnly): o seletor no Shell escreve via document.cookie e chama
// router.refresh(); os server components leem aqui via cookies() de next/headers.
// Ambiente NUNCA é "all" (testnet/mainnet isolados); wallet admite "all".

export const ENV_COOKIE = "tokio_env";
export const WALLET_COOKIE = "tokio_wallet";

export type Environment = "testnet" | "mainnet";

// Store = ReadonlyRequestCookies (cookies() de next/headers). Tipado como
// { get(name): { value: string } | undefined } para evitar acoplamento.
type CookieStore = {
  get(name: string): { value: string } | undefined;
};

// Ambiente ativo (default testnet no primeiro acesso). Valida a whitelist.
export function readEnv(store: CookieStore): Environment {
  const v = store.get(ENV_COOKIE)?.value;
  return v === "mainnet" ? "mainnet" : "testnet";
}

// Wallet ativa (default "all" = Todas Wallets).
export function readWallet(store: CookieStore): string {
  const v = store.get(WALLET_COOKIE)?.value;
  return v && v.trim() ? v : "all";
}
