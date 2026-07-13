# Módulo Oracle Mismatch — vigilância de descolamento de oráculo (MVP, camada Hermes)

Detecta quando o preço de um listing da Hyperliquid **descola** da sua referência
e **alerta um humano**. NUNCA opera. Nasceu do flow do vídeo "This Hermes Agent
Trading Flow… Hyperliquid": o pré-IPO SpaceX caiu ~40% por **misconfig de
oráculo** (não por mercado) e o valor foi **ser avisado a tempo** — o autor opera
manualmente, não confia auto-compra em alerta.

> **Expectativa correta:** isto é **ferramenta de vigilância, não fonte de receita**.
> Misconfigs de oráculo são raros e episódicos. O ganho é o aviso a tempo —
> inclusive de divergência HIP-3 que afete outras estratégias do Tokio.

## Fronteira humana (inegociável)

- **Hermes alerta. Eduardo decide e opera.** O scanner só lê dados **públicos** e
  dispara Telegram + log. **Zero** ordem, zero `/intent`, zero `/cancel`.
- Não toca engine, schema SQLite, gateway, dashboard nem credencial de trading.
  Por construção, `§8.4.1` (protocolo do gateway) **não se aplica** e não há gate
  de mainnet/caps aqui.
- Segredos (`TELEGRAM_*`) só são **lidos** do `.env`; nunca impressos/logados. O
  state file não contém segredo algum.

## Arquitetura (tudo dentro do Hermes)

```
watchlist.yaml ─┐
                ├─> scanner.py --once ──> [HL público /info metaAndAssetCtxs]
state/…json  ───┘         │                   (default cripto + builder dexs HIP-3)
   (ring buffers +        ├─> ref: hl_peer (Δ% vs mediana Δ% dos peers) | cex (nível)
    open_alert)           ├─> regra de disparo + stale + debounce/histerese
                          └─> alerta: Telegram + logs/oracle_mismatch.jsonl
```

- **`scanner.py`** — script self-contained (stdlib + `httpx` + `yaml`, **sem
  importar `engine/`**). Roda `--once` por ciclo (cron), stateless entre execuções.
- **`watchlist.yaml`** — o que vigiar (Hermes-owned; sem DB/migração no MVP).
- **`state/oracle_mismatch_state.json`** (raiz do repo, gitignored) — ring buffers
  de amostras + evento aberto. Necessário porque o `hl_peer` é **temporal** e o
  cron é stateless.
- **`logs/oracle_mismatch.jsonl`** — trilha de auditoria (samples, alertas,
  warm-up, stale, reset). `logs/oracle_mismatch-cron.log` — stdout/stderr do cron.

## Q1 — Como definir quais tickers acompanhar?

Watchlist em arquivo (`watchlist.yaml`), editável pelo Hermes/Eduardo. Critério de
seleção (lógica do vídeo — **mercados frágeis**):

- **HIP-3 / pré-IPO / ações** na HL (perps de builder, oráculo de fonte única,
  book fino) — onde misconfig acontece. Ex.: `xyz:SPCX` (SpaceX), `xyz:TSLA`.
- **Cripto líquida** (BTC/ETH) para o caminho CEX.

**Pré-passo obrigatório antes de editar o YAML — confirmar a string exata:** os
HIP-3 são **builder-namespaced** (não é `SPCX`, é `xyz:SPCX`). Rode:

```bash
python skill/references/oracle_mismatch/scanner.py --list-symbols --filter xyz:
```

Copie a **string exata** do meta. Símbolo não listado → `enabled: false` +
comentário, **nunca** chute. (O `--list-symbols` já mescla o meta padrão + todos
os perp DEXs de builder via `perpDexs`.)

## Q2 — Como definir as fontes oficiais? (providers por `ref_kind`)

1. **`hl_peer`** (grátis, sem key — recomendado p/ HIP-3/pré-IPO): compara a
   **variação** (Δ%) do par numa janela contra a **mediana das variações** do
   `peer_group` na mesma janela. Desambiguação embutida: **só alerta se o par
   descolou e os peers não** — exatamente o "os outros pré-IPO não caíram" do vídeo.
2. **`cex`** (grátis, sem key — p/ cripto): REST público de ticker spot
   (`binance`/`coinbase`/`bybit`); comparação de **nível** HL vs spot.

## Modelo de detecção (o núcleo — não simplificar de volta p/ nível)

### `hl_peer` (temporal, janela)
Δ% de cada ativo = `(preço_atual / preço_na_borda_da_janela − 1) × 100`, usando a
amostra persistida mais próxima de `now − window_s` (tolerância ±90s). **Dispara se,
no mesmo ciclo:**
1. `|Δ%_par| > threshold_pct` (default 10), **e**
2. `|mediana(Δ%_peers)| < threshold_pct / 3` (peers "parados"), **e**
3. nº de peers com amostra válida na janela **≥ 2**.

### `cex` (nível, stateless)
`diff_pct = (px_hl / px_cex − 1) × 100`; alerta se `|diff_pct| > threshold_pct`. O
estado só é usado aqui para o **debounce**.

### Warm-up
Sem baseline suficiente na janela (< 2 amostras, ou borda fora da tolerância ±90s),
o par fica em **warm-up** e **não alerta** — nunca inventa baseline. Após boot/reset,
espere ~10 min (a `window_s`) sem alertas `hl_peer`.

### "Stale" / amostra inválida (nunca vira alerta)
Referência inválida se **qualquer**: HTTP ≠ 200 / timeout (5s) / payload sem o
campo; timestamp do dado > 120s (quando o provider expõe); (`hl_peer`) borda sem
amostra na tolerância ou < 2 peers válidos; preço ≤ 0. Loga `sample_invalid` +
motivo, sem alerta. **Falha de fetch não pode virar falso positivo.**

### Debounce / histerese
- Ao alertar, grava `open_alert`. Enquanto `|diff| > threshold × 0.7`, evento segue
  **aberto** (sem novo Telegram; loga `alert_ongoing`).
- Fecha após `|diff| < threshold × 0.7` por **2 ciclos** (`alert_closed` com
  duração/pico) e re-arma o par.
- Evento aberto > 30 min → **um** Telegram "AINDA ABERTO" (lembrete único), depois
  silencia até fechar.

## Operação (runbook)

```bash
# da raiz do repo (cwd = repo root)
PY=.venv/bin/python
SC=skill/references/oracle_mismatch/scanner.py

$PY $SC --list-symbols [--filter xyz:]   # passo 0: confirmar strings do meta
$PY $SC --once --dry-run                 # ciclo sem enviar Telegram (teste)
$PY $SC --once                           # ciclo real (o que o cron roda)
$PY $SC --reset-state                    # zera todo o estado (warm-up total)
$PY $SC --reset-state hip3-spcx          # zera só o alerta aberto de um par
```

- **Checar o que aconteceu:** `tail -f logs/oracle_mismatch.jsonl` (eventos:
  `sample`, `alert_open`, `alert_ongoing`, `alert_closed`, `warmup`,
  `sample_invalid`, `state_reset`, `dex_fetch_failed`).
- **Silenciar/ajustar um par:** editar `watchlist.yaml` (`enabled: false`, ou subir
  `threshold_pct` / mudar `window_s`, `peer_group`).
- **Telegram:** precisa de `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` no `.env`. Sem
  eles, o ciclo loga `skipped_unconfigured` e **não quebra** (segue logando).

### Cron (o "a cada 1 minuto")

Entrada no crontab do usuário `tokio` (operação do Hermes):

```cron
* * * * * cd /home/tokio/Tokio && .venv/bin/python skill/references/oracle_mismatch/scanner.py --once >> logs/oracle_mismatch-cron.log 2>&1
```

- Habilitar: `crontab -e` e adicionar a linha. Desabilitar: comentar/remover.
- Checar: `crontab -l | grep oracle_mismatch` e `tail logs/oracle_mismatch-cron.log`.
- **Warm-up esperado** nos primeiros ~10 min após habilitar/boot (pares `hl_peer`
  sem janela ainda).

### Listing Watch — detecção de novos listings (1x/dia)

O universe do builder dex `xyz:` muda: novos equities/pré-IPO aparecem e
sommem. O scanner só vigia o que está no `watchlist.yaml`, então um listing
novo passa despercebido até alguém adicionar manualmente. O listing watch
fecha essa lacuna:

```cron
0 12 * * * cd /home/tokio/Tokio && .venv/bin/python skill/references/oracle_mismatch/listing_watch.py >> logs/oracle_mismatch-listing.log 2>&1
```

- Roda **09:00 SP** (12:00 UTC) — pega listings adicionados overnight/pré-market
  US antes da abertura regular (10:30 SP).
- Compara o universe `xyz:` atual contra o snapshot do dia anterior
  (`state/oracle_listings_snapshot.json`).
- **Silencioso** quando não há mudanças (só loga o ciclo no JSONL).
- **Alerta Telegram** quando há símbolos novos (🆕) ou removidos (❌), com a
  lista e o preço oracle de cada um.
- Mesma fronteira: só leitura pública + alerta, zero ordem, zero engine.
- Reaproveita `fetch_all_hl_prices()` do `scanner.py` (import direto).

## Pitfalls

- **Falso positivo** de `hl_peer` se o peer-group for pequeno/correlacionado demais,
  ou se o par tiver news próprio legítimo (ex.: pré-IPO em rodada). Use ≥ 3 peers.
- **Rate-limit de CEX** (429): o fetch respeita timeout e loga `sample_invalid`;
  não derruba o ciclo. Se recorrente, reduza pares `cex` ou troque de venue.
- **State corrompido:** JSON inválido → renomeado `.corrupt-<ts>`, recomeça vazio,
  loga `state_reset`, entra em warm-up. Nunca alerta com estado duvidoso.
- **Polling 1 min + janela 10 min** pode perder dislocations muito rápidas — é
  vigilância, não HFT.

## Fase 2 (fora do MVP — domínio do CONSTRUTOR)

Migração `0020_oracle_mismatch.sql` (tabelas `om_*` + registro em `strategies`,
isolamento §5.1–5.4), `adapter.oracle_px()`, eventos `om.*` na tabela `events`,
dashboard `/oracle-mismatch` (§5.3), descoberta automática de listings e eventual
execução assistida (aí sim `§8.4.1` + gates humanos). Detalhes em
`docs/CURSOR_UPDATES.md` (UPDATE-0044).
