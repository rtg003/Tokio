# hl-auth · P0 · DISCOVERY

Verificações V1–V9 da SPEC `docs/specs/SPEC_HL_METAMASK_AUTH_v2.md` §9 (+ V9, a
invariante do Hermes acrescentada no plano). **Entregável de portão:** este documento
precisa da **aprovação do rtg003** antes de qualquer código de produção (P1+).

- **Data:** 2026-07-08
- **Ambiente inspecionado:** repo em `main`, venv `.venv` (Python 3.14).
- **Versões instaladas:** `hyperliquid-python-sdk **0.24.0**`, `eth_account **0.13.7**`,
  `cryptography` **NÃO instalado** (será dep nova — §V6/§riscos).
  Obs.: a spec §1.10 assumia `>=0.15`; a instalada é 0.24.0 (superior, compatível).

Legenda de status:
- ✅ **RESOLVIDO** — respondido por evidência de código (verbatim abaixo).
- 🟡 **RESOLVIDO c/ ressalva** — respondido por código, mas requer confirmação viva antes de mainnet.
- ⏳ **PENDENTE (validação viva)** — exige build/testnet/deploy real; não dá para fechar só lendo.

---

## V1 — Estrutura EXATA do typed data `approveAgent` ✅ RESOLVIDO

Fonte: `.venv/lib/python3.14/site-packages/hyperliquid/utils/signing.py`.

`sign_agent` (linhas 412-424) delega a `sign_user_signed_action` com estes
`payload_types` e `primaryType`:

```python
def sign_agent(wallet, action, is_mainnet):
    return sign_user_signed_action(
        wallet,
        action,
        [
            {"name": "hyperliquidChain", "type": "string"},
            {"name": "agentAddress", "type": "address"},
            {"name": "agentName", "type": "string"},
            {"name": "nonce", "type": "uint64"},
        ],
        "HyperliquidTransaction:ApproveAgent",
        is_mainnet,
    )
```

`user_signed_payload` (linhas 217-237) monta o EIP-712 completo:

```python
def user_signed_payload(primary_type, payload_types, action):
    chain_id = int(action["signatureChainId"], 16)
    return {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "types": {
            primary_type: payload_types,
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": primary_type,
        "message": action,
    }
```

`Exchange.approve_agent` (`exchange.py:635-657`) mostra a ação e a submissão:

```python
def approve_agent(self, name=None):
    agent_key = "0x" + secrets.token_hex(32)
    account = eth_account.Account.from_key(agent_key)
    timestamp = get_timestamp_ms()
    is_mainnet = self.base_url == MAINNET_API_URL
    action = {
        "type": "approveAgent",
        "agentAddress": account.address,
        "agentName": name or "",
        "nonce": timestamp,
    }
    signature = sign_agent(self.wallet, action, is_mainnet)
    if name is None:
        del action["agentName"]
    return (self._post_action(action, signature, timestamp), agent_key)
```

`_post_action` (`exchange.py:101-110`) → `POST {base_url}/exchange`:

```python
payload = {"action": action, "nonce": nonce, "signature": signature,
           "vaultAddress": ..., "expiresAfter": self.expires_after}
```

`sign_inner` (`signing.py:452-455`) devolve a assinatura no shape `{r,s,v}`:

```python
signed = wallet.sign_message(encode_typed_data(full_message=data))
return {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
```

**Consequência de arquitetura (importante):** `approve_agent` gera o par do agent e
assina com `self.wallet` — a **master key**. No nosso modelo o master é a MetaMask do
usuário, e o gateway **não** tem a master key. Logo **não usamos `approve_agent`
direto**. O fluxo correto (F2 da spec):
1. Gateway gera o par do agent (`"0x"+secrets.token_hex(32)` + `Account.from_key`),
   monta a `action` `approveAgent` e o **typed data** acima (setando `signatureChainId`
   e `hyperliquidChain`; ver V2).
2. Web faz a MetaMask assinar esse typed data (viem `signTypedData`), obtendo uma
   assinatura de 65 bytes → **split em `{r,s,v}`** (formato exigido pelo `/exchange`).
3. Gateway submete `POST {base_url}/exchange` com `{action, nonce, signature,
   vaultAddress:null, expiresAfter:null}`.

O front precisa replicar o typed data **1:1** com o de cima (nomes/ordem/tipos), senão
a HL devolve "invalid signature" opaco (risco §12 da spec).

## V2 — `signatureChainId` por ambiente 🟡 RESOLVIDO c/ ressalva

`sign_user_signed_action` (`signing.py:247-253`), com o comentário do próprio autor do
SDK:

```python
def sign_user_signed_action(wallet, action, payload_types, primary_type, is_mainnet):
    # signatureChainId is the chain used by the wallet to sign and can be any chain.
    # hyperliquidChain determines the environment and prevents replaying an action on
    # a different chain.
    action["signatureChainId"] = "0x66eee"
    action["hyperliquidChain"] = "Mainnet" if is_mainnet else "Testnet"
    ...
```

**Descoberta que corrige a spec (D6) e o plano:** o `signatureChainId` **"can be any
chain"** — é apenas a chain que a carteira usa para assinar; **quem separa mainnet de
testnet é o campo `hyperliquidChain`** ("Mainnet"/"Testnet"). O SDK instalado usa
`0x66eee` (Arbitrum Sepolia) **para os DOIS ambientes**. A spec D6 assumia trocar para
Arbitrum One `0xa4b1` no mainnet — **não é exigido pela HL** segundo o SDK.

**Implicação prática para o front:**
- O `domain.chainId` do EIP-712 = `int(signatureChainId, 16)`. A MetaMask exibe/assina
  esse `chainId`. Duas opções válidas:
  - (a) **Espelhar o SDK:** usar `signatureChainId = 0x66eee` sempre e só variar
    `hyperliquidChain`. Menos switches de rede na MetaMask; máxima paridade com o que
    o gateway/adapters já fazem.
  - (b) **Seguir D6:** trocar de chain por ambiente (`0xa4b1`/`0x66eee`) via
    `wallet_switchEthereumChain`. Funciona também (a HL aceita qualquer
    `signatureChainId`), mas exige a carteira estar na rede certa e dá mais fricção.
- **Recomendação:** adotar (a) — simplifica a UX e evita depender de `switchChain`.
  Reavaliar em D6 na hora de escrever P2.

**Ressalva (por que não é ✅ pleno):** a afirmação "can be any chain" é do autor do SDK;
antes de habilitar **mainnet** (P3) é obrigatório provar um `approveAgent` **aceito na
HL testnet** com a assinatura vinda da MetaMask (não do SDK) — ver V4/E2E. Se por
algum motivo a HL rejeitar, cai-se na opção (b).

## V3 — Listagem de agentes do master ✅ RESOLVIDO

`Info.extra_agents` (`info.py:744-763`):

```python
def extra_agents(self, user: str) -> Any:
    # POST /info  {"type":"extraAgents","user": user}
    # Returns: [ {"name": str, "address": str, "validUntil": int}, ... ]
    return self.post("/info", {"type": "extraAgents", "user": user})
```

Serve de base para (a) o health-check BD⇄on-chain (P4) e (b) obter `validUntil` (V5).
`validUntil` é epoch (int) — a UI precisa formatar. Consultar sempre pelo **master
address** (não pelo agent).

## V4 — Reaprovar named agent com o mesmo nome ⏳ PENDENTE (validação viva)

D4 depende de que reaprovar `engine_gateway` **substitua** a chave sem consumir um dos
3 slots named. O SDK não garante isso no código — é comportamento do lado HL.
**Ação P0 (testnet):** aprovar `engine_gateway`, checar `extra_agents`; reaprovar
`engine_gateway` com novo keypair; confirmar que continua 1 named agent com esse nome
(endereço novo) e que o slot não dobrou. **Requer MetaMask + master na testnet.**

## V5 — `valid_until` de named agents ⏳ PENDENTE (validação viva)

O `validUntil` vem de `extra_agents` (V3). Falta confirmar na HL testnet: se a
aprovação **exige/aceita** expiração explícita, qual o **máximo vigente**, e se named
agents expiram de fato (ADR 0001 não fixa). Alimenta o banner de validade e o
`expiry_scan` (F4). **Ação P0:** ler `validUntil` retornado após o V4 e registrar aqui.

## V6 — Compat wagmi@2 / viem@2 / react-query com Next 15.1 + React 19 ✅ RESOLVIDO (build real)

Fatos de código:
- `web/package.json`: Next **15.1.0**, React **19.0.0**, npm; **sem** wagmi/viem/siwe/
  react-query hoje.
- `web/Dockerfile`: 3 estágios, todos `FROM node:22-alpine`; instala com
  `npm install --no-audit --no-fund` (deps stage). Não usa `--legacy-peer-deps` hoje.

**Resultado do build real (2026-07-09):** `npm install wagmi@^2 viem@^2
@tanstack/react-query@^5` resolveu **sem ERESOLVE** e **sem** `--legacy-peer-deps`
(448 pacotes; só warnings de deprecação). `npm run build` **PASSOU** — todas as
rotas geradas, standalone ok.

**Desvio vs plano original (D4):** o pacote **`siwe`** foi **descartado**. `siwe@3`
faz peer-dep de `ethers`; puxá-lo para um stack baseado em viem é redundante. Usamos
o módulo nativo **`viem/siwe`** (`generateSiweNonce`/`createSiweMessage`/`parseSiweMessage`)
+ `verifyMessage` do viem. Deps finais: `wagmi@^2.19`, `viem@^2.55`,
`@tanstack/react-query@^5.101`.

**Ressalva:** o build rodou em macOS (darwin), não em `node:22-alpine`. O build Docker
no deploy deve reconfirmar (risco baixo — variantes musl do `sharp` existem). Sem
`--legacy-peer-deps` no Dockerfile.

## V7 — Propagação de `TOKIO_KEYRING_SECRET` + backup sem `.env` ✅ RESOLVIDO (c/ passo operacional)

Fatos de código:
- **systemd** injeta o env do `.env`: `deploy/systemd/tokio.service:14`
  `EnvironmentFile=/home/tokio/Tokio/.env`. Logo `TOKIO_KEYRING_SECRET` posto no `.env`
  chega ao processo do engine. (Web/compose: `deploy/autodeploy.sh:44` faz
  `set -a && . ../.env && set +a`.)
- **Backup NÃO inclui o `.env`:** `deploy/backup_sqlite.sh` só arquiva `data/tokio.db`
  (`sqlite3 .backup` → gzip → offsite, linhas 119-125). Ele **lê** o `.env` (linha 15)
  apenas para pegar `BACKUP_REMOTE` etc., mas nunca copia o arquivo.

**Propriedade de segurança confirmada:** o backup offsite carrega o SQLite com
`hl_agents.privkey_enc` **cifrado**; o segredo de decifra (`TOKIO_KEYRING_SECRET`) vive
só no `.env`/systemd, que **não** vai no backup. Dump vazado ⇒ chaves ilegíveis. ✅

**Passo operacional (P3, via Hermes):** adicionar `TOKIO_KEYRING_SECRET` ao `.env` da
VPS (e avaliar autogeração em `deploy/bootstrap_vps.sh`, que já autogera tokens —
linha 132). Sem isso o keyring não decifra no boot (mas há fallback `.env` — ver V9).

## V8 — Rede interna web→gateway:8700 para `/control/hl/*` ✅ RESOLVIDO (baixo risco)

O proxy `web/app/api/control/[...path]/route.ts` já fala com `http://gateway:8700`
(compose) / `127.0.0.1:8700` (VPS) para os POSTs existentes (`/control/strategy/*`,
`/control/trader/*`). Os novos `/control/hl/*` usam **o mesmo host:porta e o mesmo
header `X-Control-Token`** — nenhuma rota nova no Caddy, nenhuma porta nova. Só é
preciso estender a **allowlist do proxy** (edição já mapeada na spec §6.2). Confirmação
viva no smoke test do P2.

## V9 — INVARIANTE Hermes: ordem nunca bloqueada + restart reconstrói signer ⏳ PENDENTE (validação viva no P2)

Requisito rtg003: ordens do Hermes/runners (`POST /intent`/`/cancel`) **não podem
ganhar nenhum impedimento**. Fatos de código que sustentam a invariante:
- `/intent` e `/cancel` (`server.py:408-412`) **não** passam por `_control_auth` nem
  pelo cookie da web; usam `_adapter_for(env)` e o adapter assina. SIWE/keyring **não
  tocam esse caminho**.
- Mainnet sem adapter já é recusada hoje (`server.py:502`) — o keyring não cria atalho
  para burlar o gate humano.

Travas de continuidade a implementar (P2/P3) e **provar vivo**:
1. **Precedência com fallback:** `resolve_active_key(env)` = keyring `active` → senão
   `.env` plaintext. Nunca fica sem signer durante a transição.
2. **Startup guard:** o gateway valida que há adapter vivo para cada ambiente
   configurado antes de aceitar intents.
3. **Restart:** `systemctl restart` com `TOKIO_KEYRING_SECRET` presente reconstrói
   `adapters[env]` a partir do keyring; se o keyring falhar, degrada para `.env`
   (não para "sem execução"). **Ação:** teste de restart no P2 antes de remover as
   chaves do `.env` (P3).

---

## Resumo do portão P0

| V | Tema | Status | Bloqueia |
|---|---|---|---|
| V1 | typed data `approveAgent` | ✅ RESOLVIDO | — |
| V2 | `signatureChainId` | 🟡 c/ ressalva (usar `0x66eee`+`hyperliquidChain`) | Mainnet (P3) |
| V3 | `extra_agents` | ✅ RESOLVIDO | — |
| V4 | re-aprovar mesmo nome | ⏳ testnet + MetaMask | D4 / rotação (P2/P4) |
| V5 | `valid_until` | ⏳ testnet | banner/expiry (P4) |
| V6 | build wagmi/viem (+react-query) | ✅ RESOLVIDO (build real; `siwe`→`viem/siwe`) | — |
| V7 | secret no deploy + backup sem `.env` | ✅ RESOLVIDO (+passo op.) | — |
| V8 | rede web→gateway | ✅ RESOLVIDO | — |
| V9 | invariante Hermes | ⏳ smoke test P2 | P3 (limpeza `.env`) |

**Resolvidos (6/9):** V1, V3, V6 (build real), V7, V8 e — com a ressalva de confirmar
na testnet antes de mainnet — V2. **Exigem validação viva (3/9):** V4 e V5 (testnet +
MetaMask), V9 (smoke test no P2). Nenhum dos pendentes bloqueia P1 (SIWE, já
implementado) nem começar P2.

**Decisão pedida ao rtg003 (portão):**
1. Aprovar seguir para P1 com os 5 itens resolvidos.
2. Confirmar a recomendação do V2 (opção (a): `signatureChainId=0x66eee` + variar só
   `hyperliquidChain`) ou preferir a opção (b) do D6 (chain-switch `0xa4b1`/`0x66eee`).
3. Ciente de que V4/V5/V6/V9 fecham durante P0→P2 com testnet/build reais (precisam de
   MetaMask + master na testnet quando chegar a hora).
