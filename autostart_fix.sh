#!/bin/bash
# =============================================================
#  Akfotek Ltd — Autostart Fix & Verification Script
#  Run on IoT2050 as root:  bash autostart_fix.sh
# =============================================================

GREEN='\033[0;32m'; BLUE='\033[0;34m'
YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC}   $1"; }
info() { echo -e "${BLUE}[..]${NC}   $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   Akfotek — Autostart Fix & Verification            ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

[[ $EUID -ne 0 ]] && { fail "Run as root: sudo bash autostart_fix.sh"; exit 1; }

# ── 1. Write the energy-meter-pq service file ─────────────────
info "Writing energy-meter-pq service..."
cat > /etc/systemd/system/energy-meter-pq.service << 'EOF'
[Unit]
Description=Akfotek Power Quality & Energy Monitoring (Port 5002)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/energy_meter
ExecStart=/usr/bin/python3 /root/energy_meter/plc_reader.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=energy-meter-pq

[Install]
WantedBy=multi-user.target
EOF
log "energy-meter-pq service file written"

# ── 2. Write the cloudflared service file ─────────────────────
info "Writing cloudflared service..."
cat > /etc/systemd/system/cloudflared.service << 'EOF'
[Unit]
Description=Cloudflare Tunnel
After=network-online.target energy-meter-pq.service
Wants=network-online.target
Requires=energy-meter-pq.service

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --config /root/.cloudflared/config.yml run
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cloudflared

[Install]
WantedBy=multi-user.target
EOF
log "cloudflared service file written"

# ── 3. Reload systemd ─────────────────────────────────────────
info "Reloading systemd daemon..."
systemctl daemon-reload
log "systemd reloaded"

# ── 4. Enable both services ───────────────────────────────────
info "Enabling energy-meter-pq on boot..."
systemctl enable energy-meter-pq.service
log "energy-meter-pq enabled"

info "Enabling cloudflared on boot..."
systemctl enable cloudflared.service
log "cloudflared enabled"

# ── 5. Check app files exist ──────────────────────────────────
info "Checking app files in /root/energy_meter..."
MISSING=0
for f in plc_reader.py dashboard.html; do
    if [[ -f "/root/energy_meter/$f" ]]; then
        log "  Found: /root/energy_meter/$f"
    else
        fail "  Missing: /root/energy_meter/$f"
        MISSING=1
    fi
done
if [[ $MISSING -eq 1 ]]; then
    warn "Copy files from your PC first:"
    warn "  scp -r C:\\Users\\ASUS\\energy_meter root@192.168.0.144:/root/"
    exit 1
fi

# ── 6. Check cloudflared config ───────────────────────────────
info "Checking cloudflared config..."
if [[ -f "/root/.cloudflared/config.yml" ]]; then
    log "Config found: /root/.cloudflared/config.yml"
    if grep -q "home.akfotekengineering.com" /root/.cloudflared/config.yml; then
        log "home.akfotekengineering.com route is in config"
    else
        warn "home.akfotekengineering.com not found in config!"
        warn "Add this to /root/.cloudflared/config.yml (before the http_status:404 line):"
        echo ""
        echo "    - hostname: home.akfotekengineering.com"
        echo "      service: http://localhost:5002"
        echo ""
    fi
else
    fail "Config missing: /root/.cloudflared/config.yml"
    exit 1
fi

# ── 7. Stop any orphan processes on port 5002 ─────────────────
info "Clearing port 5002 if occupied..."
PIDS=$(ss -tlnp 2>/dev/null | grep ':5002' | grep -oP 'pid=\K[0-9]+' || true)
if [[ -n "$PIDS" ]]; then
    for PID in $PIDS; do
        kill -9 "$PID" 2>/dev/null && warn "Killed stale process on port 5002 (PID $PID)"
    done
fi

# ── 8. Start both services fresh ──────────────────────────────
info "Starting energy-meter-pq..."
systemctl restart energy-meter-pq.service
sleep 3

if systemctl is-active --quiet energy-meter-pq.service; then
    log "energy-meter-pq is running"
else
    fail "energy-meter-pq failed to start"
    echo "--- Last 20 log lines ---"
    journalctl -u energy-meter-pq -n 20 --no-pager
    exit 1
fi

# ── 9. Confirm Flask is on port 5002 ──────────────────────────
info "Confirming Flask is listening on port 5002..."
sleep 2
if ss -tlnp | grep -q ':5002'; then
    log "Port 5002 is OPEN and listening"
else
    fail "Port 5002 is NOT open — Flask may have crashed"
    journalctl -u energy-meter-pq -n 20 --no-pager
    exit 1
fi

# ── 10. Quick local API test ──────────────────────────────────
info "Testing local API..."
RESP=$(curl -s --max-time 3 http://localhost:5002/api/health || echo "FAILED")
if echo "$RESP" | grep -q "ok"; then
    log "API responded: $RESP"
else
    warn "API did not respond as expected: $RESP"
fi

# ── 11. Start cloudflared ─────────────────────────────────────
info "Starting cloudflared..."
systemctl restart cloudflared.service
sleep 4

if systemctl is-active --quiet cloudflared.service; then
    log "cloudflared is running"
else
    fail "cloudflared failed to start"
    journalctl -u cloudflared -n 20 --no-pager
    exit 1
fi

# ── 12. Print final status ────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   AUTOSTART VERIFICATION COMPLETE                   ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Services:"
systemctl is-active energy-meter-pq &>/dev/null \
    && echo -e "  ${GREEN}●${NC} energy-meter-pq    RUNNING (port 5002)" \
    || echo -e "  ${RED}●${NC} energy-meter-pq    NOT running"
systemctl is-active cloudflared &>/dev/null \
    && echo -e "  ${GREEN}●${NC} cloudflared        RUNNING" \
    || echo -e "  ${RED}●${NC} cloudflared        NOT running"

systemctl is-enabled energy-meter-pq &>/dev/null \
    && echo -e "  ${GREEN}✓${NC} energy-meter-pq    ENABLED (auto-starts on reboot)" \
    || echo -e "  ${RED}✗${NC} energy-meter-pq    NOT enabled for boot"
systemctl is-enabled cloudflared &>/dev/null \
    && echo -e "  ${GREEN}✓${NC} cloudflared        ENABLED (auto-starts on reboot)" \
    || echo -e "  ${RED}✗${NC} cloudflared        NOT enabled for boot"

echo ""
echo "  Local:  http://$(hostname -I | awk '{print $1}'):5002"
echo "  Remote: https://home.akfotekengineering.com"
echo ""
echo "  To verify after reboot:"
echo "    systemctl status energy-meter-pq cloudflared"
echo ""
echo "  To watch live logs:"
echo "    journalctl -u energy-meter-pq -f"
echo "    journalctl -u cloudflared -f"
echo ""
