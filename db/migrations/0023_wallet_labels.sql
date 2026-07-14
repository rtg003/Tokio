-- 0023_wallet_labels — rótulos de wallet geridos no app.
-- O nome da conta na MetaMask (ex.: "Hyperliquid 1") é interno da extensão e
-- NÃO é exposto a sites (a MetaMask só entrega o endereço via window.ethereum).
-- Para o combo de Wallets do topo mostrar "Hyperliquid 1 — 0x4124…", guardamos
-- um rótulo por endereço aqui (fonte única SQLite, ADR §5.4). Ato humano
-- autenticado edita via /control/wallet/{address}/label.
CREATE TABLE IF NOT EXISTS wallet_labels (
    address TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
