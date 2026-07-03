# PROMPT — Módulo Discovery de Traders (Copy Trade · ct_default) · v5

> Executar no Cursor/Claude Code, dentro do repositório `https://github.com/rtg003/Tokio`.
> **CONTEXTO: o sistema JÁ ESTÁ EM EXECUÇÃO** (build + handoff concluídos). Este documento
> SUBSTITUI integralmente a spec breve de discovery da Fase 3: se o módulo já existir com a
> lógica trivial, REFATORE para esta spec preservando dados (re-upsert dos candidatos na
> tabela `traders`). A lógica vigente em produção deve ser registrada retroativamente como
> `logic_version: 1` em `docs/discovery_changelog.md`; esta spec implementa a `logic_version: 2`.
> Convenções do projeto valem integralmente: Python 3.11+ tipado, pydantic, logs JSONL,
> migrations versionadas, conventional commits, branch + PR. Local do código:
> `engine/strategies/copy_trade/discovery.py` (+ módulos auxiliares na mesma pasta).

---

## OBJETIVO

Encontrar, qualificar e ranquear traders da Hyperliquid que sejam **lucrativos, consistentes e COPIÁVEIS** — nesta ordem de importância invertida: copiabilidade é filtro eliminatório; lucratividade sem copiabilidade é irrelevante. O alvo NÃO é o topo cru do leaderboard (dominado por scalpers, baleias não-espelháveis, vaults e sortudos de um trade).

Regras invioláveis:
- **Read-only**: o discovery apenas lê dados públicos. Nunca envia ordem, nunca assina nada.
- **Dados de MAINNET**: leaderboard e fills públicos de mainnet, mesmo com a execução do sistema em testnet (dados de testnet são ruído).
- **Nenhum trader é adicionado automaticamente**: a saída é sugestão + relatório; a inclusão em `traders/` permanece gate humano (via dashboard ou Hermes com confirmação).
- Rate-limit friendly: requisições em lote, cache local (SQLite), backoff exponencial, e orçamento de requests configurável por varredura.

## FONTES DE DADOS (validar endpoints atuais na doc oficial antes de codar)

1. **Leaderboard** (janelas dia/semana/mês/all-time — PnL, ROI, volume) para a coleta ampla de candidatos.
2. **Info API** por endereço, para aprofundamento:
   - `userFillsByTime` — histórico de fills (trades, timestamps, preços, tamanhos, fees, direção).
   - `clearinghouseState` — equity atual, posições abertas, alavancagem.
   - `portfolio` — histórico de equity/PnL por janela (curva para drawdown).
   - Ledger de depósitos/saques — para separar crescimento por trading de crescimento por aporte.
3. Registrar em ADR quais endpoints foram usados e suas limitações (profundidade de histórico, paginação).

## PIPELINE — FUNIL DE 3 ESTÁGIOS

### Estágio 1 · Coleta ampla
- Puxar top N (default: 500) do leaderboard em QUATRO janelas: 7d, 30d, 60d e 90d.
- **Fonte secundária de candidatos**: endereços recorrentes em fills grandes no feed público de trades dos ativos líquidos (captura bons traders fora das janelas do leaderboard). Ambas as fontes alimentam o MESMO funil.
- **Regra de entrada**: PnL positivo em **≥ 3 das 4 janelas, sendo 30d e 60d obrigatórias**. A janela 7d PODE ser negativa — semana ruim é normal em trader de expectância positiva; exigi-la positiva compraria "mão quente" e descartaria consistentes em drawdown semanal. Consistência em TODAS as 4 janelas rende bônus no score (Estágio 3).
- Cachear resultados brutos com timestamp (evitar re-fetch na mesma varredura).

### Estágio 2 · Filtros eliminatórios (hard filters — binários, baratos primeiro)
Aplicar em ordem de custo crescente de dados. Reprovou em um, sai do funil (logar o motivo):

| # | Filtro | Default (configurável) |
|---|--------|------------------------|
| F1 | Atividade recente | ≥ 1 trade nos últimos 7 dias |
| F2 | Amostra mínima | ≥ 30 trades fechados E ≥ 60 dias de histórico |
| F3 | Anti-scalper | holding médio ≥ 2h E ≤ 20 trades/dia em média |
| F4 | Retorno mínimo | **TWRR 30d ≥ 5%** — retorno ponderado pelo tempo, neutro a depósitos/saques; validar o cálculo contra o portfolio API oficial da Hyperliquid |
| F5 | Drawdown | max DD 90d ≤ 25% (via curva do portfolio) |
| F6 | Concentração de PnL | top 3 trades ≤ 50% do PnL do período |
| F7 | Alavancagem | alavancagem média ≤ 15x |
| F8 | Liquidez dos ativos | ≥ 80% do volume em ativos do top de liquidez (lista configurável) |
| F9 | Anti-MM/vault/arb | reprovar padrões: > 200 trades/dia, exposição delta-neutra persistente, PnL/volume ≈ 0 |
| F10 | Anti-aporte | TODAS as métricas de retorno usam TWRR; reprovar adicionalmente se o crescimento de equity do período vier majoritariamente de depósitos |
| F11 | Espelhabilidade | posição típica proporcionalizável: cópia estimada ≥ US$ 10 notional com o capital configurado, e tamanho do trader não dominante no book |

### Estágio 3 · Scoring ponderado (só sobreviventes)
Score 0-100 composto (pesos em config, defaults abaixo):
- 25% — Consistência: nº de janelas positivas + estabilidade do PnL semanal (desvio-padrão baixo = melhor; proxy de Sharpe com PnL diário se a curva permitir).
- 20% — Profit factor com **crédito gradativo**: integral até 3.0; meio-crédito de 3.0 a 5.0, sendo que este trecho estendido só conta se n_trades ≥ 60 na janela (PF extremo com amostra pequena é variância, não habilidade); acima de 5.0 não pontua. PF calculado **incluindo o PnL não realizado das posições abertas** no fechamento da janela — PF apenas de realizados é inflável ao não fechar perdedores.
- 15% — ROI 30d ajustado (log-scale para não premiar alavancagem).
- 15% — Qualidade do drawdown (magnitude E velocidade de recuperação).
- 15% — Copiabilidade: holding time no sweet spot (4h–3d), liquidez dos ativos, frequência moderada.
- 10% — Expectância por trade líquida do custo estimado de cópia (fees taker + slippage estimado; se a expectância não paga o custo, score de expectância = 0).

Classificar estilo (`swing` | `posição` | `misto`) por holding médio, para exibição.

**Ajustes pós-score (aplicados ao score composto):**
- **+5** · consistência total: PnL positivo nas 4 janelas (7d, 30d, 60d, 90d).
- **−10** · risco de liquidação: alguma posição aberta atual a menos de 10% do preço de liquidação (bomba-relógio, por melhores que sejam as métricas históricas).
- **−5** · crowding/fama: wallet no top 20 all-time do leaderboard — as mais vigiadas têm milhares de copiadores, slippage de entrada maior e edge que decai mais rápido.

**Classificação bidimensional de coorte** (exibição e análise): cada candidato recebe dois rótulos — por tamanho de equity (ex.: Shrimp < US$ 250 … Whale … Leviathan ≥ US$ 5M) e por PnL acumulado (Rekt … Money Printer). Faixas em config.

### Coorte de controle (rekt)
Manter também, a cada varredura, uma coorte dos **consistentemente perdedores** (espelho invertido dos filtros: PnL negativo em ≥ 3 janelas). Dois usos obrigatórios:
1. **Validação do funil**: em backtest, o score DEVE separar claramente a coorte smart da rekt — se não separa, a lógica está quebrada (teste automatizado).
2. **Sinal de divergência**: posicionamento agregado smart vs. rekt por ativo (quando os perdedores lotam um lado e os qualificados estão do outro) — insumo do briefing matinal do Hermes, NÃO sinal de execução automática.

## SAÍDA

1. **Tabelas**:
   - `traders` — **tabela ÚNICA de candidatos e copiados** (upsert por address; ciclo de vida via `status`; reprovados permanecem com status REJEITADO + `motivo_reprovacao`). Garanta por migration que ela exista neste formato (crie ou ALTERE a existente; migre e delete eventuais YAMLs de traders remanescentes). Este módulo a POPULA e ESTENDE com as colunas de métricas abaixo. É a fonte única da tabela exibida na dashboard Tokio e nos relatórios do Hermes. **Ordenação padrão em toda exibição: `score` DECRESCENTE** (do mais indicado ao menos indicado).
   - `cohort_snapshots` — migration nova: agregados por varredura: scan_id, coorte (`smart` | `rekt`), ativo, viés líquido long/short (%), alavancagem média, nº de wallets, timestamp.

**Colunas da tabela de traders** (dashboard Tokio + relatórios do Hermes — mesma fonte, mesma ordem):

| # | Coluna | Conteúdo |
|---|--------|----------|
| 1 | Rank | posição no ranking (por score desc) |
| 2 | Trader | label + endereço truncado (link para o dossiê `inspect`) |
| 3 | Score | 0-100, com ajustes pós-score aplicados — chave de ordenação |
| 4 | Coorte | bidimensional: tamanho × PnL (ex.: `Whale · Money Printer`) |
| 5 | TWRR 30d | retorno % neutro a aportes |
| 6 | PnL 30d | USDC líquido |
| 7 | Janelas | consistência (ex.: `4/4` ou `3/4`) |
| 8 | Profit factor | valor bruto, incl. não realizado (crédito no score: integral até 3.0; meio-crédito 3.0–5.0 se n ≥ 60) |
| 9 | Win rate | % |
| 10 | Max DD 90d | % |
| 11 | Trades 30d | quantidade |
| 12 | Holding médio | duração típica |
| 13 | Alav. média | x |
| 14 | Dist. liquidação | % da posição aberta mais próxima (⚠ se < 10%) |
| 15 | Equity | USDC |
| 16 | Estilo | swing / posição / misto |
| 17 | Ativos | top 3 por volume |
| 18 | Última atividade | timestamp do último fill |
| 19 | Status | SUGERIDO → DRY-RUN → COPIANDO / PAUSADO / REJEITADO / ARQUIVADO |
| 20 | Origem | `scan` (varredura) / `hermes` / `dashboard` (sugestão manual) |
| 21 | logic_version | versão da lógica que qualificou o candidato |
| 22 | Ações | copiar / pausar / dossiê (na dashboard) |

Colunas 1-10 + 19 são o núcleo sempre visível; as demais aparecem em modo expandido/dossiê. **Regra de fonte única**: traders sugeridos via Hermes ou manualmente pela dashboard entram como candidatos e passam pelo MESMO funil e MESMA `logic_version` da varredura automática — nenhuma via alternativa cria trader fora da lógica; a coluna `origem` só registra por onde entrou.
2. **Relatório** por varredura: JSON (máquina) + Markdown (humano) em `reports/discovery/`, com top 10 ranqueado e justificativa numérica por candidato + estatísticas do funil (quantos entraram, quantos caíram em cada filtro).
3. **Log JSONL** granular: cada candidato avaliado, cada reprovação com filtro e valores, duração, requests usados.
4. **CLI**: `discovery scan` (varredura completa), `discovery inspect <address>` (dossiê completo de um endereço, incluindo distância de liquidação das posições abertas e classificação de coorte), `discovery positioning` (posicionamento agregado da coorte qualificada: net long/short por ativo, alavancagem média, preço médio de entrada — e a divergência vs. coorte rekt), `discovery token <ativo>` (deep dive invertido: o que a coorte qualificada está fazendo num ativo específico), `discovery report --last`. O agendamento diário 05:00 America/Sao_Paulo usa `discovery scan` (cron do Hermes/host, conforme HANDOFF); `positioning` alimenta o briefing matinal.

## EVOLUÇÃO DA LÓGICA (requisito, não opcional)

- Todos os thresholds e pesos vivem em `discovery_config.yaml` com **versão da lógica** (`logic_version: 1`). A lógica versionada e controlada é fundamental: cada candidato registra a `logic_version` que o qualificou, permitindo comparar safras de sugestões entre versões.
- **Autoridade do Hermes (gravar também na skill/)**: o Hermes Agent PODE e DEVE atualizar a lógica/filtros do discovery quando tiver evidência clara — tipicamente após uma cópia malsucedida cujo post-mortem aponte falha previsível dos filtros, ou uma constatação forte nos dados — ou quando o humano solicitar. Condições invariáveis: SEMPRE via PR com justificativa numérica (nunca edição direta), SEMPRE com bump de `logic_version`, e SEMPRE registrando no log de evolução. Na dúvida, propõe e aguarda; com certeza absoluta, executa o PR e notifica. Esta permissão e este procedimento devem constar explicitamente no `strategy.md` do copy trade e ser referenciados no `SKILL.md`.
- **Log de evolução (duas camadas)**: (1) `docs/discovery_changelog.md` — humano-legível: versão, data, autor (hermes/humano), motivo, evidência que motivou, parâmetros alterados (antes → depois), resultado esperado; (2) evento JSONL `logic_updated` com o mesmo payload, replicado para a tabela `events`.
- **Feedback loop**: quando um trader copiado for pausado/removido por desempenho ruim, registrar em `docs/post_mortems/` quais métricas do discovery FALHARAM em prever o problema — insumo direto para a próxima `logic_version`.
- Testes unitários obrigatórios para as funções de métrica (TWRR, profit factor, concentração de PnL, drawdown de curva, holding médio, detecção anti-MM, separação de coortes smart vs. rekt) com fixtures de dados sintéticos — incluindo casos armadilha: o sortudo de um trade, o scalper lucrativo, o delta-neutro, o inflado por depósitos.

## ACEITE

- `discovery scan` completa uma varredura real de mainnet dentro do orçamento de requests, popula a tabela `traders` (todas as colunas especificadas, upsert por address) e `cohort_snapshots`, e gera relatório com ≥ 3 candidatos aprovados + estatísticas do funil.
- `discovery inspect` produz dossiê completo de um endereço arbitrário; `discovery positioning` e `discovery token` funcionando com a divergência smart vs. rekt.
- Ordenação por score decrescente verificada na saída e documentada como default da tabela.
- Reprovações logadas com motivo e valores; `docs/discovery_changelog.md` criado com a entrada da `logic_version: 1`; `pytest` verde nas métricas, incluindo o teste de separação de coortes.
- Nenhuma ordem enviada, nenhuma chave usada (verificável no código: o módulo não importa o signer).

---

**Antes de codar**: valide os endpoints atuais (leaderboard e Info API) na documentação oficial da Hyperliquid, confirme profundidade de histórico disponível por endpoint, e apresente um mini-plano (1 página) com o mapeamento endpoint → métrica. Aguarde aprovação humana do mini-plano.
