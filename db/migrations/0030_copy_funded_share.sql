-- 0030_copy_funded_share — UPDATE-0074 (SIM NET REAL p/ hiperativos). ADITIVA
-- (só ADD COLUMN; nenhuma linha é reescrita).
--
-- Contexto: o SIM NET de traders hiperativos (ex.: 0xd487) oscilava −$1000 ↔
-- $149k ↔ $496k porque, sob a restrição de capital concorrente do Fix A
-- (`copy_simulation.model_concurrency`), só ~0.1% do book do trader cabe no
-- orçamento do espelho ($1000×lev). Um ponto-estimativa tirado de 0.1% do book é
-- ruído. Duas colunas suportam a correção:
--
--  * `sim_funded_share` — fração do notional DESEJADO que coube no orçamento
--    concorrente (métrica do Fix A). A UI mostra "cópia parcial (X% do book)" e o
--    gate de confiabilidade (`copy_simulation.min_funded_share`) rebaixa a
--    confiança p/ `sampled` quando abaixo do limiar — o trader CONTINUA
--    disponível (não é descartado), mas sai do topo por um número irreal.
--  * `sim_f15_net_usd` — o net do F15 (30d, SEM latência). O `sim_net_pnl_usd`
--    passou a carregar o stage4 (60d, COM latência) = valor EXIBIDO/ordenado;
--    o F15 segue vivo só como gate barato e como base do score `sim_net`, então
--    é persistido à parte p/ o reclassify recompor o score sem reler o pipeline.
--
-- Só metadado/persistência — NÃO altera o caminho de ordem (INVARIANTE §8.4.1).

ALTER TABLE traders ADD COLUMN sim_funded_share REAL;  -- fração do book espelhável ($ / lev)
ALTER TABLE traders ADD COLUMN sim_f15_net_usd REAL;   -- net F15 (30d, sem latência) p/ score/reclassify
