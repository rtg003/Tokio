-- 0011_drop_replication_queue — SQLite is now the only database.
-- The old Supabase outbox is intentionally discarded; durable state lives in
-- the operational tables and is backed up from the SQLite file.

DROP TABLE IF EXISTS replication_queue;
