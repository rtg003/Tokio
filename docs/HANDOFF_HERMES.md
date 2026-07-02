# HANDOFF — operação do Tokio pelo Hermes Agent

> Contrato de passagem do CONSTRUTOR (agente de build) para o OPERADOR
> (Hermes). Tudo aqui é executável a partir do repositório
> `https://github.com/rtg003/Tokio` na VPS.

## 1. Setup do ambiente na VPS

A VPS já roda o Luthor (Polymarket) e o Hermes. **Antes de subir qualquer
serviço**:

1. **Inventário de recursos** (obrigatório): `free -h`, `nproc`, `df -h`,
   `docker stats --no-stream`. O stack completo pede ~2 GB de RAM e ~2,5 CPUs
   nos limites configurados (gateway 512M/1.0, runners 256–384M/0.5 cada,
   replicador 256M/0.5, web 512M/0.5, proxy 256M/0.3). Se a folga for
   insuficiente com margem de segurança, **PARE e reporte ao humano com os
   números**.
2. Dependências: Docker + Docker Compose v2, git, make. Para uso da CLI fora
   dos containers: Python 3.11+ e `pip install -e ".[dev]"`.
3. Clone e configuração:

```bash
git clone https://github.com/rtg003/Tokio.git && cd Tokio
cp .env.example .env && chmod 600 .env
```

4. **Credenciais (protocolo obrigatório)**: o humano preenche o `.env`
   diretamente no arquivo, FORA de qualquer sessão de agente. Valide apenas a
   PRESENÇA das variáveis — nunca imprima valores:

```bash
for v in HL_ACCOUNT_ADDRESS HL_AGENT_PRIVATE_KEY SUPABASE_URL SUPABASE_ANON_KEY \
         SUPABASE_SERVICE_ROLE_KEY DATABASE_URL GATEWAY_CONTROL_TOKEN TV_WEBHOOK_TOKEN \
         NEXT_PUBLIC_SUPABASE_URL NEXT_PUBLIC_SUPABASE_ANON_KEY; do
  grep -q "^$v=..*" .env && echo "$v: presente" || echo "$v: FALTANDO"
done
```

   - Projeto Supabase: **novo e separado do projeto do Luthor**, criado pelo
     humano. Migration inicial (uma vez): `psql "$DATABASE_URL" -f db/migrations/supabase/0001_initial.sql`.
   - Usuário do web app: no dashboard do Supabase → Authentication →
     desabilitar signups → criar `rtg003@gmail.com` com senha definida pelo
     humano. Nunca em código ou repo.
   - Agent wallets na Hyperliquid (humano, via UI/API): `engine_gateway`
     (assinatura do engine) e `hermes_ops` (operações do Hermes, com
     expiração). Nunca reutilizar endereços de agents desregistradas.
   - **Rotação de chaves**: gerar nova chave no dashboard (Supabase) ou nova
     agent wallet (HL) → atualizar `.env` → `docker compose restart` dos
     serviços afetados. A chave antiga é revogada na origem.

5. **DNS `tokio.bz`** (Hostinger, nameservers `byte.dns-parking.com` /
   `pixel.dns-parking.com`):
   - `A @ → 2.57.91.91` — **já criado**.
   - `CNAME www → tokio.bz` — **pendente: criar na zona da Hostinger**.
   - Confirmar propagação antes do primeiro deploy: `dig +short tokio.bz`
     deve devolver `2.57.91.91` e `dig +short www.tokio.bz` o apex.
6. **Conflito de portas (ADR 0006)**: `ss -tlnp | grep -E ':80|:443'`.
   Se JÁ existir um reverse proxy servindo Luthor/Hermes, **não suba o
   serviço `proxy`** — adicione o vhost ao proxy existente (conteúdo em
   `deploy/Caddyfile`: `tokio.bz → web:3000`, `www` redireciona ao apex) e
   suba somente `web` na rede compartilhada. Registre o que foi feito.
7. Deploy: `make deploy` (idempotente: build → migrations → up). Rollback:
   `git checkout <commit estável> && make deploy` (volumes de dados intactos).
8. Verificação pós-deploy: `make status`; `curl -sI https://tokio.bz` (TLS
   válido, 200/307); **portas do engine inacessíveis externamente**:
   `nmap -p 8700,8701 <IP público>` deve dar `closed/filtered` (só 80/443/SSH
   abertas).

## 2. Registro da skill no Hermes

Duas vias (documente qual usou):

- **Symlink** (recomendado): `ln -s /caminho/para/Tokio/skill ~/.hermes/skills/trade`
  — a skill acompanha o repositório via `git pull`.
- **Diretório externo de skills**: configure o diretório de skills externas do
  Hermes (config do Hermes → skills) apontando para `/caminho/para/Tokio/skill`.

Valide com o fluxo do próprio Hermes: a skill `trade` deve aparecer no
catálogo e o frontmatter (name: `trade`) deve carregar. O corpo referencia
scripts via caminho relativo ao repo — execute sempre a partir da raiz do
repositório.

## 3. Comandos operacionais por serviço

Prefixo comum: `cd /caminho/para/Tokio`. Em produção use
`docker compose -f docker-compose.yml -f docker-compose.prod.yml` (alias
`COMPOSE` abaixo); em dev, `docker compose`.

| Serviço | Ação | Comando |
|---|---|---|
| todos | start | `make deploy` (prod) / `make up` (dev) |
| todos | stop | `$COMPOSE down` |
| todos | status | `make status` |
| todos | logs | `make logs` (ou `$COMPOSE logs -f <serviço>`) |
| gateway | restart | `$COMPOSE restart gateway` |
| gateway | health | `$COMPOSE exec web wget -qO- http://gateway:8700/health` (rede interna) |
| gateway | logs | `$COMPOSE logs -f gateway` |
| runner copy trade | start/stop/restart | `$COMPOSE up -d runner-copytrade` / `stop` / `restart` |
| runner tradingview | start/stop/restart | `$COMPOSE up -d runner-tradingview` / `stop` / `restart` |
| runner dummy | idem | `$COMPOSE ... runner-dummy` |
| replicator | restart / logs | `$COMPOSE restart replicator` / `logs -f replicator` |
| web | restart / logs | `$COMPOSE restart web` / `logs -f web` |
| proxy | reload / logs | `$COMPOSE restart proxy` / `logs -f proxy` |
| engine | migrate | `$COMPOSE exec gateway python -m engine.cli db migrate` |
| engine | strategy list | `$COMPOSE exec gateway python -m engine.cli strategy list` |
| engine | strategy archive | `$COMPOSE exec gateway python -m engine.cli strategy archive <id> --yes` |
| engine | report diário | `$COMPOSE exec gateway python -m engine.cli report --daily` |
| engine | report por estratégia | `$COMPOSE exec gateway python -m engine.cli report --strategy <id>` |
| engine | KILL switch | `$COMPOSE exec gateway python -m engine.cli kill --reason "<motivo>"` |
| engine | remover KILL | `$COMPOSE exec gateway python -m engine.cli unkill` |
| engine | replicação manual | `$COMPOSE exec gateway python -m engine.cli replicate once` |
| deploy | deploy/rollback | `make deploy` / procedimento em `make rollback` |
| discovery | varredura | `$COMPOSE exec gateway python -m engine.strategies.copy_trade.discovery --top 10` |
| scanner | varredura | `$COMPOSE exec gateway python -m engine.strategies.tradingview.scanner` |
| backtest | rodada | `$COMPOSE exec gateway python -m engine.strategies.tradingview.backtest.harness --symbol BTC --interval 4h --days 90` |

## 4. Jobs de cron sugeridos (comandos exatos)

Ajuste `/opt/Tokio` para o caminho real. Horários em America/Sao_Paulo.

```cron
# health check a cada 5 min — inclui lag de replicação Supabase (alerta se lag > 60s)
*/5 * * * * cd /opt/Tokio && docker compose exec -T web wget -qO- http://gateway:8700/health | tee -a logs/health-cron.log

# resumo diário por exceção — 07:00
0 7 * * * cd /opt/Tokio && docker compose exec -T gateway python -m engine.cli report --daily

# briefing de mercado (scanner) — 05:00, com destaque de fim de semana p/ gap CME
0 5 * * * cd /opt/Tokio && docker compose exec -T gateway python -m engine.strategies.tradingview.scanner

# varredura de traders (discovery) — 05:30
30 5 * * * cd /opt/Tokio && docker compose exec -T gateway python -m engine.strategies.copy_trade.discovery --top 10

# revisão semanal de desempenho — segunda 08:00 (relatório por estratégia das ativas)
0 8 * * 1 cd /opt/Tokio && docker compose exec -T gateway python -m engine.cli strategy list
```

No Hermes, cadastre-os como cron jobs do agente anexando a skill `trade`, para
que o resultado seja interpretado e o humano notificado por exceção.

## 5. Checklist de verificação pós-onboarding (executar item a item)

1. [ ] `make status` — todos os serviços `running`.
2. [ ] Health do gateway: `ok: true`, `kill_switch: false`, `network: testnet`.
3. [ ] `strategy list` mostra as estratégias esperadas, todas `dry_run`
       (exceto as explicitamente ativadas pelo humano).
4. [ ] `report --daily` roda sem erro.
5. [ ] Replicação: `replication_queue_depth` próximo de 0 e `replication_lag_s < 60`.
6. [ ] Web: login em `https://tokio.bz` com o usuário provisionado; KPIs,
       ordens, trades e logs renderizam dados do Supabase (testnet).
7. [ ] Tema light/dark alterna e persiste; layout mobile ok (drawer).
8. [ ] Portas do engine fechadas externamente (`nmap` do item 1.8).
9. [ ] KILL switch: `kill` → gateway recusa intents → `unkill` → normaliza.
10. [ ] Teste de caos: `docker kill tokio-runner-dummy` não afeta o gateway;
        o restart policy o traz de volta.
11. [ ] Skill registrada: Hermes lista `trade` e executa `strategy list` via runbook.

## 6. Gates operacionais e troubleshooting

**Gates (exigem humano; o Hermes prepara a evidência e pergunta):**

- **Trader novo (copy trade)**: relatório do discovery + YAML em
  `traders/` com `dry_run: true`. Ativação (`dry_run→active`) só com evidência
  de expectância positiva líquida registrada em `docs/` e aprovação humana.
- **Promoção dry_run→active de qualquer estratégia**: mesmo gate acima. A API
  de controle da web só reativa `paused/auto_paused` — nunca promove dry-run.
- **MAINNET**: gate permanente. Mudar `config/settings.yaml`
  (`exchange.network`) + `.env` exige checklist de segurança + aprovação
  explícita. A web NUNCA alterna rede (toggle é leitura).
- **Aumento de caps de risco** (`config/settings.yaml → risk`): proposta com
  justificativa numérica → humano aprova → commit + restart do gateway.
- **Arquivamento**: `strategy archive <id> --yes` → escrever post-mortem em
  `docs/post_mortems/<id>.md` → agregar lição em `skill/references/lessons.md`
  via PR. Histórico nunca é apagado do banco.

**Troubleshooting comum:**

| Sintoma | Causa provável | Ação |
|---|---|---|
| `kill_switch: true` no health | arquivo `KILL` presente | investigar motivo no log (`killswitch.engaged`), resolver, `unkill`, reiniciar runners |
| circuit breaker aberto (todas auto-pausadas) | perda diária ≥ cap | analisar `report --daily`; reativar via web/control só após diagnóstico |
| `replication_lag_s` crescendo | Supabase fora / chave rotacionada | engine segue normal (local-first); checar `logs -f replicator`; corrigir `.env`; a fila drena sozinha |
| ordens `rejected: below_min_notional_10` | sizing < US$ 10 | comportamento esperado (mínimo da corretora); ajustar `value` do trader |
| fills sem estratégia (`fill.unattributed`) | ordem manual fora do engine | não misturar operação manual na mesma conta; usar wallet separada |
| WS de copy trade silencioso | reconexão | ver eventos `ws.*`; restart do runner-copytrade se persistir |
| web 502 em `/api/control/*` | gateway reiniciando | aguardar restart policy; checar `logs gateway` |
| TLS não emite | DNS não propagado ou 80/443 ocupadas | validar `dig`; ADR 0006 (proxy único) |
| query na HL devolve vazio | consultou endereço da agent wallet | consultar SEMPRE o endereço da conta master |
