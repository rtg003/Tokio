# SPEC — Autenticação MetaMask + Keyring de Agent Wallets Hyperliquid (Tokio)

**Módulo:** `hl-auth`

**Versão:** v2.0 — direcionada ao repo `rtg003/Tokio` (main @ 2026-07-08)

**Executor:** Claude Code / Cursor (CONSTRUTOR, conforme AGENTS.md)

**Destino deste arquivo:** `docs/specs/SPEC_HL_METAMASK_AUTH_v2.md`

**Changelog v2.0 (vs v1.1):** spec reescrita após leitura do repositório. A Fase 0 genérica foi ELIMINADA — as respostas estão no código e estão registradas na §1. Restam 8 verificações pontuais (§9). Mapa de mudanças arquivo a arquivo (§6). Alinhada às regras invioláveis do `AGENTS.md` (gates humanos, §5.3 páginas por funcionalidade, §5.4 SQLite via gateway, regra do mesmo commit).

---

## 1. Estado atual verificado no repo (fatos, não suposições)

| # | Fato verificado | Fonte |
|---|---|---|
| 1 | **Dual-env simultâneo JÁ EXISTE no gateway.** `GatewayState.adapters: dict[network→adapter]`; testnet sempre criada; mainnet criada SE `HL_MAINNET_ACCOUNT_ADDRESS` + `HL_MAINNET_AGENT_PRIVATE_KEY` existirem no env. Intents têm campo `environment` (`testnet\|mainnet\|paper`); `/balance?env=`; `/health` lista `environments`. | `engine/gateway/server.py` (main(), `_adapter_for`, IntentModel) |
| 2 | **Chaves de agente hoje: PLAINTEXT no `.env`** (`HL_AGENT_PRIVATE_KEY`, `HL_MAINNET_AGENT_PRIVATE_KEY`), lidas no boot do gateway. Não há keyring nem cifra. Rotação exige editar `.env` + restart. | `.env.example`, `adapter.py`, `server.py:1003` |
| 3 | **Gateway é o único signatário** (agent wallet `engine_gateway`), nonces serializados pelo processo. Runners só enviam intents. | ADR 0001, docstring do adapter |
| 4 | **Limites HL já documentados no repo:** 1 unnamed + 3 named por master (+2 por subaccount); janela de nonce `(T−2d, T+1d)`, 100 maiores por signatário; endereço de agente desregistrada NUNCA é reutilizado (replay). | `docs/decisions/0001-agent-wallets-por-papel.md` |
| 5 | **Web:** Next 15 App Router + React 19, Node 22-alpine. Sem wagmi/viem/siwe no `package.json`. Auth: senha (`DASHBOARD_PASSWORD`) → cookie HMAC (`DASHBOARD_AUTH_SECRET`, TTL 7d) via `web/lib/auth.ts`; `middleware.ts` protege tudo exceto `/login`. | `web/package.json`, `web/lib/auth.ts`, `web/middleware.ts` |
| 6 | **Web ⇄ gateway:** exclusivamente pelo proxy server-side `web/app/api/control/[...path]/route.ts` — GETs read-only allowlisted (`health, ledger, positions, balance, traders`); POSTs mutantes vão a `/control/...` com header `X-Control-Token` (`GATEWAY_CONTROL_TOKEN`, validado por `_control_auth`). Browser nunca vê o token. Gateway escuta só rede interna (compose) / `127.0.0.1` (VPS, ADR 0007). | proxy route, `server.py:378` |
| 7 | **BD:** SQLite WAL único (diretiva 2026-07-05, AGENTS.md §5.4); Supabase removido. Migrations = `db/migrations/NNNN_*.sql` ordenados, aplicados por `Database.migrate()`. Próximo número livre: **0014**. Telas leem SQLite **via gateway**. | `engine/core/db.py`, `db/migrations/` |
| 8 | **Regras de produto:** dashboard = ato humano (gate) desde 2026-07-05; página por funcionalidade com rota/componentes/camada de dados próprios (§5.3); mainnet sem credenciais é RECUSADA pelo gateway; trabalho direto na `main` com commits pequenos; **toda mudança que afete o Hermes exige entrada em `docs/HERMES_UPDATES.md` no mesmo commit**. | `AGENTS.md` |
| 9 | Notificações via Telegram (`engine/core/notifier.py`, `notifications.channel: telegram`). Deploy prod: systemd (`engine/supervisor.py` + `deploy/engine-processes.yaml`) + Caddy `tokio.bz → 127.0.0.1:3002`. | `settings.yaml`, `deploy/` |
| 10 | SDK: `hyperliquid-python-sdk>=0.15`; `eth_account` já é dependência transitiva usada no adapter. | `pyproject.toml`, `adapter.py` |

**Consequência central:** o gateway já resolve a simultaneidade testnet+mainnet. Este módulo NÃO cria dual-env — ele substitui o elo fraco (chaves plaintext em `.env`, provisionadas manualmente fora do sistema) por: cerimônia MetaMask na dashboard → keyring cifrado no SQLite → hot-reload de adapters no gateway → ciclo de vida gerenciado. E adiciona login SIWE.

## 2. Objetivo

1. **Login na dashboard via MetaMask (SIWE/EIP-4361)**, coexistindo com a senha atual (break-glass).

2. **Página `/hyperliquid`** (nova, própria, conforme §5.3): dois painéis lado a lado — **Testnet e Mainnet visíveis, gerenciáveis e ATIVOS ao mesmo tempo** — para provisionar/rotacionar/revogar agent wallets via assinatura EIP-712 `approveAgent` na MetaMask.

3. **Keyring cifrado no SQLite** substituindo `HL_AGENT_PRIVATE_KEY`/`HL_MAINNET_AGENT_PRIVATE_KEY` plaintext, com hot-reload de adapters (sem restart do gateway).

4. **Ciclo de vida**: monitor de expiração, fila de renovação, revogação com corte imediato do ambiente.

**Garantia de automação:** MetaMask assina só (a) login SIWE, (b) `approveAgent` 1x por ambiente, (c) rotações. Ordens em runtime seguem 100% com o gateway (fato §1.3) — nada muda no caminho quente.

## 3. Não-objetivos

- Alterar o modelo de assinatura de ordens (ADR 0001 permanece: gateway único signatário).

- Depósito/saque/bridge automatizado.

- Outras wallets além da MetaMask (wagmi deixa WalletConnect barato para v2).

- Tocar nos gates de traders/caps de risco (invioláveis; ver §5 desta spec).

## 4. Decisões de arquitetura (com racional ancorado no repo)

**D1 — Endpoints de provisionamento vivem no GATEWAY, não na web.** AGENTS.md §5.4: telas leem SQLite via gateway; o gateway já é dono do `Database`, do `httpx`, do `eth_account` e dos adapters. A web fica só com UI + assinatura MetaMask + proxy. Novos endpoints entram no padrão existente: GETs read-only sem token; mutações sob `/control/hl/...` com `Depends(_control_auth)`.

**D2 — SIWE coexiste com a senha (não substitui em P1).** A senha autenticada é hoje a definição formal de "ato humano" nos gates (AGENTS.md, diretiva 2026-07-05). Trocar o mecanismo de login altera a semântica de um gate — exige atualização explícita do `AGENTS.md` no mesmo commit e ciência do Hermes (inbox). P1 adiciona SIWE como método alternativo com allowlist `AUTH_ALLOWED_ADDRESSES`; a remoção da senha é decisão futura de rtg003, fora desta spec.

**D3 — Keyring no SQLite com precedência sobre o `.env`.** No boot e no reload, o gateway resolve a chave de cada ambiente na ordem: `hl_agents (status=active)` → fallback env plaintext (compat durante a migração). Quando os dois ambientes estiverem provisionados pelo keyring, as vars plaintext são removidas do `.env` (entrada no inbox do Hermes instruindo a limpeza).

**D4 — Nome do agente é estável por ambiente: `engine_gateway`** (mesmo papel do ADR 0001). Rotação reaprova o MESMO nome com keypair novo — na HL, reaprovar um named agent existente substitui a chave, preservando o orçamento de 3 named (⚠️ V4 na §9). Endereço antigo nunca é reutilizado (regra do ADR 0001).

**D5 — Hot-reload de adapter por ambiente.** Após `activate`, o gateway reconstrói `adapters[env]` com a chave nova (sob o lock do adapter; novo signatário ⇒ nonces zerados sem conflito, pois o contador é por endereço de assinante). Sem restart, sem janela sem assinatura maior que a troca do dict.

**D6 — Cerimônia dual-env na MESMA tela, chain-switch por assinatura.** A separação de ambientes é por requisição (endpoint HL + `hyperliquidChain` no typed data), não por sessão — por isso os dois painéis operam simultaneamente. A MetaMask só assina typed data com o `chainId` da chain ativa: o painel força `wallet_switchEthereumChain` (Arbitrum One `0xa4b1` p/ Mainnet; Arbitrum Sepolia `0x66eee` p/ Testnet ⚠️ V2) imediatamente antes de cada `signTypedData`. Assinar Testnet e Mainnet em sequência na mesma tela = dois switches transparentes.

## 5. Regras invioláveis herdadas (o Claude Code NÃO pode relaxar)

1. Ritual pré-alteração do `AGENTS.md` §2 como primeira ação da sessão (pull main, ler `docs/CURSOR_UPDATES.md`, aplicar PENDENTEs).

2. Mainnet sem agente válido (keyring ou env) continua sendo RECUSADA pelo gateway — o keyring não cria caminho novo para burlar o gate.

3. Nada do engine exposto publicamente: novos endpoints ficam atrás do proxy da web; Caddy não ganha rotas novas.

4. `strategy_id`/escopo em toda query de exibição (§5.1) — a página `/hyperliquid` é visão de SISTEMA (agentes/infra), não exibe dados de estratégia; se exibir saldo, usa `/balance?env=` já existente.

5. Regra do mesmo commit: cada fase que mude comportamento operacional gera `UPDATE-NNNN` em `docs/HERMES_UPDATES.md` no mesmo commit.

## 6. Mapa de mudanças (arquivo a arquivo)

### 6.1 Engine (Python)

| Arquivo | Ação |
|---|---|
| `db/migrations/0014_hl_agents.sql` | **novo** — tabelas §7 |
| `engine/core/keyring.py` | **novo** — AES-256-GCM (`cryptography`), `encrypt_key/decrypt_key`, deriva de `TOKIO_KEYRING_SECRET` (32B base64); erro fatal claro se secret ausente/malformado |
| `engine/gateway/hl_agents.py` | **novo** — casos de uso: `prepare(env,name)`, `activate(env, signature, nonce)`, `list()`, `revoke(env)`, `resolve_active_key(env)`, `expiry_scan()`; submissão do `approveAgent` via `httpx` ao endpoint do ambiente |
| `engine/gateway/server.py` | **editar** — (a) rotas novas: `GET /hl/agents` (read-only, sem token, shape sem chaves); `POST /control/hl/agents/prepare`, `POST /control/hl/agents/activate`, `POST /control/hl/agents/{env}/revoke` — todas `Depends(_control_auth)`; (b) `main()`: resolução de chave via keyring com fallback env (D3); (c) método `reload_adapter(env)` no `GatewayState` (D5); (d) task asyncio diária `expiry_scan` → notifier Telegram + status `expiring` |
| `pyproject.toml` | **editar** — adicionar `cryptography>=42` (avaliar `pynacl` como alternativa; escolher UMA) |
| `.env.example` | **editar** — adicionar `TOKIO_KEYRING_SECRET=`, `AUTH_ALLOWED_ADDRESSES=`; anotar `HL_*_PRIVATE_KEY` como LEGADO até migração |

### 6.2 Web (Next.js)

| Arquivo | Ação |
|---|---|
| `web/package.json` | **editar** — `wagmi@^2`, `viem@^2`, `@tanstack/react-query` (peer do wagmi), `siwe@^3` (⚠️ V6 compat Next 15/React 19) |
| `web/lib/wallet.ts` | **novo** — config wagmi: chains Arbitrum One + Arbitrum Sepolia, connector MetaMask (injected) |
| `web/lib/auth.ts` | **editar** — sessão ganha payload com `method: password\|siwe` e `address?`; verificação SIWE (nonce single-use server-side TTL 5min, domain `tokio.bz`, allowlist) |
| `web/app/api/auth/siwe/nonce/route.ts` + `web/app/api/auth/siwe/verify/route.ts` | **novos** |
| `web/app/login/page.tsx` | **editar** — botão "Sign in with MetaMask" ao lado do form de senha |
| `web/app/(app)/hyperliquid/page.tsx` | **novo** — página própria (§5.3) |
| `web/components/hyperliquid/EnvPanel.tsx`, `AgentCard.tsx`, `ProvisionFlow.tsx` | **novos** — dois `EnvPanel` renderizados lado a lado (`testnet`, `mainnet`), estado independente, ambos montados simultaneamente |
| `web/lib/hyperliquid/data.ts` | **novo** — camada de dados própria da página (fetch em `/api/control/hl/agents`) |
| `web/app/api/control/[...path]/route.ts` | **editar** — allowlist: GET `hl/agents`; POST patterns `^hl/agents/(prepare\|activate)$`, `^hl/agents/(testnet\|mainnet)/revoke$` |
| `web/components/Shell.tsx` | **editar** — item de navegação "Hyperliquid" |

### 6.3 Processo/documentação (mesmo commit de cada fase)

- `docs/decisions/0011-siwe-e-keyring-hl.md` — ADR desta spec (D1–D6).

- `docs/HERMES_UPDATES.md` — UPDATEs: novo env var, migração das chaves, limpeza do `.env`, novos endpoints de controle, runbook de rotação.

- `AGENTS.md` — nota de que o ato humano do login passa a incluir SIWE (D2).

## 7. Schema — `db/migrations/0014_hl_agents.sql` (SQLite, padrão do repo)

```sql
CREATE TABLE hl_agents (
  id             TEXT PRIMARY KEY,                 -- uuid4 gerado na aplicação
  env            TEXT NOT NULL CHECK (env IN ('testnet','mainnet')),
  master_address TEXT NOT NULL,
  agent_address  TEXT NOT NULL UNIQUE,             -- nunca reutilizado (ADR 0001)
  agent_name     TEXT NOT NULL DEFAULT 'engine_gateway',
  privkey_enc    TEXT NOT NULL,                    -- base64(iv || tag || ciphertext)
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','active','expiring','revoked','expired')),
  approved_at    TEXT,                             -- ISO-8601 UTC (padrão utcnow() do repo)
  valid_until    TEXT,
  revoked_at     TEXT,
  created_at     TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_hl_agents_active
  ON hl_agents (env) WHERE status IN ('active','expiring');  -- 1 ativo por ambiente

CREATE TABLE hl_auth_audit (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  at     TEXT NOT NULL,
  actor  TEXT NOT NULL,          -- address SIWE ou 'password_session'
  action TEXT NOT NULL,          -- siwe_login|agent_prepare|agent_activate|agent_revoke|agent_expiring|adapter_reload
  env    TEXT CHECK (env IN ('testnet','mainnet')),
  detail TEXT                    -- JSON (sem chaves, sem assinaturas)
);
```

`privkey_enc` não aparece em NENHUMA resposta de endpoint nem em log (regra do repo: segredos nunca logados — `config.py`).

## 8. Fluxos

### F1 — SIWE (login)

`GET /api/auth/siwe/nonce` (single-use, TTL 5min, server-side) → `personal_sign` da mensagem SIWE (`domain tokio.bz`, chain ativa) → `POST /api/auth/siwe/verify` valida assinatura + nonce + **allowlist `AUTH_ALLOWED_ADDRESSES`** (vazia ⇒ SIWE desabilitado, 503) → cookie de sessão no formato atual (§6.2). Middleware inalterado (só valida o cookie).

### F2 — Provisionamento (idêntico nos dois painéis; ambos utilizáveis na mesma sessão, em qualquer ordem)

1. Painel `env` → **Provision**: proxy → `POST /control/hl/agents/prepare {env}`.

2. Gateway: gera keypair (`eth_account`), grava `pending` cifrado, retorna `{agent_address, nonce_ms, master_address}`. Nada de chave na resposta.

3. Front: `wallet_switchEthereumChain` p/ chain do `env` → `signTypedData` (typed data `HyperliquidTransaction:ApproveAgent`, `hyperliquidChain: Mainnet|Testnet`, `agentAddress`, `agentName`, `nonce` — estrutura EXATA copiada do `sign_agent`/`approve_agent` do SDK instalado, ⚠️ V1). Operador confere endereço+ambiente na MetaMask.

4. Proxy → `POST /control/hl/agents/activate {env, agent_address, signature, nonce}` → gateway submete ao `POST {base_url}/exchange` do ambiente (`base_url` = constantes já existentes no adapter). `ok` ⇒ `active` (+`valid_until` se aplicável) → `reload_adapter(env)` → audit + Telegram. Erro ⇒ mantém `pending`, retorna o erro bruto da HL à UI.

5. Painel mostra: endereço, status, validade, botões Rotate/Revoke. Os DOIS painéis podem exibir agentes `active` ao mesmo tempo — aceite obrigatório.

**Pré-condições checadas pelo gateway antes do prepare (aviso na UI):** master existe no ambiente (`/info` `clearinghouseState`); Testnet sem saldo ⇒ mensagem apontando o faucet.

### F3 — Rotação

= F2 com o MESMO `agent_name` (D4). Sequência: novo `active` + reload ⇒ anterior marcado `revoked` (a HL já o substituiu). Endereço antigo permanece na tabela (histórico) e nunca é reaproveitado.

### F4 — Expiração

Task diária no gateway: `valid_until − now < 14d` ⇒ `expiring` + Telegram + banner na página. `< 48h` ⇒ alerta crítico; se expirar: adapter do ambiente é removido do dict ⇒ intents daquele env passam a falhar com `ambiente não configurado` (comportamento já existente, fato §1.1) — o outro ambiente segue operando.

### F5 — Revogação (kill parcial)

`POST /control/hl/agents/{env}/revoke` ⇒ `revoked` + remoção do adapter do dict (efeito <5s) + audit + Telegram. UI avisa: **isso NÃO desativa a chave na Hyperliquid** — vazamento suspeito exige rotação (F3) ou remoção no app oficial. Não confundir com o `KILL` file global (que permanece intocado).

## 9. Verificações remanescentes do Claude Code (fazer ANTES de codar; anexar resultado em `docs/hl-auth/DISCOVERY.md`)

- [ ] **V1** — Estrutura EXATA do typed data/payload `approveAgent` no `hyperliquid-python-sdk` instalado (ler `hyperliquid/utils/signing.py` do site-packages, não a memória). Replicar 1:1 no front (viem `signTypedData`) e na submissão.

- [ ] **V2** — `signatureChainId` aceito por ambiente hoje (spec assume `0xa4b1`/`0x66eee`); confirmar no código do SDK/docs.

- [ ] **V3** — Endpoint de listagem de agentes do master (`/info type=extraAgents`?) — nome e shape da resposta, para o health-check BD⇄on-chain.

- [ ] **V4** — Reaprovar named agent com o mesmo nome substitui a chave sem consumir slot (base da D4). Testar na Testnet antes de assumir.

- [ ] **V5** — `valid_until` de named agents: se a aprovação exige/aceita expiração e qual máximo vigente (ADR 0001 não fixa; UI precisa exibir).

- [ ] **V6** — Compat `wagmi@2`/`siwe` com Next 15 + React 19 + node:22-alpine (build do `web/Dockerfile` passa; sem `--legacy-peer-deps` silencioso).

- [ ] **V7** — Propagação de `TOKIO_KEYRING_SECRET` nos DOIS modos de deploy: compose (`env_file: .env`) e systemd/supervisor (conferir como `deploy/systemd` injeta env) — e confirmar que `deploy/backup_sqlite.sh` NÃO inclui o `.env` no backup offsite (propriedade: dump vazado ⇒ chaves ilegíveis).

- [ ] **V8** — Rede interna: o container `web` alcança `gateway:8700` para os novos POSTs `/control/hl/*` (já alcança para os existentes — só confirmar que nenhum firewall/systemd bloqueia no modo VPS `127.0.0.1`).

## 10. Fases e aceites

| Fase | Entrega | Aceite |
|---|---|---|
| **P0** | V1–V8 em `docs/hl-auth/DISCOVERY.md`; divergências apontadas | Aprovação de rtg003 |
| **P1** | SIWE coexistindo com senha + allowlist + audit + ADR 0011 + AGENTS.md atualizado | Login SIWE com master ok; address fora da allowlist ⇒ 403; senha segue funcionando; replay de nonce rejeitado |
| **P2** | Migration 0014 + keyring + endpoints + página `/hyperliquid` com os DOIS painéis (provisionamento habilitado só Testnet) | Agente Testnet provisionado via MetaMask; `reload_adapter` sem restart; ordem mínima Testnet executada; painel Mainnet visível com estado "sem agente" |
| **P3** | Habilitar provisionamento Mainnet (mesmo código; gate: exige `clearinghouseState` com saldo) + migração das chaves legadas + limpeza `.env` (via inbox Hermes) | **Agentes ativos nos dois ambientes SIMULTANEAMENTE; ordem mínima em cada um na mesma janela**; fallback env removido |
| **P4** | Expiry scan + rotação + revogação + health-check BD⇄on-chain (V3) | Simulações: expiração ⇒ só o env afetado cai; revogação <5s; rotação sem downtime perceptível |

Cada fase = commits pequenos direto na `main` + UPDATE no inbox do Hermes no mesmo commit quando houver impacto operacional (P2, P3 e P4 sempre têm).

## 11. Testes (padrão `tests/` do repo — pytest, asyncio auto, sem rede real)

`tests/test_hl_keyring.py`, `tests/test_hl_agents_endpoints.py`, `tests/test_hl_agents_lifecycle.py`:

1. Keyring: roundtrip encrypt/decrypt; secret ausente ⇒ erro fatal claro; dump da tabela sem secret ⇒ ilegível.

2. `prepare` grava `pending` cifrado e resposta NÃO contém chave (assert no shape).

3. `activate` com resposta HL mockada (`httpx` mock/transport): `ok` ⇒ `active` + `reload_adapter` chamado; erro HL ⇒ segue `pending` e erro propagado.

4. Índice único: segundo `active` no mesmo env ⇒ falha (rotação passa por transição correta).

5. Precedência D3: com registro `active` no BD, env var plaintext é IGNORADA; sem registro, fallback funciona (compat).

6. Revogação remove adapter do dict ⇒ intent para o env ⇒ `ambiente não configurado`; o OUTRO env continua aceitando intents (teste com dois PaperAdapters, padrão `test_chaos_runner_isolation.py`).

7. Expiry scan: `valid_until` próximo ⇒ `expiring` + notifier chamado (mock).

8. Endpoints `/control/hl/*` sem `X-Control-Token` ⇒ 401/403 (padrão `_control_auth`).

9. Web (se houver harness de rota; senão, teste manual documentado): nonce SIWE single-use; address fora da allowlist ⇒ 403; proxy rejeita path `hl/agents/qualquercoisa` fora da allowlist.

10. E2E manual Testnet (runbook em `docs/hl-auth/`): cerimônia completa nos dois painéis + ordem mínima, com evidências coladas no DISCOVERY.md.

## 12. Riscos residuais

- **Typed data divergente** = "invalid signature" opaco. Mitigação: V1 (copiar do SDK instalado) + validar todo o fluxo na Testnet antes de habilitar Mainnet (ordem das fases já impõe).

- **`cryptography` no Dockerfile.engine**: wheel musl/alpine vs glibc — conferir base image do engine; se for alpine, preferir wheel disponível ou trocar para `pynacl`.

- **Sessões SIWE e senha coexistindo** ampliam superfície de login; allowlist vazia DESLIGA o SIWE (fail-closed).

- **Rotação depende de humano com MetaMask** (~semestral, se V5 confirmar expiração): a fila de renovação com 14d + Telegram existe exatamente para isso.

- **Backup offsite** (`deploy/backup_sqlite.sh`) passa a carregar chaves cifradas: aceitável SÓ se V7 confirmar que o secret não viaja junto.
