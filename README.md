# Tokio — sistema de automação de trades

Sistema de automação de trades em corretoras cripto (Hyperliquid, testnet por
default) com duas camadas:

1. **ENGINE** — processos Python determinísticos 24/7: um **gateway de
   execução** (único processo que fala com a corretora) + **um runner por
   estratégia** (copy trade, TradingView via webhook, standalone). Nenhum LLM
   participa da decisão trade-a-trade em produção.
2. **SKILL do Hermes** (`skill/`) — runbook no padrão agentskills.io que
   ensina o Hermes Agent a operar, monitorar e manter o sistema.

Objetivo único: **gerar lucro consistente** — sempre medido líquido de taxas e
slippage, com expectância positiva comprovada e drawdown controlado.

Documentos principais: [`PLAN.md`](PLAN.md) · ADRs em
[`docs/decisions/`](docs/decisions/) · handoff operacional em
[`docs/HANDOFF_HERMES.md`](docs/HANDOFF_HERMES.md) (Fase 8).

> **Agentes (Cursor ⇄ Hermes)**: este repo é trabalhado por dois agentes em
> paralelo. O protocolo bilateral de coordenação — inboxes
> `docs/HERMES_UPDATES.md`/`docs/CURSOR_UPDATES.md`, ritual pré-alteração,
> regra do mesmo PR e desempate de área — está em [`AGENTS.md`](AGENTS.md)
> (ADR 0009) e é de leitura/execução OBRIGATÓRIA no início de toda sessão.

## Arquitetura (resumo)

```
runners (1 processo por estratégia)
  copy_trade / tradingview / standalone
        │ intents (IPC interno)
        ▼
gateway ── risk_enforcer (caps, circuit breaker, kill switch)
        ── ledger (posição virtual por estratégia via cloid)
        ── ExchangeAdapter ──► Hyperliquid (SDK oficial, testnet default)
        │
SQLite + JSONL (fonte de verdade local)
        │ replicação assíncrona em lote
        ▼
Supabase ──► web (Next.js · tokio.bz)
```

Regras inegociáveis:

- Runners **nunca** falam direto com a corretora; o banco **nunca** é
  barramento de ordens.
- O gateway é o **único signatário** (agent wallet `engine_gateway`).
- Local-first: outage do Supabase não para o engine (fila local + retry).
- Nenhuma estratégia sai de `dry_run` sem evidência de expectância positiva
  líquida de taxas registrada em `docs/`.

## Setup (desenvolvimento)

Requisitos: Python 3.11+, Node 20+ (web), Docker + Compose (produção).

```bash
# engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# variáveis de ambiente (NUNCA commitar o .env real)
cp .env.example .env   # preencher fora de sessões de agente; chmod 600 .env

# banco local (SQLite) — migrations
python -m engine.cli db migrate

# testes
pytest
```

## Comandos operacionais (CLI)

```bash
python -m engine.cli strategy list             # fonte da verdade dinâmica (lê do banco)
python -m engine.cli strategy archive <nome>   # descarte limpo (histórico permanece no banco)
python -m engine.cli report --daily            # relatório por exceção (portfólio)
python -m engine.cli report --strategy <nome>  # detalhe por estratégia
python -m engine.cli kill                      # kill switch (cria arquivo sentinela KILL)
python -m engine.cli db migrate                # aplica migrations no SQLite local
```

## Serviços (docker compose)

| Serviço | Papel |
|---|---|
| `gateway` | Único processo dono da corretora (risco global + ledger + adapter) |
| `runner-*` | Um por estratégia/módulo (copy trade, tradingview, standalone) |
| `replicator` | Replicação assíncrona SQLite → Supabase |
| `web` | Dashboard Next.js (lê Supabase; controla via API interna do gateway) |
| `proxy` | Caddy com TLS automático servindo `tokio.bz` → web (produção) |

Gateway, runners e replicador ficam apenas na rede interna do Docker — nenhuma
porta do engine é publicada no host.

## Estrutura do repositório

Ver [`PLAN.md`](PLAN.md) e o prompt de build. Pastas principais: `engine/`
(gateway, core, exchanges, strategies), `db/migrations/`, `skill/`, `web/`,
`docs/`, `tests/`, `config/`.

## Ferramentas de análise (CLI)

```bash
# discovery de traders p/ copy trade (relatório ranqueado JSON + markdown)
python -m engine.strategies.copy_trade.discovery --top 10

# scanner 24/7: gap do CME, anomalias de funding, baixa liquidez
python -m engine.strategies.tradingview.scanner

# backtest local com candles históricos da Hyperliquid (métricas líquidas)
python -m engine.strategies.tradingview.backtest.harness --symbol BTC --interval 4h --days 90
```

## Deploy (produção)

Produção roda na VPS compartilhada via **systemd + supervisor** (ADR 0007):
`tokio.service` (web em 127.0.0.1:3002) + `tokio-engine.service`
(`engine/supervisor.py` mantendo gateway/replicator/runners como processos
isolados), atrás do Caddy compartilhado servindo `https://tokio.bz`. Deploy
contínuo por GitHub Actions no push em `main`
([.github/workflows/deploy-vps.yml](.github/workflows/deploy-vps.yml)).
Docker Compose (`make up`/`make deploy`) fica para dev local ou VPS dedicada.
Procedimento completo, DNS, gates e troubleshooting:
[`docs/HANDOFF_HERMES.md`](docs/HANDOFF_HERMES.md).

## Segurança

- `.env` está no `.gitignore` desde o commit 0; nenhum secret é commitado,
  impresso ou logado — em nenhum nível.
- `service_role` do Supabase existe apenas nos containers do engine; o web usa
  anon key com RLS ligado em todas as tabelas.
- Mainnet, aumento de caps e ativação de estratégias são **gates humanos**
  (ver `docs/HANDOFF_HERMES.md`).
