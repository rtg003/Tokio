#!/usr/bin/env bash
# ============================================================================
# Tokio — bootstrap COMPLETO da VPS compartilhada (PARTE A + build + Caddy).
#
# Rodar como o OPERADOR (rtg003), com sudo, na VPS 46.202.189.126.
# O repo é PRIVADO: o acesso é via deploy key read-only (o script gera e
# orienta). Com o repo já clonado:
#
#   sudo bash /home/tokio/Tokio/deploy/bootstrap_vps.sh
#   # branch diferente de main (pré-merge): TOKIO_BRANCH=<branch> sudo -E bash ...
#
# IDEMPOTENTE: pode rodar quantas vezes precisar; cada etapa detecta o que
# já existe. Se faltar credencial no .env, ele avisa, NÃO sobe o engine, e
# você roda de novo depois de preencher.
#
# REGRAS DE ISOLAMENTO RESPEITADAS:
#   - não lê nem toca /home/luthor, luthor.service, dash-lbx;
#   - Caddy: apenas APPEND de bloco novo + validate + RELOAD (nunca restart);
#   - tudo do Tokio binda somente em 127.0.0.1.
# ============================================================================
set -euo pipefail

APP_USER="tokio"
APP_DIR="/home/tokio/Tokio"
# Repo é PRIVADO: clone/pull via deploy key (read-only) do usuário tokio.
REPO_URL="git@github.com:rtg003/Tokio.git"
GIT_SSH='ssh -i /home/tokio/.ssh/gh_repo_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new'
BRANCH="${TOKIO_BRANCH:-main}"
CADDYFILE="/etc/caddy/Caddyfile"
REQUIRED_VARS=(HL_ACCOUNT_ADDRESS HL_AGENT_PRIVATE_KEY SUPABASE_URL SUPABASE_ANON_KEY
               SUPABASE_SERVICE_ROLE_KEY DATABASE_URL NEXT_PUBLIC_SUPABASE_URL
               NEXT_PUBLIC_SUPABASE_ANON_KEY)

log()  { echo -e "\n\033[1;33m[tokio-bootstrap]\033[0m $*"; }
ok()   { echo -e "  \033[1;32m✓\033[0m $*"; }
warn() { echo -e "  \033[1;31m!\033[0m $*"; }

[ "$(id -u)" -eq 0 ] || { echo "ERRO: rode com sudo (sudo bash $0)"; exit 1; }

# ----------------------------------------------------------------------------
log "1/9 usuário isolado '$APP_USER'"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" --home "/home/$APP_USER" "$APP_USER"
  ok "usuário criado"
else
  ok "usuário já existe"
fi
groupadd -f deployers
usermod -aG deployers "$APP_USER"
chmod 700 "/home/$APP_USER"
chmod 750 /home/luthor 2>/dev/null || true
ok "grupos e permissões aplicados (/home/$APP_USER 700; /home/luthor 750)"

# ----------------------------------------------------------------------------
log "2/9 chave SSH dedicada de deploy (GitHub Actions)"
KEY="/home/$APP_USER/.ssh/gh_actions_deploy"
if [ ! -f "$KEY" ]; then
  sudo -u "$APP_USER" mkdir -p "/home/$APP_USER/.ssh"
  sudo -u "$APP_USER" ssh-keygen -t ed25519 -f "$KEY" -N "" -C "gha-tokio-deploy" >/dev/null
  ok "chave gerada"
else
  ok "chave já existe"
fi
PUB="$(cat "$KEY.pub")"
AUTH="/home/$APP_USER/.ssh/authorized_keys"
sudo -u "$APP_USER" touch "$AUTH"
grep -qF "$PUB" "$AUTH" || echo "$PUB" | sudo -u "$APP_USER" tee -a "$AUTH" >/dev/null
sudo -u "$APP_USER" chmod 600 "$AUTH"
ok "authorized_keys ok"

# ----------------------------------------------------------------------------
log "3/9 sudoers mínimo (SOMENTE restart/status dos 2 services do Tokio)"
cat > /etc/sudoers.d/tokio <<'SUDOERS'
tokio ALL=(root) NOPASSWD: /usr/bin/systemctl restart tokio.service, /usr/bin/systemctl status tokio.service, /usr/bin/systemctl restart tokio-engine.service, /usr/bin/systemctl status tokio-engine.service
SUDOERS
chmod 440 /etc/sudoers.d/tokio
visudo -c >/dev/null && ok "sudoers válido"

# ----------------------------------------------------------------------------
log "4/9 repositório em $APP_DIR (repo privado — deploy key read-only)"
DEPLOY_KEY="/home/$APP_USER/.ssh/gh_repo_deploy"
if [ ! -f "$DEPLOY_KEY" ]; then
  sudo -u "$APP_USER" ssh-keygen -t ed25519 -f "$DEPLOY_KEY" -N "" -C "tokio-repo-deploy" >/dev/null
fi
if ! grep -q "gh_repo_deploy" "/home/$APP_USER/.ssh/config" 2>/dev/null; then
  sudo -u "$APP_USER" tee -a "/home/$APP_USER/.ssh/config" >/dev/null <<'SSHCFG'
Host github.com
  IdentityFile ~/.ssh/gh_repo_deploy
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHCFG
  sudo -u "$APP_USER" chmod 600 "/home/$APP_USER/.ssh/config"
fi
if ! sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git ls-remote "$REPO_URL" HEAD >/dev/null 2>&1; then
  warn "GitHub ainda não aceita a deploy key deste servidor."
  warn "Adicione a chave abaixo em: github.com/rtg003/Tokio → Settings → Deploy keys →"
  warn "'Add deploy key' → título 'vps-tokio' → NÃO marque 'Allow write access'."
  echo "  ============================================================"
  cat "$DEPLOY_KEY.pub"
  echo "  ============================================================"
  warn "Depois rode este script de novo."
  exit 0
fi
if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  ok "clonado (branch $BRANCH)"
else
  sudo -u "$APP_USER" git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git -C "$APP_DIR" fetch origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" checkout "$BRANCH" >/dev/null 2>&1 || true
  sudo -u "$APP_USER" env GIT_SSH_COMMAND="$GIT_SSH" git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  ok "atualizado (branch $BRANCH)"
fi

# ----------------------------------------------------------------------------
log "5/9 runtimes (Node LTS via nvm + venv Python) — como $APP_USER"
# nvm não carrega em shell não-interativo: sourcing explícito sempre
NVM_SH='export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh";'
if ! sudo -u "$APP_USER" bash -c "$NVM_SH command -v node" >/dev/null 2>&1; then
  sudo -u "$APP_USER" bash -c 'curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash' >/dev/null
  sudo -u "$APP_USER" bash -c "$NVM_SH nvm install --lts" >/dev/null
fi
NODE_BIN="$(sudo -u "$APP_USER" bash -c "$NVM_SH command -v node")"
ln -sf "$NODE_BIN" /usr/local/bin/tokio-node
ok "node $(sudo -u "$APP_USER" bash -c "$NVM_SH node --version") (symlink /usr/local/bin/tokio-node)"
if [ ! -d "$APP_DIR/.venv" ]; then
  sudo -u "$APP_USER" bash -c "cd $APP_DIR && python3 -m venv .venv" \
    || { apt-get install -y -q python3.12-venv && sudo -u "$APP_USER" bash -c "cd $APP_DIR && python3 -m venv .venv"; }
fi
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/pip install -q -e ." && ok "engine instalado no venv"

# ----------------------------------------------------------------------------
log "6/9 .env (600, owner $APP_USER) + tokens autogerados"
ENVF="$APP_DIR/.env"
if [ ! -f "$ENVF" ]; then
  sudo -u "$APP_USER" cp "$APP_DIR/.env.example" "$ENVF"
  warn ".env criado a partir do template — preencha as credenciais nele"
fi
chown "$APP_USER:$APP_USER" "$ENVF"; chmod 600 "$ENVF"
# tokens internos: gerar automaticamente se vazios (não são credenciais externas)
for tok in GATEWAY_CONTROL_TOKEN TV_WEBHOOK_TOKEN; do
  if ! grep -q "^$tok=..*" "$ENVF"; then
    v="$(openssl rand -hex 32)"
    grep -q "^$tok=" "$ENVF" && sed -i "s|^$tok=.*|$tok=$v|" "$ENVF" || echo "$tok=$v" >> "$ENVF"
    ok "$tok gerado automaticamente"
  else
    ok "$tok já definido"
  fi
done
# na VPS o IPC é local
grep -q "^GATEWAY_HOST=127.0.0.1$" "$ENVF" || sed -i "s|^GATEWAY_HOST=.*|GATEWAY_HOST=127.0.0.1|" "$ENVF"
ok "GATEWAY_HOST=127.0.0.1"
MISSING=()
for v in "${REQUIRED_VARS[@]}"; do grep -q "^$v=..*" "$ENVF" || MISSING+=("$v"); done
if [ "${#MISSING[@]}" -gt 0 ]; then
  warn "variáveis FALTANDO no $ENVF: ${MISSING[*]}"
  warn "preencha (sudo -u $APP_USER nano $ENVF) e rode este script de novo."
  warn "se você preencheu um .env em outro caminho: sudo cp <seu_env> $ENVF && sudo chown $APP_USER:$APP_USER $ENVF && sudo chmod 600 $ENVF"
  ENV_READY=0
else
  ok "todas as variáveis obrigatórias presentes"
  ENV_READY=1
fi

# ----------------------------------------------------------------------------
log "7/9 build (engine migrations + web standalone)"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && .venv/bin/python -m engine.cli db migrate" && ok "migrations locais ok"

# Migrations do Supabase (Postgres) — idempotentes (IF NOT EXISTS / DROP POLICY IF EXISTS)
if grep -q "^DATABASE_URL=..*" "$ENVF"; then
  command -v psql >/dev/null 2>&1 || apt-get install -y -q postgresql-client >/dev/null 2>&1
  MIG_OK=1
  for sqlfile in "$APP_DIR"/db/migrations/supabase/*.sql; do
    if ! sudo -u "$APP_USER" bash -c "set -a; . $ENVF; set +a; psql \"\$DATABASE_URL\" -v ON_ERROR_STOP=1 -q -f $sqlfile" 2>/tmp/tokio-psql-err.log; then
      MIG_OK=0
      warn "migration Supabase falhou ($(basename "$sqlfile")):"
      head -3 /tmp/tokio-psql-err.log | sed 's/^/    /'
      if grep -qiE "could not translate|Network is unreachable|timeout" /tmp/tokio-psql-err.log; then
        warn "provável host direto db.* (só IPv6). Use a connection string do"
        warn "SESSION POOLER (Connect → Session pooler) na linha DATABASE_URL= do .env"
        warn "e rode este script de novo."
      fi
    fi
  done
  [ "$MIG_OK" -eq 1 ] && ok "migrations Supabase aplicadas (idempotente)"
else
  warn "DATABASE_URL ausente — migrations do Supabase puladas"
fi
# NEXT_PUBLIC_* é BUILD-time no Next: exportar o .env antes do build
sudo -u "$APP_USER" bash -c "$NVM_SH cd $APP_DIR/web && set -a && . ../.env && set +a && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null && rm -rf .next/standalone/.next/static .next/standalone/public && cp -r .next/static .next/standalone/.next/static && cp -r public .next/standalone/public" \
  && ok "web buildado (standalone)"

# ----------------------------------------------------------------------------
log "8/9 systemd units (+ autodeploy pull-based a cada 2 min)"
cp "$APP_DIR/deploy/systemd/tokio.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/tokio-engine.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/tokio-autodeploy.service" /etc/systemd/system/
cp "$APP_DIR/deploy/systemd/tokio-autodeploy.timer" /etc/systemd/system/
chmod +x "$APP_DIR/deploy/autodeploy.sh"
systemctl daemon-reload
systemctl enable tokio.service tokio-engine.service >/dev/null 2>&1
systemctl enable --now tokio-autodeploy.timer >/dev/null 2>&1
ok "autodeploy: a VPS puxa origin/main a cada 2 min e se atualiza sozinha"
systemctl restart tokio.service && ok "tokio.service (web) no ar"
if [ "$ENV_READY" -eq 1 ]; then
  systemctl restart tokio-engine.service && ok "tokio-engine.service (engine) no ar"
else
  warn "engine NÃO iniciado (faltam credenciais no .env) — rode o script de novo após preencher"
fi

# ----------------------------------------------------------------------------
log "9/9 Caddy compartilhado (append + validate + RELOAD; Luthor intocado)"
LUTHOR_BEFORE="$(curl -sI -m 10 -o /dev/null -w '%{http_code}' https://luthor.io || echo 000)"
if ! grep -q "tokio.bz" "$CADDYFILE"; then
  cp "$CADDYFILE" "$CADDYFILE.bak-$(date +%s)"
  cat >> "$CADDYFILE" <<'CADDY'

tokio.bz, www.tokio.bz {
	encode gzip
	reverse_proxy 127.0.0.1:3002
}
CADDY
  if caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
    # reload NUNCA pode derrubar o script (nem o Luthor): não-fatal, com diagnóstico
    if systemctl reload caddy 2>/dev/null; then
      ok "vhost tokio.bz adicionado e caddy recarregado"
    elif caddy reload --config "$CADDYFILE" 2>/dev/null; then
      ok "vhost aplicado via 'caddy reload' direto (admin API)"
    else
      warn "reload do Caddy FALHOU — config antiga segue ativa (Luthor intacto)."
      warn "Nesta VPS a admin API do Caddy é desligada (hardening): reload não"
      warn "funciona. Aplicar o vhost exige um restart breve (~1-2s, operador):"
      warn "    sudo caddy validate --config $CADDYFILE && sudo systemctl restart caddy"
    fi
  else
    LATEST_BAK="$(ls -t "$CADDYFILE".bak-* | head -1)"
    cp "$LATEST_BAK" "$CADDYFILE"
    warn "caddy validate FALHOU — Caddyfile restaurado do backup, nada recarregado. Verifique manualmente."
  fi
else
  ok "vhost tokio.bz já presente"
fi
LUTHOR_AFTER="$(curl -sI -m 10 -o /dev/null -w '%{http_code}' https://luthor.io || echo 000)"
echo "  luthor.io antes=$LUTHOR_BEFORE depois=$LUTHOR_AFTER (devem ser iguais e 2xx/3xx)"

# ----------------------------------------------------------------------------
log "VALIDAÇÃO"
sleep 2
echo "  binds (esperado: só 127.0.0.1):"
ss -tlnp | grep -E ':3002|:8700|:8701' | sed 's/^/    /' || true
echo "  gateway /health:"
curl -sm 5 http://127.0.0.1:8700/health | sed 's/^/    /' || warn "gateway ainda não responde (normal se .env incompleto)"
echo "  https://tokio.bz :"
curl -sIm 15 https://tokio.bz | head -1 | sed 's/^/    /' || warn "TLS ainda emitindo — tente de novo em 1 min"

# ----------------------------------------------------------------------------
log "ÚLTIMO PASSO MANUAL — secret de deploy do GitHub Actions"
echo "  Copie a PRIVATE KEY abaixo para: github.com/rtg003/Tokio → Settings →"
echo "  Secrets and variables → Actions → New repository secret → nome: VPS_SSH_KEY"
echo "  ============================================================"
cat "$KEY"
echo "  ============================================================"
echo "  Depois disso, todo push na main faz deploy sozinho."
