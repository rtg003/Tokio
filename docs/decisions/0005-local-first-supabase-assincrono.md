# ADR 0005 — Local-first: SQLite é fonte de verdade; Supabase é réplica assíncrona

- Status: aceito
- Data: 2026-07-02

## Contexto

O hot path de execução (sub-segundo) não pode depender de rede externa. O
Supabase serve análise, dashboards e o web app — não a execução.

## Decisão

- SQLite (WAL) + JSONL na máquina do engine são a **fonte de verdade
  operacional**. O hot path escreve apenas localmente.
- Um worker dedicado replica em **lote e assincronamente** para o Supabase
  (Postgres). Indisponibilidade do Supabase gera fila local (tabela
  `replication_queue`) com retry e sincronização posterior — o engine nunca
  bloqueia nem falha por causa do Supabase.
- Teste de outage simulado é critério de aceite da Fase 1.
- `service_role` key existe somente nos containers do engine (replicador);
  o web usa URL + anon key com RLS ligado em todas as tabelas.
- Dashboards leem `strategy_metrics_daily` — nunca varrem `events`.

## Consequências

- Lag de replicação é monitorado e logado (health check do Hermes).
- Retenção de `events` no Supabase: 90 dias (debug de alto volume fica só no
  JSONL local, com rotação diária).
