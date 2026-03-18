# Power Quality & Energy Monitoring Dashboard
# Akfotek Ltd Engineering

Deploy path : /root/energy_meter
App port    : 5002  (your existing dashboards use 5000 and 5001)
IoT2050 IP  : 192.168.200.1

---

## STEP 1 — Copy files from your PC to IoT2050

Open PowerShell on your PC and run:

    scp -r "C:\Users\ASUS\energy_meter" root@192.168.200.1:/root/

This copies the whole folder to /root/energy_meter on the IoT2050.

---

## STEP 2 — SSH into the IoT2050

    ssh root@192.168.200.1

---

## STEP 3 — Run the setup script

    cd /root/energy_meter
    bash setup_iot2050.sh

The script will:
- Check your existing dashboards on ports 5000/5001 are safe
- Install any missing Python packages
- Deploy files to /root/energy_meter
- Create and enable systemd service: energy-meter-pq
- Auto-start on every reboot

---

## STEP 4 — Test locally

Open a browser and go to:

    http://192.168.200.1:5002

---

## STEP 5 — Add to your existing Cloudflare tunnel

You already have a tunnel running with 2 dashboards.
DO NOT create a new tunnel — just add a new ingress route.

  a) Edit your existing tunnel config:
     nano /etc/cloudflared/config.yml

  b) Add this NEW block inside the ingress section,
     BEFORE the final http_status:404 line:

       - hostname: energy.yourdomain.com
         service: http://localhost:5002

     Example of what your full config should look like:

       tunnel: YOUR_TUNNEL_ID
       credentials-file: /etc/cloudflared/YOUR_TUNNEL_ID.json

       ingress:
         - hostname: dashboard1.yourdomain.com   # your existing
           service: http://localhost:5000

         - hostname: dashboard2.yourdomain.com   # your existing
           service: http://localhost:5001

         - hostname: energy.yourdomain.com       # NEW - add this
           service: http://localhost:5002

         - service: http_status:404              # always last

  c) Add the DNS record:
     cloudflared tunnel route dns <your-tunnel-name> energy.yourdomain.com

  d) Restart cloudflared to apply:
     systemctl restart cloudflared

  e) Confirm all 3 routes are working:
     systemctl status cloudflared
     journalctl -u cloudflared -n 20

Your new dashboard will be live at:
    https://energy.yourdomain.com

---

## Useful Commands on IoT2050

  View logs         : journalctl -u energy-meter-pq -f
  Restart dashboard : systemctl restart energy-meter-pq
  Stop dashboard    : systemctl stop energy-meter-pq
  Service status    : systemctl status energy-meter-pq
  All 3 services    : systemctl status energy-meter-pq cloudflared

---

## Updating files later

From your PC:

  # Update dashboard only (no restart needed)
  scp "C:\Users\ASUS\energy_meter\dashboard.html" root@192.168.200.1:/root/energy_meter/

  # Update Python backend (restart needed)
  scp "C:\Users\ASUS\energy_meter\plc_reader.py" root@192.168.200.1:/root/energy_meter/
  ssh root@192.168.200.1 "systemctl restart energy-meter-pq"

---

## Troubleshooting

  Port 5002 already in use    : ss -tlnp | grep 5002
  PLC not reachable           : ping 192.168.200.100
  PUT/GET error               : TIA Portal > PLC Properties > Protection & Security
  DB3 optimized block access  : Disable in TIA Portal on DB3
  Cloudflare not routing      : Check hostname in config.yml matches DNS record exactly
  All services status         : systemctl status energy-meter-pq cloudflared
