-- 0022_orders_leverage — grava a alavancagem efetiva por ordem.
-- Migração ADITIVA. `orders`/`fills` não guardavam alavancagem nem margem
-- (conceitos de POSIÇÃO na Hyperliquid, não de cada trade). A dashboard passa a
-- exibir Alav./Margem por ordem e por trade: a alavancagem é a efetiva já
-- teto-limitada (min do intent, do ativo e do global) no `handle_intent`; a
-- margem é derivada na UI (notional / alavancagem). Ordens gravadas antes desta
-- migração ficam com `leverage` NULL → a UI mostra "—".
ALTER TABLE orders ADD COLUMN leverage REAL;
