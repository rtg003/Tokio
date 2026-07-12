# tv_explain_decision — explica qualquer evento do tv_events

Traduz um evento do Logs do módulo (`tv_events`) em uma explicação clara:
checklist do validator, diff de versão, ou incidente. Read-only.

## Fonte

```bash
# Logs unificados do módulo (SIGNAL | INCIDENT | HERMES | USER | SYSTEM)
curl -s "http://127.0.0.1:8700/api/tv/events?limit=50"
curl -s "http://127.0.0.1:8700/api/tv/events?kind=SIGNAL&before=<ts_cursor>"
```

Cada linha traz `ts, kind, severity, summary, ref_id, detail`. O `detail` é o
JSON com o miolo.

## Como explicar por tipo

- **SIGNAL**: `detail.checks` é o checklist ORDENADO do §8.2 (1–13). A **primeira
  falha** encerra e o resto fica `skipped`. Reporte `required` vs `actual` do
  check que falhou e o `block_code` (ex.: `STRATEGY_DISABLED`, `SIZE_BELOW_MINIMUM`,
  `SYMBOL_LOCKED`, `DUPLICATE`). `netting_plan` e `computed_size_usd` mostram o
  que teria sido executado.
- **HERMES / USER**: `detail` tem `version`, `changed_by`, `change_summary`,
  `config`. Compare com a versão anterior (consulte `tv_strategy_versions` por
  `strategy_id`) para descrever o diff exato.
- **INCIDENT**: `detail.details` + `resolved`; ex.: `INCIDENT_UNPROTECTED_POSITION`
  (stop rejeitado ⇒ fechamento + incidente).
- **SYSTEM**: eventos operacionais `tv.*` (criação, ativação, promoção,
  notificação de mainnet).

## Guardrails

- Explique o que o sistema DECIDIU e por quê — nunca sugira burlar um bloqueio.
- Se citar números, tire-os do `detail`, não estime.
- Isolamento §5.1: só fale de dados do módulo TV; a view já garante o escopo.
