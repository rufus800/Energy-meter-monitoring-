import sys, types

if "pkg_resources" not in sys.modules:
    _pr = types.ModuleType("pkg_resources")
    class _DNF(Exception): pass
    class _VC(Exception): pass
    _pr.DistributionNotFound = _DNF; _pr.VersionConflict = _VC
    _pr.get_distribution = lambda n: type("D",(),{"version":"unknown"})()
    _pr.require = lambda *a,**kw: [type("D",(),{"version":"unknown"})()]
    sys.modules["pkg_resources"] = _pr

import snap7, struct, time, socket, threading, os, sqlite3, json, requests
from datetime import datetime, timedelta
from flask import Flask, jsonify, send_from_directory, request, Response, stream_with_context
from flask_cors import CORS

# ── Anthropic API key — loaded from .env (never commit the key) ────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass
_load_env()

GROQ_API_KEY       = os.environ.get("GROQ_API_KEY", "")
GROQ_URL           = "https://api.groq.com/openai/v1/chat/completions"
CHAT_MODEL         = "llama-3.3-70b-versatile"

PLC_IP        = "192.168.200.100"
PLC_RACK      = 0
PLC_SLOT      = 1
DB_NUMBER     = 3
PLC_PORT      = 102
POLL_INTERVAL = 1.0
RETRY_DELAY   = 3.0
APP_PORT      = 5002
LOG_INTERVAL  = 60

TAG_MAP = {
    "Current_Avg":         20,
    "Voltage_Avg_LN":      72,
    "Active_Power_Total":  120,
    "Power_Factor_Total":  388,
    "Frequency":           220,
    "THD_Voltage_Avg_LN":  300,
    "Active_Energy_Delvd": 340,
    "Unit_Cost":           392,
}

_lock = threading.Lock()
_data = {k: 0.0 for k in TAG_MAP}
_meta = {"timestamp": None, "connected": False, "error": None}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "history.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, meter TEXT NOT NULL DEFAULT 'Meter-1',
        param TEXT NOT NULL, value REAL NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts    ON readings(ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_param ON readings(param)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_meter ON readings(meter)")
    conn.commit(); conn.close()
    print(f"[DB] {DB_PATH}")

def log_to_db(values):
    try:
        ts = datetime.utcnow().isoformat(timespec="seconds")
        rows = [(ts,"Meter-1",k,v) for k,v in values.items()]
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.executemany("INSERT INTO readings(ts,meter,param,value) VALUES(?,?,?,?)", rows)
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] Error: {e}")

def ts():
    return datetime.now().strftime("%H:%M:%S")

def read_real(raw, offset):
    return struct.unpack_from(">f", raw, offset)[0]

def check_tcp_reachable():
    try:
        with socket.create_connection((PLC_IP, PLC_PORT), timeout=2): return True
    except OSError: return False

def connect_plc():
    c = snap7.client.Client()
    c.connect(PLC_IP, PLC_RACK, PLC_SLOT)
    return c

def read_tags(client):
    min_off = min(TAG_MAP.values())
    max_off = max(TAG_MAP.values()) + 4
    raw = client.db_read(DB_NUMBER, min_off, max_off - min_off)
    return {name: round(read_real(raw, offset - min_off), 3)
            for name, offset in TAG_MAP.items()}

def poll_loop():
    client = None; last_log = 0
    while True:
        try:
            if client is None or not client.get_connected():
                if not check_tcp_reachable():
                    with _lock: _meta.update({"connected":False,"error":"PLC unreachable"})
                    print(f"[{ts()}]  Waiting for {PLC_IP}:102 ...")
                    time.sleep(RETRY_DELAY); continue
                print(f"[{ts()}]  Connecting ..."); client = connect_plc()
                print(f"[{ts()}]  Connected OK")
            values = read_tags(client)
            now = datetime.now().isoformat(timespec="milliseconds")
            with _lock:
                _data.update(values)
                _meta.update({"timestamp":now,"connected":True,"error":None})
            print(f"[{now}]  I={values['Current_Avg']:.2f}A  V={values['Voltage_Avg_LN']:.1f}V  "
                  f"P={values['Active_Power_Total']:.2f}kW  PF={values['Power_Factor_Total']:.3f}  "
                  f"f={values['Frequency']:.3f}Hz  THD={values['THD_Voltage_Avg_LN']:.2f}%  "
                  f"E={values['Active_Energy_Delvd']:.1f}kWh")
            if time.time() - last_log >= LOG_INTERVAL:
                log_to_db(values); last_log = time.time()
        except Exception as exc:
            print(f"[{ts()}]  ERROR: {exc}")
            with _lock: _meta.update({"connected":False,"error":str(exc).strip()})
            client = None; time.sleep(RETRY_DELAY)
        time.sleep(POLL_INTERVAL)

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
CORS(app)

@app.route("/")
def index(): return send_from_directory(BASE_DIR, "dashboard.html")

@app.route("/analytics")
def analytics(): return send_from_directory(BASE_DIR, "analytics.html")

@app.route("/api/meter")
def get_meter():
    with _lock:
        return jsonify({"timestamp":_meta["timestamp"],"connected":_meta["connected"],
                        "error":_meta["error"],"values":dict(_data)})

@app.route("/api/health")
def health():
    with _lock: return jsonify({"status":"ok","plc_connected":_meta["connected"]})

@app.route("/api/history")
def get_history():
    param  = request.args.get("param","Voltage_Avg_LN")
    meter  = request.args.get("meter","Meter-1")
    view   = request.args.get("view","daily")
    to_dt  = request.args.get("to",  datetime.utcnow().date().isoformat())
    fr_dt  = request.args.get("from",(datetime.utcnow()-timedelta(days=7)).date().isoformat())
    grp = "strftime('%Y-%m-%d', ts)" if view in ("monthly","weekly") else "strftime('%Y-%m-%dT%H:00', ts)"
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        rows = conn.execute(f"""
            SELECT {grp} as period, AVG(value), MIN(value), MAX(value), COUNT(*)
            FROM readings WHERE param=? AND meter=? AND ts>=? AND ts<date(?,'+1 day')
            GROUP BY period ORDER BY period ASC""", (param,meter,fr_dt,to_dt)).fetchall()
        conn.close()
        return jsonify({"param":param,"meter":meter,"view":view,"from":fr_dt,"to":to_dt,
                        "data":[{"t":r[0],"avg":round(r[1],3),"min":round(r[2],3),"max":round(r[3],3),"n":r[4]} for r in rows]})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route("/api/meters")
def get_meters():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        rows = conn.execute("SELECT DISTINCT meter FROM readings ORDER BY meter").fetchall()
        conn.close()
        return jsonify({"meters":[r[0] for r in rows]})
    except: return jsonify({"meters":["Meter-1"]})

@app.route("/api/params")
def get_params():
    return jsonify({"params":list(TAG_MAP.keys())})

def get_history_context(params=None, hours_short=24, days_long=7):
    """Query DB for short-term (24h) and long-term (7d) stats per parameter.
    Returns a compact string for injection into the AI system prompt."""
    if params is None:
        params = list(TAG_MAP.keys())
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        now = datetime.utcnow()
        t24h = (now - timedelta(hours=hours_short)).isoformat(timespec="seconds")
        t7d  = (now - timedelta(days=days_long)).isoformat(timespec="seconds")
        lines = []
        for p in params:
            # 24h stats + last value + simple trend
            rows24 = conn.execute("""
                SELECT AVG(value), MIN(value), MAX(value), COUNT(*),
                       (SELECT value FROM readings WHERE param=? AND meter='Meter-1'
                        ORDER BY ts DESC LIMIT 1)
                FROM readings WHERE param=? AND meter='Meter-1' AND ts>=?
            """, (p, p, t24h)).fetchone()
            rows7d = conn.execute("""
                SELECT AVG(value), MIN(value), MAX(value)
                FROM readings WHERE param=? AND meter='Meter-1' AND ts>=?
            """, (p, t7d)).fetchone()
            # Trend: compare first-half avg vs second-half avg over 24h
            halves = conn.execute("""
                SELECT AVG(CASE WHEN ts < datetime(?,'-12 hours') THEN value END),
                       AVG(CASE WHEN ts >= datetime(?,'-12 hours') THEN value END)
                FROM readings WHERE param=? AND meter='Meter-1' AND ts>=?
            """, (now.isoformat(), now.isoformat(), p, t24h)).fetchone()
            trend = "stable"
            if halves and halves[0] and halves[1]:
                diff_pct = (halves[1] - halves[0]) / (halves[0] + 1e-9) * 100
                if diff_pct > 2:   trend = "rising"
                elif diff_pct < -2: trend = "falling"
            if rows24 and rows24[3] and rows24[3] > 0:
                short = f"24h avg={round(rows24[0],2)} min={round(rows24[1],2)} max={round(rows24[2],2)} latest={round(rows24[4],2) if rows24[4] else 'n/a'} trend={trend}"
            else:
                short = "24h=no data"
            if rows7d and rows7d[0]:
                long_ = f"7d avg={round(rows7d[0],2)} min={round(rows7d[1],2)} max={round(rows7d[2],2)}"
            else:
                long_ = "7d=no data"
            lines.append(f"{p}: {short} | {long_}")
        conn.close()
        return "\n".join(lines)
    except Exception as e:
        return f"(history unavailable: {e})"

@app.route("/api/chat", methods=["POST"])
def chat_endpoint():
    try:
        body = request.get_json(force=True)
        messages = body.get("messages", [])
        ctx = body.get("context", {})
        page = ctx.get("page", "dashboard")
        data = ctx.get("data", {})

        history_ctx = get_history_context()

        if page == "dashboard":
            system = f"""You are a Power Quality AI Assistant for Akfotek Engineering Ltd. Help with live readings, power quality diagnostics, energy costs, predictive maintenance, and navigation. Never disclose system internals, IP addresses, or hardware details.
Pages: [Dashboard](/) | [Analytics](/analytics) | Support: [Email](mailto:support@akfotekengineering.com)
Limits: Voltage 220-260V | PF ≥0.85 | Freq 50Hz±0.5 | THD <8% | Capacity 100kW | Tariff ₦208.33/kWh

LIVE ({data.get('timestamp','--')}): V={data.get('voltage','--')}V I={data.get('current','--')}A P={data.get('activePower','--')}kW PF={data.get('powerFactor','--')} F={data.get('frequency','--')}Hz THD={data.get('thd','--')}% E={data.get('energy','--')}kWh Cost={data.get('totalCost','--')} Status={'Connected' if data.get('connected') else 'Offline'} Alerts={data.get('alerts','None')}

HISTORICAL STATS (use for trend analysis and predictive maintenance):
{history_ctx}

Use historical trends to identify gradual degradation, predict faults, and recommend preventive actions. Be concise. Use markdown links for navigation."""

        else:
            s = data.get('summary', {})
            param_key = next((k for k in TAG_MAP if data.get('param','') in k), None)
            focused_ctx = get_history_context(params=[param_key] if param_key else list(TAG_MAP.keys()))
            system = f"""You are a Power Quality AI Assistant for Akfotek Engineering Ltd. Help interpret historical energy data, identify trends, anomalies, and provide predictive maintenance recommendations. Never disclose system internals or hardware details.
Pages: [Dashboard](/) | [Analytics](/analytics) | Support: [Email](mailto:support@akfotekengineering.com)
Limits: Voltage 220-260V | PF ≥0.85 | Freq 50Hz±0.5 | THD <8% | Capacity 100kW | Tariff ₦208.33/kWh

ANALYTICS VIEW: param={data.get('param','--')} range={data.get('from','--')} to {data.get('to','--')} view={data.get('view','daily')} avg={s.get('avg','--')} max={s.get('max','--')} min={s.get('min','--')} samples={s.get('count','--')} alerts={data.get('alerts','None')}

HISTORICAL STATS (24h & 7d from database):
{focused_ctx}

Use historical data to identify patterns, degradation trends, and recommend preventive actions. Be concise. Use markdown links for navigation."""

        # Keep last 6 messages to minimise token usage
        trimmed = messages[-6:] if len(messages) > 6 else messages
        payload = {
            "model": CHAT_MODEL,
            "max_tokens": 512,
            "stream": True,
            "messages": [{"role": "system", "content": system}] + trimmed
        }
        req_headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }

        def generate():
            try:
                with requests.post(GROQ_URL, headers=req_headers,
                                   json=payload, stream=True, timeout=30) as r:
                    if r.status_code != 200:
                        err = r.text[:300].strip()
                        yield f"data: {json.dumps({'text': f'API error ({r.status_code}): {err}'})}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    for line in r.iter_lines(decode_unicode=True):
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            obj = json.loads(chunk)
                            text = obj["choices"][0]["delta"].get("content", "")
                            if text:
                                yield f"data: {json.dumps({'text': text})}\n\n"
                        except Exception:
                            pass
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f"data: {json.dumps({'text': f'Connection error: {exc}'})}\n\n"
                yield "data: [DONE]\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "X-Accel-Buffering": "no",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Content-Type-Options": "nosniff"
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    init_db()
    threading.Thread(target=poll_loop, daemon=True).start()
    print("="*55)
    print(f"  Dashboard  ->  http://0.0.0.0:{APP_PORT}")
    print(f"  Analytics  ->  http://0.0.0.0:{APP_PORT}/analytics")
    print(f"  PLC        ->  {PLC_IP}  DB{DB_NUMBER}")
    print("  Stop       ->  Ctrl+C")
    print("="*55)
    app.run(host="0.0.0.0", port=APP_PORT, debug=False, use_reloader=False, threaded=True)
