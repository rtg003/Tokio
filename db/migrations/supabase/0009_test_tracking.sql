-- 0009_test_tracking.sql
-- Migration de teste: valida que o apply_supabase_migrations.sh registra
-- a migration na tabela de controle schema_migrations_supabase.
-- Idempotente (IF NOT EXISTS) — não altera schema de produção.

CREATE TABLE IF NOT EXISTS _migration_test (id int);
