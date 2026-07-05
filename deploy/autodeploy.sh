#!/usr/bin/env bash
# ============================================================================
# Tokio — deploy contínuo PULL-BASED (executado pelo tokio-autodeploy.timer).
#
# A cada execução: compara HEAD local com origin/main; se mudou, faz pull,
# rebuilda engine+web e reinicia os services. Roda como ROOT (instalado pelo
# operador via bootstrap); builds executam como usuário tokio.
#
# Vantagens sobre push-based (GitHub Actions -> SSH): nenhum secret no GitHub
# e nenhuma dependência da rede GitHub->Hostinger (flaky).
# ============================================================================
set -euo pipefail

APP_DIR="/home/tokio/Tokio"
BRANCH="main"
APP_USER="tokio"
GIT_SSH='ssh -i /home/tokio/.ssh/gh_repo_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
NVM_SH='export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh";'
LOCK="/run/tokio-autodeploy.lock"

exec 9>"$LOCK"
flock -n 9 || { echo "[autodeploy] já em execução"; exit 0; }

cd "$APP_DIR"
sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git fetch --quiet origin "$BRANCH"
LOCAL="$(sudo -u "$APP_USER" git rev-parse HEAD)"
REMOTE="$(sudo -u "$APP_USER" git rev-parse "origin/$BRANCH")"
CURRENT="$(sudo -u "$APP_USER" git rev-parse --abbrev-ref HEAD)"

if [ "$LOCAL" = "$REMOTE" ] && [ "$CURRENT" = "$BRANCH" ]; then
  exit 0  # nada novo
fi

echo "[autodeploy] $(date -Is) ${LOCAL:0:7} -> ${REMOTE:0:7} (branch atual: $CURRENT)"
sudo -u "$APP_USER" git checkout "$BRANCH" 2>/dev/null \
  || sudo -u "$APP_USER" git checkout -b "$BRANCH" "origin/$BRANCH"
sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git pull --ff-only origin "$BRANCH"

# engine
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/pip install -q -e . && .venv/bin/python -m engine.cli db migrate"

# web (DASHBOARD_* é runtime/build-time: exporta o .env antes)
sudo -u "$APP_USER" bash -c "$NVM_SH cd $APP_DIR/web \
  && set -a && . ../.env && set +a \
  && npm ci --no-audit --no-fund >/dev/null \
  && npm run build >/dev/null \
  && rm -rf .next/standalone/.next/static .next/standalone/public \
  && cp -r .next/static .next/standalone/.next/static \
  && cp -r public .next/standalone/public"

systemctl restart tokio-engine.service tokio.service
echo "[autodeploy] ok $(date -Is) — deployado ${REMOTE:0:7}"
