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
python3 - << 'PYEOF'
import json, os, stat

data = json.loads(os.environ["SECRET_JSON"])
app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) \
    if "__file__" in dir() else os.getcwd()

# DUCKDNS_TOKEN is used by the cron job below, not passed to docker-compose
skip = {"DUCKDNS_TOKEN"}
lines = [f"{k}={v}" for k, v in data.items() if k not in skip]

env_path = os.path.join(os.environ.get("APP_DIR", app_dir), ".env")
with open(env_path, "w") as f:
    f.write("\n".join(lines) + "\n")
os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
print(f"  Written {len(lines)} keys to {env_path}")
PYEOF

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

echo "=== Starting CertFlow ==="
cd "$APP_DIR"
docker compose up -d --build
echo "  Done. Run 'docker compose logs -f' to watch startup."
