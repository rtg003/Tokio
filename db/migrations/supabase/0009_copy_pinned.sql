-- 0009_copy_pinned (Supabase/Postgres) — espelho da flag inviolável (Bloco 3).
-- Passo MANUAL pós-deploy:
--   psql "$DATABASE_URL" -f db/migrations/supabase/0009_copy_pinned.sql
-- Sem isso o replicator falha com PGRST204 na coluna nova.

ALTER TABLE traders ADD COLUMN IF NOT EXISTS copy_pinned INTEGER NOT NULL DEFAULT 0;
