# Tokio — idempotent operations. See docs/HANDOFF_HERMES.md for procedures.
#
# NOTE (ADR 0007): compose targets are for LOCAL DEV or a dedicated VPS.
# Production on the shared VPS uses systemd (deploy/systemd/*) + the
# GitHub Actions workflow (.github/workflows/deploy-vps.yml) — not `make deploy`.

COMPOSE_DEV  = docker compose
COMPOSE_PROD = docker compose -f docker-compose.yml -f docker-compose.prod.yml

.PHONY: test migrate up down deploy rollback logs status

test:
	pytest -q

migrate:
	python -m engine.cli db migrate
	@echo "Supabase (uma vez, pelo humano): psql \"$$DATABASE_URL\" -f db/migrations/supabase/0001_initial.sql"

up:
	$(COMPOSE_DEV) up -d --build

down:
	$(COMPOSE_DEV) down

# Idempotent production deploy: build, run local migrations inside the
# gateway container, bring everything up. Requires .env (chmod 600) present.
deploy:
	@test -f .env || (echo "ERRO: .env ausente — copie de .env.example (chmod 600)" && exit 1)
	$(COMPOSE_PROD) build
	$(COMPOSE_PROD) up -d gateway
	$(COMPOSE_PROD) exec gateway python -m engine.cli db migrate
	$(COMPOSE_PROD) up -d
	@echo "deploy ok — verifique: make status && curl -sI https://tokio.bz"

# Rollback: checkout the previous tag/commit and redeploy (images are rebuilt
# from the working tree; data volumes are untouched).
rollback:
	@echo "1) git log --oneline -5   (escolha o commit estável)"
	@echo "2) git checkout <commit>"
	@echo "3) make deploy"

status:
	$(COMPOSE_PROD) ps

logs:
	$(COMPOSE_PROD) logs -f --tail 100
