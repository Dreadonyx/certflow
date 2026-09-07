# CertFlow — Deployment & Operations Guide

## Architecture

```
Internet → Caddy (:80/:443) → web:5000 (internal Docker network)
                              ↑
                           Redis (rate-limit store)
```

Port `5000` is **not** exposed to the host. Access the app only via the Caddy domain.

---

## ⚠️ IP Changes on EC2 Stop → Start

Every time you **stop and start** the EC2 instance, AWS assigns a **new public IP**.  
Your DuckDNS record (`certflow.duckdns.org`) must be updated, or the site will be unreachable.

### Fix: Auto-update DuckDNS on boot (run once)

SSH into the instance and add a cron job:

```bash
# Replace YOUR_TOKEN with your DuckDNS token
echo '@reboot sleep 30 && curl -s "https://www.duckdns.org/update?domains=certflow&token=YOUR_TOKEN&ip=" > /home/ubuntu/duckdns.log 2>&1' | crontab -
```

This automatically updates DuckDNS with the new IP on every reboot.

### Alternative: Elastic IP (set-and-forget)

Allocate an **Elastic IP** in the AWS console and associate it with the instance.  
The IP never changes. Free while the instance is running; ~$0.005/hr when stopped.

---

## Starting the App

```bash
# 1. Start the instance from the AWS EC2 console

# 2. SSH in using the new IP shown in the EC2 console
ssh -i certflow-key.pem ubuntu@<NEW_IP>

# 3. Start all containers
cd Certs-Automator
docker compose up -d

# 4. Wait ~60 seconds for Caddy to obtain a TLS certificate, then visit:
#    https://certflow.duckdns.org
```

---

## Stopping the App

```bash
# Gracefully stop all containers (data is preserved in Docker volumes)
docker compose down
```

Then stop the EC2 instance from the AWS console to avoid compute charges.

> **Note:** EBS storage and any allocated Elastic IP still incur charges while the instance is stopped.

---

## Useful Commands

```bash
# Check container status
docker compose ps

# View live logs
docker compose logs -f

# View logs for a specific service
docker compose logs web --tail=50
docker compose logs caddy --tail=50

# Rebuild and restart (after code changes)
docker compose up -d --build

# Restart a single service
docker compose restart caddy
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `ERR_CONNECTION_REFUSED` on port 5000 | Port 5000 is internal only | Access via `https://certflow.duckdns.org` |
| `ERR_CONNECTION_REFUSED` on domain | Caddy not running or wrong IP in DuckDNS | Check `docker compose ps`, update DuckDNS |
| `caddy` container restarting | Invalid Caddyfile directive | Run `docker compose logs caddy` |
| `web` container restarting | Missing Python dependency or config error | Run `docker compose logs web` |
| Site works but rate-limits broken | Redis not healthy | Run `docker compose ps` — redis should show `(healthy)` |
