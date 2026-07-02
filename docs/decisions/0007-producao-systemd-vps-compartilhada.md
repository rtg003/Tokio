# ADR 0007 — Produção via systemd na VPS compartilhada (sem Docker)

- Status: aceito
- Data: 2026-07-02
- Substitui parcialmente: uso do docker compose em PRODUÇÃO (segue em dev)

## Contexto

A VPS de produção (Hostinger Vilnius, `46.202.189.126`) é compartilhada com o
Luthor (bot com dinheiro real) e opera sob regras de isolamento estritas:
um usuário Linux por app, sudoers mínimo (`systemctl restart/status` dos
services do próprio app), reverse proxy único (Caddy compartilhado) e bind
exclusivamente em `127.0.0.1`.

Rodar Docker convencional exigiria colocar o usuário `tokio` no grupo
`docker`, que é equivalente a root — um container poderia montar
`/home/luthor` e ler segredos. Isso violaria as regras da VPS.

## Decisão

- Em produção, o Tokio roda como **2 units systemd**:
  - `tokio.service` — web Next.js (standalone) em `127.0.0.1:3002`;
  - `tokio-engine.service` — `engine/supervisor.py`, que mantém gateway,
    replicator e cada runner como **processos separados** com restart e
    backoff individuais (o isolamento por processo do design original é
    preservado; muda apenas o supervisor: systemd+supervisor em vez de
    compose).
- Lista de processos em `deploy/engine-processes.yaml`; binds internos
  forçados a `127.0.0.1` (`GATEWAY_BIND`, `TV_WEBHOOK_BIND`).
- Exposição pública apenas via bloco no Caddy compartilhado
  (`deploy/Caddyfile`), aplicado com `caddy reload` — nunca restart, para não
  derrubar os vhosts do Luthor.
- Deploy contínuo por GitHub Actions (`.github/workflows/deploy-vps.yml`):
  push em `main` → `git pull` → build (venv + `next build`) → restart dos
  dois services via sudoers mínimo.
- Docker Compose (`docker-compose*.yml`) permanece como ambiente de
  desenvolvimento/teste local e para eventual VPS dedicada futura.
- Webhook do TradingView: sem rota pública por enquanto; o runner TV roda
  interno em `127.0.0.1:8701` (rota no Caddy documentada para quando ativar).

## Consequências

- O sudoers do usuário `tokio` precisa incluir os DOIS services
  (`tokio.service` e `tokio-engine.service`).
- `make deploy` (compose) não se aplica à VPS compartilhada; o caminho de
  produção é o workflow de GHA. Documentado no HANDOFF.
- Limites de recursos passam de compose para systemd (`MemoryMax`,
  `CPUQuota` nas units).
