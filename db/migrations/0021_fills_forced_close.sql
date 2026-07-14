-- 0021_fills_forced_close — marca fills de fechamento FORÇADO pela venue.
-- Migração ADITIVA. A Hyperliquid manda `dir` no fill cru (ex.: "Auto-Deleveraging",
-- "Liquidation"). Um fechamento forçado NUNCA pode virar posição oposta no ledger
-- virtual (short fantasma quando o ledger dessincroniza com a venue em books rasos):
-- `forced_close=1` faz o replay do `hydrate_from_db` clampar a posição em zero em vez
-- de flip-through-zero. Fills de ordem própria / paper / teste ficam 0 (default).
ALTER TABLE fills ADD COLUMN forced_close INTEGER DEFAULT 0;
