# ADR 0001 — Agent wallets por papel, nunca por estratégia

- Status: aceito
- Data: 2026-07-02

## Contexto

A Hyperliquid limita agent wallets (API wallets) a **1 unnamed + 3 named por
conta master**, com +2 named por subaccount. Agent wallets apenas assinam: não
isolam capital (posições netam na conta), não multiplicam o rate limit por
endereço (que é da conta master) e não servem para queries (queries usam o
endereço da conta real).

Nonces são armazenados **por signatário** (100 maiores), com janela
`(T - 2 dias, T + 1 dia)`. Endereços de agents desregistradas têm o nonce
podado — reutilizá-los permite replay.

## Decisão

Agent wallets por **papel**:

| Wallet | Papel |
|---|---|
| `engine_gateway` | Único signatário do engine. Contador atômico de nonce no processo do gateway. |
| `hermes_ops` | Operações do Hermes Agent, registrada com expiração (`valid_until`). Nunca usada pelo engine. |

Runners jamais assinam transações; enviam intents ao gateway. Endereços de
agents desregistradas nunca são reutilizados (gerar wallet nova sempre).

## Consequências

- Um único ponto de serialização de nonce → sem corrida entre processos.
- A atribuição por estratégia é feita via `cloid` + ledger virtual (ADR 0002),
  não via wallet.
- Se no futuro houver signatários paralelos por subaccount: 1 API wallet por
  subaccount (recomendação oficial de nonce).
