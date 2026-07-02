# Template obrigatório de `strategy.md`

Toda estratégia (qualquer módulo) descreve sua lógica de decisão em um
`strategy.md` próprio, isolado na pasta da estratégia. Parâmetros de
estratégias diferentes NUNCA se misturam no mesmo arquivo.

```markdown
# <id da estratégia>

- id: <prefixo_do_modulo>_<nome_curto>   # ct_whale01, tv_gap_fade, sa_dca_btc
- module: copy_trade | tradingview | standalone
- status: draft | dry_run | active | paused | auto_paused | archived
- hipótese: <que ineficiência de mercado esta estratégia explora>
- edge esperado: <expectância estimada por trade, líquida de taxas, e base da estimativa>
- parâmetros-chave: <tabela ou lista dos parâmetros que definem o comportamento>
- thresholds: <critérios de auto-pausa: min_net_pnl, min_win_rate, eval_window_days, min_trades>

## Regras de decisão

<entradas, saídas, sizing, SL/TP — determinístico e completo>

## Changelog de decisões

- YYYY-MM-DD: <mudança e justificativa numérica>
```

Convenção de nomes: `<prefixo_do_modulo>_<nome_curto>`. A descoberta é sempre
dinâmica via `strategy list` — nenhum documento mantém índice estático.
