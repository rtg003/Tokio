---
name: trade
description: Opera o engine de trades Tokio (gateway + runners) 24/7
---

# trade — operação do engine Tokio

**Objetivo principal e único: gerar lucro consistente**, sempre líquido de
taxas e slippage. Guardrails de risco e gates de aprovação são a CONDIÇÃO do
lucro, nunca concorrentes dele: em conflito aparente, o guardrail vence e o
humano é notificado. Jamais aumente caps, remova stops, pule dry-run ou migre
para mainnet por conta própria.

## Quando usar

Use esta skill para operar, monitorar e manter o sistema de trades do repo
`rtg003/Tokio`: iniciar/parar serviços, verificar saúde, gerar relatórios,
pausar/ativar/arquivar estratégias e responder a incidentes.

## Arquitetura em 30 segundos

- `gateway`: ÚNICO processo que fala com a corretora (Hyperliquid, testnet
  default). Aplica risco global, ledger por estratégia e kill switch.
- 1 runner por estratégia (processos isolados): `copy_trade`, `tradingview`
  (webhook), `standalone/*`. Runners enviam intents ao gateway — nunca à
  corretora.
- SQLite/JSONL locais são a fonte de verdade; Supabase é réplica assíncrona
  para dashboards (`tokio.bz`). Outage do Supabase NÃO para o engine.

## Descoberta dinâmica de estratégias (NUNCA use índice estático)

A fonte da verdade é o banco. Para saber o que existe e em que estado:

```bash
# ${HERMES_SKILL_DIR} aponta para <repo>/skill — a raiz do repo é um nível acima
cd ${HERMES_SKILL_DIR}/.. && python -m engine.cli strategy list
```

Estados: `draft → dry_run → active → paused/auto_paused → archived`.
`dry_run` é o default de tudo que é novo — sem exceção.

## Operação dos serviços

```bash
docker compose up -d                     # sobe tudo
docker compose ps                        # status
docker compose logs -f gateway           # logs de um serviço
docker compose restart runner-copytrade  # reinicia um runner (não afeta os demais)
docker compose stop runner-<nome>        # para um runner específico
curl -s http://gateway:8700/health       # health do gateway (rede interna)
```

Autenticação: segredos vêm do `.env` (permissão 600, preenchido pelo humano).
Valide apenas a PRESENÇA das variáveis — NUNCA imprima, ecoe ou logue valores.

## Procedimento ÚNICO para operar qualquer estratégia

O contrato é uniforme (`base_runner`): o mesmo procedimento serve para todas.

1. `python -m engine.cli strategy list` — confirme id e estado.
2. Leia o `strategy.md` da estratégia (lógica de decisão isolada por pasta).
3. Métricas: `python -m engine.cli report --strategy <id>`.
4. Pausar/ativar: API de controle do gateway (`POST /control/strategy/<id>/pause`
   ou `/activate`, header `X-Control-Token`). Ativar só funciona de
   `paused/auto_paused`; promover de `dry_run` a `active` é gate humano com
   evidência de expectância positiva líquida registrada em `docs/`.
5. Arquivar: `python -m engine.cli strategy archive <id> --yes`
   (cancela ordens, marca `archived`, move a pasta; histórico fica no banco).
   Depois escreva o post-mortem em `docs/post_mortems/<id>.md` e agregue a
   lição em `skill/references/lessons.md` via PR.

## Relatórios e emergência

```bash
python -m engine.cli report --daily      # por exceção: agregado + violações
python -m engine.cli kill --reason "..." # KILL switch global (arquivo sentinela)
python -m engine.cli unkill              # remove após resolver o incidente
```

Circuit breaker: perda diária no cap pausa TODAS as estratégias
automaticamente e notifica. Investigue antes de reativar.

## Pitfalls

- Queries na Hyperliquid usam o endereço da CONTA, não o da agent wallet
  (agent só assina; consultar agent devolve vazio).
- Ordens < US$ 10 notional são rejeitadas pela corretora (o engine pula e loga).
- Nunca reutilize endereços de agent wallets desregistradas (replay de nonce).
- Dashboards leem `strategy_metrics_daily` — nunca calcule métricas varrendo
  `events`.
- Mainnet é gate humano fora do seu alcance; o toggle da web é só leitura.

## Verificação (após qualquer intervenção)

1. `docker compose ps` — todos os serviços `running`.
2. `curl http://gateway:8700/health` — `ok: true`, `kill_switch: false`,
   `replication_lag_s` baixo (< 60s com Supabase configurado).
3. `python -m engine.cli strategy list` — estados esperados.
4. Logs sem erros novos: `docker compose logs --since 10m gateway | grep -i error`.

Referências: `references/strategy_md_template.md` (template obrigatório de
`strategy.md`) e `references/lessons.md` (lições agregadas de post-mortems).
