# ADR 0006 — Proxy único na VPS (verificar 80/443 antes de subir o Caddy)

- Status: aceito
- Data: 2026-07-02

## Contexto

A VPS de destino já roda o Luthor (Polymarket) e o Hermes Agent. O TLS
automático do Caddy exige as portas 80/443 alcançáveis e o DNS de `tokio.bz`
resolvendo (registro `A @ → 2.57.91.91` já criado na Hostinger; falta o
`CNAME www → tokio.bz`).

## Decisão

- ANTES de subir o serviço `proxy`, inspecionar o que está vinculado às portas
  80/443 (`ss -tlnp`).
- Se já existir um reverse proxy servindo outros sistemas, **não subir um
  segundo proxy**: adicionar o vhost `tokio.bz` ao proxy existente (ou migrar
  tudo para o Caddy de forma planejada). O procedimento operacional está no
  `docs/HANDOFF_HERMES.md`.
- Gateway, runners e replicador ficam APENAS na rede interna do Docker —
  nenhuma porta do engine publicada no host ou na internet. Só o proxy expõe
  80/443, servindo exclusivamente o `web`.

## Consequências

- O deploy físico é um gate humano com checklist (inventário de RAM/CPU/disco
  da VPS incluído) antes do primeiro `make deploy`.
