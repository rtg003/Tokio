# sa_dca_btc

- id: sa_dca_btc
- module: standalone
- status: dry_run (TEMPLATE — nunca ativar; existe para documentar o padrão)
- hipótese: nenhuma — DCA simples é o exemplo mínimo do contrato de uma
  estratégia standalone (lifecycle, intents ao gateway, heartbeat, thresholds
  herdados de `base_runner`).
- edge esperado: n/a (template). Uma standalone real precisa registrar
  hipótese + evidência de expectância positiva líquida antes de sair de dry_run.
- parâmetros-chave: `notional_usd` por compra, `interval_hours`,
  `max_exposure_usd` (cap aplicado pelo gateway).
- thresholds: exemplo de configuração de auto-pausa em `config.yaml`.

## Regras de decisão

1. A cada `interval_hours`, envia intent de compra market de `notional_usd`
   em `symbol` — via gateway, como toda estratégia.
2. Para de acumular quando o cap `max_exposure_usd` é atingido (o gateway
   rejeita e o runner loga a decisão).
3. Kill switch, auto-pausa por thresholds e archive funcionam sem código
   extra — vêm de `base_runner`.

## Como criar uma standalone nova (checklist do template)

1. Copie esta pasta para `standalone/<sa_nome>/` e renomeie o id.
2. Escreva o `strategy.md` (template em `skill/references/strategy_md_template.md`).
3. Implemente `on_cycle()` no runner herdando `BaseRunner`.
4. Adicione o serviço no `docker-compose.yml` (1 processo por estratégia,
   com limites de CPU/mem).
5. Rode em dry-run; só proponha ativação com evidência de expectância
   positiva líquida de taxas registrada em `docs/`.

## Changelog de decisões

- 2026-07-02: criação como template documentado (dry-run permanente).
