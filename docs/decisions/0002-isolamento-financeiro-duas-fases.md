# ADR 0002 — Isolamento financeiro em duas fases (ledger virtual → subaccounts)

- Status: aceito
- Data: 2026-07-02

## Contexto

Subaccounts na Hyperliquid só desbloqueiam após **US$ 100k de volume
acumulado** (10 iniciais; +1 por US$ 100M adicionais; máximo 50). O teto não
comporta dezenas de estratégias. Na corretora, o netting de posições é por
ativo — duas estratégias no mesmo símbolo compartilham a posição real.

## Decisão

- **Fase A (dia 1)**: o `ledger.py` do gateway mantém posição virtual, capital
  alocado e PnL **por estratégia**, com atribuição de fills via `cloid`.
  É a única fonte de atribuição de capital/PnL por estratégia.
- **Fase B (pós-desbloqueio)**: subaccounts por **bucket de risco/módulo**
  (não por estratégia), assinadas pelo gateway via campo `vaultAddress`.
  O ledger continua fazendo a atribuição fina por estratégia dentro de cada
  bucket.
- O `ExchangeAdapter` e o config de estratégia nascem com
  `subaccount_address: Optional[str]` para a Fase B não exigir refatoração.
- Estratégias em direções opostas no mesmo símbolo: o ledger detecta e emite
  alerta; a política default é permitir (as posições virtuais permanecem
  corretas; o netting real reduz margem usada), com opção de bloqueio por
  config global.

## Consequências

- PnL por estratégia sempre atribuível desde o primeiro trade.
- Migração para subaccounts é aditiva (preencher `subaccount_address`).
