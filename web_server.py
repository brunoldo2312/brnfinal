# web_server.py — API REST + dashboard RWA.

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
USE_NGROK = os.environ.get("BRN_USE_NGROK", "0") == "1"

WEB_USER = os.environ.get("BRN_WEB_USER", "admin")
WEB_PASS = os.environ.get("BRN_WEB_PASS", "")
FAUCET_MAX_PER_IP = os.environ.get("BRN_FAUCET_MAX_PER_IP", "3")

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app,
                  default_limits=["300 per hour"],
                  storage_uri="memory://")

_blockchain_ref = None
_faucet_handler = None


def set_blockchain(bc):
    global _blockchain_ref
    _blockchain_ref = bc


def set_faucet_handler(fn):
    global _faucet_handler
    _faucet_handler = fn


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not WEB_PASS:
            return Response("Servidor mal configurado: BRN_WEB_PASS vazio.", 500)
        auth = request.authorization
        if not auth or auth.username != WEB_USER or auth.password != WEB_PASS:
            return Response("Acesso negado.", 401,
                            {"WWW-Authenticate": 'Basic realm="BRN RWA"'})
        return f(*args, **kwargs)
    return decorated


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>BRN RWA — Painel</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system,"Segoe UI",Roboto,sans-serif;
         background:#0d1117; color:#e6edf3; margin:0; padding:24px; }
  h1 { margin:0 0 4px; font-size:22px; }
  h2 { font-size:15px; margin:24px 0 8px; color:#8b949e;
       text-transform:uppercase; letter-spacing:.5px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:20px; }
  .cards { display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }
  .card { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:12px 16px; min-width:150px; }
  .card .label { font-size:11px; color:#8b949e; text-transform:uppercase; }
  .card .value { font-size:20px; font-weight:600; margin-top:4px; }
  table { width:100%; border-collapse:collapse; background:#161b22;
          border-radius:8px; overflow:hidden; margin-bottom:12px; }
  th,td { padding:10px 12px; text-align:left; font-size:13px;
          border-bottom:1px solid #21262d; }
  th { background:#1c2128; font-weight:600; color:#8b949e;
       text-transform:uppercase; font-size:11px; }
  tr:hover { background:#1c2128; }
  .addr { font-family:Consolas,monospace; font-size:12px; color:#58a6ff; }
  .amount { color:#3fb950; font-weight:600; }
  .badge { padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }
  .badge.confirmada { background:#1f6feb33; color:#58a6ff; }
  .badge.pendente { background:#d2992233; color:#d29922; }
  .badge.approved { background:#23863633; color:#3fb950; }
  .badge.pending { background:#d2992233; color:#d29922; }
  .badge.revoked, .badge.rejected { background:#f8514933; color:#f85149; }
  .empty { text-align:center; color:#8b949e; padding:40px; }
  .form { background:#161b22; border:1px solid #30363d; border-radius:8px;
          padding:16px; margin-bottom:20px; display:flex; gap:8px; flex-wrap:wrap; }
  .form input, .form select { padding:8px 12px; background:#0d1117; color:#e6edf3;
                  border:1px solid #30363d; border-radius:6px; font-family:monospace; }
  .form button { padding:8px 16px; background:#238636; color:#fff; border:0;
                 border-radius:6px; cursor:pointer; font-weight:600; }
  .form button:hover { background:#2ea043; }
</style>
</head>
<body>
  <h1>🏦 BRN RWA — Plataforma de Ativos Tokenizados</h1>
  <div class="sub">Atualização a cada 3s · <span id="last-update">—</span></div>

  <div class="cards">
    <div class="card"><div class="label">Blocos</div><div class="value" id="stat-blocks">—</div></div>
    <div class="card"><div class="label">Ativos</div><div class="value" id="stat-assets">—</div></div>
    <div class="card"><div class="label">KYC aprovados</div><div class="value" id="stat-kyc">—</div></div>
    <div class="card"><div class="label">Transferências</div><div class="value" id="stat-total">—</div></div>
    <div class="card"><div class="label">Slashed</div><div class="value" id="stat-slashed">—</div></div>
    <div class="card"><div class="label">Finalizado</div><div class="value" id="stat-final">—</div></div>
  </div>

  <div class="form">
    <input id="faucet-addr" placeholder="Endereço brn1… para o faucet BRN" size="50">
    <button onclick="requestFaucet()">Pedir BRN</button>
    <span id="faucet-msg" style="font-size:12px; align-self:center;"></span>
  </div>

  <h2>Ativos registrados</h2>
  <table>
    <thead><tr><th>ID</th><th>Nome</th><th>Tipo</th><th>Emissor</th>
      <th>Supply</th><th>Max</th><th>Restrito</th></tr></thead>
    <tbody id="assets-rows"><tr><td colspan="7" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Transferências recentes</h2>
  <table>
    <thead><tr><th>Data</th><th>Tipo</th><th>Ativo</th><th>Origem</th>
      <th>Destino</th><th>Valor</th><th>Status</th></tr></thead>
    <tbody id="tx-rows"><tr><td colspan="7" class="empty">Carregando…</td></tr></tbody>
  </table>

  <h2>Compliance (KYC)</h2>
  <table>
    <thead><tr><th>Endereço</th><th>Status</th><th>Nível</th>
      <th>Jurisdição</th><th>Verificado por</th><th>Expira</th></tr></thead>
    <tbody id="kyc-rows"><tr><td colspan="6" class="empty">Carregando…</td></tr></tbody>
  </table>

<script>
function shortAddr(a){ return a && a.length>22 ? a.slice(0,10)+'…'+a.slice(-8) : (a||'—'); }

async function load(){
  try {
    const r = await fetch('/api/summary');
    if (r.status === 401) { document.body.innerHTML='<p style="padding:40px">Login necessário.</p>'; return; }
    const d = await r.json();
    document.getElementById('stat-blocks').textContent = d.stats.blocks;
    document.getElementById('stat-assets').textContent = d.stats.assets;
    document.getElementById('stat-kyc').textContent    = d.stats.kyc_approved;
    document.getElementById('stat-total').textContent  = d.stats.transfers;
    document.getElementById('stat-slashed').textContent= d.stats.slashed;
    document.getElementById('stat-final').textContent  = '#' + d.stats.finalized;
    document.getElementById('last-update').textContent = 'atualizado ' + d.stats.now;

    document.getElementById('assets-rows').innerHTML = d.assets.length ? d.assets.map(a => `
      <tr>
        <td class="addr">${a.asset_id}</td>
        <td>${a.name}</td>
        <td>${a.asset_type}</td>
        <td class="addr" title="${a.issuer}">${shortAddr(a.issuer)}</td>
        <td class="amount">${a.total_supply}</td>
        <td>${a.max_supply || '—'}</td>
        <td>${a.transfer_restricted ? '🔒' : '🟢'}</td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">Nenhum ativo.</td></tr>';

    document.getElementById('tx-rows').innerHTML = d.transfers.length ? d.transfers.map(t => `
      <tr>
        <td>${t.datetime}</td>
        <td>${t.type}</td>
        <td class="addr">${t.asset_id}</td>
        <td class="addr" title="${t.from}">${shortAddr(t.from)}</td>
        <td class="addr" title="${t.to}">${shortAddr(t.to)}</td>
        <td class="amount">${t.amount}</td>
        <td><span class="badge ${t.status}">${t.status}</span></td>
      </tr>`).join('') : '<tr><td colspan="7" class="empty">Nenhuma transferência.</td></tr>';

    document.getElementById('kyc-rows').innerHTML = d.kyc.length ? d.kyc.map(k => `
      <tr>
        <td class="addr" title="${k.address}">${shortAddr(k.address)}</td>
        <td><span class="badge ${k.status}">${k.status}</span></td>
        <td>${k.level}</td>
        <td>${k.jurisdiction || '—'}</td>
        <td class="addr">${shortAddr(k.verified_by)}</td>
        <td>${k.expires_at ? new Date(k.expires_at*1000).toLocaleDateString() : '—'}</td>
      </tr>`).join('') : '<tr><td colspan="6" class="empty">Nenhum KYC.</td></tr>';
  } catch(e){ console.error(e); }
}

async function requestFaucet(){
  const addr = document.getElementById('faucet-addr').value.trim();
  const msg = document.getElementById('faucet-msg');
  msg.textContent = '…';
  try {
    const r = await fetch('/api/faucet', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({address: addr})});
    const j = await r.json();
    msg.textContent = j.msg || (j.ok?'ok':'erro');
    msg.style.color = j.ok ? '#3fb950' : '#f85149';
  } catch(e){ msg.textContent = 'erro de rede'; }
}

load();
setInterval(load, 3000);
</script>
</body></html>
"""


@app.route("/")
@require_auth
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/summary")
@require_auth
@limiter.limit("120 per minute")
def api_summary():
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    bc = _blockchain_ref

    # Ativos com supply calculado
    assets = []
    for a in bc.registry.assets.values():
        d = a.to_dict()
        d["total_supply"] = bc.state.total_supply.get(a.asset_id, 0.0)
        assets.append(d)

    # Transferências: últimas N de todos os tipos
    transfers = []
    for blk in bc.chain:
        for tx in blk.transactions:
            transfers.append({
                "type": tx["type"], "asset_id": tx["asset_id"],
                "from": tx["from"], "to": tx["to"],
                "amount": tx["amount"],
                "timestamp": tx["timestamp"],
                "datetime": _iso(tx["timestamp"]),
                "status": "confirmada", "block": blk.index,
            })
    for tx in bc.pending:
        transfers.append({
            "type": tx["type"], "asset_id": tx["asset_id"],
            "from": tx["from"], "to": tx["to"], "amount": tx["amount"],
            "timestamp": tx["timestamp"], "datetime": _iso(tx["timestamp"]),
            "status": "pendente", "block": None,
        })
    transfers.sort(key=lambda t: t["timestamp"], reverse=True)
    transfers = transfers[:100]

    kyc_list = [r.to_dict() for r in bc.registry.compliance.values()]
    kyc_approved = sum(1 for r in kyc_list if r["status"] == "approved")

    return jsonify({
        "assets": assets,
        "transfers": transfers,
        "kyc": kyc_list,
        "stats": {
            "blocks": len(bc.chain),
            "assets": len(bc.registry.assets),
            "kyc_approved": kyc_approved,
            "transfers": len(transfers),
            "slashed": len(bc.slashed),
            "finalized": bc.finality.finalized_height,
            "now": datetime.now().strftime("%H:%M:%S"),
        }
    })


@app.route("/api/portfolio/<address>")
@require_auth
@limiter.limit("60 per minute")
def api_portfolio(address):
    if _blockchain_ref is None:
        return jsonify(error="blockchain não inicializada"), 503
    return jsonify(address=address, portfolio=_blockchain_ref.portfolio(address))


@app.route("/api/assets")
@require_auth
def api_assets():
    if _blockchain_ref is None:
        return jsonify([])
    out = []
    for a in _blockchain_ref.registry.assets.values():
        d = a.to_dict()
        d["total_supply"] = _blockchain_ref.state.total_supply.get(a.asset_id, 0.0)
        out.append(d)
    return jsonify(out)


@app.route("/api/kyc", methods=["POST"])
@require_auth
@limiter.limit("30 per minute")
def api_kyc_register():
    """Aprovar KYC de um endereço. Requer assinatura do transfer_agent."""
    bc = _blockchain_ref
    if bc is None:
        return jsonify(ok=False, msg="blockchain não inicializada"), 503
    d = request.get_json(silent=True) or {}
    required = ["asset_id", "address", "agent_address",
                "agent_private_key", "agent_public_key", "nonce"]
    for r in required:
        if r not in d:
            return jsonify(ok=False, msg=f"faltando '{r}'"), 400

    from bruno_blockchain_real import Transaction
    md = {
        "status": d.get("status", "approved"),
        "level": d.get("level", "basic"),
        "jurisdiction": d.get("jurisdiction", ""),
        "expires_at": float(d.get("expires_at", 0)),
        "restrictions": d.get("restrictions", []),
        "extra": d.get("extra", {}),
    }
    tx = Transaction.build(
        tx_type="kyc_register", asset_id=d["asset_id"],
        sender_address=d["agent_address"], receiver_address=d["address"],
        amount=0, nonce=int(d["nonce"]),
        private_key_hex=d["agent_private_key"],
        public_key_hex=d["agent_public_key"],
        metadata=md)
    r = bc.add_transaction(tx)
    return jsonify(r), (200 if r.get("ok") else 400)


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
    return jsonify(_blockchain_ref.slashing_report() if _blockchain_ref else [])


@app.route("/api/finality")
@require_auth
def api_finality():
    return jsonify(_blockchain_ref.finality_report() if _blockchain_ref else {})


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ngrok": bool(NGROK_AUTHTOKEN)})


def _run_flask():
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)


def start_web_server(blockchain):
    set_blockchain(blockchain)
    threading.Thread(target=_run_flask, daemon=True).start()
    print(f"[web] painel RWA em http://{WEB_HOST}:{WEB_PORT} (user: {WEB_USER})")

    if not USE_NGROK:
        return
    if not NGROK_AUTHTOKEN:
        print("[ngrok] NGROK_AUTHTOKEN ausente.")
        return
    try:
        from pyngrok import ngrok, conf
        conf.get_default().auth_token = NGROK_AUTHTOKEN
        url = ngrok.connect(WEB_PORT, "http").public_url
        print(f"[ngrok] público em: {url}")
    except ImportError:
        print("[ngrok] pyngrok não instalado. pip install pyngrok")
    except Exception as e:
        print(f"[ngrok] erro: {e}")


if __name__ == "__main__":
    from bruno_blockchain_real import Blockchain
    start_web_server(Blockchain())
    while True:
        time.sleep(1)
