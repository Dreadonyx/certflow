#!/usr/bin/env bash
# Runs once on the EC2 instance (called by user-data, or manually).
# Fetches secrets from Secrets Manager, writes .env, sets up DuckDNS, starts app.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
REGION=$(curl -sf http://169.254.169.254/latest/meta-data/placement/region || echo "us-east-1")

echo "=== Fetching secrets from certflow/app-secrets ==="
SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id certflow/app-secrets \
  --region "$REGION" \
  --query SecretString \
  --output text)

echo "=== Writing .env ==="
export SECRET_JSON
python3 - > "$APP_DIR/.env" << 'PYEOF'
import json, os

data = json.loads(os.environ["SECRET_JSON"])
# DUCKDNS_TOKEN is used by the cron job below, not passed to docker-compose
skip = {"DUCKDNS_TOKEN"}
for k, v in data.items():
    if k not in skip:
        print(f"{k}={v}")
PYEOF
chmod 600 "$APP_DIR/.env"
echo "  Written $(wc -l < "$APP_DIR/.env") keys to $APP_DIR/.env"

echo "=== Setting up DuckDNS cron ==="
DUCKDNS_TOKEN=$(python3 -c "import json,os; d=json.loads(os.environ['SECRET_JSON']); print(d.get('DUCKDNS_TOKEN',''))")
DOMAIN_FULL=$(python3 -c "import json,os; d=json.loads(os.environ['SECRET_JSON']); print(d.get('DOMAIN_NAME','certflow.duckdns.org'))")
DOMAIN_LABEL=$(echo "$DOMAIN_FULL" | cut -d. -f1)

if [ -n "$DUCKDNS_TOKEN" ] && [ "$DUCKDNS_TOKEN" != "REPLACE_WITH_DUCKDNS_TOKEN" ]; then
  CRON_LINE="@reboot sleep 30 && curl -s \"https://www.duckdns.org/update?domains=${DOMAIN_LABEL}&token=${DUCKDNS_TOKEN}&ip=\" >> \$HOME/duckdns.log 2>&1"
  (crontab -l 2>/dev/null | grep -v duckdns.org; echo "$CRON_LINE") | crontab -
  curl -s "https://www.duckdns.org/update?domains=${DOMAIN_LABEL}&token=${DUCKDNS_TOKEN}&ip=" >> "$HOME/duckdns.log" 2>&1 || true
  echo "  DuckDNS cron installed; initial update triggered."
else
  echo "  DUCKDNS_TOKEN not set - skipping. Update the secret and re-run to enable."
fi

echo "=== Ensuring buildx is current (compose build needs >= 0.17) ==="
ARCH=$(uname -m); [ "$ARCH" = "x86_64" ] && ARCH="amd64" || ARCH="arm64"
BUILDX_URL=$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
  | grep -o "\"browser_download_url\": *\"[^\"]*linux-${ARCH}\"" \
  | head -1 | cut -d'"' -f4)
mkdir -p ~/.docker/cli-plugins
curl -fsSL "$BUILDX_URL" -o ~/.docker/cli-plugins/docker-buildx
chmod +x ~/.docker/cli-plugins/docker-buildx

echo "=== Starting CertFlow ==="
cd "$APP_DIR"
docker compose up -d --build
echo "  Done. Run 'docker compose logs -f' to watch startup."
