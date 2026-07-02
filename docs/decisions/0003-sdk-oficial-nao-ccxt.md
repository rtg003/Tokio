# ADR 0003 — Hyperliquid v1 usa o SDK oficial, não CCXT

- Status: aceito
- Data: 2026-07-02

## Contexto

O copy trade depende de **subscrições WebSocket a fills de endereços de
terceiros** (`userFills` para endereço arbitrário), que estão fora da API
unificada do CCXT. O SDK oficial `hyperliquid-python-sdk` expõe subscrições
WS, `cloid`, agent wallets e assinatura EIP-712.

## Decisão

- `engine/exchanges/hyperliquid/adapter.py` implementa o `ExchangeAdapter`
  (ABC em `engine/exchanges/base.py`) usando o SDK oficial, com testnet como
  default (`https://api.hyperliquid-testnet.xyz`).
- Corretoras futuras podem usar CCXT atrás do mesmo adapter (ex.:
  `exchanges/ccxt_generic/`) — a interface `ExchangeAdapter` é a fronteira.

## Consequências

- Todo o engine fala apenas com `ExchangeAdapter`; a troca de corretora é
  configuração (`config/settings.yaml`), não refatoração.
