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

## Ambiente de produção (VPS)

Produção roda em **systemd + supervisor**, sem Docker (ADR 0007). Docker
Compose é só desenvolvimento local. Usuário `tokio`, repo em
`/home/tokio/Tokio`, venv em `.venv/`.

**Variável de ambiente**: `HERMES_SKILL_DIR` aponta para o symlink
`~/.hermes/skills/trade`, que por sua vez aponta para `<repo>/skill`. A
raiz do repo é um nível acima: `cd ${HERMES_SKILL_DIR}/..`.

## Descoberta dinâmica de estratégias (NUNCA use índice estático)

A fonte da verdade é o banco. Para saber o que existe e em que estado:

```bash
cd ${HERMES_SKILL_DIR}/.. && .venv/bin/python -m engine.cli strategy list
```

Estados: `draft → dry_run → active → paused/auto_paused → archived`.
`dry_run` é o default de tudo que é novo — sem exceção.

## Operação dos serviços (systemd — produção)

```bash
# Engine (gateway + runners + replicator)
sudo -n systemctl restart tokio-engine.service   # restart tudo
sudo -n systemctl status tokio-engine.service     # status
journalctl -u tokio-engine.service -f --no-pager # logs (engine)
tail -f /home/tokio/Tokio/logs/gateway-*.jsonl   # logs JSONL por processo

# Web (Next.js dashboard)
sudo -n systemctl restart tokio.service
sudo -n systemctl status tokio.service

# Health do gateway
curl -s http://127.0.0.1:8700/health

# Processo individual (habilitar/desabilitar)
# Editar deploy/engine-processes.yaml (enabled:) + restart do engine
```

Autenticação: segredos vêm do `.env` (permissão 600, owner tokio,
preenchido pelo humano). Valide apenas a PRESENÇA das variáveis — NUNCA
imprima, ecoe ou logue valores.

## Procedimento ÚNICO para operar qualquer estratégia

O contrato é uniforme (`base_runner`): o mesmo procedimento serve para todas.

1. `python -m engine.cli strategy list` — confirme id e estado.
2. Leia o `strategy.md` da estratégia (lógica de decisão isolada por pasta).
3. Métricas: `python -m engine.cli report --strategy <id>`.
4. Pausar/reativar: API de controle do gateway (`POST /control/strategy/<id>/pause`
   ou `/activate`, header `X-Control-Token`) — `/activate` só funciona de
   `paused/auto_paused`. Promover de `dry_run` a `active` é GATE HUMANO:
   `python -m engine.cli strategy activate <id> --evidence docs/<arquivo>`
   (exige a evidência de expectância positiva líquida; execute somente após
   confirmação humana explícita no turno).
5. Arquivar: `python -m engine.cli strategy archive <id> --yes`
   (cancela ordens, marca `archived`, move a pasta; histórico fica no banco).
   Depois escreva o post-mortem em `docs/post_mortems/<id>.md` e agregue a
   lição em `skill/references/lessons.md` via PR.

## Ordem de teste (testnet)

Para validar o ciclo completo via gateway `/intent`:

```bash
TOKEN=*** "^GATEWAY_CONTROL_TOKEN=*** .env | cut -d= -f2)
curl -s -X POST http://127.0.0.1:8700/intent \
  -H "Content-Type: application/json" \
  -H "X-Control-Token: $TOKEN" \
  -d '{"strategy_id":"tv_gap_fade","symbol":"BTC","side":"buy","size":0.002,"order_type":"market"}'
```

Usar `size` (base units), NUNCA `notional_usd` — ver pitfall float_to_wire abaixo.
Confirmar nos logs: `order.result` → `fill.recorded` com cloid, price, fee.

## Relatórios e emergência

```bash
python -m engine.cli report --daily      # por exceção: agregado + violações
python -m engine.cli kill --reason "..." # KILL switch global (cancela ordens abertas)
python -m engine.cli unkill              # remove após resolver o incidente
```

## Traders de copy trade (tabela única — ADR 0008)

`python -m engine.cli trader list` — candidatos + copiados, por score.
Ciclo: SUGERIDO → DRY_RUN → COPIANDO (Gate 2, humano) · PAUSADO/REJEITADO.
Aprovar (SÓ com confirmação humana no turno):
`trader approve <address>` (dry-run) · `trader approve <address> --live
--evidence docs/<arquivo>` (dinheiro real no espelhamento). Pausar/retomar/
rejeitar: API de controle do gateway (`POST /control/trader/<addr>/status`).
Toda mudança fica logada em `events` (`trader.*`).

Circuit breaker: perda diária no cap pausa TODAS as estratégias
automaticamente e notifica. Investigue antes de reativar.

## Pitfalls

- Queries na Hyperliquid usam o endereço da CONTA, não o da agent wallet
  (agent só assina; consultar agent devolve vazio).
- Ordens < US$ 10 notional são rejeitadas pela corretora (o engine pula e loga).
- **float_to_wire rounding**: se usar `notional_usd`, o engine calcula size =
  notional/price que pode ter mais casas decimais que o `szDecimals` do ativo
  (ex: BTC = 5). A HL rejeita com `('float_to_wire causes rounding', ...)`.
  Solução: passar `size` direto (ex: 0.002 BTC), nunca `notional_usd`.
- **Agent wallet vs master account**: a agent wallet deriva da
  `HL_AGENT_PRIVATE_KEY` e deve estar registrada (`approveAgent`) na master
  account (`HL_ACCOUNT_ADDRESS`). Se a HL retornar "User or API Wallet does
  not exist", a agent não está registrada — registrar via UI da testnet ou SDK.
- **Saldo spot vs perp**: depósito na HL vai para a spot wallet primeiro.
  Transferir para a perp account (L2) antes de operar — senão
  `accountValue: 0.0` mesmo com saldo na spot.
- **Testnet vs mainnet agent wallets**: podem ser endereços diferentes. A chave
  no `.env` deve corresponder à agent wallet da REDE correta (testnet ou
  mainnet). Conferir derivando o endereço da chave com `eth_account.Account.from_key`.
- Nunca reutilize endereços de agent wallets desregistradas (replay de nonce).
- Dashboards leem `strategy_metrics_daily` — nunca calcule métricas varrendo
  `events`.
- Mainnet é gate humano fora do seu alcance; o toggle da web é só leitura.
- Sudoers do `tokio` limitado a `systemctl restart/status` de
  `tokio-engine.service` e `tokio.service` apenas.
- Caddy admin API desligada nesta VPS — mudança de vhost exige `caddy validate`
  + `systemctl restart caddy` (não reload).

## Verificação (após qualquer intervenção)

1. `sudo -n systemctl status tokio-engine.service` e `tokio.service` = active (running).
2. `curl -s http://127.0.0.1:8700/health` — `ok: true`, `kill_switch: false`,
   `replication_lag_s` baixo (< 60s com Supabase configurado).
3. `python -m engine.cli strategy list` — estados esperados.
4. Logs sem erros novos: `tail -20 /home/tokio/Tokio/logs/gateway-*.jsonl | grep -i error`.

Referências: `references/strategy_md_template.md` (template obrigatório de
`strategy.md`), `references/lessons.md` (lições agregadas de post-mortems) e
`references/hyperliquid_ops.md` (receitas de operação da HL: saldo, agent
wallet, ordens de teste).
