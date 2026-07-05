-- 0010_purge_dry_run_strategies — diretiva rtg003 (2026-07-05).
-- Remove estratégias DRY_RUN legadas e mantém somente a estratégia ativa
-- ct_48295497 / trader 0x482954976e8778433e9446309e37b52648bd7404.

DELETE FROM strategy_metrics_daily
WHERE strategy_id IN ('ct_whale01', 'dm_pulse', 'tv_funding_extreme', 'tv_gap_fade');

DELETE FROM fills
WHERE strategy_id IN ('ct_whale01', 'dm_pulse', 'tv_funding_extreme', 'tv_gap_fade');

DELETE FROM orders
WHERE strategy_id IN ('ct_whale01', 'dm_pulse', 'tv_funding_extreme', 'tv_gap_fade');

DELETE FROM events
WHERE strategy_id IN ('ct_whale01', 'dm_pulse', 'tv_funding_extreme', 'tv_gap_fade');

DELETE FROM strategies
WHERE id IN ('ct_whale01', 'dm_pulse', 'tv_funding_extreme', 'tv_gap_fade');

-- O executor de copy trade recria ct_whale01 se um trader operável com esse
-- nome continuar na tabela. Não tocar no trader ativo ct_48295497.
DELETE FROM traders
WHERE lower(coalesce(name, '')) = 'whale01'
  AND address <> '0x482954976e8778433e9446309e37b52648bd7404';
