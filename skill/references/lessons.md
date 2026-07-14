# Lições agregadas (post-mortems de estratégias arquivadas)

> Atualizado via PR sempre que uma estratégia é arquivada. Fonte: os
> post-mortems individuais em `docs/post_mortems/`. Testar muitas estratégias
> e descartar rápido as perdedoras é parte central do método — o registro da
> lição é o insumo do objetivo de lucro consistente.

## Formato de cada lição

- **Data · estratégia**: 1 linha com a lição acionável (o que mudar na próxima).

## Lições

_(nenhuma ainda — o histórico começa com o primeiro arquivamento)_

## Deploy manual do web (Next.js standalone) — SEMPRE copiar assets estáticos

**Data**: 2026-07-14
**Erro**: após `npm run build`, reiniciar o systemd sem copiar `.next/static`
e `public` para `.next/standalone/`. Resultado: CSS/JS retornam 404, dashboard
perde toda formatação. O navegador faz cache do 404 com `max-age=31536000
immutable` (1 ano), então mesmo após corrigir o usuário continua vendo quebrado
até limpar cache manual.

**Regra**: NUNCA fazer deploy manual do web sem rodar o autodeploy.sh, OU
executar manualmente os 3 comandos críticos:

```bash
cd /home/tokio/Tokio/web
cp -r .next/static .next/standalone/.next/static
cp -r public .next/standalone/public
sudo systemctl restart tokio.service
```

O `output: "standalone"` no next.config.ts gera o server.js autocontido mas
NÃO copia os assets estáticos — o autodeploy.sh (linhas 47-49) faz esse cp.
Sempre prefira `deploy/autodeploy.sh` em vez de restart manual.
