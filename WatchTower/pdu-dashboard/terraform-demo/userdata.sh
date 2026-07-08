#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# WatchTower Demo — EC2 User Data (runs on first boot)
# Installs Docker, clones the app, and starts it automatically
# ─────────────────────────────────────────────────────────────────────────────
set -e

exec > /var/log/watchtower-setup.log 2>&1
echo "=== WatchTower setup starting at $(date) ==="

# ── Install Docker ───────────────────────────────────────────────────────────
dnf update -y
dnf install -y docker git
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user

# ── Install Docker Compose ───────────────────────────────────────────────────
COMPOSE_VERSION="v2.29.1"
curl -L "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# ── Clone WatchTower ─────────────────────────────────────────────────────────
cd /opt
git clone https://github.com/tholmes721/WatchTower.git
cd WatchTower/WatchTower/pdu-dashboard

# ── Set session secret ───────────────────────────────────────────────────────
cat > .env <<EOF
DATABASE_URL=sqlite+aiosqlite:////app/data/pdu.db
WATCHTOWER_SESSION_SECRET=${session_secret}
EOF

# ── Update docker-compose to use the .env ────────────────────────────────────
cat > docker-compose.yml <<'COMPOSE'
version: "3.9"

services:
  watchtower:
    build: .
    ports:
      - "80:8000"
    volumes:
      - watchtower-data:/app/data
    env_file:
      - .env
    restart: unless-stopped

volumes:
  watchtower-data:
COMPOSE

# ── Build and start ──────────────────────────────────────────────────────────
docker-compose up -d --build

# ── Create systemd service for auto-start on reboot ──────────────────────────
cat > /etc/systemd/system/watchtower.service <<'UNIT'
[Unit]
Description=WatchTower PDU Dashboard
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/WatchTower/WatchTower/pdu-dashboard
ExecStart=/usr/local/bin/docker-compose up -d
ExecStop=/usr/local/bin/docker-compose down

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable watchtower.service

echo "=== WatchTower setup complete at $(date) ==="
echo "=== Access at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4):8000 ==="
