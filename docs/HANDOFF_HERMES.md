# HANDOFF — operação do Tokio pelo Hermes Agent

> Contrato de passagem do CONSTRUTOR (agente de build) para o OPERADOR
> (Hermes). Produção roda na **VPS compartilhada** (Hostinger Vilnius,
> `46.202.189.126`), que também hospeda o **Luthor** (bot com dinheiro real).
> Modelo de produção: **systemd + supervisor**, sem Docker (ADR 0007).
> Docker Compose é só desenvolvimento local.

## 0. Regras de isolamento da VPS (invioláveis)

- NUNCA ler/tocar `/home/luthor` nem segredos do Luthor (`LUTHOR_*`,
  `POLYMARKET_*`, `POLY_BUILDER_*`, wallet, DATABASE_URL dele).
- NUNCA reiniciar/parar `luthor.service`, `dash-lbx`, nem dar restart no
  Caddy — mudanças de proxy só com `sudo caddy validate` + `systemctl reload caddy`.
- O usuário `tokio` só tem sudo para `systemctl restart/status` de
  `tokio.service` e `tokio-engine.service`. Se algo pedir mais que isso,
  PARE e acione o operador (rtg003).
- Apps bindam SOMENTE em `127.0.0.1` (web: 3002; gateway: 8700; TV: 8701).
  Exposição pública é exclusiva do Caddy compartilhado.
- Segredos do Tokio: apenas `/home/tokio/Tokio/.env` (chmod 600, owner tokio).

## 1. Setup na VPS (PARTE A — operador com sudo, uma vez)

**Caminho rápido (recomendado)** — o repo é PRIVADO, então o primeiro acesso
usa uma deploy key read-only. Três blocos, como rtg003 na VPS:

```bash
# (1) criar usuário + deploy key do repo e IMPRIMIR a chave pública
sudo adduser --disabled-password --gecos "" --home /home/tokio tokio 2>/dev/null || true
sudo -u tokio mkdir -p /home/tokio/.ssh
sudo -u tokio ssh-keygen -t ed25519 -f /home/tokio/.ssh/gh_repo_deploy -N "" -C "tokio-repo-deploy"
sudo cat /home/tokio/.ssh/gh_repo_deploy.pub
```

Adicionar a chave pública impressa em: github.com/rtg003/Tokio → Settings →
**Deploy keys** → Add deploy key → título `vps-tokio` → **sem** write access.

```bash
# (2) clonar e (3) rodar o bootstrap idempotente
sudo -u tokio env GIT_SSH_COMMAND='ssh -i /home/tokio/.ssh/gh_repo_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' \
  git clone git@github.com:rtg003/Tokio.git /home/tokio/Tokio
sudo bash /home/tokio/Tokio/deploy/bootstrap_vps.sh
```

O script faz o resto (sudoers, runtimes, units, build, Caddy com
validate+reload, tokens autogerados, validação) e, faltando credenciais no
`.env`, avisa e não sobe o engine — preencha e rode de novo. Ao final imprime
a private key de deploy do GitHub Actions — copie para o secret
`VPS_SSH_KEY` do repo (Settings → Secrets → Actions).

Detalhe do que o script cria (ou faça manualmente, se preferir):

1. Usuário `tokio` (home 700), no grupo `deployers`; `/home/luthor` com 750.
2. Chave dedicada `gh_actions_deploy` no `authorized_keys` do tokio; a
   private key vai para o secret `VPS_SSH_KEY` do repo (Settings → Secrets →
   Actions).
3. Sudoers (`/etc/sudoers.d/tokio`) — **os dois services**:

```
tokio ALL=(root) NOPASSWD: /usr/bin/systemctl restart tokio.service, /usr/bin/systemctl status tokio.service, /usr/bin/systemctl restart tokio-engine.service, /usr/bin/systemctl status tokio-engine.service
```

4. Repo clonado em `/home/tokio/Tokio`; Node LTS via nvm no home do tokio;
   `python3 -m venv .venv && .venv/bin/pip install -e .`.
5. Units instaladas a partir dos templates do repo:
   `deploy/systemd/tokio.service` (web, 127.0.0.1:3002) e
   `deploy/systemd/tokio-engine.service` (supervisor do engine) →
   `/etc/systemd/system/` + `daemon-reload` + `enable`.
6. Bloco do Tokio ACRESCENTADO ao Caddyfile compartilhado (conteúdo em
   `deploy/Caddyfile`) → `sudo caddy validate` → `sudo systemctl reload caddy`.
7. `.env` preenchido pelo humano em `/home/tokio/Tokio/.env` (chmod 600),
   fora de sessões de agente. **Na VPS use `GATEWAY_HOST=127.0.0.1`**.
   Validar apenas PRESENÇA (nunca imprimir valores):

```bash
for v in HL_ACCOUNT_ADDRESS HL_AGENT_PRIVATE_KEY SUPABASE_URL SUPABASE_ANON_KEY \
         SUPABASE_SERVICE_ROLE_KEY DATABASE_URL GATEWAY_CONTROL_TOKEN TV_WEBHOOK_TOKEN \
         NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY GATEWAY_HOST; do
  grep -q "^$v=..*" .env && echo "$v: presente" || echo "$v: FALTANDO"
done
```

8. **DNS (Hostinger)**: o registro antigo `A tokio.bz → 2.57.91.91` está
   ERRADO (IP de estacionamento do domínio) — **corrigir para
   `46.202.189.126`** e criar `www` (A para o mesmo IP ou CNAME → tokio.bz).
   Validar: `dig +short tokio.bz` → `46.202.189.126`. O Caddy emite o TLS no
   primeiro request após propagar.

Supabase (projeto **Tokio**) — estado: migration `0001_initial` aplicada,
signups desabilitados, usuário `rtg003@gmail.com` criado (itens 1–3 do passo
a passo, concluídos em 2026-07-02).

## 2. Deploy contínuo

- **Caminho normal**: push/merge em `main` dispara
  `.github/workflows/deploy-vps.yml` → SSH como `tokio` → `git pull` → build
  (venv + migrations + `next build` standalone) → `sudo -n systemctl restart`
  dos dois services. Re-trigger manual: aba Actions → "Deploy Tokio to VPS".
- Pegadinhas herdadas: `request_pty: true` + `sudo -n` são obrigatórios;
  `dial tcp :22: i/o timeout` do runner do GitHub é flaky — apenas re-trigger.
- **Rollback**: `git revert` do commit ruim em `main` (novo push = novo
  deploy). Dados locais (`data/`, `logs/`) não são tocados pelo deploy.

## 3. Registro da skill no Hermes

- Symlink (recomendado): `ln -s /home/tokio/Tokio/skill ~/.hermes/skills/trade`
  — a skill acompanha o repo via deploy.
- Alternativa: apontar o diretório de skills externas do Hermes para
  `/home/tokio/Tokio/skill`.
- Validar: o Hermes lista a skill `trade` e executa `strategy list`.

## 4. Comandos operacionais (como usuário `tokio`, em `/home/tokio/Tokio`)

| Alvo | Ação | Comando |
|---|---|---|
| engine (tudo) | restart | `sudo -n systemctl restart tokio-engine.service` |
| engine (tudo) | status | `sudo -n systemctl status tokio-engine.service` |
| engine | logs | `journalctl --user-unit= -u tokio-engine.service -f` (se sem acesso: `logs/*.jsonl` no repo) |
| processo individual (gateway/runner) | habilitar/desabilitar | editar `deploy/engine-processes.yaml` (`enabled:`) + restart do engine |
| web | restart / status | `sudo -n systemctl restart tokio.service` / `status` |
| gateway | health | `curl -s http://127.0.0.1:8700/health` |
| CLI | migrate | `.venv/bin/python -m engine.cli db migrate` |
| CLI | strategy list | `.venv/bin/python -m engine.cli strategy list` |
| CLI | strategy archive | `.venv/bin/python -m engine.cli strategy archive <id> --yes` |
| CLI | report diário | `.venv/bin/python -m engine.cli report --daily` |
| CLI | report por estratégia | `.venv/bin/python -m engine.cli report --strategy <id>` |
| CLI | KILL switch | `.venv/bin/python -m engine.cli kill --reason "<motivo>"` |
| CLI | remover KILL | `.venv/bin/python -m engine.cli unkill` (+ restart do engine) |
| CLI | replicação manual | `.venv/bin/python -m engine.cli replicate once` |
| discovery | varredura | `.venv/bin/python -m engine.strategies.copy_trade.discovery --top 10` |
| scanner | varredura | `.venv/bin/python -m engine.strategies.tradingview.scanner` |
| backtest | rodada | `.venv/bin/python -m engine.strategies.tradingview.backtest.harness --symbol BTC --interval 4h --days 90` |

O supervisor reinicia sozinho qualquer processo que cair (backoff
exponencial); `health.child_exited` nos logs indica quedas.

## 5. Crons sugeridos (crontab do usuário `tokio`)

```cron
# health check a cada 5 min (gateway + lag de replicação; alerta se lag > 60s)
*/5 * * * * cd /home/tokio/Tokio && curl -s http://127.0.0.1:8700/health >> logs/health-cron.log 2>&1

# resumo diário por exceção — 07:00 (America/Sao_Paulo; ajuste o TZ do servidor)
0 7 * * * cd /home/tokio/Tokio && .venv/bin/python -m engine.cli report --daily

# briefing de mercado (scanner) — 05:00, com destaque de fim de semana p/ gap CME
0 5 * * * cd /home/tokio/Tokio && .venv/bin/python -m engine.strategies.tradingview.scanner

# varredura de traders (discovery) — 05:30
30 5 * * * cd /home/tokio/Tokio && .venv/bin/python -m engine.strategies.copy_trade.discovery --top 10

# revisão semanal — segunda 08:00
0 8 * * 1 cd /home/tokio/Tokio && .venv/bin/python -m engine.cli strategy list
```

No Hermes, cadastre como cron jobs do agente com a skill `trade` anexada,
para interpretar o resultado e notificar o humano por exceção.

## 6. Checklist pós-onboarding (executar item a item)

1. [ ] `sudo -n systemctl status tokio-engine.service` e `tokio.service` = active (running).
2. [ ] `ss -tlnp | grep -E '3002|8700|8701'` — tudo em `127.0.0.1`, nada em `0.0.0.0`.
3. [ ] Health do gateway: `ok: true`, `kill_switch: false`, `network: testnet`.
4. [ ] `strategy list` — estratégias esperadas, todas `dry_run`.
5. [ ] Replicação: `replication_queue_depth` ~0 e `replication_lag_s < 60`.
6. [ ] `curl -I https://tokio.bz` = 200/307 + TLS válido.
7. [ ] **`curl -I https://luthor.io` continua 200 (vizinho intacto).**
8. [ ] Login na web com `rtg003@gmail.com`; KPIs/ordens/logs renderizam.
9. [ ] Tema light/dark alterna e persiste; layout mobile ok.
10. [ ] `sudo -u tokio cat /home/luthor/luthor/.env` = Permission denied (isolamento).
11. [ ] KILL switch: `kill` → gateway recusa → `unkill` + restart → normaliza.
12. [ ] Caos: `kill -9` no PID do runner-dummy (habilite-o antes em
        `deploy/engine-processes.yaml`) não afeta o gateway; supervisor o traz de volta.
13. [ ] Push em `main` dispara o GHA e reinicia os services sozinho.
14. [ ] Skill registrada: Hermes lista `trade` e roda `strategy list`.

## 7. Gates operacionais e troubleshooting

**Gates (exigem humano; o Hermes prepara a evidência e pergunta):**

- **Trader novo (copy trade)**: relatório do discovery + YAML em `traders/`
  com `dry_run: true`. Ativação (`dry_run→active`) só com evidência de
  expectância positiva líquida registrada em `docs/` e aprovação humana.
- **Promoção dry_run→active**: mesmo gate. A API de controle da web só
  reativa `paused/auto_paused` — nunca promove dry-run.
- **MAINNET**: gate permanente (config + `.env` + checklist + aprovação
  explícita). A web nunca alterna rede.
- **Aumento de caps de risco** (`config/settings.yaml → risk`): proposta com
  justificativa numérica → humano aprova → commit + deploy.
- **Webhook do TradingView**: sem rota pública por enquanto (decisão
  2026-07-02). Ativar = adicionar `handle /webhook*` no bloco do Caddy
  (ver `deploy/Caddyfile`) — mudança do operador.
- **Arquivamento**: `strategy archive <id> --yes` → post-mortem em
  `docs/post_mortems/<id>.md` → lição em `skill/references/lessons.md` via PR.

**Troubleshooting comum:**

| Sintoma | Causa provável | Ação |
|---|---|---|
| `kill_switch: true` no health | arquivo `KILL` presente | investigar (`killswitch.engaged` no log), resolver, `unkill`, restart do engine |
| circuit breaker aberto (todas auto-pausadas) | perda diária ≥ cap | `report --daily`; reativar só após diagnóstico |
| `replication_lag_s` crescendo | Supabase fora / chave rotacionada | engine segue (local-first); checar logs do replicator; corrigir `.env`; fila drena sozinha |
| `health.child_exited` em loop | crash recorrente de um processo | ver o log JSONL do processo; se preciso, `enabled: false` no yaml + restart e investigar |
| ordens `rejected: below_min_notional_10` | sizing < US$ 10 | esperado (mínimo da corretora); ajustar `value` do trader |
| fills sem estratégia (`fill.unattributed`) | ordem manual na mesma conta | não operar manualmente na conta do engine |
| web 502 em `/api/control/*` | gateway reiniciando | aguardar supervisor; checar health |
| GHA falha com `i/o timeout` no SSH | rede GitHub→Hostinger flaky | re-trigger do workflow |
| TLS não emite | DNS ainda no IP antigo (2.57.91.91) | corrigir registro A → `46.202.189.126`; `caddy reload` |
| query na HL devolve vazio | consultou endereço da agent wallet | consultar SEMPRE o endereço da conta master |
| `luthor.io` fora do ar após mexer no proxy | restart do Caddy em vez de reload | `sudo systemctl reload caddy` e avisar o operador IMEDIATAMENTE |
