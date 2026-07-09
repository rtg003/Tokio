# ADR 0011 — SIWE (login MetaMask) + Keyring cifrado de agent wallets HL

- Status: aceito (P1 SIWE implementado); P2–P4 planejados
- Data: 2026-07-09
- Gatilho: SPEC `hl-auth` v2.0 (`docs/specs/SPEC_HL_METAMASK_AUTH_v2.md`) +
  requisito rtg003: poder **logar com uma wallet nova e operar com ela**
  (Testnet primeiro). Hoje a dashboard autentica por senha→cookie HMAC e o
  gateway assina ordens com agent keys em texto puro no `.env`.
- Descoberta que embasa as decisões: `docs/hl-auth/DISCOVERY.md` (V1–V9).

## Contexto

- Auth atual: senha → cookie HMAC `tokio_session` (`web/lib/auth.ts`),
  TTL 7d. Login por `POST /api/login`.
- Assinatura HL: gateway é único signatário (ADR 0001); agent keys em
  `HL_AGENT_PRIVATE_KEY` / `HL_MAINNET_AGENT_PRIVATE_KEY` no `.env`, lidas em
  `engine/gateway/server.py`.
- SDK HL 0.24.0: `approve_agent` gera o par do agent e assina com a master
  key — que o gateway **não** tem no modelo novo (a master é a MetaMask do
  usuário). Logo o fluxo é: gateway monta o typed data → MetaMask assina →
  gateway submete ao `/exchange`.

## Decisões

- **D1 — SIWE convive com senha.** O login por carteira é um caminho
  adicional; a senha continua funcionando. Ambos emitem o MESMO cookie
  `tokio_session`. Nenhuma sessão viva quebra (formato de token
  retrocompatível).
- **D2 — A verificação SIWE reside na WEB (edge), não no gateway.** A web faz
  o challenge (nonce de uso único, TTL 5 min, server-side) e verifica a
  assinatura EIP-191 (`viem/siwe` + `verifyMessage`). O gateway não ganha
  endpoint de auth humana. Racional: o cookie já é emitido/validado na web
  (`middleware.ts`); manter a fronteira reduz superfície e não toca no
  caminho de ordem.
- **D3 — Allowlist fecha por padrão.** `AUTH_ALLOWED_ADDRESSES` (lista
  separada por vírgula). Vazio ⇒ SIWE inativo. Endereço fora da lista é
  recusado ANTES de verificar a assinatura.
- **D4 — `viem/siwe` em vez do pacote `siwe`.** O `siwe@3` faz peer-dep de
  `ethers`; usar o módulo nativo do viem evita puxar o ethers para um stack
  baseado em viem. (V6: build real passou com wagmi@2 + viem@2 +
  @tanstack/react-query@5 no Next 15.1 / React 19.)
- **D5 — Keyring AES-256-GCM (P2).** As agent keys migram do `.env` para o
  SQLite cifrado (`hl_agents.privkey_enc`), chave derivada de
  `TOKIO_KEYRING_SECRET`. Nunca loga plaintext. Precedência keyring > `.env`
  com fallback durante a transição — o gateway nunca fica sem signer.
- **D6 — `signatureChainId` (resolução do V2).** O SDK hardcoda
  `0x66eee` (Arbitrum Sepolia) para testnet E mainnet; só `hyperliquidChain`
  ("Testnet"/"Mainnet") decide o ambiente. Comentário do próprio SDK:
  "signatureChainId... can be any chain". **Decisão: opção (a)** — fixar
  `0x66eee` e variar só `hyperliquidChain`. (b) chain-switch
  `0xa4b1`/`0x66eee` fica como fallback puramente de UX na MetaMask.
  Confirmar contra a HL real antes do P3 (mainnet).

## Invariante — o caminho de ordem do Hermes fica intocado

`POST /intent` / `POST /cancel` → `_adapter_for(env)` → adapter assina. Este
trabalho **não adiciona gate** nesse caminho:

- SIWE é só login humano na dashboard (cookie `tokio_session`). O Hermes não
  loga por SIWE; `/intent` não passa por esse cookie.
- O keyring (P2) muda só a *origem* da agent key (`.env` → cifrado), não o
  fluxo de assinatura. Gateway segue único signatário (ADR 0001).
- Continuidade: precedência keyring com fallback ao `.env`; startup guard;
  remoção das chaves do `.env` (P3) só após provar restart+keyring.

## Consequências

- P1 (feito): `web/lib/wallet.ts`, rotas `/api/auth/siwe/{nonce,verify}`,
  botão MetaMask no login, allowlist, `middleware.ts` libera as rotas SIWE.
- P2+: migration `0014_hl_agents.sql`, `engine/core/keyring.py`,
  `engine/gateway/hl_agents.py`, página `/hyperliquid`, hot-reload de
  adapters com `account_address = master_address` do agent ativo.
- Gate humano de mainnet preservado (`server.py`): mainnet sem credenciais
  configuradas continua recusada.
- Hermes: UPDATE em `docs/HERMES_UPDATES.md` só no deploy (P2+) — novos
  secrets (`TOKIO_KEYRING_SECRET`, `AUTH_ALLOWED_ADDRESSES`), migration 0014.
