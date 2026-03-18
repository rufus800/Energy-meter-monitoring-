#!/bin/bash
# =============================================================
#  Akfotek Ltd — Power Quality Dashboard Setup for IoT2050
#  Deploy path: /root/energy_meter
#  App port:    5002  (dashboards 1+2 already use 5000/5001)
#  Run as root: bash setup_iot2050.sh
# =============================================================
set -e

GREEN='\033[0;32m'; BLUE='\033[0;34m'
YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC}   $1"; }
info() { echo -e "${BLUE}[..]${NC}   $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║  Akfotek — Power Quality Dashboard  IoT2050 Setup   ║"
echo "  ║  Port 5002  |  Path: /root/energy_meter             ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

# ── Root check ────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && fail "Run as root: sudo bash setup_iot2050.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="/root/energy_meter"
SERVICE_NAME="energy-meter-pq"

# ── Check existing dashboards ─────────────────────────────────
info "Checking existing dashboards on this device..."
for p in 5000 5001; do
    if ss -tlnp 2>/dev/null | grep -q ":$p " || netstat -tlnp 2>/dev/null | grep -q ":$p "; then
        log "Port $p is in use (existing dashboard detected)"
    else
        warn "Port $p appears free — existing dashboard may not be running"
    fi
done
log "New dashboard will use port 5002"

# ── Install Python packages if missing ────────────────────────
info "Checking Python dependencies..."
python3 -c "import snap7" 2>/dev/null && log "python-snap7 already installed" || {
    info "Installing python-snap7..."
    pip3 install --break-system-packages python-snap7 2>/dev/null || pip3 install python-snap7
    log "python-snap7 installed"
}
python3 -c "import flask" 2>/dev/null && log "flask already installed" || {
    info "Installing flask + flask-cors..."
    pip3 install --break-system-packages flask flask-cors 2>/dev/null || pip3 install flask flask-cors
    log "flask installed"
}

# ── Deploy files ───────────────────────────────────────────────
info "Deploying to $DEPLOY_DIR ..."
mkdir -p "$DEPLOY_DIR"
cp "$SCRIPT_DIR/plc_reader.py"  "$DEPLOY_DIR/"
cp "$SCRIPT_DIR/dashboard.html" "$DEPLOY_DIR/"
log "Files copied to $DEPLOY_DIR"

# ── Check for port conflict ────────────────────────────────────
if ss -tlnp 2>/dev/null | grep -q ":5002 "; then
    warn "Port 5002 is already in use! Check what is running there:"
    ss -tlnp | grep ":5002" || true
    warn "Stop that process before continuing, or change APP_PORT in plc_reader.py"
    read -p "  Continue anyway? [y/N] " yn
    [[ "$yn" != "y" ]] && exit 1
fi

# ── Install systemd service ────────────────────────────────────
info "Installing systemd service: $SERVICE_NAME ..."
cp "$SCRIPT_DIR/energy-meter.service" /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service
systemctl restart ${SERVICE_NAME}.service
sleep 3

if systemctl is-active --quiet ${SERVICE_NAME}.service; then
    log "Service $SERVICE_NAME is running on port 5002"
else
    warn "Service may not have started. Check logs:"
    echo "  journalctl -u $SERVICE_NAME -n 30"
fi

# ── Cloudflare instructions ────────────────────────────────────
IOT_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   SETUP COMPLETE                                     ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Local access:"
echo "    http://${IOT_IP}:5002"
echo ""
echo "  ── CLOUDFLARE — Add to existing tunnel ─────────────────"
echo ""
echo "  1. Open your existing tunnel config on this device:"
echo "     nano /etc/cloudflared/config.yml"
echo ""
echo "  2. Add this block to the ingress section (before the"
echo "     http_status:404 catch-all at the end):"
echo ""
echo "       - hostname: energy.yourdomain.com"
echo "         service: http://localhost:5002"
echo ""
echo "  3. Add the DNS record for the new subdomain:"
echo "     cloudflared tunnel route dns <your-tunnel-name> energy.yourdomain.com"
echo ""
echo "  4. Restart cloudflared to apply the new route:"
echo "     systemctl restart cloudflared"
echo ""
echo "  5. Verify all 3 dashboards are routing correctly:"
echo "     cloudflared tunnel info <your-tunnel-name>"
echo ""
echo "  Your dashboard will then be live at:"
echo "     https://energy.yourdomain.com"
echo ""
echo "  ── Useful commands ──────────────────────────────────────"
echo "  View logs:    journalctl -u $SERVICE_NAME -f"
echo "  Restart:      systemctl restart $SERVICE_NAME"
echo "  Status:       systemctl status $SERVICE_NAME"
echo ""
