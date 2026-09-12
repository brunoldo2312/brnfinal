# web_server.py — Dashboard com HTTP Basic Auth + rate limiting + faucet.

import os
import threading
import time
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, render_template_string, request, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

NGROK_AUTHTOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
WEB_HOST = os.environ.get("BRN_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("BRN_WEB_PORT", "5000"))
USE_NGROK = os.environ.get("BRN_USE_NGROK", "1") == "1"

WEB_USER = os.environ.get("BRN_WEB_USER", "admin")
WEB_PASS = os.environ.get("BRN_WEB_PASS", "")
FAUCET_MAX_PER_IP = os.environ.get("BRN_FAUCET_MAX_PER_IP", "3")

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["300 per hour"],
    storage_uri="memory://",
)

_blockchain_ref = None
_faucet_handler = None


def set_blockchain(bc):
    global _blockchain_ref
    _blockchain_ref = bc


def set_faucet_handler(fn):
    global _faucet_handler
    _faucet_handler = fn


# ---------------- Autenticação Basic ----------------
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASS:
            return Response("Servidor mal configurado: BRN_WEB_PASS vazio.", 500)
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response(
                "Acesso negado.",
                401,
                {"WWW-Authenticate": 'Basic realm="BRN Dashboard"'}
            )
        return f(*args, **kwargs)
    return decorated


# ---------------- Coleta ----------------
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def collect_transfers(limit: int = 500) -> list:
    if _blockchain_ref is None:
        return []
    transfers = []
    for blk in _blockchain_ref.chain:
        for tx in blk.transactions:
            transfers.append({
                "status": "confirmada",
                "block_index": blk.index,
                "block_hash": blk.hash[:16] + "...",
                "validator": blk.validator,
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "amount": tx.get("amount", 0.0),
                "nonce": tx.get("nonce", 0),
                "timestamp": tx.get("timestamp", blk.timestamp),
                "datetime": _iso(tx.get("timestamp", blk.timestamp)),
            })
    for tx in _blockchain_ref.pending:
        transfers.append({
            "status": "pendente",
            "block_index": None, "block_hash": None, "validator": None,
            "from": tx.get("from", ""), "to": tx.get("to", ""),
            "amount": tx.get("amount", 0.0), "nonce": tx.get("nonce", 0),
            "timestamp": tx.get("timestamp", time.time()),
            "datetime": _iso(tx.get("timestamp", time.time())),
        })
    transfers.sort(key=lambda t: t["timestamp"], reverse=True)
    return transfers[:limit]


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>BRN — Transferências ao vivo</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system,"Segoe UI",Roboto,sans-serif;
         background:#0d1117; color:#e6edf3; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .cards { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px 16px; min-width:150px; }
  .card .label { font-size:11px; color:#8b949e; text-transform:uppercase;
                 letter-spacing:.5px; }
  .card .value { font-size:20px; font-weight:600; margin-top:4px; }
  table { width:100%; border-collapse:collapse; background:#161b22;
          border-radius:8px; overflow:hidden; }
  th,td { padding:10px 12px; text-align:left; font-size:13px;
          border-bottom:1px solid #21262d; }
  th { background:#1c2128; font-weight:600; color:#8b949e;
       text-transform:uppercase; font-size:11px; letter-spacing:.4px; }
  tr:hover { background:#1c2128; }
  .addr { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:#58a6ff; }
  .amount { color:#3fb950; font-weight:600; }
  .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
  .badge.confirmada { background:#1f6feb33; color:#58a6ff; }
  .badge.pendente { background:#d2992233; color:#d29922; }
  .empty { text-align:center; color:#8b949e; padding:40px; }
  .faucet { background:#161b22; border:1px solid #30363d; border-radius:8px;
            padding:16px; margin-bottom:20px; display:flex; gap:8px; align-items:center; }
  .faucet input { flex:1; padding:8px 12px; background:#0d1117; color:#e6edf3;
                  border:1px solid #30363d; border-radius:6px; font-family:monospace; }
  .faucet button { padding:8px 16px; background:#238636; color:#fff; border:0;
                   border-radius:6px; cursor:pointer; font-weight:600; }
  .faucet button:hover { background:#2ea043; }
  .faucet .msg { font-size:12px; margin-left:8px; }
</style>
</head>
<body>
  <h1>🟢 BRN — Transferências ao vivo</h1>
  <div class="sub">Atualização automática a cada 3s · <span id="last-update">—</span></div>

  <div class="cards">
    <div class="card"><div class="label">Blocos</div><div class="value" id="stat-blocks">—</div></div>
    <div class="card"><div class="label">Transferências</div><div class="value" id="stat-total">—</div></div>
    <div class="card"><div class="label">Confirmadas</div><div class="value" id="stat-conf">—</div></div>
    <div class="card"><div class="label">Pendentes</div><div class="value" id="stat-pend">—</div></div>
    <div class="card"><div class="label">Volume</div><div class="value" id="stat-volume">—</div></div>
    <div class="card"><div class="label">Slashed</div><div class="value" id="stat-slashed">—</div></div>
    <div class="card"><div class="label">Finalizado</div><div class="value" id="stat-final">—</div></div>
  </div>

  <div class="faucet">
    <input id="faucet-addr" placeholder="Endereço brn1… para receber do faucet" />
    <button onclick="requestFaucet()">Pedir do Faucet</button>
    <span class="msg" id="faucet-msg"></span>
  </div>

  <table>
    <thead>
      <tr><th>Data / Hora</th><th>Status</th><th>Origem</th><th>Destino</th>
          <th>Valor</th><th>Bloco</th></tr>
    </thead>
    <tbody id="rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

<script>
async function load() {
  try {
    const r = await fetch('/api/transfers');
    if (r.status === 401) {
      document.body.innerHTML = '<p style="padding:40px">Faça login para acessar.</p>';
      return;
    }
    const data = await r.json();
    render(data);
  } catch (e) { console.error(e); }
}
function shortAddr(a) {
  if (!a) return '—';
  return a.length > 22 ? a.slice(0,10)+'…'+a.slice(-8) : a;
}
function render(data) {
  document.getElementById('stat-blocks').textContent = data.stats.blocks;
  document.getElementById('stat-total').textContent  = data.stats.total;
  document.getElementById('stat-conf').textContent   = data.stats.confirmed;
  document.getElementById('stat-pend').textContent   = data.stats.pending;
  document.getElementById('stat-volume').textContent = data.stats.volume.toFixed(2);
  document.getElementById('stat-slashed').textContent= data.stats.slashed;
  document.getElementById('stat-final').textContent  = '#' + data.stats.finalized;
  document.getElementById('last-update').textContent = 'atualizado ' + data.stats.now;

  const rows = document.getElementById('rows');
  if (data.transfers.length === 0) {
    rows.innerHTML = '<tr><td colspan="6" class="empty">Nenhuma transferência ainda.</td></tr>';
    return;
  }
  rows.innerHTML = data.transfers.map(x => `
    <tr>
      <td>${x.datetime}</td>
      <td><span class="badge ${x.status}">${x.status}</span></td>
      <td class="addr" title="${x.from}">${shortAddr(x.from)}</td>
      <td class="addr" title="${x.to}">${shortAddr(x.to)}</td>
      <td class="amount">${x.amount.toFixed(4)} BRN</td>
      <td>${x.block_index !== null ? '#'+x.block_index : '—'}</td>
    </tr>
  `).join('');
}
async function requestFaucet() {
  const addr = document.getElementById('faucet-addr').value.trim();
  const msg  = document.getElementById('faucet-msg');
  msg.textContent = '…';
  try {
    const r = await fetch('/api/faucet', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr})
    });
    const j = await r.json();
    msg.textContent = j.msg || (j.ok ? 'ok' : 'erro');
    msg.style.color = j.ok ? '#3fb950' : '#f85149';
  } catch (e) { msg.textContent = 'erro de rede'; }
}
load();
setInterval(load, 3000);
</script>
</body>
</html>
"""


@app.route("/")
@require_auth
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/transfers")
@require_auth
@limiter.limit("120 per minute")
def api_transfers():
    transfers = collect_transfers()
    confirmed = [t for t in transfers if t["status"] == "confirmada"]
    pending   = [t for t in transfers if t["status"] == "pendente"]
    volume    = sum(t["amount"] for t in confirmed)
    blocks    = len(_blockchain_ref.chain) if _blockchain_ref else 0
    slashed   = len(_blockchain_ref.slashed) if _blockchain_ref else 0
    finalized = (_blockchain_ref.finality.finalized_height if _blockchain_ref else -1)
    return jsonify({
        "transfers": transfers,
        "stats": {
            "blocks": blocks, "total": len(transfers),
            "confirmed": len(confirmed), "pending": len(pending),
            "volume": volume, "slashed": slashed, "finalized": finalized,
            "now": datetime.now().strftime("%H:%M:%S"),
        }
    })


@app.route("/api/faucet", methods=["POST"])
@require_auth
@limiter.limit(f"{FAUCET_MAX_PER_IP} per hour")
def api_faucet():
    if _faucet_handler is None:
        return jsonify({"ok": False, "msg": "Faucet não inicializado."}), 503
    data = request.get_json(silent=True) or {}
    addr = (data.get("address") or "").strip()
    if not addr.startswith("brn1") or len(addr) < 20:
        return jsonify({"ok": False, "msg": "Endereço inválido."}), 400
    r = _faucet_handler(addr)
    return jsonify(r), (200 if r.get("ok") else 429)


@app.route("/api/slashing")
@require_auth
def api_slashing():
    if _blockchain_ref is None:
        return jsonify([])
    return jsonify(_blockchain_ref.slashing_report())


@app.route("/api/finality")
@require_auth
def api_finality():
    if _blockchain_ref is None:
        return jsonify({})
    return jsonify(_blockchain_ref.finality_report())


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ngrok": bool(NGROK_AUTHTOKEN)})


def _run_flask():
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_server(blockchain):
    set_blockchain(blockchain)
    threading.Thread(target=_run_flask, daemon=True).start()
    print(f"[web] dashboard local em http://{WEB_HOST}:{WEB_PORT} (user: {WEB_USER})")

    if not USE_NGROK:
        return
    if not NGROK_AUTHTOKEN:
        print("[ngrok] NGROK_AUTHTOKEN não definido. Pulando túnel.")
        return
    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = NGROK_AUTHTOKEN
        url = ngrok.connect(WEB_PORT, "http").public_url
        print(f"[ngrok] dashboard público em: {url}")
        print(f"[ngrok] use user={WEB_USER} e a senha de BRN_WEB_PASS para acessar")
    except ImportError:
        print("[ngrok] pyngrok não instalado. Rode: pip install pyngrok")
    except Exception as e:
        print(f"[ngrok] erro: {e}")


if __name__ == "__main__":
    from bruno_blockchain_real import Blockchain
    start_web_server(Blockchain())
    while True:
        time.sleep(1)