#!/usr/bin/env bash
# Helper: update certflow/app-secrets in Secrets Manager with real values.
# Run this on your LOCAL machine (needs AWS CLI + credentials).
# Usage: bash scripts/update-secrets.sh
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SECRET_ID="certflow/app-secrets"

echo "=== Updating $SECRET_ID in $REGION ==="
echo

# Fetch current secret so we only overwrite what we change
CURRENT=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_ID" \
  --region "$REGION" \
  --query SecretString --output text)

echo "Current values (SECRET_KEY hidden):"
python3 -c "
import json, os
d = json.loads(os.environ['CURRENT'])
for k, v in d.items():
    print(f'  {k}: {\"***\" if k == \"SECRET_KEY\" else v}')
" <<< "$CURRENT"
echo

# ── Generate a new admin password hash ────────────────────────────────────
echo "To generate an Argon2id hash for a new admin password, run:"
echo "  python3 -c \"from argon2 import PasswordHasher; print(PasswordHasher().hash('YourPassword'))\""
echo

read -rp "Enter ADMIN_PASSWORD_HASH (or press Enter to keep current): " NEW_HASH
read -rp "Enter DUCKDNS_TOKEN (or press Enter to keep current): " NEW_DUCKDNS
read -rp "Enter DOMAIN_NAME (or press Enter to keep 'certflow.duckdns.org'): " NEW_DOMAIN
echo

UPDATED=$(python3 - "$NEW_HASH" "$NEW_DUCKDNS" "$NEW_DOMAIN" <<'PYEOF'
import json, sys, os

current = json.loads(os.environ["CURRENT"])
new_hash, new_duckdns, new_domain = sys.argv[1], sys.argv[2], sys.argv[3]

if new_hash:
    current["ADMIN_PASSWORD_HASH"] = new_hash
if new_duckdns:
    current["DUCKDNS_TOKEN"] = new_duckdns
if new_domain:
    current["DOMAIN_NAME"] = new_domain

print(json.dumps(current))
PYEOF
)

aws secretsmanager update-secret \
  --secret-id "$SECRET_ID" \
  --region "$REGION" \
  --secret-string "$UPDATED"

echo "Secret updated. SSH into the instance and run:"
echo "  bash ~/Certs-Automator/scripts/server-setup.sh"
echo "to apply the new values."
